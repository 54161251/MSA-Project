"""Settings read from the environment (and .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "makerspace"
VAR_DIR = ROOT / "var"


@dataclass(frozen=True)
class Settings:
    db_url: str
    checkpoint_db: str
    llm_provider: str
    llm_model: str
    anthropic_api_key: str
    llm_base_url: str
    llm_api_key: str
    llm_min_interval: float
    admin_name: str
    admin_email: str
    admin_password: str
    api_url: str
    scheduler: bool
    daily_hour: int


def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        db_url=env("MSA_DB_URL", f"sqlite:///{VAR_DIR / 'msa.db'}"),
        checkpoint_db=env("MSA_CHECKPOINT_DB", str(VAR_DIR / "checkpoints.db")),
        llm_provider=env("MSA_LLM_PROVIDER", "offline").strip().lower(),
        llm_model=env("MSA_LLM_MODEL", "claude-sonnet-5-5"),
        anthropic_api_key=env("ANTHROPIC_API_KEY", "").strip(),
        llm_base_url=env("MSA_LLM_BASE_URL", "").strip().rstrip("/"),
        llm_api_key=env("MSA_LLM_API_KEY", "").strip(),
        llm_min_interval=float(env("MSA_LLM_MIN_INTERVAL", "0") or 0),
        admin_name=env("MSA_ADMIN_NAME", "Shop Staff"),
        admin_email=env("MSA_ADMIN_EMAIL", "staff@example.org"),
        admin_password=env("MSA_ADMIN_PASSWORD", "change-me-now"),
        api_url=env("MSA_API_URL", "http://127.0.0.1:8000"),
        scheduler=env("MSA_SCHEDULER", "on").lower() in ("1", "on", "true", "yes"),
        daily_hour=int(env("MSA_DAILY_HOUR", "7")),
    )
