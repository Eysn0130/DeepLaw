"""Named, deterministic Living Wiki projection profiles."""

from __future__ import annotations

from typing import Any

_PROFILE_SCHEMA = "deeplaw.projection-profile/v1"
_PROFILE_VERSION = "1"
_PROFILE_NAMES = frozenset({"minimal", "standard", "full"})

# Keep feature names explicit.  The projection manifest binds this complete
# dictionary so a rebuild cannot silently change the selected surface.
_FEATURES = (
    "root_index",
    "overview",
    "source_pages",
    "core_object_pages",
    "recent_changes",
    "gaps",
    "kind_shards",
    "kind_indexes",
    "communities",
    "global_canvas",
    "kind_canvas",
    "community_canvas",
    "per_object_canvas",
    "local_canvas_per_object",
)


def _profile(*, name: str, enabled: set[str]) -> dict[str, Any]:
    return {
        "schema_version": _PROFILE_SCHEMA,
        "name": name,
        "version": _PROFILE_VERSION,
        **{feature: feature in enabled for feature in _FEATURES},
    }


_PROFILES = {
    "minimal": _profile(
        name="minimal",
        enabled={
            "root_index",
            "overview",
            "source_pages",
            "core_object_pages",
            "recent_changes",
            "gaps",
        },
    ),
    "standard": _profile(
        name="standard",
        enabled={
            "root_index",
            "overview",
            "source_pages",
            "core_object_pages",
            "recent_changes",
            "gaps",
            "kind_shards",
            "kind_indexes",
            "communities",
            "global_canvas",
            "kind_canvas",
            "community_canvas",
        },
    ),
    "full": _profile(
        name="full",
        enabled={
            "root_index",
            "overview",
            "source_pages",
            "core_object_pages",
            "recent_changes",
            "gaps",
            "kind_shards",
            "kind_indexes",
            "communities",
            "global_canvas",
            "kind_canvas",
            "community_canvas",
            "per_object_canvas",
            "local_canvas_per_object",
        },
    ),
}


def projection_profile(name: str = "standard") -> dict[str, Any]:
    """Resolve a named profile into a fresh, contract-shaped plain dictionary."""

    if not isinstance(name, str) or name not in _PROFILE_NAMES:
        raise ValueError("projection profile is invalid")
    # Return a new dict so callers cannot mutate the registry used by later
    # rebuilds.  Values are scalar strings/bools by contract.
    return dict(_PROFILES[name])


__all__ = ["projection_profile"]
