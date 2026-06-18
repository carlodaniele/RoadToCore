from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
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


@dataclass
class ChatBuffer:
    images: list[BufferedImage] = field(default_factory=list)


class TelegramBufferStore:
    """In-memory buffer keyed by chat_id.

    This is enough for local development. A shared store (Redis/Postgres)
    can replace it in production.
    """

    def __init__(self) -> None:
        self._by_chat: dict[int, ChatBuffer] = {}

    def add_images(self, chat_id: int, images: list[BufferedImage]) -> None:
        if chat_id not in self._by_chat:
            self._by_chat[chat_id] = ChatBuffer()
        self._by_chat[chat_id].images.extend(images)

    def pop_images(self, chat_id: int) -> list[BufferedImage]:
        if chat_id not in self._by_chat:
            return []
        images = self._by_chat[chat_id].images
        self._by_chat[chat_id] = ChatBuffer()
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
