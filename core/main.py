from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException

from core.ai.pipeline import AIPipeline
from core.config.env_validation import EnvValidationReport, validate_env_for_runtime
from core.delivery.dispatch import AstroConfig, DeliveryConfig, DeliveryDispatcher, WordPressConfig
from core.delivery.outbox_worker import deliver_pending_outbox
from core.output.schema import normalize_payload, validate_payload
from core.telegram.client import TelegramClient
from core.telegram.webhook import (
    TelegramBufferStore,
    build_idempotency_key,
    extract_message,
    parse_allowed_chat_ids,
    parse_audio_message,
    parse_photo_message,
)


load_dotenv()

env_report: EnvValidationReport = validate_env_for_runtime()

app = FastAPI(title="RoadToCode Core", version="0.1.0")
store = TelegramBufferStore()
pipeline = AIPipeline(
    provider=os.getenv("AI_PROVIDER", "google"),
    default_language=os.getenv("AI_DEFAULT_LANGUAGE", "English"),
    google_api_key=os.getenv("GOOGLE_API_KEY", ""),
    transcription_model=os.getenv("GOOGLE_TRANSCRIPTION_MODEL", "gemini-2.0-flash"),
    generation_model=os.getenv("GOOGLE_GENERATION_MODEL", "gemini-2.0-flash"),
)

allowed_chat_ids = parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
outbox_dir = Path(os.getenv("ROADTOCODE_OUTBOX_DIR", "./outbox"))
outbox_dir.mkdir(parents=True, exist_ok=True)
assets_dir = Path(os.getenv("ROADTOCODE_ASSETS_DIR", str(outbox_dir / "assets")))
assets_dir.mkdir(parents=True, exist_ok=True)
idempotency_dir = outbox_dir / ".idempotency"
idempotency_dir.mkdir(parents=True, exist_ok=True)

telegram_client = TelegramClient(telegram_token) if telegram_token else None

delivery = DeliveryDispatcher(
    config=DeliveryConfig(
        retries=int(os.getenv("DELIVERY_RETRIES", "2")),
        retry_backoff_seconds=float(os.getenv("DELIVERY_RETRY_BACKOFF_SECONDS", "2.0")),
        wp=WordPressConfig(
            enabled=os.getenv("DELIVERY_WORDPRESS_ENABLED", "false").lower() == "true",
            endpoint=os.getenv("DELIVERY_WORDPRESS_ENDPOINT", "").strip(),
            username=os.getenv("DELIVERY_WORDPRESS_USERNAME", "").strip(),
            app_password=os.getenv("DELIVERY_WORDPRESS_APP_PASSWORD", "").strip(),
            timeout=float(os.getenv("DELIVERY_WORDPRESS_TIMEOUT", "30")),
        ),
        astro=AstroConfig(
            enabled=os.getenv("DELIVERY_ASTRO_ENABLED", "false").lower() == "true",
            node_bin=os.getenv("DELIVERY_ASTRO_NODE_BIN", "node").strip(),
            adapter_dist_path=os.getenv("DELIVERY_ASTRO_ADAPTER_DIST", "").strip(),
            content_dir=os.getenv("DELIVERY_ASTRO_CONTENT_DIR", "").strip(),
            public_dir=os.getenv("DELIVERY_ASTRO_PUBLIC_DIR", "").strip(),
            assets_dir=os.getenv("DELIVERY_ASTRO_ASSETS_DIR", "").strip(),
        ),
    ),
    outbox_dir=outbox_dir,
)


def _is_allowed_chat(chat_id: int) -> bool:
    return not allowed_chat_ids or chat_id in allowed_chat_ids


def _assert_secret(provided_secret: str | None) -> None:
    if not webhook_secret:
        return
    if provided_secret != webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _persist_output(payload: dict[str, Any]) -> str:
    event_id = payload["event_id"]
    file_path = outbox_dir / f"{event_id}.json"
    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(file_path)


def _download_and_store_images(
    chat_id: int,
    event_id: str,
    images: list[Any],
) -> list[dict[str, Any]]:
    if telegram_client is None:
        return []

    image_assets: list[dict[str, Any]] = []
    event_assets_dir = assets_dir / str(chat_id) / event_id
    event_assets_dir.mkdir(parents=True, exist_ok=True)

    for index, image in enumerate(images):
        file_id = getattr(image, "file_id", "")
        if not file_id:
            continue

        try:
            downloaded = telegram_client.download_file(file_id)
        except Exception:
            continue

        suffix = Path(downloaded.file_path).suffix or ".jpg"
        file_name = f"image-{index + 1}{suffix}"
        file_path = event_assets_dir / file_name
        file_path.write_bytes(downloaded.content)

        image_assets.append(
            {
                "asset_ref": str(file_path.resolve()),
                "caption": "",
                "alt": "",
                "width": getattr(image, "width", None),
                "height": getattr(image, "height", None),
            }
        )

    return image_assets


def _idempotency_file(idempotency_key: str) -> Path:
    return idempotency_dir / f"{idempotency_key}.json"


def _find_existing_output(idempotency_key: str) -> tuple[str, str] | None:
    marker_path = _idempotency_file(idempotency_key)
    if not marker_path.exists():
        return None

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    event_id = str(marker.get("event_id", "")).strip()
    if not event_id:
        return None

    output_path = outbox_dir / f"{event_id}.json"
    if not output_path.exists():
        return None

    return event_id, str(output_path)


def _mark_processed(idempotency_key: str, event_id: str) -> None:
    marker_path = _idempotency_file(idempotency_key)
    marker_path.write_text(json.dumps({"event_id": event_id}, indent=2), encoding="utf-8")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if not env_report.missing_keys else "degraded",
        "strict_mode": env_report.strict_mode,
        "missing_env_keys_count": len(env_report.missing_keys),
    }


@app.get("/health/env")
def health_env() -> dict[str, Any]:
    return {
        "strict_mode": env_report.strict_mode,
        "missing_env_keys": env_report.missing_keys,
    }


@app.post("/outbox/deliver")
def deliver_outbox() -> dict[str, Any]:
    results = deliver_pending_outbox(delivery, outbox_dir)
    return {
        "status": "ok",
        "results": results,
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
        store.add_images(message_chat_id, images)
        return {
            "status": "buffered",
            "chat_id": message_chat_id,
            "images_buffered": len(images),
        }

    audio_event = parse_audio_message(message)
    if not audio_event:
        return {"status": "ignored", "reason": "message has no image/audio"}

    buffered_images = store.pop_images(audio_event.chat_id)
    idempotency_key = build_idempotency_key(audio_event)

    existing = _find_existing_output(idempotency_key)
    if existing:
        event_id, output_path = existing
        return {
            "status": "duplicate",
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "output_path": output_path,
        }

    if telegram_client is None:
        raise HTTPException(status_code=500, detail="Telegram token not configured")

    try:
        downloaded_audio = telegram_client.download_file(audio_event.file_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to download Telegram audio: {exc}") from exc

    payload = pipeline.process(
        audio_event=audio_event,
        images=buffered_images,
        idempotency_key=idempotency_key,
        audio_bytes=downloaded_audio.content,
        audio_mime_type=downloaded_audio.mime_type,
    )

    payload["assets"]["images"] = _download_and_store_images(
        chat_id=audio_event.chat_id,
        event_id=payload["event_id"],
        images=buffered_images,
    )

    payload = normalize_payload(payload)
    validate_payload(payload)

    output_path = _persist_output(payload)
    _mark_processed(idempotency_key, payload["event_id"])

    if os.getenv("DELIVERY_AUTORUN", "false").lower() == "true":
        deliver_pending_outbox(delivery, outbox_dir)

    return {
        "status": "processed",
        "event_id": payload["event_id"],
        "idempotency_key": payload["idempotency_key"],
        "output_path": output_path,
    }
