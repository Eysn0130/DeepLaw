from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

from ..util import canonical_json, sha256_bytes

PROFILE_SCHEMA: Final = "deeplaw.living-wiki-compiler-profile/v1"
SEMANTIC_PROFILE_SCHEMA: Final = "deeplaw.semantic-compilation-profile/v2"
SEMANTIC_PROFILE_V3_SCHEMA: Final = "deeplaw.semantic-compilation-profile/v3"

SEMANTIC_DUTIES: Final = (
    "source_summary",
    "key_claims",
    "entities",
    "concepts",
    "events",
    "procedures",
    "comparisons",
    "typed_relations",
    "contradiction_scan",
    "identity_resolution",
    "unresolved_questions",
    "source_coverage",
    "affected_synthesis_detection",
    "overview_impact",
    "limitations_and_warnings",
)

REQUIRED_SEMANTIC_DUTIES: Final = frozenset(
    {
        "source_summary",
        "contradiction_scan",
        "identity_resolution",
        "source_coverage",
        "affected_synthesis_detection",
        "limitations_and_warnings",
    }
)

_DEFAULT_TEMPLATE = {
    "schema_version": PROFILE_SCHEMA,
    "compiler_profile": "living-wiki-agent",
    "compiler_profile_version": "1",
    "prompt_template_id": "deeplaw.living-wiki-compile/v1",
    "plan_contract": "deeplaw.source-compilation-plan/v1",
    "authority": "agent_derived",
    "legal_authority": False,
    "network_policy": "host-controlled",
    "hidden_reasoning_persistence": False,
    "source_instruction_policy": "untrusted-data",
    "publication_policy": "source-revision-all-or-nothing",
}

DEFAULT_COMPILER_PROFILE: Final = {
    **_DEFAULT_TEMPLATE,
    "prompt_config_sha256": sha256_bytes(
        canonical_json(
            {
                "prompt_template_id": _DEFAULT_TEMPLATE["prompt_template_id"],
                "plan_contract": _DEFAULT_TEMPLATE["plan_contract"],
                "authority": _DEFAULT_TEMPLATE["authority"],
                "legal_authority": _DEFAULT_TEMPLATE["legal_authority"],
                "source_instruction_policy": _DEFAULT_TEMPLATE[
                    "source_instruction_policy"
                ],
            }
        ).encode("utf-8")
    ),
    "plan_configuration_sha256": sha256_bytes(
        canonical_json(
            {
                "plan_contract": _DEFAULT_TEMPLATE["plan_contract"],
                "publication_policy": _DEFAULT_TEMPLATE["publication_policy"],
                "identity_policy": "exact-semantic-key-alias-collision-fail-closed",
                "source_binding": "exact-revision-fragment-locator",
            }
        ).encode("utf-8")
    ),
}

_SEMANTIC_TEMPLATE = {
    "schema_version": SEMANTIC_PROFILE_SCHEMA,
    "compiler_profile": "living-wiki-agent",
    "compiler_profile_version": "2",
    "prompt_template_id": "deeplaw.living-wiki-observe-finalize/v2",
    "observation_plan_contract": "deeplaw.source-compilation-observation-plan/v2",
    "publication_plan_contract": "deeplaw.semantic-publication-plan/v2",
    "authority": "agent_derived",
    "legal_authority": False,
    "network_policy": "host-controlled",
    "hidden_reasoning_persistence": False,
    "source_instruction_policy": "untrusted-data",
    "publication_policy": "run-finalization-all-or-nothing",
    "semantic_duties": list(SEMANTIC_DUTIES),
}

SEMANTIC_COMPILER_PROFILE: Final = {
    **_SEMANTIC_TEMPLATE,
    "prompt_config_sha256": sha256_bytes(
        canonical_json(
            {
                "prompt_template_id": _SEMANTIC_TEMPLATE["prompt_template_id"],
                "observation_plan_contract": _SEMANTIC_TEMPLATE[
                    "observation_plan_contract"
                ],
                "publication_plan_contract": _SEMANTIC_TEMPLATE[
                    "publication_plan_contract"
                ],
                "semantic_duties": _SEMANTIC_TEMPLATE["semantic_duties"],
                "authority": "agent_derived",
                "legal_authority": False,
                "source_instruction_policy": "untrusted-data",
            }
        ).encode("utf-8")
    ),
    "plan_configuration_sha256": sha256_bytes(
        canonical_json(
            {
                "observation_identity": "run-packet-content-addressed",
                "inventory_policy": "bounded-run-local-noncanonical",
                "identity_policy": (
                    "run-finalization-exact-key-alias-collision-fail-closed"
                ),
                "source_binding": "exact-revision-fragment-locator-quote",
                "publication_policy": "run-finalization-all-or-nothing",
            }
        ).encode("utf-8")
    ),
}

# v3 is deliberately additive.  Observation packets remain the v2 contract; the
# deterministic applicability policy is owned by DeepLaw and is bound into every
# v3 publication/finalization artifact rather than being supplied by a model.
SEMANTIC_APPLICABILITY_POLICY: Final = {
    "policy_id": "deeplaw.semantic-duty-applicability/v1",
    "duties": list(SEMANTIC_DUTIES),
    "rules": {
        "source_summary": "admitted-nonempty-source-v1",
        "key_claims": "admitted-nonempty-source-v1",
        "entities": "matching-observation-or-existing-v1",
        "concepts": "matching-observation-or-existing-v1",
        "events": "matching-observation-existing-or-timeline-v1",
        "procedures": "procedure-media-structure-observation-or-existing-v1",
        "comparisons": "table-structure-observation-or-existing-v1",
        "typed_relations": "relation-observation-or-two-identities-v1",
        "contradiction_scan": "deterministic-scan-v1",
        "identity_resolution": "deterministic-scan-v1",
        "unresolved_questions": "question-signal-v1",
        "source_coverage": "deterministic-scan-v1",
        "affected_synthesis_detection": "deterministic-scan-v1",
        "overview_impact": "overview-impact-signal-v1",
        "limitations_and_warnings": "deterministic-scan-v1",
    },
    "closed_facts": (
        "source metadata/media/bytes/lifecycle; bounded Source IR node types and "
        "signals; fragment inventory; observation kinds/refs; existing objects and "
        "relations; synthesis inputs; registered grant policy context"
    ),
}
SEMANTIC_APPLICABILITY_POLICY_SHA256: Final = sha256_bytes(
    canonical_json(SEMANTIC_APPLICABILITY_POLICY).encode("utf-8")
)

_SEMANTIC_V3_TEMPLATE = {
    "schema_version": SEMANTIC_PROFILE_V3_SCHEMA,
    "compiler_profile": "living-wiki-agent",
    "compiler_profile_version": "3",
    "prompt_template_id": "deeplaw.living-wiki-observe-finalize/v3",
    "observation_plan_contract": "deeplaw.source-compilation-observation-plan/v2",
    "publication_plan_contract": "deeplaw.semantic-publication-plan/v3",
    "duty_report_contract": "deeplaw.semantic-compilation-duty-report/v2",
    "finalization_packet_contract": "deeplaw.semantic-finalization-packet/v2",
    "applicability_policy": SEMANTIC_APPLICABILITY_POLICY["policy_id"],
    "applicability_policy_sha256": SEMANTIC_APPLICABILITY_POLICY_SHA256,
    "authority": "agent_derived",
    "legal_authority": False,
    "network_policy": "host-controlled",
    "hidden_reasoning_persistence": False,
    "source_instruction_policy": "untrusted-data",
    "publication_policy": "run-finalization-all-or-nothing",
    "semantic_duties": list(SEMANTIC_DUTIES),
}

SEMANTIC_COMPILER_PROFILE_V3: Final = {
    **_SEMANTIC_V3_TEMPLATE,
    "prompt_config_sha256": sha256_bytes(
        canonical_json(
            {
                "prompt_template_id": _SEMANTIC_V3_TEMPLATE["prompt_template_id"],
                "observation_plan_contract": _SEMANTIC_V3_TEMPLATE["observation_plan_contract"],
                "publication_plan_contract": _SEMANTIC_V3_TEMPLATE["publication_plan_contract"],
                "duty_report_contract": _SEMANTIC_V3_TEMPLATE["duty_report_contract"],
                "finalization_packet_contract": _SEMANTIC_V3_TEMPLATE[
                    "finalization_packet_contract"
                ],
                "applicability_policy": _SEMANTIC_V3_TEMPLATE["applicability_policy"],
                "applicability_policy_sha256": SEMANTIC_APPLICABILITY_POLICY_SHA256,
                "semantic_duties": _SEMANTIC_V3_TEMPLATE["semantic_duties"],
                "authority": "agent_derived",
                "legal_authority": False,
                "source_instruction_policy": "untrusted-data",
            }
        ).encode("utf-8")
    ),
    "plan_configuration_sha256": sha256_bytes(
        canonical_json(
            {
                "observation_identity": "run-packet-content-addressed",
                "inventory_policy": "bounded-run-local-noncanonical",
                "identity_policy": "run-finalization-exact-key-alias-collision-fail-closed",
                "source_binding": "exact-revision-fragment-locator-quote",
                "publication_policy": "run-finalization-all-or-nothing",
                "applicability_policy": SEMANTIC_APPLICABILITY_POLICY["policy_id"],
                "applicability_policy_sha256": SEMANTIC_APPLICABILITY_POLICY_SHA256,
                "ordered_duty_digest": "canonical-duty-type-order-v1",
            }
        ).encode("utf-8")
    ),
}


def compiler_profile(
    profile: str = "living-wiki-agent",
    version: str = "1",
) -> dict[str, Any]:
    """Return a closed, repository-owned provenance descriptor."""

    if profile != DEFAULT_COMPILER_PROFILE["compiler_profile"]:
        raise KeyError("compiler profile is unavailable")
    if version == "1":
        return deepcopy(DEFAULT_COMPILER_PROFILE)
    if version == "2":
        return deepcopy(SEMANTIC_COMPILER_PROFILE)
    if version == "3":
        return deepcopy(SEMANTIC_COMPILER_PROFILE_V3)
    raise KeyError("compiler profile is unavailable")
