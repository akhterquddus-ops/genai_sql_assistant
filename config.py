"""
config.py - Load and validate application settings.

All settings come from environment variables, which python-dotenv loads
from a local ".env" file. Credentials therefore never appear in the code
and never end up in Git.

Every other module calls get_settings() instead of reading os.environ
directly. That keeps validation and defaults in one place.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import load_dotenv

# Read .env (if present) into os.environ. Real environment variables win,
# which is how servers and containers are normally configured.
load_dotenv()


class ConfigError(Exception):
    """Raised when a required setting is missing or invalid."""


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    if required and not value:
        raise ConfigError(
            f"Missing setting '{name}'. Add it to your .env file (see .env.example)."
        )
    return value


def _get_int(name: str, default: int) -> int:
    raw = _get(name, str(default))
    # Allow inline comments such as "30   # seconds"
    raw = raw.split("#", 1)[0].strip()
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"Setting '{name}' must be a whole number, got '{raw}'.")
    if value <= 0:
        raise ConfigError(f"Setting '{name}' must be greater than 0.")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name, "yes" if default else "no").split("#", 1)[0].strip().lower()
    if raw in ("yes", "true", "1"):
        return True
    if raw in ("no", "false", "0"):
        return False
    raise ConfigError(f"Setting '{name}' must be yes/no, got '{raw}'.")


@dataclass(frozen=True)
class DatabaseSettings:
    server: str
    database: str
    auth_mode: str                                   # "sql" or "windows"
    username: str | None
    # repr=False: printing or logging the settings object never shows the password.
    password: str | None = field(repr=False)
    driver: str
    trust_server_certificate: bool
    login_timeout: int
    query_timeout: int
    max_rows: int


def load_settings() -> DatabaseSettings:
    """Read settings from the environment and validate them."""
    auth_mode = (_get("DB_AUTH_MODE", "sql") or "sql").lower()
    if auth_mode not in ("sql", "windows"):
        raise ConfigError("DB_AUTH_MODE must be 'sql' or 'windows'.")

    # Username/password are only required for SQL authentication.
    needs_login = auth_mode == "sql"

    return DatabaseSettings(
        server=_get("DB_SERVER", required=True),
        database=_get("DB_DATABASE", required=True),
        auth_mode=auth_mode,
        username=_get("DB_USERNAME", required=needs_login),
        password=_get("DB_PASSWORD", required=needs_login),
        driver=_get("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        trust_server_certificate=_get_bool("DB_TRUST_SERVER_CERTIFICATE", True),
        login_timeout=_get_int("DB_LOGIN_TIMEOUT", 5),
        query_timeout=_get_int("DB_QUERY_TIMEOUT", 30),
        max_rows=_get_int("MAX_RESULT_ROWS", 1000),
    )


@lru_cache(maxsize=1)
def get_settings() -> DatabaseSettings:
    """Load settings once and reuse them (cached)."""
    return load_settings()


# ---------------------------------------------------------------------------
# Local LLM (Ollama) settings - Phase 4
# ---------------------------------------------------------------------------

# Hosts that mean "this computer". Anything else would send our database
# schema and results over the network, which this project must not do.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _get_float(name: str, default: float) -> float:
    raw = _get(name, str(default)).split("#", 1)[0].strip()
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"Setting '{name}' must be a number, got '{raw}'.")


@dataclass(frozen=True)
class OllamaSettings:
    url: str             # where the Ollama server listens
    model: str           # e.g. qwen2.5-coder:3b
    rewrite_model: str   # model for follow-up rewriting (defaults to `model`)
    timeout: int         # seconds to wait for one answer
    temperature: float   # 0 = most predictable output (best for SQL)
    context_size: int    # tokens the model can "see" at once (prompt + answer)


def load_ollama_settings() -> OllamaSettings:
    url = _get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    host = urlparse(url).hostname or ""
    allow_remote = _get_bool("OLLAMA_ALLOW_REMOTE", False)
    if host not in _LOCAL_HOSTS and not allow_remote:
        raise ConfigError(
            f"OLLAMA_URL points to '{host}', which is not this computer. "
            "This project keeps all data local. Set OLLAMA_ALLOW_REMOTE=yes "
            "only if you really mean to use another machine."
        )

    temperature = _get_float("OLLAMA_TEMPERATURE", 0.0)
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError("OLLAMA_TEMPERATURE must be between 0 and 2.")

    model = _get("OLLAMA_MODEL", "qwen2.5-coder:3b")
    return OllamaSettings(
        url=url,
        model=model,
        rewrite_model=_get("OLLAMA_REWRITE_MODEL", "") or model,
        timeout=_get_int("OLLAMA_TIMEOUT", 180),
        temperature=temperature,
        context_size=_get_int("OLLAMA_CONTEXT_SIZE", 4096),
    )


@lru_cache(maxsize=1)
def get_ollama_settings() -> OllamaSettings:
    return load_ollama_settings()


# ---------------------------------------------------------------------------
# Display settings - Phase 7
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_currency() -> tuple[str, str]:
    """(symbol, name) used when the AI explanation mentions money."""
    return (_get("CURRENCY_SYMBOL", "Rs.") or "Rs.",
            _get("CURRENCY_NAME", "Pakistani Rupees") or "Pakistani Rupees")
