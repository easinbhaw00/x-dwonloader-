"""XENO v7 public-post resolver with bounded multi-provider fallback.

The adapters use published public API interfaces. This module never logs in to X,
uses browser cookies, or bypasses private/protected access.
"""
import asyncio
import copy
import os
import random
import re
import time
from urllib.parse import urlparse

import httpx

from .policy import enforce_content_policy

ALLOWED_VIDEO_HOSTS = {"video.twimg.com"}


def _to_int(value, default=0):
    """Upstream bitrate/duration values are untrusted; never let a bad value crash extraction."""
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default

# (internal label, URL template, response adapter)
DEFAULT_ENDPOINTS = [
    ("primary_v2", "https://api.fxtwitter.com/2/status/{id}", "fx"),
    ("canary_v2", "https://api-canary.fxtwitter.com/2/status/{id}", "fx"),
    ("vx_public", "https://api.vxtwitter.com/i/status/{id}", "vx"),
    ("legacy_v1", "https://api.fxtwitter.com/status/{id}", "fx"),
    ("canary_legacy", "https://api-canary.fxtwitter.com/status/{id}", "fx"),
]


class FxTwitterError(RuntimeError):
    """Safe, user-facing extraction failure."""


class FxTwitterProvider:
    def __init__(self):
        self.endpoints = list(DEFAULT_ENDPOINTS)
        extras = [
            item.strip().rstrip("/")
            for item in os.getenv("XENO_FX_API_EXTRA_BASES", "").split(",")
            if item.strip()
        ]
        # Only operator-configured HTTPS bases are accepted.
        for base in extras:
            parsed = urlparse(base)
            if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                self.endpoints.append(("custom_compatible", base + "/2/status/{id}", "fx"))

        self.timeout = max(3.0, min(float(os.getenv("XENO_PROVIDER_TIMEOUT", "12")), 30.0))
        self.retries = max(0, min(int(os.getenv("XENO_PROVIDER_RETRIES", "1")), 2))
        self.cooldown_seconds = max(10, min(int(os.getenv("XENO_PROVIDER_COOLDOWN_SECONDS", "45")), 600))
        self.cache_ttl = max(0, min(int(os.getenv("XENO_METADATA_CACHE_SECONDS", "20")), 120))
        self.allow_sensitive = os.getenv("XENO_ALLOW_SENSITIVE_WITH_PROVIDER", "false").lower() == "true"
        self.age_verified = os.getenv("XENO_AGE_ELIGIBILITY_VERIFIED", "false").lower() == "true"
        self._blocked_until = {}
        self._cache = {}
        self._semaphore = asyncio.Semaphore(5)

    @property
    def configured(self):
        return bool(self.endpoints)

    @staticmethod
    def _post_id(post_url):
        match = re.search(r"/status/(\d+)", post_url)
        if not match:
            raise ValueError("Could not read the X post ID.")
        return match.group(1)

    @staticmethod
    def _endpoint_parts(entry):
        # Backward-compatible with v6 tests/configuration using 2-tuples.
        if len(entry) == 2:
            label, template = entry
            kind = "vx" if "vxtwitter" in template.lower() or "vx" in label.lower() else "fx"
            return label, template, kind
        return entry[0], entry[1], entry[2]

    @staticmethod
    def _normalise(payload, kind="fx"):
        if not isinstance(payload, dict):
            return None
        if kind == "vx":
            tweet = payload.get("tweet") if isinstance(payload.get("tweet"), dict) else payload
            if not isinstance(tweet, dict):
                return None
            media_items = tweet.get("media_extended") or []
            videos = []
            for item in media_items:
                if not isinstance(item, dict):
                    continue
                media_type = str(item.get("type") or "").lower()
                if media_type in {"video", "gif", "animated_gif"}:
                    videos.append({
                        "type": "video",
                        "url": item.get("url"),
                        "thumbnail_url": item.get("thumbnail_url"),
                        "duration": item.get("duration_millis"),
                        "formats": [{"container": "mp4", "url": item.get("url")}],
                    })
            if not videos:
                # mediaURLs is mixed media; accept only URLs that are clearly MP4.
                for media_url in tweet.get("mediaURLs") or []:
                    if isinstance(media_url, str) and urlparse(media_url).path.lower().endswith(".mp4"):
                        videos.append({"type": "video", "url": media_url, "formats": [{"container": "mp4", "url": media_url}]})
            return {
                "text": tweet.get("text") or "",
                "author": {"protected": bool(tweet.get("protected", False))},
                "possibly_sensitive": bool(tweet.get("possibly_sensitive", False)),
                "protected": bool(tweet.get("protected", False)),
                "media": {"videos": videos},
            }

        # FxTwitter v2: {"code": 200, "status": {...}}; older shape: {"tweet": {...}}.
        code = payload.get("code", 200)
        try:
            if int(code) in (401, 403):
                raise PermissionError("This post is private, protected, or unavailable to public access.")
        except (TypeError, ValueError):
            pass
        tweet = payload.get("status") or payload.get("tweet") or payload
        if not isinstance(tweet, dict):
            return None
        if tweet.get("type") not in (None, "status"):
            return None
        return tweet

    @staticmethod
    def _variants(item):
        candidates = []
        for variant in item.get("formats", []) or []:
            if not isinstance(variant, dict):
                continue
            url = variant.get("url")
            container = str(variant.get("container") or variant.get("content_type") or "").lower()
            if url and (container == "mp4" or "video/mp4" in container or urlparse(str(url)).path.lower().endswith(".mp4")):
                candidates.append((_to_int(variant.get("bitrate")), str(url)))

        for variant in item.get("variants", []) or []:
            if not isinstance(variant, dict):
                continue
            url = variant.get("url") or variant.get("src")
            content_type = str(variant.get("content_type") or variant.get("contentType") or variant.get("type") or "").lower()
            if url and ("mp4" in content_type or urlparse(str(url)).path.lower().endswith(".mp4")):
                candidates.append((_to_int(variant.get("bitrate")), str(url)))

        url = item.get("url") or item.get("src")
        fmt = str(item.get("format") or item.get("type") or "").lower()
        if url and ("mp4" in fmt or urlparse(str(url)).path.lower().endswith(".mp4")):
            candidates.append((_to_int(item.get("bitrate")), str(url)))

        unique = {}
        for bitrate, url in candidates:
            unique[url] = max(bitrate, unique.get(url, 0))
        return sorted(((bitrate, url) for url, bitrate in unique.items()), reverse=True)

    def _is_blocked(self, label):
        return self._blocked_until.get(label, 0) > time.monotonic()

    def _cooldown(self, label, seconds=None):
        duration = self.cooldown_seconds if seconds is None else max(10, min(int(seconds), 600))
        self._blocked_until[label] = time.monotonic() + duration

    def _cached(self, post_id):
        entry = self._cache.get(post_id)
        if not entry:
            return None
        expires, data = entry
        if expires <= time.monotonic():
            self._cache.pop(post_id, None)
            return None
        # Copy nested values so a caller cannot mutate the cache.
        return copy.deepcopy(data)

    def _store_cache(self, post_id, data):
        if not self.cache_ttl:
            return
        now = time.monotonic()
        # Opportunistic expiry cleanup and bounded cache size.
        self._cache = {key: value for key, value in self._cache.items() if value[0] > now}
        if len(self._cache) >= 500:
            oldest = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest, None)
        self._cache[post_id] = (now + self.cache_ttl, copy.deepcopy(data))

    @staticmethod
    def _safe_video_url(url):
        try:
            parsed = urlparse(str(url))
            return (
                parsed.scheme == "https"
                and parsed.hostname in ALLOWED_VIDEO_HOSTS
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
            )
        except (TypeError, ValueError):
            return False

    async def _request_json(self, client, label, endpoint):
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                response = await client.get(endpoint)
                if response.status_code in (401, 403):
                    raise PermissionError("This post is private, protected, or unavailable to public access.")
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
                    try:
                        wait = min(float(retry_after), self.cooldown_seconds) if retry_after else None
                    except (TypeError, ValueError):
                        wait = None
                    self._cooldown(label, wait)
                    # Respect rate limiting: do not retry this provider in the same request.
                    return None
                if response.status_code >= 500:
                    self._cooldown(label)
                    if attempt < self.retries:
                        await asyncio.sleep(min(0.25 * (2 ** attempt) + random.uniform(0, 0.15), 1.0))
                        continue
                    return None
                if response.status_code in (404, 410):
                    return None
                if response.status_code != 200:
                    return None
                payload = response.json()
                if isinstance(payload, dict):
                    code = payload.get("code")
                    if code is not None and int(code) in (401, 403):
                        raise PermissionError("This post is private, protected, or unavailable to public access.")
                return payload
            except PermissionError:
                raise
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                last_error = "request/response failure"
                if attempt < self.retries:
                    await asyncio.sleep(min(0.25 * (2 ** attempt) + random.uniform(0, 0.15), 1.0))
                    continue
                self._cooldown(label, min(self.cooldown_seconds, 30))
                return None
        return None

    async def extract(self, post_url):
        post_id = self._post_id(post_url)
        cached = self._cached(post_id)
        if cached is not None:
            return cached

        async with self._semaphore:
            # Recheck after waiting for the concurrency slot.
            cached = self._cached(post_id)
            if cached is not None:
                return cached

            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                headers={"User-Agent": "XENO/7.0", "Accept": "application/json"},
            ) as client:
                for entry in self.endpoints:
                    label, template, kind = self._endpoint_parts(entry)
                    if self._is_blocked(label):
                        continue
                    endpoint = template.format(id=post_id)
                    payload = await self._request_json(client, label, endpoint)
                    if payload is None:
                        continue

                    tweet = self._normalise(payload, kind)
                    if not tweet:
                        continue
                    author = tweet.get("author")
                    if not isinstance(author, dict):
                        author = {}
                    if author.get("protected") is True or tweet.get("protected") is True:
                        raise PermissionError("Private/protected posts are not supported by XENO.")

                    sensitive = bool(tweet.get("possibly_sensitive") or tweet.get("sensitive"))
                    if sensitive:
                        enforce_content_policy(
                            "sensitive",
                            authorized_access=self.allow_sensitive,
                            age_eligible=self.age_verified,
                        )

                    media = tweet.get("media")
                    if not isinstance(media, dict):
                        media = {}
                    raw = media.get("videos") or []
                    if not raw:
                        raw = [
                            item for item in (media.get("all") or [])
                            if isinstance(item, dict)
                            and str(item.get("type", "")).lower() in {"video", "animated_gif", "gif"}
                        ]

                    output = []
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        safe = [
                            {"bitrate": bitrate, "url": url}
                            for bitrate, url in self._variants(item)
                            if self._safe_video_url(url)
                        ]
                        safe.sort(key=lambda item: item["bitrate"], reverse=True)
                        if safe:
                            output.append({
                                "type": "video",
                                "duration": item.get("duration") or item.get("duration_ms"),
                                "preview_image_url": item.get("thumbnail_url") or item.get("thumbnail"),
                                "variants": [
                                    {
                                        "id": str(index),
                                        "quality": f"{variant['bitrate']//1000} kbps" if variant["bitrate"] else "MP4",
                                        "bitrate": variant["bitrate"],
                                    }
                                    for index, variant in enumerate(safe)
                                ],
                                "_urls": [variant["url"] for variant in safe],
                            })

                    if output:
                        result = {
                            "post_id": post_id,
                            "text": tweet.get("text") or "",
                            "possibly_sensitive": sensitive,
                            "media": output,
                            "download_ready": True,
                            "message": "Public post media found. Select an available MP4 quality.",
                        }
                        self._store_cache(post_id, result)
                        return result

            raise FxTwitterError(
                "XENO could not find a supported public MP4 video. The post may be unavailable, "
                "contain no supported direct MP4, or the service may be temporarily busy. Please try again later."
            )

    async def get_variant(self, post_url, media_index, variant_index):
        data = await self.extract(post_url)
        try:
            return data["media"][media_index]["_urls"][variant_index]
        except (IndexError, KeyError, TypeError):
            raise ValueError("Selected MP4 quality is no longer available. Extract the post again.")
