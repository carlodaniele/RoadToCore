from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any

import httpx


@dataclass
class WordPressConfig:
    enabled: bool
    endpoint: str
    username: str
    app_password: str
    timeout: float = 30.0
    assets_public_url: str = ""


@dataclass
class AstroConfig:
    enabled: bool
    node_bin: str
    adapter_dist_path: str
    content_dir: str
    public_dir: str
    assets_dir: str


@dataclass
class DeliveryConfig:
    retries: int
    retry_backoff_seconds: float
    wp: WordPressConfig
    astro: AstroConfig


def _build_gallery_blocks(images: list[dict[str, Any]]) -> str:
    """Build Gutenberg gallery blocks from image assets.
    
    Similar to nomad-pipeline's gallery construction logic.
    Supports GPS coordinates in image metadata.
    
    Args:
        images: List of image dicts with asset_ref, url, alt, caption, gps.
    
    Returns:
        HTML string with Gutenberg gallery/image blocks.
    """
    if not images:
        return ""
    
    gallery_html = ""
    
    if len(images) > 1:
        # Multi-image gallery
        gallery_html = '\n\n<!-- wp:gallery {"linkTo":"none"} -->\n'
        gallery_html += '<figure class="wp-block-gallery has-nested-images columns-default is-cropped">\n'
        
        for img in images:
            img_url = img.get("url", img.get("asset_ref", ""))
            img_alt = img.get("alt", "")
            img_caption = img.get("caption", "")
            
            # Add GPS metadata as comment if available
            gps_comment = ""
            if img.get("gps"):
                lat = img["gps"].get("latitude")
                lon = img["gps"].get("longitude")
                if lat is not None and lon is not None:
                    gps_comment = f" (📍 {lat:.4f}, {lon:.4f})"
            
            gallery_html += f'<!-- wp:image {{"lightbox":{{"enabled":true}},"linkDestination":"none"}} -->\n'
            gallery_html += f'<figure class="wp-block-image size-large">'
            gallery_html += f'<img src="{img_url}" alt="{img_alt}" />'
            if img_caption:
                gallery_html += f'<figcaption class="wp-element-caption">{img_caption}{gps_comment}</figcaption>'
            gallery_html += '</figure>\n'
            gallery_html += '<!-- /wp:image -->\n'
        
        gallery_html += '</figure>\n'
        gallery_html += '<!-- /wp:gallery -->\n'
    
    elif len(images) == 1:
        # Single image
        img = images[0]
        img_url = img.get("url", img.get("asset_ref", ""))
        img_alt = img.get("alt", "")
        img_caption = img.get("caption", "")
        
        gps_comment = ""
        if img.get("gps"):
            lat = img["gps"].get("latitude")
            lon = img["gps"].get("longitude")
            if lat is not None and lon is not None:
                gps_comment = f" (📍 {lat:.4f}, {lon:.4f})"
        
        gallery_html = '\n\n<!-- wp:image {"lightbox":{"enabled":true},"linkDestination":"none"} -->\n'
        gallery_html += '<figure class="wp-block-image size-large">'
        gallery_html += f'<img src="{img_url}" alt="{img_alt}" />'
        if img_caption:
            gallery_html += f'<figcaption class="wp-element-caption">{img_caption}{gps_comment}</figcaption>'
        gallery_html += '</figure>\n'
        gallery_html += '<!-- /wp:image -->\n'
    
    return gallery_html


class DeliveryDispatcher:
    def __init__(self, config: DeliveryConfig, outbox_dir: Path) -> None:
        self.config = config
        self.outbox_dir = outbox_dir
        self.delivered_dir = outbox_dir / ".delivered"
        self.failed_dir = outbox_dir / ".failed"
        self.delivered_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _attempt_with_retry(fn, retries: int, backoff_seconds: float) -> None:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                fn()
                return
            except Exception as exc:  # pragma: no cover - runtime network/process failures
                last_error = exc
                if attempt < retries:
                    sleep(backoff_seconds * (2 ** attempt))
        if last_error is not None:
            raise last_error

    def _dispatch_wordpress(self, payload: dict[str, Any]) -> None:
        if not self.config.wp.enabled:
            return
        if not self.config.wp.endpoint:
            raise RuntimeError("WordPress dispatch enabled but endpoint is empty")

        # Convert local asset paths to public HTTP URLs if configured
        if self.config.wp.assets_public_url:
            payload_str = json.dumps(payload)
            # Match patterns like /any/path/outbox/assets/chat_id/event_id/file
            # and convert to https://assets_url/assets/chat_id/event_id/file
            payload_str = re.sub(
                r'[^"]*outbox/assets/([^"]+)',
                lambda m: f"{self.config.wp.assets_public_url}/assets/{m.group(1)}",
                payload_str,
            )
            payload = json.loads(payload_str)

        # Build and append gallery blocks from image assets
        images = payload.get("assets", {}).get("images", [])
        if images:
            gallery_html = _build_gallery_blocks(images)
            if gallery_html:
                # Append gallery blocks to content body
                content = payload.get("content", {})
                body = content.get("body", "")
                # If body is structured sections, convert to flat HTML
                if isinstance(body, list):
                    body = ""
                content["body"] = (body + gallery_html).strip()
                payload["content"] = content

        with httpx.Client(timeout=self.config.wp.timeout) as client:
            response = client.post(
                self.config.wp.endpoint,
                json=payload,
                auth=(self.config.wp.username, self.config.wp.app_password),
                headers={"content-type": "application/json"},
            )
            response.raise_for_status()

    def _dispatch_astro(self, payload_path: Path) -> None:
        if not self.config.astro.enabled:
            return

        adapter_path = Path(self.config.astro.adapter_dist_path)
        if not adapter_path.exists():
            raise RuntimeError(f"Astro adapter binary not found: {adapter_path}")

        cmd = [
            self.config.astro.node_bin,
            str(adapter_path),
            "--input",
            str(payload_path),
            "--content-dir",
            self.config.astro.content_dir,
            "--public-dir",
            self.config.astro.public_dir,
            "--assets-dir",
            self.config.astro.assets_dir,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Astro adapter failed: {result.stderr.strip()}")

    def dispatch_payload(self, payload_path: Path) -> dict[str, Any]:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))

        def _run_wp() -> None:
            self._dispatch_wordpress(payload)

        def _run_astro() -> None:
            self._dispatch_astro(payload_path)

        self._attempt_with_retry(_run_wp, self.config.retries, self.config.retry_backoff_seconds)
        self._attempt_with_retry(_run_astro, self.config.retries, self.config.retry_backoff_seconds)

        destination = self.delivered_dir / payload_path.name
        payload_path.replace(destination)

        return {
            "status": "delivered",
            "event_id": payload.get("event_id"),
            "path": str(destination),
        }

    def mark_failed(self, payload_path: Path, reason: str) -> str:
        failed_path = self.failed_dir / payload_path.name
        payload_path.replace(failed_path)

        reason_path = failed_path.with_suffix(failed_path.suffix + ".error.txt")
        reason_path.write_text(reason, encoding="utf-8")
        return str(failed_path)
