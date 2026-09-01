from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmarks.release.candidate_artifact_path_policy import (
    CandidateArtifactPathPolicyError,
    normalize_junit,
    validate_candidate_full_raw_evidence,
    validate_requirements,
)


def _junit(*, body: str, classname: str = "tests.fixture", name: str = "test_case") -> bytes:
    return (
        "<testsuites><testsuite tests=\"1\" failures=\"0\" errors=\"0\" skipped=\"0\">"
        f'<testcase classname="{classname}" name="{name}">{body}</testcase>'
        "</testsuite></testsuites>"
    ).encode()


def test_normalize_junit_removes_only_checkout_root_and_preserves_identity_and_outcome(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = tmp_path / "candidate-tests.xml"
    output = tmp_path / "normalized.xml"
    fixture_identity = f"tests.fixture[{checkout}/tests/fixture.py]"
    source.write_bytes(
        _junit(
            body=(
                "<skipped/><system-out>Traceback: "
                f"{checkout}/tests/test_demo.py:7: reason</system-out>"
            ),
            classname=fixture_identity,
        )
    )

    result = normalize_junit(source, output, checkout)
    normalized = output.read_bytes()

    assert result.replacements == 1
    assert f"{checkout}/tests/test_demo.py".encode() not in normalized
    assert b"tests/test_demo.py:7: reason" in normalized
    assert fixture_identity.encode() in normalized
    assert b"<skipped" in normalized
    assert b"<system-out>Traceback: tests/test_demo.py:7: reason</system-out>" in normalized
    assert result.input_sha256 != result.output_sha256


def test_normalize_junit_accepts_the_exact_supplied_symlinked_root_spelling(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    (real_parent / "checkout").mkdir()
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    checkout = alias_parent / "checkout"
    source = tmp_path / "candidate-tests.xml"
    output = tmp_path / "normalized.xml"
    source.write_bytes(
        _junit(body=f"<skipped>{checkout}/tests/test_demo.py:7: reason</skipped>")
    )

    result = normalize_junit(source, output, checkout)

    assert result.replacements == 1
    assert b"tests/test_demo.py:7: reason" in output.read_bytes()


@pytest.mark.parametrize(
    "unsafe",
    [
        "/opt/other/checkout/tests/test_demo.py:7",
        "/home/other/checkout/tests/test_demo.py:7",
        r"C:\\other\\checkout\\tests\\test_demo.py:7",
        r"\\server\share\tests\test_demo.py:7",
        "Secret: do-not-retain",
    ],
)
def test_normalize_junit_rejects_unrelated_paths_and_secret_markers(
    tmp_path: Path, unsafe: str
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = tmp_path / "candidate-tests.xml"
    output = tmp_path / "normalized.xml"
    source.write_bytes(_junit(body=f"<system-out>{unsafe}</system-out>"))

    with pytest.raises(CandidateArtifactPathPolicyError):
        normalize_junit(source, output, checkout)


def test_normalize_junit_keeps_explicit_identity_fixture_path(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = tmp_path / "candidate-tests.xml"
    output = tmp_path / "normalized.xml"
    identity = r"tests.fixture[/home/runner/work/fixture.py]"
    raw = _junit(body="", classname=identity)
    source.write_bytes(raw)

    result = normalize_junit(source, output, checkout)

    assert result.replacements == 0
    assert output.read_bytes() == raw


def test_normalize_junit_does_not_exempt_namespaced_identity_attributes(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = tmp_path / "candidate-tests.xml"
    output = tmp_path / "normalized.xml"
    source.write_bytes(
        b'<testsuites xmlns:x="urn:fixture"><testsuite><testcase '
        b'classname="tests.fixture" name="test_case" '
        b'x:classname="/home/runner/work/DeepLaw/leak.py"/>'
        b"</testsuite></testsuites>"
    )

    with pytest.raises(CandidateArtifactPathPolicyError):
        normalize_junit(source, output, checkout)
    assert not output.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX spelling policy")
def test_posix_normalizer_does_not_remove_backslash_root_variant(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = tmp_path / "candidate-tests.xml"
    output = tmp_path / "normalized.xml"
    non_native = str(checkout).replace("/", "\\") + r"\tests\test_demo.py:7"
    source.write_bytes(_junit(body=f"<system-out>{non_native}</system-out>"))

    result = normalize_junit(source, output, checkout)

    assert result.replacements == 0
    assert output.read_bytes() == source.read_bytes()


def test_requirements_relative_uv_output_comment_passes_and_runner_path_fails(
    tmp_path: Path,
) -> None:
    good = tmp_path / "candidate-requirements.txt"
    good.write_text(
        "# This file was autogenerated by uv via the following command:\n"
        "#    uv export --output-file candidate-requirements.txt\n"
        "package==1.0.0 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    validate_requirements(good)

    bad = tmp_path / "bad-requirements.txt"
    bad.write_text(
        "#    uv export --output-file /home/runner/work/DeepLaw/requirements.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateArtifactPathPolicyError):
        validate_requirements(bad)


def test_final_validator_allows_identity_fixtures_and_skips_structured_or_binary_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw"
    verified = root / "verified-candidate-artifacts"
    verified.mkdir(parents=True)
    (verified / "candidate-requirements.txt").write_text(
        "#    uv export --output-file candidate-requirements.txt\n",
        encoding="utf-8",
    )
    (root / "candidate-tests.xml").write_bytes(
        _junit(
            body="<system-out>tests/test_demo.py:7: reason</system-out>",
            classname=r"tests.fixture[/home/runner/work/fixture.py]",
        )
    )
    wheel_bytes = b"binary /home/runner/work/DeepLaw Secret: ignored\x00"
    (verified / "deeplaw-0.13.0-py3-none-any.whl").write_bytes(wheel_bytes)
    (root / "platform-core-test-manifest-v2.json").write_text(
        '{"node_id":"fixture[/home/runner/work/example.py]"}\n',
        encoding="utf-8",
    )

    result = validate_candidate_full_raw_evidence(root)

    assert result.requirements_files == 1
    assert result.files_scanned == 2
    assert result.bytes_scanned == sum(
        path.stat().st_size
        for path in (
            verified / "candidate-requirements.txt",
            root / "candidate-tests.xml",
        )
    )


def test_final_validator_rejects_path_in_skipped_system_text(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    (root / "candidate-requirements.txt").write_text(
        "#    uv export --output-file candidate-requirements.txt\n",
        encoding="utf-8",
    )
    (root / "candidate-tests.xml").write_bytes(
        _junit(body="<system-out>/home/runner/work/DeepLaw/tests/test.py:4</system-out>")
    )

    with pytest.raises(CandidateArtifactPathPolicyError):
        validate_candidate_full_raw_evidence(root)


def test_final_validator_requires_one_retained_requirements_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    outside = tmp_path / "candidate-requirements.txt"
    outside.write_text(
        "#    uv export --output-file candidate-requirements.txt\n",
        encoding="utf-8",
    )

    with pytest.raises(CandidateArtifactPathPolicyError, match="exactly one"):
        validate_candidate_full_raw_evidence(root)
    with pytest.raises(CandidateArtifactPathPolicyError, match="escapes"):
        validate_candidate_full_raw_evidence(root, requirements_path=outside)


@pytest.mark.parametrize(
    "filename",
    ["auth.json", "credentials.txt", "private-key.pem", "raw-log.txt"],
)
def test_final_validator_rejects_formal_forbidden_filenames(
    tmp_path: Path,
    filename: str,
) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    (root / "candidate-requirements.txt").write_text(
        "#    uv export --output-file candidate-requirements.txt\n",
        encoding="utf-8",
    )
    (root / filename).write_text("sanitized\n", encoding="utf-8")

    with pytest.raises(CandidateArtifactPathPolicyError, match="prohibited"):
        validate_candidate_full_raw_evidence(root)


def test_normalize_junit_rejects_symlink_and_unsafe_xml(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    real = tmp_path / "real.xml"
    real.write_bytes(_junit(body=""))
    link = tmp_path / "link.xml"
    if hasattr(os, "symlink"):
        link.symlink_to(real)
        with pytest.raises(CandidateArtifactPathPolicyError):
            normalize_junit(link, tmp_path / "normalized.xml", checkout)

    unsafe = tmp_path / "unsafe.xml"
    unsafe.write_bytes(
        b'<!DOCTYPE testsuite [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<testsuite><testcase classname="tests.fixture" name="test_case">'
        b"<system-out>&x;</system-out></testcase></testsuite>"
    )
    with pytest.raises(CandidateArtifactPathPolicyError):
        normalize_junit(unsafe, tmp_path / "unsafe-normalized.xml", checkout)
