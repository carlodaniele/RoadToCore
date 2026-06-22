"""Telegram polling worker.

Run once per invocation: fetches all pending Telegram updates, processes any
audio messages (with buffered images from the same batch), delivers to WordPress,
and acknowledges the updates so they are not returned again.

Intended to be called from a scheduled GitHub Actions workflow (or any scheduler).

Usage:
    python -m core.polling
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from core.ai.pipeline import AIPipeline
from core.delivery.dispatch import AstroConfig, DeliveryConfig, DeliveryDispatcher, WordPressConfig
from core.delivery.outbox_worker import deliver_pending_outbox
from core.output.exif import extract_exif_metadata, optimize_image, rotate_image_by_exif
from core.output.schema import normalize_payload, validate_payload
from core.telegram.client import TelegramClient
from core.telegram.webhook import (
    build_idempotency_key,
    extract_message,
    parse_allowed_chat_ids,
    parse_audio_message,
    parse_photo_message,
)


def _get_updates(token: str) -> list[dict[str, Any]]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = httpx.get(url, params={"timeout": 0, "limit": 100}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates failed: {data}")
    return data.get("result", [])


def _delete_webhook(token: str, drop_pending_updates: bool = False) -> None:
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    resp = httpx.post(
        url,
        data={"drop_pending_updates": "true" if drop_pending_updates else "false"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"deleteWebhook failed: {data}")


def _acknowledge_updates(token: str, max_update_id: int) -> None:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    httpx.get(url, params={"offset": max_update_id + 1, "limit": 1, "timeout": 0}, timeout=30)


def _download_images(
    telegram_client: TelegramClient,
    images: list[Any],
    assets_dir: Path,
    chat_id: int,
    event_id: str,
) -> list[dict[str, Any]]:
    event_assets_dir = assets_dir / str(chat_id) / event_id
    event_assets_dir.mkdir(parents=True, exist_ok=True)
    image_assets = []
    for index, image in enumerate(images):
        file_id = getattr(image, "file_id", "")
        if not file_id:
            continue
        try:
            print(f"    [IMAGES] Image {index + 1}: Downloading from Telegram...")
            downloaded = telegram_client.download_file(file_id)
            print(f"    [IMAGES] Image {index + 1}: Downloaded ({len(downloaded.content)} bytes)")
            
            # Extract EXIF metadata (GPS, orientation) before processing
            print(f"    [IMAGES] Image {index + 1}: Extracting EXIF metadata...")
            exif_meta = extract_exif_metadata(downloaded.content)
            has_gps = exif_meta.latitude is not None and exif_meta.longitude is not None
            gps_info = f"GPS({exif_meta.latitude:.4f}, {exif_meta.longitude:.4f})" if has_gps else "No GPS"
            print(f"    [IMAGES] Image {index + 1}: EXIF parsed - {gps_info}")
            
            # Rotate based on EXIF orientation + optimize
            print(f"    [IMAGES] Image {index + 1}: Rotating and optimizing...")
            rotated_bytes = rotate_image_by_exif(downloaded.content)
            optimized_bytes = optimize_image(rotated_bytes)
            print(f"    [IMAGES] Image {index + 1}: Optimized ({len(optimized_bytes)} bytes)")
            
            suffix = Path(downloaded.file_path).suffix or ".jpg"
            file_path = event_assets_dir / f"image-{index + 1}{suffix}"
            file_path.write_bytes(optimized_bytes)
            print(f"    [IMAGES] Image {index + 1}: Saved to {file_path}")
            
            asset_dict: dict[str, Any] = {
                "asset_ref": str(file_path.resolve()),
                "caption": "",
                "alt": "",
                "width": getattr(image, "width", None),
                "height": getattr(image, "height", None),
            }
            
            # Add GPS coordinates if available
            if has_gps:
                asset_dict["gps"] = {
                    "latitude": exif_meta.latitude,
                    "longitude": exif_meta.longitude,
                }
            
            image_assets.append(asset_dict)
        except Exception as exc:
            print(f"    [IMAGES] ERROR: Could not download image {file_id}: {exc}")
    return image_assets


def poll_and_process() -> None:
    print("[POLLING] Starting Telegram polling worker...")
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        print("[ERROR] TELEGRAM_TOKEN not set")
        sys.exit(1)

    allowed_chat_ids = parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))

    outbox_dir = Path(os.getenv("ROADTOCORE_OUTBOX_DIR", "/tmp/roadtocore_outbox"))
    outbox_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = Path(os.getenv("ROADTOCORE_ASSETS_DIR", str(outbox_dir / "assets")))
    assets_dir.mkdir(parents=True, exist_ok=True)
    idempotency_dir = outbox_dir / ".idempotency"
    idempotency_dir.mkdir(parents=True, exist_ok=True)

    prompt_base_dir = Path(__file__).parent / "ai" / "prompts"
    ai_config_base_dir = Path(__file__).parent / "ai"

    pipeline = AIPipeline(
        provider=os.getenv("AI_PROVIDER", "google"),
        default_language=os.getenv("AI_DEFAULT_LANGUAGE", "Italian"),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        transcription_model=os.getenv("GOOGLE_TRANSCRIPTION_MODEL", "gemini-2.5-flash"),
        generation_model=os.getenv("GOOGLE_GENERATION_MODEL", "gemini-2.5-flash"),
        system_prompt=AIPipeline.load_prompt_file(
            os.getenv("ROADTOCORE_SYSTEM_PROMPT_FILE", str(prompt_base_dir / "system.prompt.md"))
        ),
        generation_prompt_template=AIPipeline.load_prompt_file(
            os.getenv("ROADTOCORE_GENERATION_PROMPT_FILE", str(prompt_base_dir / "generation.prompt.md"))
        ),
        ai_request_config=AIPipeline.load_request_config_file(
            os.getenv("ROADTOCORE_AI_REQUEST_CONFIG_FILE", str(ai_config_base_dir / "request_config.json"))
        ),
    )

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
                assets_public_url=os.getenv("ROADTOCORE_ASSETS_PUBLIC_URL", "").strip(),
            ),
            astro=AstroConfig(
                enabled=False,
                node_bin="node",
                adapter_dist_path="",
                content_dir="",
                public_dir="",
                assets_dir="",
            ),
        ),
        outbox_dir=outbox_dir,
    )

    telegram_client = TelegramClient(token)

    # --- Ensure polling mode is active (Telegram returns 409 if webhook is set) ---
    auto_delete_webhook = os.getenv("TELEGRAM_POLLING_AUTO_DELETE_WEBHOOK", "true").strip().lower() == "true"
    if auto_delete_webhook:
        print("[POLLING] Ensuring polling mode (deleting webhook)...")
        _delete_webhook(token, drop_pending_updates=False)
        print("[POLLING] Webhook deleted, polling mode active.")

    # --- Fetch all pending updates ---
    print("[POLLING] Fetching Telegram updates...")
    updates = _get_updates(token)
    if not updates:
        print("[POLLING] No pending updates.")
        return

    print(f"[POLLING] Fetched {len(updates)} update(s).")

    # --- Group images and audio events by chat_id (preserving arrival order) ---
    images_by_chat: dict[int, list] = {}
    audio_events_by_chat: dict[int, list] = {}

    for update in updates:
        message = extract_message(update)
        if not message:
            continue

        chat_id = int(message["chat"]["id"])
        if allowed_chat_ids and chat_id not in allowed_chat_ids:
            continue

        _, images = parse_photo_message(message)
        if images:
            images_by_chat.setdefault(chat_id, []).extend(images)
            continue

        audio_event = parse_audio_message(message)
        if audio_event:
            audio_events_by_chat.setdefault(chat_id, []).append(audio_event)

    # --- Process each audio event ---
    processed_count = 0

    for chat_id, audio_events in audio_events_by_chat.items():
        buffered_images = images_by_chat.get(chat_id, [])

        for audio_event in audio_events:
            idempotency_key = build_idempotency_key(audio_event)
            idempotency_file = idempotency_dir / f"{idempotency_key}.json"

            if idempotency_file.exists():
                print(f"  [POLLING] Skipping duplicate: message_id={audio_event.message_id}")
                continue

            print(f"  [POLLING] Processing audio: chat={chat_id} message_id={audio_event.message_id}")

            print(f"  [TELEGRAM] Downloading audio file (file_id={audio_event.file_id}...)...")
            try:
                downloaded_audio = telegram_client.download_file(audio_event.file_id)
                print(f"  [TELEGRAM] Audio downloaded: {len(downloaded_audio.content)} bytes")
            except Exception as exc:
                print(f"  [ERROR] Could not download audio: {exc}")
                continue

            print(f"  [AI] Starting AI pipeline (transcription + generation)...")
            payload = pipeline.process(
                audio_event=audio_event,
                images=buffered_images,
                idempotency_key=idempotency_key,
                audio_bytes=downloaded_audio.content,
                audio_mime_type=downloaded_audio.mime_type,
            )
            print(f"  [AI] Pipeline complete.")

            event_id = payload["event_id"]

            # Download and store images into ephemeral assets dir
            if buffered_images:
                print(f"  [IMAGES] Processing {len(buffered_images)} image(s) from batch...")
                image_assets = _download_images(
                    telegram_client, buffered_images, assets_dir, chat_id, event_id
                )
                if image_assets:
                    payload["assets"]["images"] = image_assets
                    print(f"  [IMAGES] Processed {len(image_assets)} image(s) with EXIF/GPS extraction.")

            print(f"  [VALIDATE] Normalizing and validating payload...")
            payload = normalize_payload(payload)
            validate_payload(payload)
            print(f"  [VALIDATE] Payload valid.")

            outbox_file = outbox_dir / f"{event_id}.json"
            outbox_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            idempotency_file.write_text(json.dumps({"event_id": event_id}, indent=2), encoding="utf-8")

            generation_model = str(payload.get("ai_meta", {}).get("model", ""))
            if "generate:fallback" in generation_model:
                print(f"  [WARNING] AI generation returned fallback model; skipping delivery.")
                failed_path = delivery.mark_failed(
                    outbox_file,
                    "AI generation returned fallback placeholder content; payload not delivered.",
                )
                print(f"  [POLLING] failed: {failed_path}")
                continue

            print(f"  [POLLING] Saved: {event_id} — {payload['content'].get('title', '(no title)')}")
            processed_count += 1

    # --- Deliver all pending payloads ---
    if processed_count > 0:
        print(f"[DELIVERY] Delivering {processed_count} payload(s) to WordPress...")
        results = deliver_pending_outbox(delivery, outbox_dir)
        for r in results:
            print(f"  [DELIVERY] {r['status']}: {r['path']}")
    else:
        print(f"[POLLING] No new payloads to deliver.")

    # --- Acknowledge all updates so Telegram won't resend them ---
    max_update_id = max(u["update_id"] for u in updates)
    print(f"[POLLING] Acknowledging {len(updates)} update(s) up to {max_update_id}...")
    _acknowledge_updates(token, max_update_id)
    print(f"[POLLING] Acknowledged updates up to {max_update_id}.") 
    print(f"[POLLING] Workflow complete.")


if __name__ == "__main__":
    poll_and_process()
