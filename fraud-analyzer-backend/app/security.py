from __future__ import annotations

import os

API_KEY_ENV = "FRAUDY_API_KEY"
CORS_ORIGINS_ENV = "FRAUDY_CORS_ORIGINS"

# Excel task pane is served from the Office add-in webpack host.
DEFAULT_CORS_ORIGINS = (
    "https://localhost:3000",
    "https://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


class MissingApiKeyError(RuntimeError):
    """Raised when FRAUDY_API_KEY is missing or blank at startup."""


class InvalidCorsOriginsError(ValueError):
    """Raised when CORS is configured as a wildcard or empty override."""


def configured_api_key() -> str:
    return os.getenv(API_KEY_ENV, "").strip()


def require_api_key() -> str:
    key = configured_api_key()
    if not key:
        raise MissingApiKeyError(
            "FRAUDY_API_KEY is required and must be a non-empty value."
        )
    return key


def cors_allow_origins(raw: str | None = None) -> list[str]:
    source = os.getenv(CORS_ORIGINS_ENV, "") if raw is None else raw
    if raw is None and not source.strip():
        return list(DEFAULT_CORS_ORIGINS)
    origins = [part.strip() for part in source.split(",") if part.strip()]
    if not origins or any(origin == "*" for origin in origins):
        raise InvalidCorsOriginsError(
            "FRAUDY_CORS_ORIGINS must list explicit origins; wildcard '*' is not allowed."
        )
    return origins
