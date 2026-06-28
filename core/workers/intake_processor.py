"""Short-lived worker that converts staged Telegram intake events into neutral payloads."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.ai.pipeline import AIPipeline
from core.config.env_validation import RUNTIME_ROLE_INTAKE_PROCESSOR, validate_env_for_runtime
from core.output.schema import normalize_payload, validate_payload
from core.telegram.client import TelegramClient
from core.telegram.webhook import AudioEvent, BufferedImage


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _load_pipeline() -> AIPipeline:
    prompt_base_dir = Path(__file__).resolve().parents[1] / "ai" / "prompts"
    ai_config_base_dir = Path(__file__).resolve().parents[1] / "ai"

    return AIPipeline(
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


def _parse_audio_event(event_payload: dict[str, Any]) -> AudioEvent:
    source = event_payload.get("source", {})
    media = event_payload.get("media", {})
    audio = media.get("audio", {})

    return AudioEvent(
        chat_id=int(source["chat_id"]),
        message_id=int(source["message_id"]),
        file_id=str(audio["file_id"]),
        file_unique_id=str(audio.get("file_unique_id")) if audio.get("file_unique_id") else None,
    )


def _parse_images(event_payload: dict[str, Any]) -> list[BufferedImage]:
    media = event_payload.get("media", {})
    images = media.get("images", [])

    parsed: list[BufferedImage] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        file_id = str(image.get("file_id", "")).strip()
        if not file_id:
            continue
        parsed.append(
            BufferedImage(
                message_id=int(image.get("message_id", 0)),
                file_id=file_id,
                file_unique_id=str(image.get("file_unique_id")) if image.get("file_unique_id") else None,
                width=int(image["width"]) if image.get("width") is not None else None,
                height=int(image["height"]) if image.get("height") is not None else None,
            )
        )
    return parsed


def _download_and_store_images(
    telegram: TelegramClient,
    images: list[BufferedImage],
    assets_dir: Path,
    chat_id: int,
    event_id: str,
) -> list[dict[str, Any]]:
    event_assets_dir = assets_dir / str(chat_id) / event_id
    event_assets_dir.mkdir(parents=True, exist_ok=True)

    image_assets: list[dict[str, Any]] = []
    for index, image in enumerate(images):
        downloaded = telegram.download_file(image.file_id)
        suffix = Path(downloaded.file_path).suffix or ".jpg"
        file_path = event_assets_dir / f"image-{index + 1}{suffix}"
        file_path.write_bytes(downloaded.content)
        image_assets.append(
            {
                "asset_ref": str(file_path.resolve()),
                "caption": "",
                "alt": "",
                "width": image.width,
                "height": image.height,
            }
        )

    return image_assets


def _load_marker(marker_path: Path) -> dict[str, Any]:
    if not marker_path.exists():
        return {}
    try:
        return _read_json(marker_path)
    except Exception:
        return {}


def run_once(max_events: int | None = None) -> dict[str, Any]:
    """Process staged intake events once and exit."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    validate_env_for_runtime(runtime_role=RUNTIME_ROLE_INTAKE_PROCESSOR)

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN not set")

    outbox_dir = Path(os.getenv("ROADTOCORE_OUTBOX_DIR", "./outbox"))
    outbox_dir.mkdir(parents=True, exist_ok=True)

    assets_dir = Path(os.getenv("ROADTOCORE_ASSETS_DIR", str(outbox_dir / "assets")))
    assets_dir.mkdir(parents=True, exist_ok=True)

    intake_dir = outbox_dir / "intake"
    events_dir = intake_dir / "events"
    processed_dir = intake_dir / "processed"
    failed_dir = intake_dir / "failed"
    marker_dir = intake_dir / ".idempotency"

    events_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)
    marker_dir.mkdir(parents=True, exist_ok=True)

    event_paths = sorted(events_dir.glob("*.json"))
    if max_events is not None and max_events > 0:
        event_paths = event_paths[:max_events]

    if not event_paths:
        return {
            "status": "ok",
            "events_found": 0,
            "processed": 0,
            "failed": 0,
            "skipped": 0,
        }

    telegram = TelegramClient(token)
    pipeline = _load_pipeline()

    processed_count = 0
    failed_count = 0
    skipped_count = 0

    for event_path in event_paths:
        try:
            event_payload = _read_json(event_path)
            idempotency_key = str(event_payload["idempotency_key"])
            intake_id = str(event_payload["intake_id"])
            marker_path = marker_dir / f"{idempotency_key}.json"
            marker = _load_marker(marker_path)

            if marker.get("status") == "payload_ready":
                skipped_count += 1
                processed_target = processed_dir / event_path.name
                event_path.replace(processed_target)
                continue

            audio_event = _parse_audio_event(event_payload)
            images = _parse_images(event_payload)

            downloaded_audio = telegram.download_file(audio_event.file_id)
            payload = pipeline.process(
                audio_event=audio_event,
                images=images,
                idempotency_key=idempotency_key,
                audio_bytes=downloaded_audio.content,
                audio_mime_type=downloaded_audio.mime_type,
            )

            event_id = str(payload["event_id"])
            image_assets = _download_and_store_images(
                telegram=telegram,
                images=images,
                assets_dir=assets_dir,
                chat_id=audio_event.chat_id,
                event_id=event_id,
            )
            payload["assets"]["images"] = image_assets

            payload = normalize_payload(payload)
            validate_payload(payload)

            payload_path = outbox_dir / f"{event_id}.json"
            _write_json_atomic(payload_path, payload)

            processed_target = processed_dir / event_path.name
            event_path.replace(processed_target)

            marker.update(
                {
                    "status": "payload_ready",
                    "intake_id": intake_id,
                    "event_path": str(processed_target),
                    "payload_event_id": event_id,
                    "payload_path": str(payload_path),
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            _write_json_atomic(marker_path, marker)
            processed_count += 1
        except Exception as exc:
            failed_target = failed_dir / event_path.name
            if event_path.exists():
                event_path.replace(failed_target)

            try:
                event_payload = _read_json(failed_target)
                idempotency_key = str(event_payload.get("idempotency_key", "")).strip()
            except Exception:
                idempotency_key = ""

            if idempotency_key:
                marker_path = marker_dir / f"{idempotency_key}.json"
                marker = _load_marker(marker_path)
                marker.update(
                    {
                        "status": "processing_failed",
                        "error": str(exc),
                        "event_path": str(failed_target),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                _write_json_atomic(marker_path, marker)

            error_path = failed_target.with_suffix(failed_target.suffix + ".error.txt")
            error_path.write_text(str(exc), encoding="utf-8")
            failed_count += 1

    return {
        "status": "ok",
        "events_found": len(event_paths),
        "processed": processed_count,
        "failed": failed_count,
        "skipped": skipped_count,
    }


def main() -> None:
    result = run_once()
    print(
        "[INTAKE] "
        f"events={result['events_found']} "
        f"processed={result['processed']} "
        f"failed={result['failed']} "
        f"skipped={result['skipped']}"
    )


if __name__ == "__main__":
    main()
