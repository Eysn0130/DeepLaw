from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from ..backfill import BackfillService
from ..compilation import (
    CompilationCoordinator,
    SemanticCompilationService,
    SynthesisRefreshService,
)
from ..compilation import (
    compiler_profile as get_compiler_profile,
)
from ..editor_bridge import context_for_editor
from ..knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _validate_contract,
    autonomous_core_installed,
)
from ..knowledge_store import KnowledgeVault
from ..persistent_read_runtime import PersistentReadRuntime
from ..read_services import SourceReadService, WikiReadService
from ..retrieval import PurposeAwareRetrievalService

_T = TypeVar("_T")


class KnowledgeOSError(Exception):
    """Stable base exception for the supported KnowledgeOS Python API."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class KnowledgeOSValidationError(KnowledgeOSError):
    pass


class KnowledgeOSNotFoundError(KnowledgeOSError):
    pass


class KnowledgeOSPermissionError(KnowledgeOSError):
    pass


class KnowledgeOSConflictError(KnowledgeOSError):
    pass


def _invoke(function: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    try:
        return function(*args, **kwargs)
    except KnowledgeOSError:
        raise
    except PermissionError as error:
        raise KnowledgeOSPermissionError(
            "permission_denied",
            "The Knowledge OS operation is outside its granted boundary.",
        ) from error
    except KeyError as error:
        raise KnowledgeOSNotFoundError(
            "not_found",
            "The requested Knowledge OS object is unavailable.",
        ) from error
    except ValueError as error:
        raise KnowledgeOSValidationError(
            "invalid_request",
            "The Knowledge OS request does not match its public contract.",
        ) from error
    except RuntimeError as error:
        raise KnowledgeOSConflictError(
            "state_conflict",
            "The Knowledge OS state changed or failed integrity validation.",
        ) from error


@dataclass(frozen=True)
class CompilationRun:
    """Public handle for one resumable Compilation Run."""

    _coordinator: CompilationCoordinator
    compilation_run_id: str
    grant_id: str
    _initial_receipt: dict[str, Any] | None = None
    compiler_profile_version: str = "1"

    def begin_receipt(self) -> dict[str, Any]:
        if self._initial_receipt is None:
            return self.status()
        return dict(self._initial_receipt)

    def next_packet(self) -> dict[str, Any] | None:
        if self.compiler_profile_version in {"2", "3"}:
            return _invoke(
                SemanticCompilationService(self._coordinator.root).next_observation_packet,
                self.compilation_run_id,
            )
        return _invoke(self._coordinator.next_packet, self.compilation_run_id)

    def stage_observations(
        self,
        plan: dict[str, Any],
        *,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        service = _invoke(SemanticCompilationService, self._coordinator.root)
        return _invoke(
            service.stage_observations,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            plan=plan,
            confirm_no_case_data=confirm_no_case_data,
        )

    def semantic_inventory(
        self,
        *,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        service = _invoke(SemanticCompilationService, self._coordinator.root)
        return _invoke(
            service.inventory,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def finalization_packet(self) -> dict[str, Any]:
        service = _invoke(SemanticCompilationService, self._coordinator.root)
        return _invoke(service.finalization_packet, self.compilation_run_id)

    def stage_publication(
        self,
        plan: dict[str, Any],
        *,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        service = _invoke(SemanticCompilationService, self._coordinator.root)
        return _invoke(
            service.stage_publication,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            plan=plan,
            confirm_no_case_data=confirm_no_case_data,
        )

    def stage(
        self,
        plan: dict[str, Any],
        *,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        return _invoke(
            self._coordinator.stage,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            plan=plan,
            confirm_no_case_data=confirm_no_case_data,
        )

    def validate(
        self,
        *,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        return _invoke(
            self._coordinator.validate,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def commit(
        self,
        *,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        target = self._coordinator.commit
        if self.compiler_profile_version in {"2", "3"}:
            target = SemanticCompilationService(self._coordinator.root).commit
        return _invoke(
            target,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def resume(
        self,
        *,
        project: bool = False,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        return _invoke(
            self._coordinator.resume,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            project=project,
            confirm_no_case_data=confirm_no_case_data,
        )

    def abort(
        self,
        *,
        reason: str,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        return _invoke(
            self._coordinator.abort,
            grant_id=self.grant_id,
            compilation_run_id=self.compilation_run_id,
            reason=reason,
            confirm_no_case_data=confirm_no_case_data,
        )

    def status(self) -> dict[str, Any]:
        if self.compiler_profile_version in {"2", "3"}:
            service = _invoke(SemanticCompilationService, self._coordinator.root)
            return _invoke(service.status, self.compilation_run_id)
        return _invoke(self._coordinator.status, self.compilation_run_id)

    def explain(self) -> dict[str, Any]:
        if self.compiler_profile_version in {"2", "3"}:
            service = _invoke(SemanticCompilationService, self._coordinator.root)
            return _invoke(service.explain, self.compilation_run_id)
        return _invoke(self._coordinator.explain, self.compilation_run_id)


@dataclass(frozen=True)
class _CompilationsAPI:
    _root: Path

    def profile(
        self,
        profile: str = "living-wiki-agent",
        version: str = "1",
    ) -> dict[str, Any]:
        return _invoke(get_compiler_profile, profile, version)

    def begin(
        self,
        *,
        grant_id: str,
        source_revision_id: str,
        compiler_profile: str,
        compiler_profile_version: str,
        host_identity: str,
        prompt_template_id: str,
        prompt_config_sha256: str,
        plan_configuration_sha256: str,
        model_identity: str | None = None,
        packet_max_fragments: int = 32,
        confirm_no_case_data: bool = False,
    ) -> CompilationRun:
        registered_profile = _invoke(
            get_compiler_profile,
            compiler_profile,
            compiler_profile_version,
        )
        if (
            prompt_template_id != registered_profile["prompt_template_id"]
            or prompt_config_sha256 != registered_profile["prompt_config_sha256"]
            or plan_configuration_sha256 != registered_profile["plan_configuration_sha256"]
        ):
            raise KnowledgeOSValidationError(
                "compiler_profile_mismatch",
                "Compilation provenance does not match the registered compiler profile.",
            )
        coordinator = _invoke(CompilationCoordinator, self._root)
        result = _invoke(
            coordinator.begin,
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            compiler_profile=compiler_profile,
            compiler_profile_version=compiler_profile_version,
            host_identity=host_identity,
            model_identity=model_identity,
            prompt_template_id=prompt_template_id,
            prompt_config_sha256=prompt_config_sha256,
            plan_configuration_sha256=plan_configuration_sha256,
            packet_max_fragments=packet_max_fragments,
            confirm_no_case_data=confirm_no_case_data,
        )
        return CompilationRun(
            coordinator,
            result["compilation_run_id"],
            grant_id,
            result,
            compiler_profile_version,
        )

    def open(self, *, compilation_run_id: str, grant_id: str) -> CompilationRun:
        coordinator = _invoke(CompilationCoordinator, self._root)
        status = _invoke(coordinator.status, compilation_run_id)
        return CompilationRun(
            coordinator,
            compilation_run_id,
            grant_id,
            compiler_profile_version=status["compiler_profile_version"],
        )

    def status(self, compilation_run_id: str) -> dict[str, Any]:
        coordinator = _invoke(CompilationCoordinator, self._root)
        status = _invoke(coordinator.status, compilation_run_id)
        if status["compiler_profile_version"] in {"2", "3"}:
            return _invoke(SemanticCompilationService(self._root).status, compilation_run_id)
        return status

    def explain(self, compilation_run_id: str) -> dict[str, Any]:
        coordinator = _invoke(CompilationCoordinator, self._root)
        status = _invoke(coordinator.status, compilation_run_id)
        if status["compiler_profile_version"] in {"2", "3"}:
            return _invoke(SemanticCompilationService(self._root).explain, compilation_run_id)
        return _invoke(coordinator.explain, compilation_run_id)

    def refresh(
        self,
        *,
        grant_id: str,
        source_revision_id: str,
        replacement_source_revision_id: str | None = None,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        coordinator = _invoke(CompilationCoordinator, self._root)
        return _invoke(
            coordinator.refresh,
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            replacement_source_revision_id=replacement_source_revision_id,
            confirm_no_case_data=confirm_no_case_data,
        )


@dataclass(frozen=True)
class _RetrievalAPI:
    _root: Path

    def query(self, query: str, **options: Any) -> dict[str, Any]:
        service = PurposeAwareRetrievalService(self._root)
        return _invoke(service.query, query, **options)


@dataclass(frozen=True)
class _ContextAPI:
    _root: Path
    _runtime_factory: Callable[[], PersistentReadRuntime]

    def compile(
        self,
        *,
        task: str,
        goal: str | None = None,
        purpose: str = "answer",
        policy: str | None = None,
        scope: str = "project",
        max_sensitivity: str = "private",
        limit: int = 8,
        max_chars: int = 8_000,
        max_tokens: int = 6_000,
        max_sources: int = 12,
        graph_hops: int = 1,
        retrieval_mode: str = "hybrid",
        as_of: str | None = None,
        kinds: tuple[str, ...] = (),
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        runtime = _invoke(self._runtime_factory)
        snapshot = _invoke(runtime.get_snapshot, operation="context")
        return _invoke(
            snapshot.store.build_capsule,
            task=task,
            goal=goal,
            purpose=purpose,
            policy=policy,
            scope=scope,
            max_sensitivity=max_sensitivity,
            limit=limit,
            max_chars=max_chars,
            max_tokens=max_tokens,
            max_sources=max_sources,
            graph_hops=graph_hops,
            retrieval_mode=retrieval_mode,
            as_of=as_of,
            kinds=kinds,
            confirm_no_case_data=confirm_no_case_data,
            _runtime_snapshot=snapshot,
        )


@dataclass(frozen=True)
class _SourcesAPI:
    _root: Path

    def list(self, **options: Any) -> dict[str, Any]:
        return _invoke(SourceReadService(self._root).execute, action="list", **options)

    def get(self, source_id: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            SourceReadService(self._root).execute,
            action="get",
            source_id=source_id,
            **options,
        )

    def fragment(self, fragment_id: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            SourceReadService(self._root).execute,
            action="fragment",
            fragment_id=fragment_id,
            **options,
        )

    def diff(
        self,
        old_source_id: str,
        new_source_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        return _invoke(
            SourceReadService(self._root).execute,
            action="diff",
            old_source_id=old_source_id,
            new_source_id=new_source_id,
            **options,
        )

    def compilation_status(
        self,
        *,
        source_revision_id: str | None = None,
    ) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            with AutonomousKnowledgeStore(self._root, read_only=True) as store:
                filters = ""
                parameters: tuple[Any, ...] = ()
                if source_revision_id is not None:
                    filters = "WHERE source_revision_id = ?"
                    parameters = (source_revision_id,)
                rows = store.connection.execute(
                    f"""
                    SELECT compilation_run_id, source_revision_id,
                           compiler_profile, compiler_profile_version,
                           status, packet_count, created_at, updated_at
                    FROM source_compilation_runs_v1
                    {filters}
                    ORDER BY created_at, compilation_run_id
                    LIMIT 1001
                    """,
                    parameters,
                ).fetchall()
                result = {
                    "schema_version": "deeplaw.source-compilation-status/v1",
                    "source_revision_id": source_revision_id,
                    "runs": [dict(row) for row in rows[:1000]],
                    "run_count": len(rows[:1000]),
                    "truncated": len(rows) > 1000,
                    "audit_head": store.audit_head,
                }
                _validate_contract("source-compilation-status.v1.schema.json", result)
                return result

        return _invoke(read)


@dataclass(frozen=True)
class _BackfillAPI:
    _root: Path

    def propose(self, **request: Any) -> dict[str, Any]:
        return _invoke(BackfillService(self._root).propose, **request)

    def validate(self, **request: Any) -> dict[str, Any]:
        return _invoke(BackfillService(self._root).validate, **request)

    def promote(self, **request: Any) -> dict[str, Any]:
        return _invoke(BackfillService(self._root).promote, **request)

    def status(self, draft_id: str) -> dict[str, Any]:
        return _invoke(BackfillService(self._root).status, draft_id)


@dataclass(frozen=True)
class _SynthesesAPI:
    _root: Path

    def refresh_tasks(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return _invoke(SynthesisRefreshService(self._root).tasks, status=status)

    def begin_refresh(self, **request: Any) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).begin, **request)

    def refresh_packet(self, synthesis_refresh_run_id: str) -> dict[str, Any] | None:
        return _invoke(
            SynthesisRefreshService(self._root).packet,
            synthesis_refresh_run_id,
        )

    def stage_refresh(self, **request: Any) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).stage, **request)

    def validate_refresh(self, **request: Any) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).validate, **request)

    def commit_refresh(self, **request: Any) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).commit, **request)

    def refresh_status(self, synthesis_refresh_run_id: str) -> dict[str, Any]:
        return _invoke(
            SynthesisRefreshService(self._root).status,
            synthesis_refresh_run_id,
        )

    def refresh_explain(self, synthesis_refresh_run_id: str) -> dict[str, Any]:
        return _invoke(
            SynthesisRefreshService(self._root).explain,
            synthesis_refresh_run_id,
        )

    def refresh_coverage(self) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).coverage)

    def resume_refresh(self, **request: Any) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).resume, **request)

    def abort_refresh(self, **request: Any) -> dict[str, Any]:
        return _invoke(SynthesisRefreshService(self._root).abort, **request)


@dataclass(frozen=True)
class _WikiAPI:
    _root: Path

    def page(self, wiki_path: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            WikiReadService(self._root).execute,
            action="page",
            wiki_path=wiki_path,
            **options,
        )

    def backlinks(self, wiki_path: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            WikiReadService(self._root).execute,
            action="backlinks",
            wiki_path=wiki_path,
            **options,
        )

    def outlinks(self, wiki_path: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            WikiReadService(self._root).execute,
            action="outlinks",
            wiki_path=wiki_path,
            **options,
        )

    def local_graph(self, knowledge_id: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            WikiReadService(self._root).execute,
            action="local_graph",
            knowledge_id=knowledge_id,
            **options,
        )

    def browse_kind(self, kind: str, **options: Any) -> dict[str, Any]:
        return _invoke(
            WikiReadService(self._root).execute,
            action="browse_kind",
            kind=kind,
            **options,
        )

    def recent_changes(self, **options: Any) -> dict[str, Any]:
        return _invoke(
            WikiReadService(self._root).execute,
            action="recent_changes",
            **options,
        )


@dataclass(frozen=True)
class _EditorContextAPI:
    _root: Path

    def compile(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return _invoke(context_for_editor, self._root, envelope)


@dataclass(frozen=True)
class KnowledgeOS:
    """Stable public facade; persistence internals are intentionally hidden."""

    _root: Path
    _runtime: PersistentReadRuntime | None = None
    _closed: bool = False

    @classmethod
    def open(cls, path: str | Path) -> KnowledgeOS:
        root = Path(path).expanduser().absolute()

        def verify() -> None:
            if not autonomous_core_installed(root):
                raise RuntimeError("Autonomous Knowledge OS is not initialized")
            with (
                KnowledgeVault(root, read_only=True) as legacy,
                AutonomousKnowledgeStore(root, read_only=True) as store,
            ):
                if (
                    legacy.audit_head != store.legacy_audit_head
                    or not legacy.verify_integrity()["valid"]
                    or not store.verify()["valid"]
                ):
                    raise RuntimeError("Knowledge OS integrity is invalid")

        _invoke(verify)
        return cls(root)

    def _ensure_runtime(self) -> PersistentReadRuntime:
        if self._closed:
            raise RuntimeError("Knowledge OS is closed")
        runtime = self._runtime
        if runtime is None:
            runtime = _invoke(PersistentReadRuntime, self._root)
            object.__setattr__(self, "_runtime", runtime)
        return runtime

    def close(self) -> None:
        """Close the verified read snapshot and its bounded identity observer."""

        runtime = self._runtime
        if runtime is not None:
            runtime.close()
        object.__setattr__(self, "_closed", True)

    def __enter__(self) -> KnowledgeOS:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        self.close()
        return False

    @property
    def compilations(self) -> _CompilationsAPI:
        return _CompilationsAPI(self._root)

    @property
    def semantic_compilations(self) -> _CompilationsAPI:
        return _CompilationsAPI(self._root)

    @property
    def retrieval(self) -> _RetrievalAPI:
        return _RetrievalAPI(self._root)

    @property
    def context(self) -> _ContextAPI:
        return _ContextAPI(self._root, self._ensure_runtime)

    @property
    def sources(self) -> _SourcesAPI:
        return _SourcesAPI(self._root)

    @property
    def backfill(self) -> _BackfillAPI:
        return _BackfillAPI(self._root)

    @property
    def syntheses(self) -> _SynthesesAPI:
        return _SynthesesAPI(self._root)

    @property
    def wiki(self) -> _WikiAPI:
        return _WikiAPI(self._root)

    @property
    def editor_context(self) -> _EditorContextAPI:
        return _EditorContextAPI(self._root)

    def verify(self) -> dict[str, Any]:
        """Return the bounded canonical and derived integrity receipt."""

        def verify_store() -> dict[str, Any]:
            with AutonomousKnowledgeStore(self._root, read_only=True) as store:
                return store.verify()

        return _invoke(verify_store)
