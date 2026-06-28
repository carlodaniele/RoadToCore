from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class DownloadedTelegramFile:
    file_id: str
    file_path: str
    content: bytes
    mime_type: str


class TelegramClient:
    def __init__(self, bot_token: str, timeout: float = 60.0) -> None:
        if not bot_token.strip():
            raise ValueError("bot_token is required")

        self.bot_token = bot_token.strip()
        self.timeout = timeout
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.file_base = f"https://api.telegram.org/file/bot{self.bot_token}"

    def download_file(self, file_id: str) -> DownloadedTelegramFile:
        with httpx.Client(timeout=self.timeout) as client:
            file_meta_response = client.get(f"{self.api_base}/getFile", params={"file_id": file_id})
            file_meta_response.raise_for_status()
            payload = file_meta_response.json()

            if not payload.get("ok"):
                raise RuntimeError(f"Telegram getFile failed: {payload}")

            file_path = str(payload["result"]["file_path"])
            file_response = client.get(f"{self.file_base}/{file_path}")
            file_response.raise_for_status()

            mime_type = file_response.headers.get("content-type", "application/octet-stream")
            return DownloadedTelegramFile(
                file_id=file_id,
                file_path=file_path,
                content=file_response.content,
                mime_type=mime_type,
            )

    def get_updates(
        self,
        *,
        limit: int = 100,
        timeout_seconds: int = 0,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout_seconds,
            "limit": limit,
        }
        if offset is not None:
            params["offset"] = offset

        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.api_base}/getUpdates", params=params)
            response.raise_for_status()
            payload = response.json()

        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        updates = payload.get("result", [])
        return updates if isinstance(updates, list) else []

    def acknowledge_updates(self, max_update_id: int) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.api_base}/getUpdates",
                params={"offset": max_update_id + 1, "limit": 1, "timeout": 0},
            )
            response.raise_for_status()

    def delete_webhook(self, drop_pending_updates: bool = False) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.api_base}/deleteWebhook",
                data={"drop_pending_updates": "true" if drop_pending_updates else "false"},
            )
            response.raise_for_status()
            payload = response.json()

        if not payload.get("ok"):
            raise RuntimeError(f"Telegram deleteWebhook failed: {payload}")
