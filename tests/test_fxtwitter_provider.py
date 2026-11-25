import pytest
from backend.fxtwitter_provider import FxTwitterProvider, FxTwitterError

class FakeResponse:
    status_code = 200
    def json(self):
        return {"tweet": {"id": "1234567890123456789", "text": "public clip",
            "possibly_sensitive": False,
            "media": {"videos": [{"type": "video", "thumbnail_url": "https://pbs.twimg.com/thumb.jpg",
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 800000, "url": "https://video.twimg.com/a/480.mp4"},
                    {"content_type": "video/mp4", "bitrate": 2000000, "url": "https://video.twimg.com/a/720.mp4"},
                    {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/a/master.m3u8"},
                    {"content_type": "video/mp4", "bitrate": 3000000, "url": "https://evil.example/a.mp4"}
                ]}]}}}

class FakeClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def get(self, url):
        assert url.endswith("/status/1234567890123456789")
        return FakeResponse()

@pytest.mark.asyncio
async def test_fxtwitter_extracts_only_x_cdn_mp4_variants(monkeypatch):
    import backend.fxtwitter_provider as mod
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    provider = FxTwitterProvider()
    data = await provider.extract("https://x.com/user/status/1234567890123456789")
    assert data["download_ready"] is True
    assert len(data["media"][0]["variants"]) == 2
    assert data["media"][0]["variants"][0]["bitrate"] == 2000000
    assert await provider.get_variant("https://x.com/user/status/1234567890123456789", 0, 1) == "https://video.twimg.com/a/480.mp4"

@pytest.mark.asyncio
async def test_private_post_is_rejected(monkeypatch):
    import backend.fxtwitter_provider as mod
    class PrivateResponse:
        status_code = 200
        def json(self): return {"tweet": {"author": {"protected": True}, "media": {}}}
    class PrivateClient(FakeClient):
        async def get(self, url): return PrivateResponse()
    monkeypatch.setattr(mod.httpx, "AsyncClient", PrivateClient)
    with pytest.raises(PermissionError):
        await FxTwitterProvider().extract("https://x.com/user/status/1234567890123456789")

@pytest.mark.asyncio
async def test_no_video_returns_clear_error(monkeypatch):
    import backend.fxtwitter_provider as mod
    class EmptyResponse:
        status_code = 200
        def json(self): return {"tweet": {"text": "text only", "media": {}}}
    class EmptyClient(FakeClient):
        async def get(self, url): return EmptyResponse()
    monkeypatch.setattr(mod.httpx, "AsyncClient", EmptyClient)
    with pytest.raises(FxTwitterError, match="could not find a supported public MP4"):
        await FxTwitterProvider().extract("https://x.com/user/status/1234567890123456789")
