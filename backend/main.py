import uuid
from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl, Field
from .policy import is_x_post_url
from .provider import ProviderNotConfigured
from .fxtwitter_provider import FxTwitterProvider, FxTwitterError
from . import engine, storage

BASE = Path(__file__).resolve().parent.parent
provider = FxTwitterProvider()

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    yield

app = FastAPI(title="XENO API", version="1.5.0",
              description="Original API for XENO website and Telegram bot.",
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

class PostRequest(BaseModel):
    url: HttpUrl

class DirectDownloadRequest(BaseModel):
    media_url: HttpUrl
    filename: str = Field(default="video.mp4", min_length=1, max_length=100)

class XDownloadRequest(BaseModel):
    url: HttpUrl
    media_index: int = Field(default=0, ge=0, le=10)
    variant_index: int = Field(default=0, ge=0, le=20)

@app.get("/")
def home():
    return FileResponse(BASE / "index.html")

@app.get("/api/v1/health")
def health():
    return {
        "ok": True, "service": "XENO API", "version": "1.5.0",
        "x_extractor": "available" if provider.configured else "unavailable",
        "direct_media_engine": bool(engine.ALLOWED_HOSTS),
        "allowed_media_hosts_count": len(engine.ALLOWED_HOSTS)
    }

@app.get("/api/v1/capabilities")
def capabilities():
    return {
        "x_post_validation": True,
        "x_post_extraction": provider.configured,
        "provider_fallbacks": bool(provider.configured),
        "x_video_download": provider.configured and bool(engine.ALLOWED_HOSTS.intersection({"video.twimg.com"})),
        "video_quality_selection": True,
        "direct_media_download": bool(engine.ALLOWED_HOSTS),
        "telegram_bot": "available_when_configured",
        "private_posts": "blocked",
        "sensitive_posts": "provider_verified_access_and_age_eligibility_required",
        "note": "Multiple public sources improve availability but cannot guarantee every post. Direct media downloads work only from operator-allowlisted HTTPS hosts."
    }

@app.post("/api/v1/validate")
def validate(req: PostRequest):
    url = str(req.url)
    if not is_x_post_url(url):
        raise HTTPException(422, "Valid HTTPS X post URL required.")
    return {"valid": True, "message": "URL format is valid. Media availability is not confirmed."}

@app.post("/api/v1/extract")
async def extract(req: PostRequest):
    url = str(req.url)
    if not is_x_post_url(url):
        raise HTTPException(422, "Valid HTTPS X post URL required.")
    try:
        data = await provider.extract(url)
        # Never send signed media URLs to the browser; expose only quality choices.
        for media in data.get("media", []):
            media.pop("_urls", None)
        return data
    except ProviderNotConfigured as exc:
        raise HTTPException(503, str(exc))
    except FxTwitterError as exc:
        raise HTTPException(502, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))

@app.post("/api/v1/download-x", status_code=202)
async def download_x(req: XDownloadRequest, tasks: BackgroundTasks):
    url = str(req.url)
    if not is_x_post_url(url):
        raise HTTPException(422, "Valid HTTPS X post URL required.")
    try:
        media_url = await provider.get_variant(url, req.media_index, req.variant_index)
        engine.validate_media_url(media_url)
    except ProviderNotConfigured as exc:
        raise HTTPException(503, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except (FxTwitterError, ValueError, engine.DownloadError) as exc:
        raise HTTPException(502, str(exc))
    job_id = str(uuid.uuid4())
    storage.create_job(job_id)
    tasks.add_task(engine.run_download, job_id, media_url, f"xeno-{job_id[:8]}.mp4")
    return {"job_id": job_id, "status": "queued", "status_url": f"/api/v1/download/{job_id}"}

@app.post("/api/v1/download-direct", status_code=202)
async def download_direct(req: DirectDownloadRequest, tasks: BackgroundTasks):
    # Validate allowlist and public DNS before creating a job.
    try:
        engine.validate_media_url(str(req.media_url))
    except engine.DownloadError as exc:
        raise HTTPException(400, str(exc))
    job_id = str(uuid.uuid4())
    storage.create_job(job_id)
    tasks.add_task(engine.run_download, job_id, str(req.media_url), req.filename)
    return {"job_id": job_id, "status": "queued", "status_url": f"/api/v1/download/{job_id}"}

@app.get("/api/v1/download/{job_id}")
def download_status(job_id: str):
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(404, "Download job not found.")
    if job["status"] == "completed":
        job["file_url"] = f"/api/v1/files/{job_id}"
    return job

@app.get("/api/v1/files/{job_id}")
def get_file(job_id: str):
    job = storage.get_job(job_id)
    path = engine.file_path_for(job)
    if not path:
        raise HTTPException(404, "Completed file not found.")
    return FileResponse(path, media_type="application/octet-stream", filename=job["filename"])
