"""Short-lived Telegram polling worker that stages intake events only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.config.env_validation import RUNTIME_ROLE_POLLING, validate_env_for_runtime
from core.telegram.client import TelegramClient
from core.telegram.events import TelegramIntakeEventStore
from core.telegram.webhook import (
    TelegramBufferStore,
    build_idempotency_key,
    extract_message,
    parse_allowed_chat_ids,
    parse_audio_message,
    parse_photo_message,
)


def run_once() -> dict[str, Any]:
    """Fetch, stage, and acknowledge updates in a single short-lived run."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    validate_env_for_runtime(runtime_role=RUNTIME_ROLE_POLLING)

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN not set")

    outbox_dir = Path(os.getenv("ROADTOCORE_OUTBOX_DIR", "./outbox"))
    outbox_dir.mkdir(parents=True, exist_ok=True)

    allowed_chat_ids = parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    auto_delete_webhook = os.getenv("TELEGRAM_POLLING_AUTO_DELETE_WEBHOOK", "true").strip().lower() == "true"

    telegram = TelegramClient(token)
    buffer_store = TelegramBufferStore(outbox_dir / "intake" / "buffer")
    intake_store = TelegramIntakeEventStore(outbox_dir)

    if auto_delete_webhook:
        telegram.delete_webhook(drop_pending_updates=False)

    updates = telegram.get_updates(limit=100, timeout_seconds=0)
    if not updates:
        return {
            "status": "ok",
            "updates_fetched": 0,
            "acknowledged_until": None,
            "images_buffered": 0,
            "audio_staged": 0,
            "duplicates": 0,
        }

    images_buffered = 0
    audio_staged = 0
    duplicates = 0
    last_safe_update_id: int | None = None

    for update in updates:
        update_id = int(update.get("update_id", 0))
        message = extract_message(update)
        if not message:
            last_safe_update_id = update_id
            continue

        chat_id = int(message["chat"]["id"])
        if allowed_chat_ids and chat_id not in allowed_chat_ids:
            last_safe_update_id = update_id
            continue

        message_chat_id, images = parse_photo_message(message)
        if images:
            buffer_store.add_images(message_chat_id, images)
            images_buffered += len(images)
            last_safe_update_id = update_id
            continue

        audio_event = parse_audio_message(message)
        if not audio_event:
            last_safe_update_id = update_id
            continue

        idempotency_key = build_idempotency_key(audio_event)
        buffered_images = buffer_store.peek_images(audio_event.chat_id)
        try:
            result = intake_store.stage_audio_intake(
                audio_event=audio_event,
                buffered_images=buffered_images,
                idempotency_key=idempotency_key,
                telegram_update_id=update_id,
            )
        except Exception:
            # Do not acknowledge past failures: failed updates should be retried.
            break

        buffer_store.clear_images(audio_event.chat_id)

        if result.status == "duplicate":
            duplicates += 1
        else:
            audio_staged += 1

        last_safe_update_id = update_id

    if last_safe_update_id is not None:
        telegram.acknowledge_updates(last_safe_update_id)

    return {
        "status": "ok",
        "updates_fetched": len(updates),
        "acknowledged_until": last_safe_update_id,
        "images_buffered": images_buffered,
        "audio_staged": audio_staged,
        "duplicates": duplicates,
    }


def main() -> None:
    result = run_once()
    print(
        "[POLLING] "
        f"fetched={result['updates_fetched']} "
        f"ack={result['acknowledged_until']} "
        f"buffered_images={result['images_buffered']} "
        f"audio_staged={result['audio_staged']} "
        f"duplicates={result['duplicates']}"
    )


if __name__ == "__main__":
    main()
