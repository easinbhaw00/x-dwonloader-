import pytest
from backend.fxtwitter_provider import FxTwitterProvider, FxTwitterError, _to_int

POST = "https://x.com/user/status/789"


def _client_returning(payload):
    class Resp:
        status_code = 200
        headers = {}

        def json(self):
            return payload

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url):
            return Resp()

    return Client


def _single_endpoint_provider():
    provider = FxTwitterProvider()
    provider.retries = 0
    provider.endpoints = [("one", "https://one.invalid/2/status/{id}", "fx")]
    return provider


def test_to_int_handles_untrusted_values():
    assert _to_int("800000") == 800000
    assert _to_int(2000000.0) == 2000000
    assert _to_int("N/A") == 0
    assert _to_int(None) == 0
    assert _to_int(float("inf")) == 0
    assert _to_int([1, 2]) == 0


@pytest.mark.asyncio
async def test_non_numeric_bitrate_does_not_crash_extraction(monkeypatch):
    import backend.fxtwitter_provider as mod
    payload = {"tweet": {"text": "clip", "media": {"videos": [{"type": "video", "formats": [
        {"container": "mp4", "url": "https://video.twimg.com/a/720.mp4", "bitrate": "N/A"},
        {"container": "mp4", "url": "https://video.twimg.com/a/480.mp4", "bitrate": "800000"},
    ]}]}}}
    monkeypatch.setattr(mod.httpx, "AsyncClient", _client_returning(payload))
    data = await _single_endpoint_provider().extract(POST)
    variants = data["media"][0]["variants"]
    assert len(variants) == 2
    assert variants[0]["bitrate"] == 800000


@pytest.mark.asyncio
async def test_unexpected_media_shape_is_reported_safely(monkeypatch):
    import backend.fxtwitter_provider as mod
    payload = {"tweet": {"text": "t", "media": [1, 2, 3]}}
    monkeypatch.setattr(mod.httpx, "AsyncClient", _client_returning(payload))
    with pytest.raises(FxTwitterError, match="could not find a supported public MP4"):
        await _single_endpoint_provider().extract(POST)


@pytest.mark.asyncio
async def test_unexpected_author_shape_is_reported_safely(monkeypatch):
    import backend.fxtwitter_provider as mod
    payload = {"tweet": {"text": "t", "author": "someone", "media": {"videos": [{"type": "video", "formats": [
        {"container": "mp4", "url": "https://video.twimg.com/a/480.mp4", "bitrate": 800000},
    ]}]}}}
    monkeypatch.setattr(mod.httpx, "AsyncClient", _client_returning(payload))
    data = await _single_endpoint_provider().extract(POST)
    assert data["download_ready"] is True
