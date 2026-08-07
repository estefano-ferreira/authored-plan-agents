"""Unit coverage for `ExtractorRepairClient` (`infrastructure/providers/extractor_repair_client.py`),
the genuine-extractor repair instrument registered in
`docs/preregistration-extractor-repair-cells.md` (cells G and H) as the practitioner "code fence
removal" counterpart to `TolerantRepairClient`'s absorbing fallback.

Fakes `IModelClient` inline (a tiny delegate returning a fixed `ModelResponse`) rather than
standing up a real provider or the full Platform -- the decorator only ever touches
`response.content`/`request.purpose`, so a fake delegate is enough to exercise it directly.
"""
from core.ports import IModelClient, ModelRequest, ModelResponse
from infrastructure.configurations import build_platform
from infrastructure.providers.extractor_repair_client import ExtractorRepairClient


class _FakeModelClient(IModelClient):
    """Returns a fixed `content` string regardless of the request; records nothing else."""

    def __init__(self, content: str, model: str = "fake-model") -> None:
        self._content = content
        self._model = model

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=self._content, input_tokens=10, output_tokens=5, cache_read_tokens=0, model=self._model,
        )


def _generation_request(content: str = "irrelevant") -> ModelRequest:
    return ModelRequest(messages=({"role": "user", "content": content},), purpose="generation")


def _selection_request(content: str = "irrelevant") -> ModelRequest:
    return ModelRequest(messages=({"role": "user", "content": content},), purpose="selection")


def test_strips_single_fence_with_language_tag():
    inner = '{"request_type": "support", "summary": "s", "reply_body": "b"}'
    fenced = f"```json\n{inner}\n```"
    client = ExtractorRepairClient(_FakeModelClient(fenced))

    response = client.complete(_generation_request())

    assert response.content == inner
    assert len(client.repairs) == 1
    assert client.repairs[0] == {"original_preview": fenced[:120]}


def test_strips_single_fence_without_language_tag():
    inner = '{"request_type": "order", "summary": "s2", "reply_body": "b2"}'
    fenced = f"```\n{inner}\n```"
    client = ExtractorRepairClient(_FakeModelClient(fenced))

    response = client.complete(_generation_request())

    assert response.content == inner
    assert len(client.repairs) == 1


def test_passes_through_bare_json_object_unchanged():
    bare = '{"request_type": "support", "summary": "s", "reply_body": "b"}'
    client = ExtractorRepairClient(_FakeModelClient(bare))

    response = client.complete(_generation_request())

    assert response.content == bare
    assert client.repairs == []


def test_passes_through_prose_unchanged():
    prose = "This is not JSON at all, just prose the model returned."
    client = ExtractorRepairClient(_FakeModelClient(prose))

    response = client.complete(_generation_request())

    assert response.content == prose
    assert client.repairs == []


def test_double_fenced_content_strips_exactly_one_layer():
    """Registered single-pass behavior (docs/preregistration-extractor-repair-cells.md,
    determination fact 3): the outer fence IS matched and stripped -- `_SINGLE_FENCE_RE` is
    anchored end-to-end with `re.DOTALL`, so `.` spans the inner, still-fenced content -- but
    there is no second pass to strip the inner fence too. One repair is recorded, and the result
    still starts with a markdown fence: neither zero layers removed, nor two.
    """
    innermost = '{"request_type": "support", "summary": "s", "reply_body": "b"}'
    double_fenced = f"```json\n```json\n{innermost}\n```\n```"
    client = ExtractorRepairClient(_FakeModelClient(double_fenced))

    response = client.complete(_generation_request())

    assert len(client.repairs) == 1
    assert response.content == f"```json\n{innermost}\n```"
    assert response.content.startswith("```json")
    assert response.content != innermost  # not two layers removed
    assert response.content != double_fenced  # not zero layers removed


def test_selection_purpose_responses_are_untouched():
    fenced = "```json\n{}\n```"
    client = ExtractorRepairClient(_FakeModelClient(fenced))

    response = client.complete(_selection_request())

    assert response.content == fenced
    assert client.repairs == []


def test_preserves_token_and_model_fields():
    inner = "hello"
    fenced = f"```\n{inner}\n```"
    delegate = _FakeModelClient(fenced, model="some-model")
    client = ExtractorRepairClient(delegate)

    response = client.complete(_generation_request())

    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.cache_read_tokens == 0
    assert response.model == "some-model"


def test_build_platform_rejects_both_repair_instruments():
    try:
        build_platform(provider="local", tolerant_repair=True, extractor_repair=True)
    except ValueError as exc:
        assert "mutually exclusive" in str(exc)
    else:
        raise AssertionError("build_platform did not raise ValueError for tolerant_repair+extractor_repair")
