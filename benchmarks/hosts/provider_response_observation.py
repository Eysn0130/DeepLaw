"""Pure, bounded observation of a Provider ChatCompletion response model field.

The caller supplies response bytes and its current frozen expected model. A
matching field records only the Provider's reported identity; it does not prove
underlying weights, model execution, response quality, or formal Authority.
Request selectors and Host configuration are never used as response evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 65536
MAX_SSE_LINES = 16384
MAX_SSE_RECORDS = 4096
MAX_SSE_LINE_BYTES = 65536
MAX_SSE_RECORD_BYTES = 262144
_MAX_METADATA_BYTES = 256
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CONTENT_TYPE = re.compile(
    r'(application/json|text/event-stream)(?: *; *charset *= *(?:utf-8|"utf-8"))?',
    re.IGNORECASE,
)


class ProviderResponseObservationError(ValueError):
    """A fixed diagnostic code without response or unexpected model content."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProviderResponseObservationError(code) from None


def _check_json_cost(text: str) -> None:
    depth = items = 0
    quoted = escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            items += 1
            _require(depth <= MAX_JSON_DEPTH, "response_json_depth")
        elif character in "]}":
            depth -= 1
        elif character in ",:":
            items += 1
        _require(items <= MAX_JSON_ITEMS, "response_json_budget")


def _json_object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, "response_json_duplicate_key")
            result[key] = value
        return result

    def constant(_: str) -> Any:
        raise ProviderResponseObservationError("response_json_nonfinite") from None

    def number(text: str) -> float:
        value = float(text)
        _require(math.isfinite(value), "response_json_nonfinite")
        return value

    try:
        text = raw.decode("utf-8", errors="strict")
        _check_json_cost(text)
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant,
                           parse_float=number)
        # JSON permits escaped lone surrogates; these are not strict UTF-8 data.
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, str):
                item.encode("utf-8", errors="strict")
    except ProviderResponseObservationError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProviderResponseObservationError("response_json_invalid") from None
    _require(isinstance(value, dict), "response_shape_invalid")
    return value


def _observe_model(raw: bytes, *, expected_model: str, chunk: bool) -> None:
    value = _json_object(raw)
    _require(value.get("object") == ("chat.completion.chunk" if chunk else "chat.completion")
             and "error" not in value, "response_shape_invalid")
    _require("model" in value and isinstance(value["model"], str) and bool(value["model"]),
             "response_model_missing")
    _require(value["model"] == expected_model, "response_model_mismatch")
    _require(isinstance(value.get("id"), str) and 0 < len(value["id"].encode()) <= 1024
             and type(value.get("created")) is int and 0 <= value["created"] <= 10**16
             and isinstance(value.get("choices"), list) and len(value["choices"]) <= 64
             and all(isinstance(choice, dict) for choice in value["choices"]),
             "response_shape_invalid")


def _observe_sse(raw: bytes, *, expected_model: str) -> int:
    _require(raw.endswith(b"\n"), "response_sse_partial")
    _require(raw.count(b"\n") <= MAX_SSE_LINES, "response_sse_budget")
    count = records = record_bytes = 0
    data: list[bytes] = []
    done = False
    # Drop the split sentinel, so a single newline cannot close a data record.
    for line in raw.split(b"\n")[:-1]:
        _require(len(line) <= MAX_SSE_LINE_BYTES, "response_sse_budget")
        if line.endswith(b"\r"):
            line = line[:-1]
        _require(b"\r" not in line, "response_sse_invalid")
        try:
            line.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ProviderResponseObservationError("response_sse_invalid") from None
        if not line:
            records += 1
            _require(records <= MAX_SSE_RECORDS, "response_sse_budget")
            if data:
                payload = b"\n".join(data)
                _require(not done, "response_sse_terminal_invalid")
                if payload == b"[DONE]":
                    _require(count > 0, "response_model_missing")
                    done = True
                else:
                    _observe_model(payload, expected_model=expected_model, chunk=True)
                    count += 1
            data.clear()
            record_bytes = 0
            continue
        record_bytes += len(line) + 1
        _require(record_bytes <= MAX_SSE_RECORD_BYTES, "response_sse_budget")
        if line.startswith(b":"):
            continue
        field, separator, value = line.partition(b":")
        _require(bool(separator), "response_sse_invalid")
        if value.startswith(b" "):
            value = value[1:]
        if field == b"data":
            data.append(value)
        elif field in (b"event", b"id", b"retry"):
            _require(len(value) <= _MAX_METADATA_BYTES
                     and all(32 <= character <= 126 for character in value),
                     "response_sse_invalid")
            if field == b"retry":
                _require(re.fullmatch(rb"[0-9]{1,10}", value) is not None
                         and int(value) <= 2**32 - 1, "response_sse_invalid")
        else:
            raise ProviderResponseObservationError("response_sse_invalid") from None
    _require(not data and record_bytes == 0 and done, "response_sse_partial")
    return count


def observe_provider_response_model(
    raw: bytes, content_type: str, *, expected_model: str
) -> dict[str, Any]:
    """Project actual model fields from one complete JSON or SSE response.

    All parsing is in memory, with fixed byte, depth, item, line and record
    bounds. The projection is reported identity only, without model execution,
    underlying-weights, or formal-Authority claims. Failure exposes a fixed code.
    """
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE_BYTES, "response_body_bound")
    _require(type(expected_model) is str and _MODEL.fullmatch(expected_model) is not None,
             "expected_model_invalid")
    _require(type(content_type) is str and 0 < len(content_type) <= 128
             and all(32 <= ord(character) <= 126 for character in content_type),
             "response_content_type_invalid")
    match = _CONTENT_TYPE.fullmatch(content_type.strip())
    _require(match is not None, "response_content_type_invalid")
    if match.group(1).lower() == "application/json":
        _observe_model(raw, expected_model=expected_model, chunk=False)
        count, format_name = 1, "json"
    else:
        count, format_name = _observe_sse(raw, expected_model=expected_model), "sse"
    return {
        "expected_model": expected_model,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
        "format": format_name,
        "model_observation_count": count,
        "completed_response_observed": True,
        "source": "provider_response_model_field",
    }
