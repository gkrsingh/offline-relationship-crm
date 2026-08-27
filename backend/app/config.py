"""Environment-backed settings.

Deliberately plain: read .env once, expose typed getters. There is no settings
framework here because there are nine values and none of them are dynamic.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# Two locations, and a name prefix, because that is where the keys actually
# live on this machine. Repo root wins if both define the same variable.
load_dotenv(REPO_ROOT / "backend" / ".env")
load_dotenv(REPO_ROOT / ".env", override=True)


def _secret(*names: str) -> str | None:
    """Read a credential, trying each name and its IA_-prefixed alias.

    Keys on this machine live in backend/.env under an IA_ prefix, and Google
    ships the same credential as GEMINI_API_KEY or GOOGLE_API_KEY depending on
    which SDK wrote the docs. Trying all of them beats reporting a key missing
    when it is sitting right there under another name.

    The value is never logged, printed or written anywhere by this module.
    """
    for name in names:
        value = os.getenv(name) or os.getenv(f"IA_{name}")
        if value:
            return value
    return None


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq").strip().lower()
GROQ_API_KEY: str | None = _secret("GROQ_API_KEY")
GEMINI_API_KEY: str | None = _secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

# When true the process refuses to open a socket and answers only from cache.
# This is how the deployed demo runs.
LLM_OFFLINE: bool = _flag("LLM_OFFLINE", default=False)

LLM_CACHE_DIR: Path = REPO_ROOT / os.getenv("LLM_CACHE_DIR", "data/cache/llm")
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

DB_PATH: Path = REPO_ROOT / os.getenv("DB_PATH", "data/crm.db")
