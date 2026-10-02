"""Public synthetic response bytes only; no network or Provider calls."""

import hashlib
import json

import pytest

from benchmarks.hosts import provider_response_observation as observation

MODEL = "deepseek-public-fixture"


def completion(*, chunk=False, model=MODEL):
    return {
        "id": "public-response-1", "created": 1234, "model": model,
        "object": "chat.completion.chunk" if chunk else "chat.completion",
        "choices": [{"index": 0, "delta" if chunk else "message": {
            "role": "assistant", "content": "public synthetic answer",
        }, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def sse(*records, terminal=True):
    return b"".join(b"data: " + encoded(value) + b"\n\n" for value in records) + (
        b"data: [DONE]\n\n" if terminal else b""
    )


def observe(raw, content_type="application/json", *, expected_model=MODEL):
    return observation.observe_provider_response_model(
        raw, content_type, expected_model=expected_model,
    )


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=UTF-8",
                                        'APPLICATION/JSON; charset="utf-8"'])
def test_complete_json_exports_only_reported_model_and_bounded_metadata(content_type):
    raw = encoded(completion())
    assert observe(raw, content_type) == {
        "expected_model": MODEL, "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw), "format": "json", "model_observation_count": 1,
        "completed_response_observed": True, "source": "provider_response_model_field",
    }
    assert "synthetic answer" not in json.dumps(observe(raw))


@pytest.mark.parametrize("content_type", ["text/event-stream", "text/event-stream; charset=utf-8"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_complete_sse_with_bounded_comments_and_control_lines_counts_only_model_chunks(
    content_type, newline,
):
    raw = (b": public keepalive\nid: public-stream\nevent: message\nretry: 100\n\n"
           + sse(completion(chunk=True), {**completion(chunk=True), "choices": []})
           + b": terminal keepalive\n\n").replace(b"\n", newline)
    value = observe(raw, content_type)
    assert value["format"] == "sse"
    assert value["model_observation_count"] == 2
    assert value["completed_response_observed"] is True
    assert value["response_bytes"] == len(raw)
    assert value["response_sha256"] == hashlib.sha256(raw).hexdigest()


def test_multiline_sse_data_json_is_bounded_and_parsed_as_one_record():
    parts = json.dumps(completion(chunk=True), indent=2).encode().split(b"\n")
    raw = b"\n".join(b"data: " + part for part in parts) + b"\n\ndata: [DONE]\n\n"
    assert observe(raw, "text/event-stream")["model_observation_count"] == 1


@pytest.mark.parametrize("tail", [b"event: message", b"id: public-stream",
                                  b"retry: 100", b": keepalive"])
def test_terminal_sse_still_requires_complete_trailing_control_records(tail):
    raw = sse(completion(chunk=True)) + tail + b"\n"
    with pytest.raises(observation.ProviderResponseObservationError, match="response_sse_partial"):
        observe(raw, "text/event-stream")
    assert observe(raw + b"\n", "text/event-stream")["completed_response_observed"] is True


@pytest.mark.parametrize("model", [
    "other-model", "/private/canary/model", "sk-canary-private-value",
    MODEL + "\u202e", {"private": "canary"}, None, "",
])
def test_unexpected_or_missing_model_never_exposes_its_value(model):
    for chunk in (False, True):
        raw = (sse(completion(chunk=True, model=model)) if chunk
               else encoded(completion(model=model)))
        with pytest.raises(observation.ProviderResponseObservationError) as caught:
            observe(raw, "text/event-stream" if chunk else "application/json")
        assert str(caught.value) in {"response_model_missing", "response_model_mismatch"}
        assert "canary" not in str(caught.value) and "private" not in str(caught.value)
        assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("chunk", [False, True])
def test_model_is_not_inferred_from_config_request_or_nested_fields(chunk):
    value = completion(chunk=chunk)
    del value["model"]
    value["request"] = {"model": MODEL}
    value["config"] = {"model": MODEL}
    raw = sse(value) if chunk else encoded(value)
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_model_missing$"):
        observe(raw, "text/event-stream" if chunk else "application/json")


def test_model_drift_in_later_chunk_is_a_fixed_mismatch():
    raw = sse(completion(chunk=True), completion(chunk=True, model="private-canary-model"))
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_model_mismatch$"):
        observe(raw, "text/event-stream")


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.replace(b'"model":', b'"model":"private-canary","model":', 1),
    lambda raw: raw.replace(b'"role":', b'"role":"private-canary","role":', 1),
])
@pytest.mark.parametrize("chunk", [False, True])
def test_duplicate_keys_rejected_at_every_depth(mutation, chunk):
    raw = mutation(encoded(completion(chunk=chunk)))
    raw = b"data: " + raw + b"\n\ndata: [DONE]\n\n" if chunk else raw
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_json_duplicate_key$"):
        observe(raw, "text/event-stream" if chunk else "application/json")


@pytest.mark.parametrize("literal", [b"NaN", b"Infinity", b"-Infinity", b"1e309", b"-1e309"])
@pytest.mark.parametrize("chunk", [False, True])
def test_nonfinite_numbers_in_opaque_content_are_rejected(literal, chunk):
    raw = encoded(completion(chunk=chunk)).replace(
        b'"total_tokens":2', b'"total_tokens":' + literal,
    )
    raw = b"data: " + raw + b"\n\ndata: [DONE]\n\n" if chunk else raw
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_json_nonfinite$"):
        observe(raw, "text/event-stream" if chunk else "application/json")


@pytest.mark.parametrize("raw,code", [
    (b"{private-canary", "response_json_invalid"),
    (b'{"model":"private-canary"', "response_json_invalid"),
    (b"\xff", "response_json_invalid"),
    (b'"private-canary"', "response_shape_invalid"),
    (b'{}', "response_shape_invalid"),
    (b'{"object":"response","model":"deepseek-public-fixture"}', "response_shape_invalid"),
])
def test_invalid_or_non_chatcompletion_json_is_rejected_without_payload(raw, code):
    with pytest.raises(observation.ProviderResponseObservationError, match=r"^" + code + "$"):
        observe(raw)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(error={"message": "private-canary"}),
    lambda value: value.update(created=True),
    lambda value: value.update(choices="private-canary"),
    lambda value: value.update(choices=[True]),
    lambda value: value.update(id=None),
    lambda value: value.update(opaque="\ud800"),
])
def test_malformed_envelope_and_invalid_unicode_are_not_completed_observations(mutation):
    value = completion()
    mutation(value)
    with pytest.raises(observation.ProviderResponseObservationError) as caught:
        observe(encoded(value))
    assert str(caught.value) in {"response_shape_invalid", "response_json_invalid"}


@pytest.mark.parametrize("raw,code", [
    (lambda: sse(completion(chunk=True), terminal=False), "response_sse_partial"),
    (lambda: sse(completion(chunk=True))[:-1], "response_sse_partial"),
    (lambda: sse(completion(chunk=True))[:-2], "response_sse_partial"),
    (lambda: b"data: [DONE]\n\n", "response_model_missing"),
    (lambda: sse(completion(chunk=True)) + b"data: [DONE]\n\n", "response_sse_terminal_invalid"),
    (lambda: sse(completion(chunk=True)) + sse(completion(chunk=True)),
     "response_sse_terminal_invalid"),
    (lambda: sse(completion()), "response_shape_invalid"),
    (lambda: sse(completion(chunk=True)) + b"unknown: private-canary\n\n", "response_sse_invalid"),
    (lambda: sse(completion(chunk=True)) + b"retry: private-canary\n\n", "response_sse_invalid"),
    (lambda: sse(completion(chunk=True)) + b"id: private\x00canary\n\n", "response_sse_invalid"),
    (lambda: sse(completion(chunk=True)) + b": \xff\n\n", "response_sse_invalid"),
])
def test_partial_malformed_and_extra_terminal_sse_are_rejected(raw, code):
    with pytest.raises(observation.ProviderResponseObservationError, match=r"^" + code + "$"):
        observe(raw(), "text/event-stream")


def test_depth_limit_applies_before_json_decoder_and_ignores_string_delimiters():
    value = completion()
    value["opaque"] = "[{:" * 100
    assert observe(encoded(value))["model_observation_count"] == 1
    value["opaque"] = 0
    for _ in range(observation.MAX_JSON_DEPTH):
        value["opaque"] = [value["opaque"]]
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_json_depth$"):
        observe(encoded(value))


def test_json_item_budget_is_checked_before_decoder(monkeypatch):
    monkeypatch.setattr(observation, "MAX_JSON_ITEMS", 2)
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_json_budget$"):
        observe(encoded(completion()))


@pytest.mark.parametrize("bound,maximum", [
    ("MAX_SSE_LINES", 1), ("MAX_SSE_RECORDS", 1),
    ("MAX_SSE_LINE_BYTES", 16), ("MAX_SSE_RECORD_BYTES", 16),
])
def test_each_sse_cost_bound_rejects_without_unbounded_processing(monkeypatch, bound, maximum):
    monkeypatch.setattr(observation, bound, maximum)
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_sse_budget$"):
        observe(sse(completion(chunk=True)), "text/event-stream")


def test_four_mib_body_boundary_and_oversize_rejection():
    value = completion()
    value["opaque"] = ""
    empty = encoded(value)
    value["opaque"] = "x" * (observation.MAX_RESPONSE_BYTES - len(empty))
    raw = encoded(value)
    assert len(raw) == 4 * 1024 * 1024
    assert observe(raw)["response_bytes"] == len(raw)
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_body_bound$"):
        observe(raw + b" ")


@pytest.mark.parametrize("content_type", [
    "text/plain", "application/problem+json", "application/json; charset=latin-1",
    "application/json; private=canary", "application/json\r\nCanary: private",
    "", None, True, "x" * 129,
])
def test_unknown_or_malformed_content_type_is_fail_closed(content_type):
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_content_type_invalid$"):
        observe(encoded(completion()), content_type)


@pytest.mark.parametrize("expected_model", [None, True, "", "/private/canary", "x" * 129,
                                          "model\ncanary", "model\u202e"])
def test_expected_freeze_identity_is_bounded_and_not_inferred(expected_model):
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^expected_model_invalid$"):
        observe(encoded(completion()), expected_model=expected_model)


@pytest.mark.parametrize("raw", [b"", "private-canary", bytearray(b"{}"), None])
def test_body_must_be_nonempty_immutable_bytes(raw):
    with pytest.raises(observation.ProviderResponseObservationError,
                       match=r"^response_body_bound$"):
        observe(raw)
