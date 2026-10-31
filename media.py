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


async def _youtube_page(url: str) -> dict:
    """Запасной путь для YouTube: описание и субтитры прямо со страницы ролика."""
    import json
    import re

    import httpx

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
               "Accept-Language": "ru,en;q=0.8"}
    out = {"title": "", "text": "", "has_speech": False}
    # shorts и youtu.be приводим к обычной ссылке — у неё самая полная страница
    vid = re.search(r"(?:v=|shorts/|youtu\.be/|embed/)([\w-]{11})", url)
    if vid:
        url = f"https://www.youtube.com/watch?v={vid.group(1)}"
    try:
        async with httpx.AsyncClient(headers=headers, cookies={"CONSENT": "YES+1"},
                                     follow_redirects=True, timeout=20) as c:
            html = (await c.get(url)).text
            m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var |</script>)",
                          html, re.S)
            if not m:
                print("youtube page: нет данных о ролике на странице", url)
                return out
            pr = json.loads(m.group(1))
            vd = pr.get("videoDetails", {})
            out["title"] = vd.get("title", "")
            parts = []
            if vd.get("author"):
                parts.append(f"Автор: {vd['author']}")
            if vd.get("shortDescription"):
                parts.append(f"Описание: {vd['shortDescription'][:3000]}")

            tracks = (pr.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
                      .get("captionTracks", []))
            if tracks:
                track = next((t for t in tracks if t.get("languageCode") == "ru"),
                             next((t for t in tracks if t.get("languageCode") == "en"), tracks[0]))
                r = await c.get(track["baseUrl"] + "&fmt=json3")
                if r.status_code == 200 and r.text.strip():
                    ev = r.json().get("events", [])
                    subs = " ".join(s.get("utf8", "") for e in ev for s in e.get("segs", []) or [])
                    subs = " ".join(subs.split())[:8000]
                    if subs:
                        parts.append(f"Речь в видео (субтитры): {subs}")
                        out["has_speech"] = True
            out["text"] = "\n".join(parts)
            print(f"youtube page: описание {len(vd.get('shortDescription', ''))} симв., "
                  f"дорожек субтитров {len(tracks)}, речь {'есть' if out['has_speech'] else 'нет'}")
    except Exception as e:
        print("youtube page error:", e)
    return out


SUPADATA_KEY = os.environ.get("SUPADATA_API_KEY", "")


async def _supadata(url: str) -> str:
    """Готовые субтитры через Supadata (100 бесплатных запросов в месяц). Только существующие,
    без платной генерации."""
    if not SUPADATA_KEY:
        return ""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.get("https://api.supadata.ai/v1/transcript",
                            params={"url": url, "text": "true", "mode": "native", "lang": "ru"},
                            headers={"x-api-key": SUPADATA_KEY})
            if r.status_code != 200:
                print("supadata:", r.status_code, r.text[:200])
                return ""
            content = r.json().get("content", "")
            if isinstance(content, list):
                content = " ".join(ch.get("text", "") for ch in content)
            return " ".join(str(content).split())[:8000]
    except Exception as e:
        print("supadata error:", e)
        return ""


async def _youtube(url: str) -> dict:
    out = await _youtube_page(url)
    if not out["has_speech"]:
        subs = await _supadata(url)
        if subs:
            out["text"] = "\n".join(x for x in [out["text"], f"Речь в видео (субтитры): {subs}"] if x)
            out["has_speech"] = True
            print(f"supadata: субтитры получены, {len(subs)} симв.")
    return out


async def from_url(url: str) -> dict:
    """{'title', 'text'} по ссылке на видео. Никогда не падает."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            meta = await asyncio.wait_for(asyncio.to_thread(_download, url, tmp), timeout=150)
        except Exception as e:
            print("video download error:", url, str(e)[:200])
            if "youtube.com" in url or "youtu.be" in url:
                return await _youtube(url)
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
