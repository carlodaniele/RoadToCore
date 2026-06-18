from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvValidationReport:
    strict_mode: bool
    missing_keys: list[str]


def _is_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_non_empty(key: str) -> bool:
    value = os.getenv(key)
    return value is not None and value.strip() != ""


def collect_missing_env_keys() -> list[str]:
    required: list[str] = [
        "TELEGRAM_TOKEN",
    ]

    ai_provider = os.getenv("AI_PROVIDER", "google").strip().lower()
    if ai_provider == "google":
        required.append("GOOGLE_API_KEY")

    if _is_enabled(os.getenv("DELIVERY_WORDPRESS_ENABLED")):
        required.extend(
            [
                "DELIVERY_WORDPRESS_ENDPOINT",
                "DELIVERY_WORDPRESS_USERNAME",
                "DELIVERY_WORDPRESS_APP_PASSWORD",
            ]
        )

    if _is_enabled(os.getenv("DELIVERY_ASTRO_ENABLED")):
        required.extend(
            [
                "DELIVERY_ASTRO_ADAPTER_DIST",
                "DELIVERY_ASTRO_CONTENT_DIR",
                "DELIVERY_ASTRO_PUBLIC_DIR",
                "DELIVERY_ASTRO_ASSETS_DIR",
            ]
        )

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique_required: list[str] = []
    for key in required:
        if key not in seen:
            seen.add(key)
            unique_required.append(key)

    return [key for key in unique_required if not _is_non_empty(key)]


def validate_env_for_runtime() -> EnvValidationReport:
    missing = collect_missing_env_keys()

    strict_override = os.getenv("ENV_VALIDATION_STRICT")
    strict_mode = _is_enabled(strict_override) if strict_override is not None else _is_enabled(os.getenv("GITHUB_ACTIONS"))

    if strict_mode and missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Missing required environment variables: "
            f"{joined}. Configure these in GitHub Secrets/Variables or runtime env."
        )

    return EnvValidationReport(strict_mode=strict_mode, missing_keys=missing)
