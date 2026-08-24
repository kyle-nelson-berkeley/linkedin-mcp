"""Credential + token storage.

Values live in ``~/.config/linkedin-mcp/.env`` (dir 0700, file 0600). The
directory is overridable through ``LINKEDIN_MCP_CONFIG_DIR`` so tests never
touch the real one. Process environment always wins over the file.

Secret values are NEVER logged or repr'd: ``scrub()`` redacts any mapping key
containing SECRET / TOKEN / PASSWORD, recursively.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

CONFIG_DIR_ENV = "LINKEDIN_MCP_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "linkedin-mcp"
ENV_FILENAME = ".env"
PROPOSALS_DIRNAME = "proposals"
APPLIED_DIRNAME = "applied"
PENDING_AUTH_FILENAME = "pending_auth.json"

# How long a handed-out OAuth CSRF state stays usable. Matches the documented
# 30-minute authorization-code lifetime (docs/api-notes.md, source 5).
PENDING_STATE_TTL = 1800

DIR_MODE = 0o700
FILE_MODE = 0o600

REDACTED = "***redacted***"
_SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD")

KEY_CLIENT_ID = "LINKEDIN_CLIENT_ID"
KEY_CLIENT_SECRET = "LINKEDIN_CLIENT_SECRET"
KEY_ACCESS_TOKEN = "LINKEDIN_ACCESS_TOKEN"
KEY_EXPIRES_AT = "LINKEDIN_ACCESS_TOKEN_EXPIRES_AT"
KEY_REFRESH_TOKEN = "LINKEDIN_REFRESH_TOKEN"
KEY_REDIRECT_URI = "LINKEDIN_REDIRECT_URI"

DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing.

    The message names the missing KEY only — never a value.
    """


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    return Path(override).expanduser() if override else DEFAULT_CONFIG_DIR


def env_path() -> Path:
    return config_dir() / ENV_FILENAME


def proposals_dir() -> Path:
    return config_dir() / PROPOSALS_DIRNAME


def applied_dir() -> Path:
    return proposals_dir() / APPLIED_DIRNAME


def ensure_dir(path: Path) -> Path:
    """Create ``path`` owner-only, tightening every level we own down to 0700.

    ``Path.mkdir(parents=True)`` creates intermediate directories with the
    default umask mode, so each level inside the config root is created and
    chmod'ed explicitly instead.
    """
    root = config_dir()
    levels: list[Path] = []
    current = path
    while True:
        levels.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent

    # Anything above the config root (e.g. ~/.config) is left alone.
    levels[-1].parent.mkdir(parents=True, exist_ok=True)
    for directory in reversed(levels):
        directory.mkdir(mode=DIR_MODE, exist_ok=True)
        os.chmod(directory, DIR_MODE)
    return path


def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def read_env_file() -> dict[str, str]:
    path = env_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as exc:  # unreadable file — surface the path, never contents
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from exc
    return _parse_env_text(text)


def get(key: str, default: str | None = None) -> str | None:
    """Process env first, then the .env file, then ``default``."""
    from_process = os.environ.get(key)
    if from_process:
        return from_process
    value = read_env_file().get(key)
    return value if value else default


def require(key: str) -> str:
    value = get(key)
    if not value:
        raise ConfigError(
            f"missing required setting {key} — add it to {env_path()} "
            "(see .env.example; values are entered by the human owner only)"
        )
    return value


def write_values(values: Mapping[str, str]) -> Path:
    """Merge ``values`` into the .env file, preserving unrelated keys.

    Directory is created 0700 and the file written 0600.
    """
    ensure_dir(config_dir())
    path = env_path()
    existing = read_env_file()
    merged = {**existing, **{str(k): str(v) for k, v in values.items()}}

    lines = [
        "# linkedin-mcp credentials — owner-only (0600). Never commit this file.",
        *(f"{key}={value}" for key, value in merged.items()),
    ]
    # Write through a private temp file so the secret never exists world-readable.
    tmp_path = path.with_suffix(".env.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, path)
    os.chmod(path, FILE_MODE)
    return path


# ------------------------------------------------- pending OAuth CSRF state


def pending_auth_path() -> Path:
    return config_dir() / PENDING_AUTH_FILENAME


def save_pending_state(state: str) -> Path:
    """Persist the CSRF state of an in-flight sign-in, owner-only (0600).

    ``auth_start(url_only=True)`` hands a state to the human inside a URL; the
    listener started later must expect that SAME state or the callback can
    never pass the CSRF check.
    """
    ensure_dir(config_dir())
    path = pending_auth_path()
    payload = json.dumps({"state": str(state), "created_at": int(time.time())})
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w") as handle:
        handle.write(payload)
    os.chmod(path, FILE_MODE)
    return path


def load_pending_state(max_age: float = PENDING_STATE_TTL) -> str | None:
    """Return the pending CSRF state, or None if absent, stale, or unreadable."""
    try:
        record = json.loads(pending_auth_path().read_text())
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(record, Mapping):
        return None
    state = record.get("state")
    created_at = record.get("created_at")
    if not isinstance(state, str) or not state:
        return None
    if not isinstance(created_at, (int, float)):
        return None
    if time.time() - created_at > max_age:
        return None
    return state


def clear_pending_state() -> None:
    """Forget the in-flight sign-in. Safe to call when nothing is pending."""
    pending_auth_path().unlink(missing_ok=True)


def _is_secret_key(key: Any) -> bool:
    upper = str(key).upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def scrub(value: Any) -> Any:
    """Return a copy of ``value`` with every secret-looking field redacted."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_secret_key(key) else scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value


def redirect_uri() -> str:
    return get(KEY_REDIRECT_URI, DEFAULT_REDIRECT_URI) or DEFAULT_REDIRECT_URI


def access_token() -> str | None:
    return get(KEY_ACCESS_TOKEN)


def save_token(
    *,
    access_token: str,
    expires_in: int | float | None = None,
    refresh_token: str | None = None,
    scope: str | None = None,
) -> Path:
    values: dict[str, str] = {KEY_ACCESS_TOKEN: access_token}
    if expires_in is not None:
        values[KEY_EXPIRES_AT] = str(int(time.time() + float(expires_in)))
    if refresh_token:
        values[KEY_REFRESH_TOKEN] = refresh_token
    if scope:
        values["LINKEDIN_SCOPE"] = scope
    return write_values(values)


def token_status() -> dict[str, Any]:
    """Non-secret summary of the stored token (never includes the token)."""
    token = access_token()
    raw_expiry = get(KEY_EXPIRES_AT)
    try:
        expires_at = int(raw_expiry) if raw_expiry else 0
    except ValueError:
        expires_at = 0
    has_token = bool(token)
    expired = (not has_token) or (expires_at > 0 and expires_at <= time.time())
    return {
        "has_token": has_token,
        "expires_at": expires_at,
        "expires_in_seconds": max(0, int(expires_at - time.time())) if expires_at else 0,
        "expired": expired,
        "has_refresh_token": bool(get(KEY_REFRESH_TOKEN)),
        "config_file": str(env_path()),
    }
