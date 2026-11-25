"""Telegram bot frontend for XENO's shared API.
Users can validate X links and request downloads from operator-allowlisted direct media URLs.
"""
import os, logging, asyncio, tempfile
from pathlib import Path
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram import FSInputFile

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = os.getenv("XENO_API_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_SEND_MB = int(os.getenv("XENO_TELEGRAM_MAX_SEND_MB", "45"))
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("xeno-bot")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to XENO.\n"
        "Send a public X post URL to find and download an available MP4 video.\n"
        "Use /media DIRECT_HTTPS_VIDEO_URL for an approved direct media URL.\n"
        "Private/protected posts and access bypass are not supported."
    )

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API}/api/v1/health")
            r.raise_for_status()
        await update.message.reply_text("XENO API is online.")
    except Exception:
        await update.message.reply_text("XENO API is unreachable.")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post_url = (update.message.text or "").strip()
    status_message = await update.message.reply_text("Checking X post and available video…")
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            check = await client.post(f"{API}/api/v1/validate", json={"url": post_url})
            check_data = check.json()
            if not check.is_success:
                raise RuntimeError(check_data.get("detail", "Unsupported X post URL."))
            extract = await client.post(f"{API}/api/v1/extract", json={"url": post_url})
            extracted = extract.json()
            if not extract.is_success:
                raise RuntimeError(extracted.get("detail", "X video extraction failed."))
            media_items = extracted.get("media", [])
            if not media_items or not media_items[0].get("variants"):
                raise RuntimeError("No downloadable MP4 video was found.")
            start = await client.post(f"{API}/api/v1/download-x", json={
                "url": post_url, "media_index": 0, "variant_index": 0
            })
            job = start.json()
            if not start.is_success:
                raise RuntimeError(job.get("detail", "Could not start video download."))
            job_id = job["job_id"]
            await status_message.edit_text("Video download started. Please wait…")
            for _ in range(240):
                await asyncio.sleep(2)
                state_response = await client.get(f"{API}/api/v1/download/{job_id}")
                state_response.raise_for_status()
                state = state_response.json()
                if state["status"] == "failed":
                    raise RuntimeError(state.get("error", "Download failed."))
                if state["status"] == "completed":
                    size = int(state.get("size_bytes") or 0)
                    if size > MAX_SEND_MB * 1024 * 1024:
                        raise RuntimeError(
                            f"Video is larger than Telegram bot's {MAX_SEND_MB} MB send limit. Use the website download link instead."
                        )
                    file_response = await client.get(f"{API}/api/v1/files/{job_id}")
                    file_response.raise_for_status()
                    with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
                        tmp.write(file_response.content)
                        tmp.flush()
                        await update.message.reply_video(
                            video=FSInputFile(tmp.name, filename="xeno-video.mp4"),
                            caption="Downloaded by XENO from a public X post."
                        )
                    await status_message.delete()
                    return
            raise RuntimeError("Timed out waiting for the video download.")
    except Exception as exc:
        log.warning("XENO post request failed: %s", type(exc).__name__)
        await status_message.edit_text(
            "XENO could not complete this request. The post may be unavailable or the service may be temporarily busy. Please try again later."
        )

async def media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /media https://approved-host.example/video.mp4")
        return
    media_url = context.args[0].strip()
    status_message = await update.message.reply_text("Checking approved media source…")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{API}/api/v1/download-direct",
                json={"media_url": media_url, "filename": "xeno-video.mp4"}
            )
            payload = response.json()
            if not response.is_success:
                raise RuntimeError(payload.get("detail", "Could not start download."))
            job_id = payload["job_id"]
            await status_message.edit_text("Download started. Waiting for completion…")
            for _ in range(180):
                await asyncio.sleep(2)
                state_response = await client.get(f"{API}/api/v1/download/{job_id}")
                state_response.raise_for_status()
                state = state_response.json()
                if state["status"] == "failed":
                    raise RuntimeError(state.get("error", "Download failed."))
                if state["status"] == "completed":
                    size = int(state.get("size_bytes") or 0)
                    if size > MAX_SEND_MB * 1024 * 1024:
                        raise RuntimeError(
                            f"File is larger than this bot's {MAX_SEND_MB} MB send limit. "
                            "Use the website download link instead."
                        )
                    file_response = await client.get(f"{API}/api/v1/files/{job_id}")
                    file_response.raise_for_status()
                    with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
                        tmp.write(file_response.content)
                        tmp.flush()
                        await status_message.edit_text("Download complete. Sending video…")
                        await update.message.reply_video(
                            video=FSInputFile(tmp.name, filename="xeno-video.mp4"),
                            caption="Downloaded by XENO from an approved direct media URL."
                        )
                    await status_message.delete()
                    return
            raise RuntimeError("Timed out waiting for the download.")
    except Exception as exc:
        log.warning("XENO post request failed: %s", type(exc).__name__)
        await status_message.edit_text(
            "XENO could not complete this request. The post may be unavailable or the service may be temporarily busy. Please try again later."
        )

def main():
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in your environment.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("media", media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()

if __name__ == "__main__":
    main()
