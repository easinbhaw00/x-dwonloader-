import os, re, socket, ipaddress, asyncio
from pathlib import Path
from urllib.parse import urlparse, urljoin
import httpx
from . import storage

DOWNLOAD_DIR = Path(os.getenv("XENO_DOWNLOAD_DIR", "downloads")).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = max(1, int(os.getenv("XENO_MAX_FILE_MB", "250"))) * 1024 * 1024
ALLOWED_HOSTS = {h.strip().lower() for h in os.getenv("XENO_ALLOWED_MEDIA_HOSTS", "video.twimg.com").split(",") if h.strip()}

class DownloadError(Exception):
    pass

def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "video.mp4").strip("._")
    return (cleaned or "video.mp4")[:100]

def validate_media_url(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or p.username or p.password:
        raise DownloadError("Only HTTPS media URLs without embedded credentials are accepted.")
    if host not in ALLOWED_HOSTS:
        raise DownloadError("Media host is not in XENO_ALLOWED_MEDIA_HOSTS.")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise DownloadError("Media host could not be resolved.")
    if not addresses:
        raise DownloadError("Media host did not resolve.")
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global:
            raise DownloadError("Non-public media hosts are blocked.")
    return host

async def run_download(job_id: str, media_url: str, requested_name: str = "video.mp4"):
    partial = DOWNLOAD_DIR / f"{job_id}.part"
    try:
        validate_media_url(media_url)
        storage.update_job(job_id, status="downloading", progress=0, downloaded_bytes=0, error=None)
        timeout = httpx.Timeout(connect=15, read=60, write=30, pool=15)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, limits=limits) as client:
            current_url = media_url
            for redirect_count in range(6):
                validate_media_url(current_url)
                async with client.stream("GET", current_url, headers={"User-Agent": "XENO/1.0"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count >= 5:
                            raise DownloadError("Media redirect could not be safely followed.")
                        current_url = urljoin(current_url, location)
                        validate_media_url(current_url)
                        continue
                    response.raise_for_status()
                    ctype = response.headers.get("content-type", "").split(";")[0].lower()
                    if not (ctype.startswith("video/") or ctype == "application/octet-stream"):
                        raise DownloadError(f"Unsupported media content type: {ctype or 'unknown'}.")
                    try:
                        length = int(response.headers.get("content-length", "0") or 0)
                    except ValueError:
                        length = 0
                    if length > MAX_BYTES:
                        raise DownloadError("File exceeds the configured size limit.")
                    total = 0
                    with partial.open("wb") as stream:
                        async for chunk in response.aiter_bytes(256 * 1024):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > MAX_BYTES:
                                raise DownloadError("File exceeds the configured size limit.")
                            stream.write(chunk)
                            progress = round(total * 100 / length, 1) if length else None
                            storage.update_job(job_id, downloaded_bytes=total, progress=progress)
                    break
        filename = f"{job_id}_{safe_filename(requested_name)}"
        final_path = DOWNLOAD_DIR / filename
        partial.replace(final_path)
        storage.update_job(job_id, status="completed", progress=100,
                           filename=filename, size_bytes=final_path.stat().st_size)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        storage.update_job(job_id, status="failed", error=str(exc))

def file_path_for(job: dict):
    if not job or job.get("status") != "completed" or not job.get("filename"):
        return None
    path = DOWNLOAD_DIR / job["filename"]
    return path if path.is_file() else None
