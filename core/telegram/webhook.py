from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any


@dataclass
class BufferedImage:
    message_id: int
    file_id: str
    file_unique_id: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class AudioEvent:
    chat_id: int
    message_id: int
    file_id: str
    file_unique_id: str | None = None


class TelegramBufferStore:
    """Persistent per-chat image buffer backed by filesystem JSON files."""

    def __init__(self, buffer_dir: Path | None = None) -> None:
        if buffer_dir is None:
            outbox_dir = Path(os.getenv("ROADTOCORE_OUTBOX_DIR", "./outbox"))
            buffer_dir = outbox_dir / "intake" / "buffer"
        self._buffer_dir = Path(buffer_dir)
        self._buffer_dir.mkdir(parents=True, exist_ok=True)

    def _chat_buffer_file(self, chat_id: int) -> Path:
        return self._buffer_dir / f"{chat_id}.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    @staticmethod
    def _decode_images(payload: dict[str, Any]) -> list[BufferedImage]:
        decoded: list[BufferedImage] = []
        for image in payload.get("images", []):
            if not isinstance(image, dict):
                continue
            file_id = str(image.get("file_id", "")).strip()
            if not file_id:
                continue
            try:
                message_id = int(image.get("message_id", 0))
            except Exception:
                message_id = 0

            decoded.append(
                BufferedImage(
                    message_id=message_id,
                    file_id=file_id,
                    file_unique_id=str(image.get("file_unique_id")) if image.get("file_unique_id") else None,
                    width=int(image["width"]) if image.get("width") is not None else None,
                    height=int(image["height"]) if image.get("height") is not None else None,
                )
            )
        return decoded

    def _read_chat_images(self, chat_id: int) -> list[BufferedImage]:
        chat_file = self._chat_buffer_file(chat_id)
        if not chat_file.exists():
            return []

        try:
            payload = json.loads(chat_file.read_text(encoding="utf-8"))
        except Exception:
            return []

        if not isinstance(payload, dict):
            return []
        return self._decode_images(payload)

    def add_images(self, chat_id: int, images: list[BufferedImage]) -> None:
        existing_images = self._read_chat_images(chat_id)
        all_images = [*existing_images, *images]
        payload = {
            "chat_id": chat_id,
            "images": [
                {
                    "message_id": image.message_id,
                    "file_id": image.file_id,
                    "file_unique_id": image.file_unique_id,
                    "width": image.width,
                    "height": image.height,
                }
                for image in all_images
            ],
        }
        self._write_json_atomic(self._chat_buffer_file(chat_id), payload)

    def peek_images(self, chat_id: int) -> list[BufferedImage]:
        return self._read_chat_images(chat_id)

    def clear_images(self, chat_id: int) -> None:
        chat_file = self._chat_buffer_file(chat_id)
        if chat_file.exists():
            chat_file.unlink()

    def pop_images(self, chat_id: int) -> list[BufferedImage]:
        images = self.peek_images(chat_id)
        self.clear_images(chat_id)
        return images


def extract_message(update: dict[str, Any]) -> dict[str, Any] | None:
    if "message" in update and isinstance(update["message"], dict):
        return update["message"]
    if "edited_message" in update and isinstance(update["edited_message"], dict):
        return update["edited_message"]
    return None


def parse_photo_message(message: dict[str, Any]) -> tuple[int, list[BufferedImage]]:
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])

    photos = message.get("photo")
    if not isinstance(photos, list) or not photos:
        return chat_id, []

    largest = photos[-1]
    image = BufferedImage(
        message_id=message_id,
        file_id=str(largest.get("file_id", "")),
        file_unique_id=str(largest.get("file_unique_id")) if largest.get("file_unique_id") else None,
        width=int(largest["width"]) if largest.get("width") else None,
        height=int(largest["height"]) if largest.get("height") else None,
    )

    if not image.file_id:
        return chat_id, []

    return chat_id, [image]


def parse_audio_message(message: dict[str, Any]) -> AudioEvent | None:
    audio = message.get("audio") or message.get("voice")
    if not isinstance(audio, dict):
        # Telegram can send audio as a generic document depending on client UX.
        document = message.get("document")
        if isinstance(document, dict):
            mime_type = str(document.get("mime_type", "")).lower()
            file_name = str(document.get("file_name", "")).lower()
            looks_like_audio_file = file_name.endswith((".mp3", ".m4a", ".ogg", ".wav", ".aac", ".flac"))
            if mime_type.startswith("audio/") or looks_like_audio_file:
                audio = document

    if not isinstance(audio, dict):
        return None

    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    file_id = str(audio.get("file_id", ""))

    if not file_id:
        return None

    file_unique_id = str(audio.get("file_unique_id")) if audio.get("file_unique_id") else None
    return AudioEvent(
        chat_id=chat_id,
        message_id=message_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
    )


def build_idempotency_key(audio_event: AudioEvent) -> str:
    raw = f"telegram:{audio_event.chat_id}:{audio_event.message_id}:{audio_event.file_unique_id or audio_event.file_id}"
    return sha256(raw.encode("utf-8")).hexdigest()


def parse_allowed_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()

    result: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            result.add(int(token))
        except ValueError:
            continue
    return result
