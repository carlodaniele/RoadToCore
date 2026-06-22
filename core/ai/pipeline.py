from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from core.telegram.webhook import AudioEvent, BufferedImage

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover - optional dependency at runtime.
    genai = None
    genai_types = None


class AIPipeline:
    """Two-step AI pipeline.

    Step 1: transcribe audio.
    Step 2: transform transcript to structured content.
    """

    def __init__(
        self,
        provider: str = "google",
        default_language: str = "English",
        google_api_key: str = "",
        transcription_model: str = "gemini-2.0-flash",
        generation_model: str = "gemini-2.0-flash",
        system_prompt: str = "",
        generation_prompt_template: str = "",
        ai_request_config: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.default_language = default_language
        self.google_api_key = google_api_key.strip()
        self.transcription_model = transcription_model
        self.generation_model = generation_model
        self.system_prompt = system_prompt.strip()
        self.generation_prompt_template = generation_prompt_template.strip()
        self.ai_request_config = ai_request_config if isinstance(ai_request_config, dict) else {}

        self._google_client = None
        if self.provider == "google" and self.google_api_key and genai is not None:
            self._google_client = genai.Client(api_key=self.google_api_key)

    @staticmethod
    def load_prompt_file(path: str) -> str:
        """Load a prompt from disk, returning empty string if unavailable."""
        if not path.strip():
            return ""

        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    @staticmethod
    def load_request_config_file(path: str) -> dict[str, Any]:
        """Load AI request config JSON from disk, returning empty dict on error."""
        if not path.strip():
            return {}

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return {}

        return data if isinstance(data, dict) else {}

    def _google_request_config(self, section: str) -> dict[str, Any]:
        """Return request config section for Google calls (transcription/generation)."""
        section_config = self.ai_request_config.get(section, {})
        if not isinstance(section_config, dict):
            return {}

        # Keep custom editorial keys out of provider request config.
        sanitized = dict(section_config)
        sanitized.pop("target_words", None)
        sanitized.pop("min_words", None)
        sanitized.pop("max_words", None)
        return sanitized

    def _build_length_instructions(self) -> str:
        """Build word-length instructions from generation config."""
        generation_cfg = self.ai_request_config.get("generation", {})
        if not isinstance(generation_cfg, dict):
            return ""

        target_words = generation_cfg.get("target_words")
        min_words = generation_cfg.get("min_words")
        max_words = generation_cfg.get("max_words")

        if isinstance(target_words, int) and target_words > 0:
            return f"Target total length: about {target_words} words."

        has_min = isinstance(min_words, int) and min_words > 0
        has_max = isinstance(max_words, int) and max_words > 0

        if has_min and has_max and min_words <= max_words:
            return f"Target total length: between {min_words} and {max_words} words."
        if has_min:
            return f"Target total length: at least {min_words} words."
        if has_max:
            return f"Target total length: up to {max_words} words."

        return ""

    @staticmethod
    def _extract_json_from_text(text: str) -> dict[str, Any] | None:
        content = text.strip()

        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            parsed = json.loads(content)
        except Exception:
            # Some providers wrap JSON with prose. Try decoding the first
            # top-level JSON object found in the text.
            decoder = json.JSONDecoder()
            for idx, char in enumerate(content):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(content[idx:])
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue
            return None

        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _extract_token_usage(response: Any) -> dict[str, int]:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return {"input": 0, "output": 0}

        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        thinking_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)

        result = {
            "input": input_tokens,
            "output": output_tokens,
        }
        if thinking_tokens > 0:
            result["thinking"] = thinking_tokens
        return result

    @staticmethod
    def _normalize_audio_mime_type(audio_bytes: bytes, mime_type: str | None) -> str:
        normalized = (mime_type or "").split(";", 1)[0].strip().lower()
        if normalized and normalized != "application/octet-stream":
            return normalized

        # Telegram file downloads often return octet-stream; infer from magic bytes.
        if audio_bytes.startswith(b"OggS"):
            return "audio/ogg"
        if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            return "audio/wav"
        if audio_bytes.startswith(b"ID3"):
            return "audio/mpeg"
        if len(audio_bytes) >= 8 and audio_bytes[4:8] == b"ftyp":
            return "audio/mp4"

        return "audio/mpeg"

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> tuple[str, dict[str, Any]]:
        if not audio_bytes:
            return "", {"provider": self.provider, "model": "none", "token_usage": {"input": 0, "output": 0}}

        resolved_mime_type = self._normalize_audio_mime_type(audio_bytes, mime_type)
        print(f"    [TRANSCRIPTION] Starting transcription ({len(audio_bytes)} bytes, {resolved_mime_type})...")
        start_time = perf_counter()

        if self._google_client is not None and genai_types is not None:
            prompt = (
                f"Transcribe this audio accurately in {self.default_language}. "
                "Return plain text only."
            )

            transcription_config = self._google_request_config("transcription")
            request_kwargs: dict[str, Any] = {}
            if transcription_config:
                request_kwargs["config"] = transcription_config

            response = self._google_client.models.generate_content(
                model=self.transcription_model,
                contents=[
                    prompt,
                    genai_types.Part.from_bytes(data=audio_bytes, mime_type=resolved_mime_type),
                ],
                **request_kwargs,
            )

            transcript = (getattr(response, "text", "") or "").strip()
            elapsed_ms = int((perf_counter() - start_time) * 1000)
            token_usage = self._extract_token_usage(response)
            print(f"    [TRANSCRIPTION] Complete ({elapsed_ms}ms, {len(transcript)} chars, tokens in={token_usage.get('input', 0)} out={token_usage.get('output', 0)})")
            return transcript, {
                "provider": "google",
                "model": self.transcription_model,
                "token_usage": token_usage,
            }

        print(f"    [TRANSCRIPTION] No AI provider; using fallback.")
        fallback = (
            "No AI provider configured. This is a fallback transcript placeholder "
            "generated by RoadToCore."
        )
        return fallback, {"provider": self.provider, "model": "fallback", "token_usage": {"input": 0, "output": 0}}

    def generate_structured_content(
        self,
        transcript: str,
        images: list[BufferedImage],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        image_count = len(images)
        print(f"    [GENERATION] Starting content generation ({len(transcript)} chars, {image_count} image(s))...")
        start_time = perf_counter()

        if self._google_client is not None:
            schema_hint = {
                "title": "string",
                "summary": "string",
                "transcript_summary": "string",
                "sections": [
                    {
                        "heading": "string",
                        "level": 2,
                        "paragraphs": ["string"],
                        "bullet_points": ["string"],
                    }
                ],
            }

            schema_hint_json = json.dumps(schema_hint)

            # If a template is configured, use placeholder substitution.
            # Supported placeholders:
            # - {{SYSTEM_PROMPT}}
            # - {{LANGUAGE}}
            # - {{SCHEMA_HINT_JSON}}
            # - {{TRANSCRIPT}}
            if self.generation_prompt_template:
                prompt = self.generation_prompt_template
                prompt = prompt.replace("{{SYSTEM_PROMPT}}", self.system_prompt)
                prompt = prompt.replace("{{LANGUAGE}}", self.default_language)
                prompt = prompt.replace("{{SCHEMA_HINT_JSON}}", schema_hint_json)
                prompt = prompt.replace("{{LENGTH_INSTRUCTIONS}}", self._build_length_instructions())
                prompt = prompt.replace("{{TRANSCRIPT}}", transcript)
            else:
                length_instructions = self._build_length_instructions()
                prompt = (
                    f"You are an editorial assistant. Create concise blog content in {self.default_language}.\n"
                    "Return ONLY valid JSON matching this shape: "
                    f"{schema_hint_json}\n"
                    "Rules: heading level must be 2 or 3, paragraphs concise, bullet_points optional.\n"
                    f"{length_instructions}\n"
                    f"Transcript:\n{transcript}"
                )

            generation_config = self._google_request_config("generation")
            request_kwargs: dict[str, Any] = {}
            if generation_config:
                request_kwargs["config"] = generation_config
            else:
                # Force JSON-only output shape when no explicit config is provided.
                request_kwargs["config"] = {"response_mime_type": "application/json"}

            config_obj = request_kwargs.get("config")
            if isinstance(config_obj, dict) and "response_mime_type" not in config_obj:
                config_obj["response_mime_type"] = "application/json"

            response = self._google_client.models.generate_content(
                model=self.generation_model,
                contents=[prompt],
                **request_kwargs,
            )
            raw_json = (getattr(response, "text", "") or "").strip()
            parsed = self._extract_json_from_text(raw_json)
            token_usage = self._extract_token_usage(response)
            if parsed:
                parsed.setdefault("transcript_full", transcript)
                elapsed_ms = int((perf_counter() - start_time) * 1000)
                print(f"    [GENERATION] Complete ({elapsed_ms}ms, tokens in={token_usage.get('input', 0)} out={token_usage.get('output', 0)})")
                return parsed, {
                    "provider": "google",
                    "model": self.generation_model,
                    "token_usage": token_usage,
                }

            # Retry once with an even stricter instruction if the first
            # candidate is not valid JSON.
            print(f"    [GENERATION] First attempt returned invalid JSON; retrying...")
            retry_prompt = (
                "Return ONLY a valid JSON object. "
                "No markdown, no code fences, no commentary.\n\n"
                f"Schema:\n{schema_hint_json}\n\n"
                f"Transcript:\n{transcript}"
            )
            retry_response = self._google_client.models.generate_content(
                model=self.generation_model,
                contents=[retry_prompt],
                **request_kwargs,
            )
            retry_raw_json = (getattr(retry_response, "text", "") or "").strip()
            retry_parsed = self._extract_json_from_text(retry_raw_json)
            retry_token_usage = self._extract_token_usage(retry_response)
            if retry_parsed:
                retry_parsed.setdefault("transcript_full", transcript)
                elapsed_ms = int((perf_counter() - start_time) * 1000)
                print(f"    [GENERATION] Retry successful ({elapsed_ms}ms total, tokens in={retry_token_usage.get('input', 0)} out={retry_token_usage.get('output', 0)})")
                return retry_parsed, {
                    "provider": "google",
                    "model": self.generation_model,
                    "token_usage": retry_token_usage,
                }

        print(f"    [GENERATION] Using fallback placeholder.")
        return {
            "title": "RoadToCore Generated Draft",
            "summary": "Automatically generated draft from an incoming Telegram audio message.",
            "transcript_summary": transcript[:500].strip() or "No transcript summary available.",
            "transcript_full": transcript,
            "sections": [
                {
                    "heading": "Overview",
                    "level": 2,
                    "paragraphs": [
                        "This draft was generated from a Telegram audio message.",
                        f"The event included {image_count} related image(s).",
                    ],
                    "bullet_points": [],
                },
                {
                    "heading": "Next Actions",
                    "level": 2,
                    "paragraphs": [
                        "Replace this section with model-generated editorial content.",
                    ],
                    "bullet_points": [
                        "Wire Telegram file download",
                        "Call provider transcription API",
                        "Call provider structured generation API",
                    ],
                },
            ],
        }, {"provider": self.provider, "model": "fallback", "token_usage": {"input": 0, "output": 0}}

    def process(
        self,
        audio_event: AudioEvent,
        images: list[BufferedImage],
        idempotency_key: str,
        audio_bytes: bytes,
        audio_mime_type: str,
    ) -> dict[str, Any]:
        started = perf_counter()
        transcript, transcription_meta = self.transcribe_audio(audio_bytes, audio_mime_type)
        content, generation_meta = self.generate_structured_content(transcript, images)
        latency_ms = int((perf_counter() - started) * 1000)

        combined_token_usage = {
            "input": int(transcription_meta["token_usage"].get("input", 0)) + int(generation_meta["token_usage"].get("input", 0)),
            "output": int(transcription_meta["token_usage"].get("output", 0)) + int(generation_meta["token_usage"].get("output", 0)),
        }
        thinking_tokens = int(transcription_meta["token_usage"].get("thinking", 0)) + int(generation_meta["token_usage"].get("thinking", 0))
        if thinking_tokens > 0:
            combined_token_usage["thinking"] = thinking_tokens

        return {
            "schema_version": "1.1.0",
            "event_id": str(uuid4()),
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "platform": "telegram",
                "chat_id": audio_event.chat_id,
                "audio_message_id": audio_event.message_id,
                "audio_file_id": audio_event.file_id,
                "audio_file_unique_id": audio_event.file_unique_id,
                "image_message_ids": [image.message_id for image in images],
            },
            "content": content,
            "assets": {
                "images": [
                    {
                        "asset_ref": f"telegram://{image.file_id}",
                        "caption": "",
                        "alt": "",
                        "width": image.width,
                        "height": image.height,
                    }
                    for image in images
                ],
            },
            "meta": {
                "language": self.default_language,
                "tags": [],
                "date": datetime.now(timezone.utc).isoformat(),
            },
            "targets": {
                "wordpress": {
                    "post_status": "draft",
                },
                "astro": {
                    "draft": True,
                },
            },
            "ai_meta": {
                "provider": self.provider,
                "model": f"transcribe:{transcription_meta.get('model', 'n/a')}|generate:{generation_meta.get('model', 'n/a')}",
                "latency_ms": latency_ms,
                "token_usage": combined_token_usage,
            },
        }
