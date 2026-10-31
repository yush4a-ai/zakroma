"""Настройки Закромов. Все значения берутся из переменных окружения (в Railway: Variables)."""
import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Кому можно пользоваться ботом: Telegram ID через запятую. Сначала только ты.
ALLOWED_USERS = {
    int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x
}

# --- Groq (бесплатно): картинки и распознавание речи, а по умолчанию и текст ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
VISION_MODEL = os.environ.get("VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-large-v3-turbo")

# --- Модель для выжимок и тегов ---
# По умолчанию Groq. Можно подставить любой OpenAI-совместимый API, например DeepSeek:
#   LLM_BASE_URL=https://api.deepseek.com  LLM_API_KEY=sk-...  TEXT_MODEL=deepseek-chat
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", GROQ_BASE_URL)
LLM_API_KEY = os.environ.get("LLM_API_KEY", GROQ_API_KEY)
TEXT_MODEL = os.environ.get("TEXT_MODEL", "llama-3.3-70b-versatile")

if not LLM_API_KEY:
    raise RuntimeError("Нужен GROQ_API_KEY (или LLM_API_KEY для другого провайдера)")

# Локальная модель для поиска по смыслу (понимает русский, работает без API)
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
