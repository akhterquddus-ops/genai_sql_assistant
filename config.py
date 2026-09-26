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
