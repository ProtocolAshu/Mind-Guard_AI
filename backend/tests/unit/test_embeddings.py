import math

import httpx
import pytest

from app.providers.base import ProviderError, ProviderRejectedError, ProviderUnavailableError
from app.providers.embeddings import HashingEmbeddingProvider, OpenAICompatibleEmbeddingProvider, cosine


async def test_hashing_embeddings_are_deterministic_normalised_and_lexically_meaningful():
    p = HashingEmbeddingProvider(dim=384)
    a, b, c, a2 = await p.embed([
        "late night instagram scrolling, accepted a 5 minute delay",
        "instagram scrolling late at night",
        "lecture on dynamic programming",
        "late night instagram scrolling, accepted a 5 minute delay",
    ])
    assert len(a) == 384 and a == a2
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    assert cosine(a, b) > 0.4 > cosine(a, c)
    assert (await p.embed([""]))[0] == [0.0] * 384


def _provider(handler):
    return OpenAICompatibleEmbeddingProvider("https://emb.local/v1", "k", "m", dim=3,
                                             transport=httpx.MockTransport(handler))


async def test_openai_compatible_success_and_request_shape():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1, 0]}, {"index": 0, "embedding": [1, 0, 0]}]})

    out = await _provider(handler).embed(["a", "b"])
    assert out == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert seen["auth"] == "Bearer k" and b'"model":"m"' in seen["body"]


@pytest.mark.parametrize(
    ("status", "body", "exc"),
    [(401, {}, ProviderRejectedError), (503, {}, ProviderUnavailableError), (429, {}, ProviderUnavailableError),
     (200, {"data": [{"index": 0, "embedding": [1, 2]}]}, ProviderError)],
)
async def test_openai_compatible_errors(status, body, exc):
    with pytest.raises(exc):
        await _provider(lambda r: httpx.Response(status, json=body)).embed(["a"])
