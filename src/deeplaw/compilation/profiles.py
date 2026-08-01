from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

from ..util import canonical_json, sha256_bytes

PROFILE_SCHEMA: Final = "deeplaw.living-wiki-compiler-profile/v1"
SEMANTIC_PROFILE_SCHEMA: Final = "deeplaw.semantic-compilation-profile/v2"

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
    raise KeyError("compiler profile is unavailable")
