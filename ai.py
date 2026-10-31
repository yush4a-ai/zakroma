"""ИИ-часть: выжимка + теги, поиск по смыслу (локальные эмбеддинги), голос (Whisper)."""
import asyncio
import base64
import io
import json
import re

import numpy as np
from openai import AsyncOpenAI

import config

llm = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
groq = (AsyncOpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL)
        if config.GROQ_API_KEY else None)
_embedder = None

PROMPT = """Ты — помощник в личной базе полезных материалов «Закрома».
Пользователь сохранил материал. Твоя задача — понять, о чём он, чтобы потом его легко было найти.

Тип: {kind}
Ссылка: {url}
Заголовок: {title}
Пометка пользователя: {note}
Содержимое:
{text}

Уже существующие теги пользователя: {tags}

Ответь ТОЛЬКО JSON без пояснений:
{{"title": "короткое понятное название, до 60 символов, по-русски",
  "summary": "1-2 предложения по-русски: что это и чем полезно",
  "tags": ["2-4 тега"]}}

Правила для тегов: строчные, по-русски, слова через _, без #.
В первую очередь бери подходящие из существующих тегов. Новый тег — только если ни один не подходит.
Если содержимого почти нет, опирайся на заголовок, ссылку и пометку."""


def _parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


def _clean_tags(tags) -> list[str]:
    out = []
    for t in tags or []:
        t = str(t).strip().lower().lstrip("#").replace(" ", "_").replace("-", "_")
        if t and t not in out:
            out.append(t)
    return out[:4]


async def analyze(kind, url, title, note, text, existing_tags, image: bytes | None = None) -> dict:
    prompt = PROMPT.format(
        kind=kind, url=url or "—", title=title or "—", note=note or "—",
        text=(text or "—")[:6000],
        tags=", ".join(existing_tags[:80]) or "пока нет",
    )
    data = {}
    try:
        if image and groq:
            # скриншот: модель, которая видит картинки
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}},
            ]
            resp = await groq.chat.completions.create(
                model=config.VISION_MODEL, max_tokens=400,
                messages=[{"role": "user", "content": content}],
            )
        else:
            resp = await llm.chat.completions.create(
                model=config.TEXT_MODEL, max_tokens=400,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
        data = _parse_json(resp.choices[0].message.content or "")
    except Exception as e:
        print("analyze error:", e)
    return {
        "title": (data.get("title") or title or note or url or "Без названия")[:120],
        "summary": data.get("summary", ""),
        "tags": _clean_tags(data.get("tags")),
    }


# ---------- поиск по смыслу ----------

def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(config.EMBED_MODEL)
    return _embedder


def _embed_sync(text: str) -> list[float]:
    vec = next(iter(_get_embedder().embed([text[:2000]])))
    return [float(x) for x in vec]


async def embed(text: str) -> list[float]:
    return await asyncio.to_thread(_embed_sync, text)


def rank(query_vec: list[float], rows, top: int = 5, min_score: float = 0.25) -> list[int]:
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    m = np.array([r["embedding"] for r in rows], dtype=np.float32)
    q = np.array(query_vec, dtype=np.float32)
    scores = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
    best = np.argsort(-scores)[:top]
    return [ids[i] for i in best if scores[i] >= min_score]


# ---------- голосовые ----------

async def transcribe(audio: bytes) -> str | None:
    if not groq:
        return None
    f = io.BytesIO(audio)
    f.name = "voice.ogg"
    r = await groq.audio.transcriptions.create(model=config.WHISPER_MODEL, file=f)
    return r.text
