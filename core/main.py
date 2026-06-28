from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException

from core.config.env_validation import EnvValidationReport, RUNTIME_ROLE_INGEST, validate_env_for_runtime
from core.telegram.events import TelegramIntakeEventStore
from core.telegram.webhook import (
    TelegramBufferStore,
    build_idempotency_key,
    extract_message,
    parse_allowed_chat_ids,
    parse_audio_message,
    parse_photo_message,
)


load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

env_report: EnvValidationReport = validate_env_for_runtime(runtime_role=RUNTIME_ROLE_INGEST)

app = FastAPI(title="RoadToCore Core", version="0.2.0")

allowed_chat_ids = parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
outbox_dir = Path(os.getenv("ROADTOCORE_OUTBOX_DIR", "./outbox"))
outbox_dir.mkdir(parents=True, exist_ok=True)

buffer_store = TelegramBufferStore(outbox_dir / "intake" / "buffer")
intake_store = TelegramIntakeEventStore(outbox_dir)


def _is_allowed_chat(chat_id: int) -> bool:
    return not allowed_chat_ids or chat_id in allowed_chat_ids


def _assert_secret(provided_secret: str | None) -> None:
    if not webhook_secret:
        return
    if provided_secret != webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if not env_report.missing_keys else "degraded",
        "runtime_role": env_report.runtime_role,
        "strict_mode": env_report.strict_mode,
        "missing_env_keys_count": len(env_report.missing_keys),
    }


@app.get("/health/env")
def health_env() -> dict[str, Any]:
    return {
        "runtime_role": env_report.runtime_role,
        "strict_mode": env_report.strict_mode,
        "missing_env_keys": env_report.missing_keys,
    }


@app.post("/webhook/telegram")
def telegram_webhook(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _assert_secret(x_telegram_bot_api_secret_token)

    message = extract_message(update)
    if not message:
        return {"status": "ignored", "reason": "unsupported update"}

    chat_id = int(message["chat"]["id"])
    if not _is_allowed_chat(chat_id):
        raise HTTPException(status_code=403, detail="Chat not allowed")

    message_chat_id, images = parse_photo_message(message)
    if images:
        buffer_store.add_images(message_chat_id, images)
        return {
            "status": "buffered",
            "chat_id": message_chat_id,
            "images_buffered": len(images),
        }

    audio_event = parse_audio_message(message)
    if not audio_event:
        return {"status": "ignored", "reason": "message has no image/audio"}

    idempotency_key = build_idempotency_key(audio_event)
    buffered_images = buffer_store.peek_images(audio_event.chat_id)

    result = intake_store.stage_audio_intake(
        audio_event=audio_event,
        buffered_images=buffered_images,
        idempotency_key=idempotency_key,
        telegram_update_id=None,
    )
    buffer_store.clear_images(audio_event.chat_id)

    return {
        "status": result.status,
        "intake_id": result.intake_id,
        "idempotency_key": result.idempotency_key,
        "event_path": result.event_path,
    }
