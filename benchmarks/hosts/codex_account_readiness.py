"""Closed, metadata-only Codex App Server account readiness probe.

The probe deliberately reuses :class:`CodexAppServerClient` for process
supervision and JSONL framing.  It sends only the public initialize lifecycle
and ``account/read`` with ``refreshToken: false``; account payload fields are
interpreted into a small closed projection and then discarded.
"""

from __future__ import annotations

import queue
from collections.abc import Mapping
from typing import Any

from benchmarks.hosts.codex_app_server_client import (
    CodexAppServerClient,
    CodexAppServerProtocolError,
)

_ACCOUNT_READ_PARAMS = {"refreshToken": False}
_ALLOWED_NOTIFICATIONS = frozenset(
    {
        "configWarning",
        "account/updated",
        "account/rateLimits/updated",
        "remoteControl/status/changed",
    }
)
_ALLOWED_ACCOUNT_TYPES = {
    "chatgpt": "chatgpt",
    "apiKey": "api_key",
}


class CodexAccountReadinessClient(CodexAppServerClient):
    """Read only closed account metadata without retaining provider payloads."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Do not allow a caller to accidentally turn the readiness seam back
        # into the normal hashed event projection.
        kwargs["discard_payload_projection"] = True
        super().__init__(*args, **kwargs)

    def _send_message(self, message: Mapping[str, Any]) -> None:
        """Permit only initialize, initialized, and the exact account/read."""

        if not isinstance(message, Mapping):
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "account readiness outbound message is invalid"
            )
        method = message.get("method")
        if method == "initialize":
            params = message.get("params")
            if (
                set(message) != {"id", "method", "params"}
                or type(message.get("id")) is not int
                or message["id"] <= 0
                or not isinstance(params, Mapping)
                or params != self._initialize_params()
                or not isinstance(params.get("capabilities"), Mapping)
                or params["capabilities"].get("experimentalApi") is not True
            ):
                self._fail_closed()
                raise CodexAppServerProtocolError(
                    "account readiness initialize request is invalid"
                )
        elif method == "initialized":
            if set(message) != {"method"}:
                self._fail_closed()
                raise CodexAppServerProtocolError(
                    "account readiness initialized notification is invalid"
                )
        elif method == "account/read":
            params = message.get("params")
            if (
                set(message) != {"id", "method", "params"}
                or type(message.get("id")) is not int
                or message["id"] <= 0
                or not isinstance(params, Mapping)
                or set(params) != {"refreshToken"}
                or params.get("refreshToken") is not False
            ):
                self._fail_closed()
                raise CodexAppServerProtocolError(
                    "account readiness account/read request is invalid"
                )
        else:
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "account readiness outbound method is not allowed"
            )
        super()._send_message(message)

    def _initialize_params(self) -> dict[str, Any]:
        return {
            "clientInfo": {
                "name": self.client_name,
                "title": self.client_title,
                "version": self.client_version,
            },
            "capabilities": {"experimentalApi": True},
        }

    def initialize(self) -> dict[str, Any]:
        """Perform initialization and discard its response payload."""

        self.start()
        if self._initialized:
            return {}
        result = self._request("initialize", self._initialize_params())
        if not isinstance(result, Mapping):
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "account readiness initialize response is invalid"
            )
        # The response is intentionally not retained or returned to callers.
        self._send_notification("initialized")
        self._initialized = True
        return {}

    def _handle_notification(self, message: Mapping[str, Any]) -> None:
        """Drain a known status envelope without projecting its private payload."""

        if not isinstance(message, Mapping) or message.get("method") not in _ALLOWED_NOTIFICATIONS:
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "account readiness received unsupported notification"
            )
        params = message.get("params")
        if params is not None and not isinstance(params, Mapping):
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "account readiness notification params are invalid"
            )
        return None

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        del message
        self._fail_closed()
        raise CodexAppServerProtocolError(
            "account readiness rejects server requests"
        )

    def close(self) -> None:
        """Stop the bounded transport and clear transient wire buffers."""

        try:
            super().close()
        finally:
            self._stdout_buffer.clear()
            self._leak_scan_tails = {"stdout": b"", "stderr": b""}
            self._turn_pending_notifications.clear()
            self._turn_pending_notification_bytes = 0
            self._tool_outputs.clear()
            self._tool_call_observations.clear()
            self._final_text_parts.clear()
            self._completed_item_text = None
            self._events.clear()
            while True:
                try:
                    self._output_queue.get_nowait()
                except queue.Empty:
                    break

    def account_readiness(self) -> dict[str, Any]:
        """Return a bounded account-type/readiness projection."""

        self.start()
        if not self._initialized:
            self.initialize()
        result = self._request("account/read", _ACCOUNT_READ_PARAMS)
        try:
            return self._project_account_readiness(result)
        except CodexAppServerProtocolError:
            self._fail_closed()
            raise

    @staticmethod
    def _project_account_readiness(result: Any) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise CodexAppServerProtocolError(
                "account/read response is invalid"
            )
        if set(result) != {"account", "requiresOpenaiAuth"}:
            raise CodexAppServerProtocolError(
                "account/read response is invalid"
            )
        requires_openai_auth = result.get("requiresOpenaiAuth")
        if type(requires_openai_auth) is not bool:
            raise CodexAppServerProtocolError(
                "account/read response is invalid"
            )

        account = result.get("account")
        if account is None:
            account_type = "none"
        elif isinstance(account, Mapping):
            wire_type = account.get("type")
            if not isinstance(wire_type, str):
                raise CodexAppServerProtocolError(
                    "account/read response is invalid"
                )
            account_type = _ALLOWED_ACCOUNT_TYPES.get(wire_type)
            if account_type is None:
                raise CodexAppServerProtocolError(
                    "account/read response is invalid"
                )
            # ``email`` and ``planType`` are intentionally never read.  The
            # mapping is discarded once this closed enum is produced.
        else:
            raise CodexAppServerProtocolError(
                "account/read response is invalid"
            )

        return {
            "account_type": account_type,
            "requires_openai_auth": requires_openai_auth,
            "status": "executed",
            "formal_admission": False,
            "claim_eligible": False,
        }


__all__ = ["CodexAccountReadinessClient"]
