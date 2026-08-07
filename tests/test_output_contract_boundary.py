"""Unit coverage for `_parse_strict` (`ai/agents/correspondence/read_and_reply.py`), the output-
contract boundary strengthened 2026-08-07 per `docs/preregistration-boundary-schema-validation.md`
to validate the complete declared `_RESPONSE_SCHEMA` -- object shape, exact key set
(`additionalProperties: false`), string types and the `request_type` enum -- instead of the
decode-shape check (`json.loads`, dict, required keys present) it started as on 2026-07-27.

One test per rejection class, in the order `_parse_strict` checks them, plus the acceptance tests:
a fully valid payload, and -- as executable documentation of the pre-registration's "What the code
already determines" fact 2 -- the historical tolerant-fallback shape
(`TolerantRepairClient`'s replacement payload), which is schema-valid by construction and so must
pass even this strengthened check.
"""
import json
from pathlib import Path

import pytest

from ai.agents import load_module

# `ai/agents/<agent>/<capability>.py` is a data folder, not a Python package (no `__init__.py` at
# any level -- see ai/agents.py's module docstring), so the capability file is reached the same way
# the platform reaches it at runtime: `load_module`, by explicit file path, never `import
# ai.agents.correspondence.read_and_reply`.
_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "ai" / "agents" / "correspondence" / "read_and_reply.py"
)
_read_and_reply = load_module(_MODULE_PATH)
_RESPONSE_SCHEMA = _read_and_reply._RESPONSE_SCHEMA
_parse_strict = _read_and_reply._parse_strict


def test_invalid_json_is_rejected():
    # Fenced markdown: the exact shape the 2026-07-27 finding motivated rejecting outright.
    fenced = "```json\n" + json.dumps({
        "request_type": "support", "summary": "s", "reply_body": "b",
    }) + "\n```"
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_strict(fenced)


def test_non_object_json_is_rejected():
    with pytest.raises(ValueError, match="parsed content is not a JSON object"):
        _parse_strict(json.dumps(["support", "s", "b"]))


def test_missing_required_key_is_rejected():
    payload = json.dumps({"request_type": "support", "summary": "s"})  # reply_body missing
    with pytest.raises(ValueError, match=r"missing required keys: \['reply_body'\]"):
        _parse_strict(payload)


def test_unexpected_key_is_rejected():
    payload = json.dumps({
        "request_type": "support", "summary": "s", "reply_body": "b", "extra": "not declared",
    })
    with pytest.raises(ValueError, match=r"unexpected keys: \['extra'\]"):
        _parse_strict(payload)


def test_non_string_value_is_rejected():
    payload = json.dumps({"request_type": "support", "summary": 123, "reply_body": "b"})
    with pytest.raises(ValueError, match="key summary is not a string"):
        _parse_strict(payload)


def test_out_of_enum_request_type_is_rejected():
    payload = json.dumps({"request_type": "bogus", "summary": "s", "reply_body": "b"})
    with pytest.raises(ValueError, match=r"request_type not in enum: 'bogus'"):
        _parse_strict(payload)


def test_valid_full_payload_passes_and_returns_dict():
    payload = {"request_type": "order", "summary": "customer wants a refund", "reply_body": "We're on it."}
    result = _parse_strict(json.dumps(payload))
    assert result == payload


def test_absorbing_fallback_payload_passes_full_validation():
    """docs/preregistration-boundary-schema-validation.md, determination fact 2: the absorbing
    fallback (`TolerantRepairClient`) replaces any non-JSON generation response with
    `{"request_type": "support", "summary": content[:200], "reply_body": content}`. That payload
    is schema-valid by construction -- `request_type` is an enum member, both other values are
    strings, exactly the three declared keys are present -- so no boundary-side validation of this
    schema, at any strength, can reject it. This test is that claim, executed."""
    garbage_content = "not json at all, just prose the model returned"
    fallback_payload = {
        "request_type": "support",
        "summary": garbage_content[:200],
        "reply_body": garbage_content,
    }
    result = _parse_strict(json.dumps(fallback_payload))
    assert result == fallback_payload


def test_schema_derivation_sanity():
    """Guards the tests above against `_RESPONSE_SCHEMA` drifting silently: the constants this
    module hardcodes in its assertions (the required/declared key set, the request_type enum) must
    still match the schema `_parse_strict` actually validates against."""
    assert set(_RESPONSE_SCHEMA["required"]) == {"request_type", "summary", "reply_body"}
    assert set(_RESPONSE_SCHEMA["properties"]) == {"request_type", "summary", "reply_body"}
    assert _RESPONSE_SCHEMA["properties"]["request_type"]["enum"] == ["support", "order", "cancellation"]
    assert _RESPONSE_SCHEMA["additionalProperties"] is False
