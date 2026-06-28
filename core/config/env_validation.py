from __future__ import annotations

import os
from dataclasses import dataclass


RUNTIME_ROLE_INGEST = "ingest"
RUNTIME_ROLE_POLLING = "polling"
RUNTIME_ROLE_INTAKE_PROCESSOR = "intake-processor"
RUNTIME_ROLE_DELIVERY_WORKER = "delivery-worker"
RUNTIME_ROLE_ALL = "all"

_KNOWN_RUNTIME_ROLES = {
    RUNTIME_ROLE_INGEST,
    RUNTIME_ROLE_POLLING,
    RUNTIME_ROLE_INTAKE_PROCESSOR,
    RUNTIME_ROLE_DELIVERY_WORKER,
    RUNTIME_ROLE_ALL,
}


@dataclass(frozen=True)
class EnvValidationReport:
    strict_mode: bool
    runtime_role: str
    missing_keys: list[str]


def _is_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_non_empty(key: str) -> bool:
    value = os.getenv(key)
    return value is not None and value.strip() != ""


def _normalize_runtime_role(runtime_role: str | None) -> str:
    candidate = (runtime_role or os.getenv("ROADTOCORE_RUNTIME_ROLE") or RUNTIME_ROLE_ALL).strip().lower()
    if candidate not in _KNOWN_RUNTIME_ROLES:
        return RUNTIME_ROLE_ALL
    return candidate


def collect_missing_env_keys(runtime_role: str | None = None) -> list[str]:
    role = _normalize_runtime_role(runtime_role)
    required: list[str] = []

    if role in {RUNTIME_ROLE_ALL, RUNTIME_ROLE_POLLING, RUNTIME_ROLE_INTAKE_PROCESSOR}:
        required.append("TELEGRAM_TOKEN")

    ai_provider = os.getenv("AI_PROVIDER", "google").strip().lower()
    if role in {RUNTIME_ROLE_ALL, RUNTIME_ROLE_INTAKE_PROCESSOR} and ai_provider == "google":
        required.append("GOOGLE_API_KEY")

    if role in {RUNTIME_ROLE_ALL, RUNTIME_ROLE_DELIVERY_WORKER} and _is_enabled(os.getenv("DELIVERY_WORDPRESS_ENABLED")):
        required.extend(
            [
                "DELIVERY_WORDPRESS_ENDPOINT",
                "DELIVERY_WORDPRESS_USERNAME",
                "DELIVERY_WORDPRESS_APP_PASSWORD",
            ]
        )

    if role in {RUNTIME_ROLE_ALL, RUNTIME_ROLE_DELIVERY_WORKER} and _is_enabled(os.getenv("DELIVERY_ASTRO_ENABLED")):
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


def validate_env_for_runtime(runtime_role: str | None = None) -> EnvValidationReport:
    resolved_role = _normalize_runtime_role(runtime_role)
    missing = collect_missing_env_keys(runtime_role=resolved_role)

    strict_override = os.getenv("ENV_VALIDATION_STRICT")
    strict_mode = _is_enabled(strict_override) if strict_override is not None else _is_enabled(os.getenv("GITHUB_ACTIONS"))

    if strict_mode and missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Missing required environment variables: "
            f"{joined}. Configure these in GitHub Secrets/Variables or runtime env."
        )

    return EnvValidationReport(strict_mode=strict_mode, runtime_role=resolved_role, missing_keys=missing)
