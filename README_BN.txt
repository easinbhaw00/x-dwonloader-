XENO — Website + Backend + Telegram Bot (FxTwitter Engine)
==========================================================

এই ZIP-এ XENO-র website, FastAPI backend, Telegram bot এবং FxTwitter-ভিত্তিক public X post video lookup engine আছে। yt-dlp ব্যবহার করা হয়নি।

কীভাবে কাজ করে
---------------
1. Website বা Telegram-এ public X post URL দিন।
2. XENO FxTwitter/FxEmbed public API থেকে পোস্টের media metadata ও MP4 URL খোঁজে।
3. পাওয়া direct MP4 quality থেকে একটি বেছে server-side download হয়।
4. Website-এ MP4 save link আসে; Telegram bot ছোট ফাইল সরাসরি পাঠাতে পারে।

গুরুত্বপূর্ণ সীমাবদ্ধতা
----------------------
- FxTwitter একটি third-party community API; এটি X-এর official API নয়। এর availability, response format ও rate limit বদলাতে পারে। সব পোস্ট/ভিডিও কাজ করবে এমন নিশ্চয়তা নেই।
- শুধু public এবং API-তে পাওয়া direct MP4 URL সমর্থিত। HLS-only (.m3u8), DRM-protected, private/protected বা unavailable পোস্ট সমর্থিত নয়।
- কোনো login, cookie harvesting, private access, DRM বা platform restriction bypass করা হয় না।
- Sensitive/age-restricted content default-এ blocked। Environment flag চালু করলেও কেবল আইনসম্মত অনুমোদিত access ও বয়স-যোগ্যতা নিশ্চিত থাকলেই ব্যবহারযোগ্য।
- FxTwitter API public endpoint-এর ওপর নির্ভর করে; production uptime বা SLA নেই।
- Live X URL দিয়ে extraction/download এই build-এর automated test-এ যাচাই করা হয়নি। Tests mock response দিয়ে provider behavior যাচাই করে।

Local run (কম্পিউটার/hosting environment)
-----------------------------------------
1. ZIP extract করুন।
2. Python 3.11+ environment তৈরি করুন।
3. `pip install -r requirements.txt`
4. `.env.example` কপি করে `.env` বানান।
5. `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
6. Website খুলুন: `http://127.0.0.1:8000`

Telegram bot
------------
1. `.env`-এ `TELEGRAM_BOT_TOKEN` দিন।
2. `XENO_API_URL`-এ deployed backend URL দিন।
3. `python -m bot.bot` চালান।

Tests
-----
`pytest -q`

API routes
----------
GET  /api/v1/health
GET  /api/v1/capabilities
POST /api/v1/validate
POST /api/v1/extract
POST /api/v1/download-x
GET  /api/v1/download/{job_id}
GET  /api/v1/files/{job_id}
POST /api/v1/download-direct

Security / deployment notes
---------------------------
- XENO_ALLOWED_MEDIA_HOSTS default-এ শুধু `video.twimg.com`; প্রয়োজন ছাড়া host allowlist বড় করবেন না।
- Public deployment-এর আগে authentication, per-user isolation, rate limiting, quotas, persistent queue, file cleanup, monitoring ও abuse protection যোগ করতে হবে। বর্তমান starter app public multi-user production-ready নয়।
- API tokens/Telegram token কখনো frontend বা GitHub repository-তে commit করবেন না।
- Hosting/domain বা third-party service-এর খরচ হতে পারে; কোনো paid service এই ZIP-এ বাধ্যতামূলক নয়।


MULTI-PROVIDER FALLBACK (v6)
- Primary: FxEmbed/FxTwitter API v2.
- Fallback: FxEmbed canary API v2.
- Compatibility fallback: FxTwitter legacy endpoint and canary legacy endpoint.
- A provider timeout, rate limit, or unavailable response moves to the next endpoint.
- HTTP 401/403 stops fallback to avoid trying to evade access restrictions.
- This improves resilience but cannot guarantee zero errors or support every X post.
- HLS-only formats are not merged in this build; only direct MP4 variants are offered.
- Optional custom HTTPS API bases may be configured with XENO_FX_API_EXTRA_BASES only when the operator controls and verifies them.


XENO v7 UPDATE
--------------
- Preserves the existing website design, frontend files, routes, and project structure.
- Adds a public VXTwitter API fallback after the primary and canary sources.
- Adds bounded retries, cooldown for rate-limited/temporarily failing sources, a small metadata cache, and a request concurrency cap.
- Public health/capabilities responses no longer disclose provider names.
- Telegram errors are now generic XENO messages; internal logs retain failure type only.
- The resolver accepts only HTTPS MP4 URLs hosted on video.twimg.com.
- Private/protected posts and access-control bypass remain unsupported.
- Live third-party endpoints can change or rate-limit. Automated tests use mocked responses and do not prove live extraction availability.
- HLS-only media is still not supported by this build.


XENO v7.1 FIX LOG
-----------------
- Fixed: unexpected non-numeric bitrate from a provider no longer crashes /api/v1/extract (HTTP 500).
- Fixed: unexpected author/media shapes from a provider are handled safely.
- Security: removed the undocumented GET /api/v1/download route that listed every job (IDs + file names) without authentication.
- Security: .dockerignore added so .env (tokens) and local data are not baked into the Docker image.
- CI: pytest-asyncio added to requirements; the async provider tests need it.
- Storage: SQLite connections are now closed after each operation.
- Bot: removed a duplicate import.
- UI: quality picker, progress bar, save button, keyboard focus ring, light/dark memory, reduced-motion support, mobile polish.
- UI: the quality list is tied to the URL it was extracted from; editing the URL clears it.
- New regression tests: tests/test_provider_robustness.py.
