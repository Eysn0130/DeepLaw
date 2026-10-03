"""Source-bound synthetic procedure for the supervised Host development lane.

This fixture uses the normal semantic compiler. It is not a demonstration that
source-free remember objects have Statement coverage in the default query path.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from benchmarks.v013 import evidence_wiki_candidate as evidence
from deeplaw.api import KnowledgeOS
from deeplaw.compilation.profiles import compiler_profile
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import canonical_json, stable_id


def seed_procedure(vault: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    body = canonical_json({key: checkpoint[key] for key in ("decision", "next_action")})
    title = "Supervised continuity procedure"
    semantic_key = "supervised:continuity:procedure"
    # The source is public, deterministic task material, never a Host transcript.
    with TemporaryDirectory(prefix="deeplaw-supervised-source-") as directory:
        source_path = Path(directory) / "supervised-procedure.md"
        source_path.write_bytes(body.encode("utf-8"))
        with KnowledgeVault(vault, read_only=False) as store:
            source = compile_source(
                store, source_path, source_kind="document", sensitivity="public",
                confirm_no_case_data=True
            )
            source_id = source["source"]["source_id"]
            manifest = store.source_review_manifest(source_id)
            store.approve_source_assets(
                source_id, confirm_reviewed=True, confirm_quarantined=True,
                review_manifest_sha256=manifest["review_manifest_sha256"],
                reviewer_id="supervised-development-fixture",
                review_reason="Reviewed synthetic public task fixture; not legal Authority.",
            )
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="supervised-development-compiler",
            operations=(
                "begin_compilation", "stage_compilation_batch", "validate_compilation",
                "commit_compilation", "freeze_semantic_inventory",
                "stage_semantic_observations", "finalize_semantic_compilation",
            ),
            allowed_scope="project", max_sensitivity="public",
        )
        knowledge_id = stable_id(
            "knowledge", store.vault_id, "source-compilation", "procedure", semantic_key
        )
    profile = compiler_profile(version="3")
    with KnowledgeOS.open(vault) as knowledge_os:
        run = knowledge_os.compilations.begin(
            grant_id=grant["grant_id"],
            source_revision_id=source["identity"]["source_revision_id"],
            compiler_profile=profile["compiler_profile"], compiler_profile_version="3",
            host_identity="supervised-development-fixture", model_identity=None,
            prompt_template_id=profile["prompt_template_id"],
            prompt_config_sha256=profile["prompt_config_sha256"],
            plan_configuration_sha256=profile["plan_configuration_sha256"],
            confirm_no_case_data=True,
        )
        packet = run.next_packet()
        if packet is None or len(packet["fragments"]) != 1:
            raise ValueError("synthetic procedure must compile to exactly one fragment")
        fragment = packet["fragments"][0]
        if fragment["text"] != body:
            raise ValueError("synthetic procedure bytes changed during compilation")
        source_ref = evidence._source_ref(packet, fragment)
        observation = evidence._observation(
            run_id=run.compilation_run_id, packet=packet, source_ref=source_ref,
            semantic_key=semantic_key, title=title, body=body,
        )
        observation["kind"] = "procedure"
        observation["observation_id"] = evidence.ObservationStore.observation_id(
            compilation_run_id=run.compilation_run_id,
            packet_id=packet["packet_id"], observation=observation,
        )
        run.stage_observations({
            "schema_version": "deeplaw.source-compilation-observation-plan/v2",
            "compilation_run_id": run.compilation_run_id,
            "source_revision_id": packet["source_revision_id"],
            "packet_id": packet["packet_id"],
            "expected_audit_head": packet["input_audit_head"],
            "observations": [observation],
            "coverage": {"packet_fragment_count": 1,
                         "covered_fragment_ids": [fragment["fragment_id"]],
                         "omitted_fragments": [], "ratio": 1.0},
            "warnings": [],
        }, confirm_no_case_data=True)
        plan = evidence._source_plan(
            packet=packet, source_ref=source_ref, semantic_key=semantic_key,
            knowledge_id=knowledge_id, title=title, body=body,
        )
        plan["object_actions"][0]["kind"] = "procedure"
        run.stage(plan, confirm_no_case_data=True)
        inventory = run.semantic_inventory(confirm_no_case_data=True)
        finalization = run.finalization_packet()
        publication = evidence._publication_plan(
            run=run, packet=packet, source_plan=plan,
            statement_plan=evidence._statement_plan(
                packet=packet, source_ref=source_ref, statement_text=body, body=body
            ),
            observation=observation, inventory=inventory, finalization=finalization,
        )
        run.stage_publication(publication, confirm_no_case_data=True)
        validation = run.validate(confirm_no_case_data=True)
        if not validation["valid"]:
            raise ValueError("synthetic procedure compilation was not valid")
        return run.commit(confirm_no_case_data=True)
