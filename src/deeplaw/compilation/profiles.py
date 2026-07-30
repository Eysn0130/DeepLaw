from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

from ..util import canonical_json, sha256_bytes

PROFILE_SCHEMA: Final = "deeplaw.living-wiki-compiler-profile/v1"

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


def compiler_profile(
    profile: str = "living-wiki-agent",
    version: str = "1",
) -> dict[str, Any]:
    """Return a closed, repository-owned provenance descriptor."""

    if profile != DEFAULT_COMPILER_PROFILE["compiler_profile"] or version != "1":
        raise KeyError("compiler profile is unavailable")
    return deepcopy(DEFAULT_COMPILER_PROFILE)
