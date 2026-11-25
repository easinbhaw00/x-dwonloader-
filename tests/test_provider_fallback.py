import pytest
from backend.fxtwitter_provider import FxTwitterProvider, FxTwitterError

@pytest.mark.asyncio
async def test_fallback_uses_next_endpoint(monkeypatch):
    provider = FxTwitterProvider()
    provider.retries = 0
    provider.endpoints = [
        ("first", "https://one.invalid/2/status/{id}", "fx"),
        ("second", "https://two.invalid/2/status/{id}", "fx"),
    ]
    calls = []

    class Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
            self.headers = {}
        def json(self):
            return self._payload

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url):
            calls.append(url)
            if "one.invalid" in url:
                return Resp(503)
            return Resp(200, {"code": 200, "status": {"type": "status", "id": "123", "text": "ok",
                "media": {"videos": [{"type": "video", "formats": [{"container": "mp4", "bitrate": 900000,
                "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/720x1280/a.mp4"}]}]}}})

    monkeypatch.setattr("backend.fxtwitter_provider.httpx.AsyncClient", Client)
    data = await provider.extract("https://x.com/user/status/123")
    assert data["download_ready"] is True
    assert "provider" not in data
    assert len(calls) == 2

@pytest.mark.asyncio
async def test_forbidden_stops_fallback(monkeypatch):
    provider = FxTwitterProvider()
    provider.retries = 0
    provider.endpoints = [
        ("first", "https://one.invalid/2/status/{id}", "fx"),
        ("second", "https://two.invalid/2/status/{id}", "fx"),
    ]
    calls = []

    class Resp:
        status_code = 403
        headers = {}
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url):
            calls.append(url)
            return Resp()

    monkeypatch.setattr("backend.fxtwitter_provider.httpx.AsyncClient", Client)
    with pytest.raises(PermissionError):
        await provider.extract("https://x.com/user/status/123")
    assert len(calls) == 1

@pytest.mark.asyncio
async def test_vx_fallback_normalizes_media_extended(monkeypatch):
    provider = FxTwitterProvider()
    provider.retries = 0
    provider.endpoints = [("vx", "https://api.vxtwitter.com/i/status/{id}", "vx")]

    class Resp:
        status_code = 200
        headers = {}
        def json(self):
            return {"tweetID": "123", "text": "clip", "possibly_sensitive": False,
                    "media_extended": [{"type": "video", "url": "https://video.twimg.com/a.mp4"}]}
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return Resp()

    monkeypatch.setattr("backend.fxtwitter_provider.httpx.AsyncClient", Client)
    data = await provider.extract("https://x.com/user/status/123")
    assert data["media"][0]["variants"][0]["quality"] == "MP4"
    assert await provider.get_variant("https://x.com/user/status/123", 0, 0) == "https://video.twimg.com/a.mp4"

@pytest.mark.asyncio
async def test_cache_prevents_duplicate_provider_requests(monkeypatch):
    provider = FxTwitterProvider()
    provider.retries = 0
    provider.endpoints = [("one", "https://one.invalid/status/{id}", "fx")]
    calls = []

    class Resp:
        status_code = 200
        headers = {}
        def json(self):
            return {"tweet": {"text": "clip", "media": {"videos": [{"type": "video",
                "formats": [{"container": "mp4", "url": "https://video.twimg.com/a.mp4"}]}]}}}
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url):
            calls.append(url)
            return Resp()

    monkeypatch.setattr("backend.fxtwitter_provider.httpx.AsyncClient", Client)
    await provider.extract("https://x.com/user/status/123")
    await provider.extract("https://x.com/user/status/123")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_cools_provider_and_moves_to_fallback(monkeypatch):
    provider = FxTwitterProvider()
    provider.retries = 2
    provider.endpoints = [
        ("limited", "https://limited.invalid/2/status/{id}", "fx"),
        ("backup", "https://backup.invalid/2/status/{id}", "fx"),
    ]
    calls = []

    class Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
            self.headers = {"Retry-After": "30"}
        def json(self):
            return self._payload

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url):
            calls.append(url)
            if "limited.invalid" in url:
                return Resp(429)
            return Resp(200, {"tweet": {"text": "ok", "media": {"videos": [{
                "type": "video", "formats": [{"container": "mp4", "url": "https://video.twimg.com/a.mp4"}]
            }]}}})

    monkeypatch.setattr("backend.fxtwitter_provider.httpx.AsyncClient", Client)
    data = await provider.extract("https://x.com/user/status/456")
    assert data["download_ready"] is True
    assert len(calls) == 2
    assert provider._is_blocked("limited")
