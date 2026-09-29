from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.security import (
    DEFAULT_CORS_ORIGINS,
    InvalidCorsOriginsError,
    MissingApiKeyError,
    configured_api_key,
    cors_allow_origins,
    require_api_key,
)

# Split so this test file does not contain the retired shared-token literal.
FORBIDDEN_SHARED_TOKEN = "".join(("super-secret-", "local-token"))
REPO_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "__pycache__"}
SCAN_SUFFIXES = {
    ".py",
    ".ts",
    ".html",
    ".md",
    ".yml",
    ".yaml",
    ".gs",
    ".json",
    ".xml",
    ".js",
    ".bat",
    ".sh",
}


class ConfiguredApiKeyTests(unittest.TestCase):
    def test_unset_is_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configured_api_key(), "")

    def test_whitespace_is_empty(self) -> None:
        with patch.dict(os.environ, {"FRAUDY_API_KEY": "   "}):
            self.assertEqual(configured_api_key(), "")

    def test_returns_stripped_key(self) -> None:
        with patch.dict(os.environ, {"FRAUDY_API_KEY": "  abc123  "}):
            self.assertEqual(configured_api_key(), "abc123")


class RequireApiKeyTests(unittest.TestCase):
    def test_startup_refuses_missing_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingApiKeyError):
                require_api_key()

    def test_startup_refuses_empty_key(self) -> None:
        with patch.dict(os.environ, {"FRAUDY_API_KEY": ""}):
            with self.assertRaises(MissingApiKeyError):
                require_api_key()

    def test_returns_configured_key(self) -> None:
        with patch.dict(os.environ, {"FRAUDY_API_KEY": "local-dev-key"}):
            self.assertEqual(require_api_key(), "local-dev-key")


class CorsOriginsTests(unittest.TestCase):
    def test_defaults_are_localhost_addin_origins(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            origins = cors_allow_origins()
        self.assertEqual(origins, list(DEFAULT_CORS_ORIGINS))
        self.assertNotIn("*", origins)
        self.assertIn("https://localhost:3000", origins)
        self.assertTrue(
            all(
                origin.startswith(
                    (
                        "http://localhost",
                        "https://localhost",
                        "http://127.0.0.1",
                        "https://127.0.0.1",
                    )
                )
                for origin in origins
            )
        )

    def test_env_list_overrides_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {"FRAUDY_CORS_ORIGINS": "https://localhost:3000, https://example.test"},
        ):
            self.assertEqual(
                cors_allow_origins(),
                ["https://localhost:3000", "https://example.test"],
            )

    def test_rejects_wildcard(self) -> None:
        with self.assertRaises(InvalidCorsOriginsError):
            cors_allow_origins("*")

    def test_rejects_wildcard_in_list(self) -> None:
        with self.assertRaises(InvalidCorsOriginsError):
            cors_allow_origins("https://localhost:3000, *")

    def test_rejects_blank_override(self) -> None:
        with self.assertRaises(InvalidCorsOriginsError):
            cors_allow_origins(" , , ")


class SharedSecretAbsentTests(unittest.TestCase):
    def test_repo_does_not_ship_shared_token(self) -> None:
        offenders: list[str] = []
        for path in REPO_ROOT.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if FORBIDDEN_SHARED_TOKEN in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
