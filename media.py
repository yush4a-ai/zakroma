"""Видео: скачиваем звук (yt-dlp), сжимаем (ffmpeg), распознаём речь (Whisper через Groq)."""
import asyncio
import os
import subprocess
import tempfile

import imageio_ffmpeg

import ai

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
MAX_SECONDS = 20 * 60          # длиннее 20 минут речь не расшифровываем, берём только описание
COOKIES = os.environ.get("YTDLP_COOKIES", "")  # необязательно: cookies.txt для Instagram

VIDEO_SITES = ("youtube.com", "youtu.be", "instagram.com", "tiktok.com", "vk.com/clip",
               "vkvideo.ru", "rutube.ru", "x.com", "twitter.com")


def is_video_url(url: str) -> bool:
    u = url.lower()
    return any(s in u for s in VIDEO_SITES)


def _to_small_mp3(src: str, dst: str):
    """Звук моно 16 кГц 32 кбит/с — 20 минут влезают в ~5 МБ."""
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "32k", "-t", str(MAX_SECONDS), dst],
        check=True, timeout=180,
    )


def _download(url: str, tmp: str) -> dict:
    import yt_dlp

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 20, "ffmpeg_location": FFMPEG,
        "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
        # сначала чистый звук, иначе любой файл, где звук точно есть
        "format": "bestaudio/best[acodec!=none]/bestvideo*+bestaudio/best",
        "max_filesize": 150 * 1024 * 1024,
    }
    if COOKIES:
        cpath = os.path.join(tmp, "cookies.txt")
        with open(cpath, "w") as f:
            f.write(COOKIES)
        opts["cookiefile"] = cpath

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        out = {
            "title": info.get("title") or "",
            "description": (info.get("description") or "")[:3000],
            "author": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration") or 0,
            "path": None,
        }
        if out["duration"] and out["duration"] > MAX_SECONDS:
            return out
        info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        if downloads:
            out["path"] = downloads[0].get("filepath")
    return out


async def _transcribe_file(path: str, tmp: str) -> str:
    mp3 = os.path.join(tmp, "audio.mp3")
    await asyncio.to_thread(_to_small_mp3, path, mp3)
    with open(mp3, "rb") as f:
        data = f.read()
    return await ai.transcribe(data, filename="audio.mp3") or ""


async def from_url(url: str) -> dict:
    """{'title', 'text'} по ссылке на видео. Никогда не падает."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            meta = await asyncio.wait_for(asyncio.to_thread(_download, url, tmp), timeout=150)
        except Exception as e:
            print("video download error:", url, e)
            return {"title": "", "text": ""}

        transcript = ""
        if meta["path"]:
            try:
                transcript = await asyncio.wait_for(_transcribe_file(meta["path"], tmp), timeout=180)
            except Exception as e:
                print("transcribe error:", e)

    parts = []
    if meta["author"]:
        parts.append(f"Автор: {meta['author']}")
    if meta["description"]:
        parts.append(f"Описание: {meta['description']}")
    if transcript:
        parts.append(f"Речь в видео: {transcript}")
    return {"title": meta["title"], "text": "\n".join(parts), "has_speech": bool(transcript)}


async def from_bytes(data: bytes, name: str = "video.mp4") -> str:
    """Расшифровка видео/аудио-файла, присланного в Telegram."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, name)
        with open(src, "wb") as f:
            f.write(data)
        try:
            return await asyncio.wait_for(_transcribe_file(src, tmp), timeout=180)
        except Exception as e:
            print("transcribe error:", e)
            return ""
