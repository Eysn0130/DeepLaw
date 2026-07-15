from __future__ import annotations

from dataclasses import replace

import pytest

from deeplaw.evidence_graph import derive_relations
from deeplaw.models import Segment, SourceDocument
from deeplaw.store import create_release_database


def make_document(*, title: str) -> SourceDocument:
    return SourceDocument(
        document_id="doc_" + "1" * 24,
        title=title,
        document_number=None,
        aliases=(),
        promulgated_on=None,
        jurisdiction="CN",
        relative_path=f"{title}.docx",
        format="DOCX",
        official_source="https://example.gov.cn/law.docx",
        source_sha256="a" * 64,
        byte_size=100,
        document_type="law",
        issuer="测试机关",
        authority_rank=100,
        effective_from="2020-01-01",
        status="verified_current",
    )


def _segment(document_id: str, text: str, *, ordinal: int = 1) -> Segment:
    from deeplaw.util import sha256_bytes, stable_id

    digest = sha256_bytes(text.encode())
    return Segment(
        segment_id=stable_id("seg", document_id, str(ordinal), digest),
        document_id=document_id,
        ordinal=ordinal,
        kind="article",
        text=text,
        text_sha256=digest,
    )


def test_relations_require_an_exact_known_document_name() -> None:
    criminal = make_document(title="中华人民共和国刑法")
    procedure = replace(
        make_document(title="公安机关办理刑事案件程序规定"),
        document_id="doc_" + "2" * 24,
    )
    unrelated = _segment(procedure.document_id, "本规定适用于刑事案件办理工作。")

    assert derive_relations([criminal, procedure], [unrelated]) == ()


def test_relations_keep_provenance_and_infer_amendment() -> None:
    criminal = make_document(title="中华人民共和国刑法")
    amendment = replace(
        make_document(title="中华人民共和国刑法修正案（十二）"),
        document_id="doc_" + "2" * 24,
        effective_from="2024-03-01",
    )
    segment = _segment(amendment.document_id, "对《中华人民共和国刑法》作如下修改。")

    relations = derive_relations([criminal, amendment], [segment])

    assert len(relations) == 1
    relation = relations[0]
    assert relation.predicate == "amends"
    assert relation.subject_document_id == amendment.document_id
    assert relation.object_document_id == criminal.document_id
    assert relation.provenance_segment_id == segment.segment_id
    assert relation.review_status == "deterministic_exact"
    assert relation.valid_from == "2024-03-01"


def test_nearest_relation_verb_distinguishes_repeal_from_amendment() -> None:
    old = make_document(title="旧金融办法")
    decision = replace(
        make_document(title="关于修改和废止部分规章的决定"),
        document_id="doc_" + "2" * 24,
    )
    segment = _segment(decision.document_id, "一、修改其他规定。二、废止《旧金融办法》。")

    relation = derive_relations([old, decision], [segment])[0]

    assert relation.predicate == "repeals"


def test_negated_or_proposed_relation_is_never_promoted_to_repeal() -> None:
    old = make_document(title="旧金融办法")
    decision = replace(
        make_document(title="关于修改和废止部分规章的决定"),
        document_id="doc_" + "2" * 24,
    )

    for text in (
        "本决定不废止《旧金融办法》。",
        "本决定尚未废止《旧金融办法》。",
        "本决定拟废止《旧金融办法》。",
        "本决定可能废止《旧金融办法》。",
    ):
        relation = derive_relations([old, decision], [_segment(decision.document_id, text)])[0]
        assert relation.predicate == "cites"


def test_storage_rejects_unimplemented_reviewed_edge_upgrade(tmp_path) -> None:
    old = make_document(title="旧金融办法")
    decision = replace(
        make_document(title="关于修改和废止部分规章的决定"),
        document_id="doc_" + "2" * 24,
    )
    segment = _segment(decision.document_id, "本决定废止《旧金融办法》。")
    relation = derive_relations([old, decision], [segment])[0]

    with pytest.raises(ValueError, match="deterministic provenance contract"):
        create_release_database(
            tmp_path / "forged.sqlite3",
            release_id="lawrel_" + "1" * 32,
            release_metadata={},
            documents=[old, decision],
            segments=[segment],
            relations=(replace(relation, review_status="reviewed"),),
        )
