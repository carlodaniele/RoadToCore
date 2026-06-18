from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    schema_path = _project_root() / "shared" / "schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def validate_payload(payload: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(payload), key=lambda err: list(err.absolute_path))
    if not errors:
        return

    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "root"
    raise ValueError(f"Payload validation failed at '{path}': {first.message}")


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)

    content = dict(normalized.get("content", {}))
    sections = []

    for raw_section in content.get("sections", []):
        if not isinstance(raw_section, dict):
            continue

        heading = str(raw_section.get("heading", "")).strip()
        level = 3 if int(raw_section.get("level", 2)) == 3 else 2

        paragraphs: list[str] = []
        for paragraph in raw_section.get("paragraphs", []):
            text = str(paragraph).strip()
            if text:
                paragraphs.append(text)

        bullets: list[str] = []
        for bullet in raw_section.get("bullet_points", []):
            text = str(bullet).strip()
            if text:
                bullets.append(text)

        if not heading and not paragraphs and not bullets:
            continue

        sections.append(
            {
                "heading": heading or "Section",
                "level": level,
                "paragraphs": paragraphs or ["Details will be added in the next revision."],
                "bullet_points": bullets,
            }
        )

    content["sections"] = sections
    content["title"] = str(content.get("title", "")).strip()
    content["summary"] = str(content.get("summary", "")).strip()
    content["transcript_summary"] = str(content.get("transcript_summary", "")).strip()
    if "transcript_full" in content and content["transcript_full"] is not None:
        content["transcript_full"] = str(content["transcript_full"]).strip()

    normalized["content"] = content
    return normalized
