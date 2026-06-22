# RoadToCore

RoadToCore is a CMS-agnostic content pipeline that receives Telegram messages, triggers on audio arrival, generates structured editorial payloads via AI, and distributes the result to target adapters (WordPress, Astro, and future CMS/frameworks).

## Architecture

- `core/`: Python webhook + orchestration + AI pipeline + schema validation.
- `shared/schema.json`: versioned contract (`1.1.x`) for all adapters.
- `adapters/wordpress/`: WordPress 7.0+ plugin (REST ingest + Abilities API + block mapping).
- `adapters/astro/`: Node/TypeScript adapter that writes Markdown files.

## Data Contract (V1.1)

The payload envelope includes:

- `schema_version` (format: `1.1.x`)
- `event_id` (UUID)
- `idempotency_key` (unique per event)
- `created_at` (ISO 8601 timestamp)
- `source` (Telegram platform info + chat/audio/image IDs)
- `content` (title, summary, transcript, structured sections)
- `assets.images` (array with `asset_ref`, `url`, `alt`, `caption`, and optional **`gps` object** with latitude/longitude)
- `meta` (language, tags, date)
- optional `targets` (WordPress/Astro specific settings)
- optional `ai_meta` (provider, model, latency, token usage)

**GPS Metadata**: Image assets now include optional `gps` field extracted from EXIF:

```json
"gps": {
  "latitude": 45.1234,
  "longitude": 7.5678
}
```

The authoritative schema is in `shared/schema.json`.

## Core Features Implemented

- GitHub Actions polling worker running every 5 minutes (cron-based).
- Telegram message polling via `getUpdates` API.
- Trigger only on audio (`audio`, `voice`, or `document` with audio MIME type).
- Image buffering by `chat_id` before audio arrives.
- **EXIF metadata extraction**: GPS coordinates and image orientation detection.
- **Image processing**: automatic rotation based on EXIF orientation + optimization (resize, compress JPEG).
- Telegram file download via Bot API.
- Telegram image download, EXIF processing, and local asset persistence.
- Two-step AI flow:
  1. audio transcription
  2. transcript -> structured content
- JSON schema validation and normalization.
- File-based idempotency replay protection.
- JSON outbox persistence for downstream adapters.
- Delivery layer with retries/backoff and `.delivered` / `.failed` folders.
- Optional direct adapter delivery (WordPress HTTP + Astro CLI).

## Core Setup

### 1) Configure environment

Copy and edit:

- `core/.env.example` -> `core/.env`

Important variables:

- `TELEGRAM_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `AI_PROVIDER` (`google`)
- `GOOGLE_API_KEY`
- `GOOGLE_TRANSCRIPTION_MODEL`
- `GOOGLE_GENERATION_MODEL`
- `ROADTOCORE_SYSTEM_PROMPT_FILE`
- `ROADTOCORE_GENERATION_PROMPT_FILE`
- `ROADTOCORE_AI_REQUEST_CONFIG_FILE`
- `ROADTOCORE_OUTBOX_DIR`
- `ROADTOCORE_ASSETS_DIR`
- `DELIVERY_AUTORUN`
- `DELIVERY_WORDPRESS_*`
- `DELIVERY_ASTRO_*`

### Prompt Customization (System + Generation)

RoadToCore supports external prompt files so you can tune behavior without code changes.

- Default system prompt: `core/ai/prompts/system.prompt.md`
- Default generation prompt: `core/ai/prompts/generation.prompt.md`

Environment variables:

- `ROADTOCORE_SYSTEM_PROMPT_FILE`
- `ROADTOCORE_GENERATION_PROMPT_FILE`
- `ROADTOCORE_AI_REQUEST_CONFIG_FILE`

The generation prompt template supports these placeholders:

- `{{SYSTEM_PROMPT}}`
- `{{LANGUAGE}}`
- `{{SCHEMA_HINT_JSON}}`
- `{{TRANSCRIPT}}`

If prompt files are missing or empty, the pipeline falls back to internal default prompts.

### AI Request Parameters Configuration

RoadToCore can also externalize request-level AI parameters (for example temperature) in JSON.

- Default file: `core/ai/request_config.json`
- Env var: `ROADTOCORE_AI_REQUEST_CONFIG_FILE`

Example:

```json
{
  "transcription": {
    "temperature": 0.1,
    "top_p": 0.95
  },
  "generation": {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "max_output_tokens": 1200,
    "target_words": 500
  }
}
```

Notes:

- `transcription` applies to the audio transcription request.
- `generation` applies to structured content generation.
- `target_words` controls approximate total output length (for example `500` or `5000`).
- You can use `min_words` and/or `max_words` as an alternative range-based constraint.
- If the file is missing/invalid, RoadToCore safely falls back to default provider behavior.

#### Practical examples

For an output around 500 words:

```json
{
  "generation": {
    "temperature": 0.7,
    "max_output_tokens": 1200,
    "target_words": 500
  }
}
```

For an output around 5000 words:

```json
{
  "generation": {
    "temperature": 0.7,
    "max_output_tokens": 9000,
    "target_words": 5000
  }
}
```

Important:

- Keep `max_output_tokens` high enough for long outputs, otherwise the model may truncate.
- `target_words`, `min_words`, and `max_words` are editorial controls used in the prompt; they are not forwarded to provider API config.

### 2) Install dependencies

```bash
cd core
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) Run API

```bash
cd ..
uvicorn core.main:app --reload --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl http://localhost:8080/health
```

Validate required environment variables (useful in GitHub Actions):

```bash
cd core
python -m core.check_github_env
```

If you want startup to fail when required keys are missing, set:

```bash
ENV_VALIDATION_STRICT=true
```

Deliver pending outbox payloads manually:

```bash
curl -X POST http://localhost:8080/outbox/deliver
```

## Image Processing (EXIF Metadata Extraction)

RoadToCore automatically processes images to extract metadata and optimize file size.

### Features

- **GPS coordinate extraction**: reads `GPSInfo` EXIF tags to capture latitude/longitude
- **Image rotation**: corrects orientation based on EXIF `Orientation` tag (common on phone photos)
- **Image optimization**: resizes to 1600px max width + compresses JPEG to quality 85

### EXIF Modules

- `core/output/exif.py`: provides extraction and optimization functions
- Functions: `extract_exif_metadata()`, `rotate_image_by_exif()`, `optimize_image()`

### Example: GPS in payload

When an image with GPS tags arrives:

```json
"assets": {
  "images": [
    {
      "asset_ref": "/tmp/image-1.jpg",
      "gps": {
        "latitude": 45.4642,
        "longitude": 9.1900
      },
      "url": "https://wordpress.example.com/wp-content/uploads/2026/06/image.jpg"
    }
  ]
}
```

The WordPress adapter automatically displays this as:

```
(45.4642, 9.1900)
```

in the image caption.

## Telegram Webhook Notes

The core expects Telegram updates JSON at:

- `POST /webhook/telegram`

If `TELEGRAM_WEBHOOK_SECRET` is set, the header `X-Telegram-Bot-Api-Secret-Token` must match.

Environment diagnostics endpoint:

- `GET /health/env`

## Provider-Agnostic Delivery with GitHub Actions

This repository now includes CI/CD workflows that keep you independent from a specific hosting provider.

- CI workflow: `.github/workflows/ci.yml`
- Release workflow: `.github/workflows/release-artifacts.yml`
- Container image: `ghcr.io/<owner>/roadtocore-core`

### What gets produced

- A Docker image for the core API is built and pushed to GitHub Container Registry (GHCR).
- A ZIP artifact for the WordPress adapter is generated and uploaded in workflow artifacts.

### Why this is hosting-independent

Any provider that supports Docker can run the same immutable image by pulling from GHCR.
You can deploy on VPS, managed container services, Kubernetes, or your own server without changing the build pipeline.

### Suggested production run command

```bash
docker run -d \
  --name roadtocore \
  -p 8080:8080 \
  --env-file /path/to/core.env \
  -v /path/to/outbox:/app/outbox \
  ghcr.io/<owner>/roadtocore-core:latest
```

### Required GitHub settings

- Ensure GitHub Actions is enabled for the repository.
- Ensure package permissions allow publishing to GHCR.
- Keep secrets in GitHub Secrets (for example provider keys used at runtime on your host, not in the image).

## WordPress Adapter

Path:

- `adapters/wordpress/`
- Latest version: **v0.2.0**

Implemented capabilities:

- REST endpoint: `POST /wp-json/roadtocore/v1/receive`
- Idempotent post create/update via `_roadtocore_idempotency_key`
- Content mapping to Gutenberg blocks (`heading`, `paragraph`, `list`)
- **Media upload**: uploads images directly to WordPress Media Library via `/wp/v2/media`
- **Gallery blocks**: automatically builds Gutenberg gallery blocks with lightbox support
- GPS coordinates: embeds location data in figure captions (if available in EXIF)
- Optional Ability registration: `roadtocore/publish-post`
- Optional AI-based image alt text enrichment when AI Client is available

### Image Processing in WordPress Adapter

When images arrive:

1. **Direct Media Library upload**: bypasses local asset URLs, ensures images are stored in WordPress
2. **Gallery block generation**: creates proper Gutenberg blocks (`wp:gallery`, `wp:image`)
3. **GPS display**: if EXIF GPS coordinates are present, they appear in the image caption as `(latitude, longitude)`
4. **Featured image**: automatically sets the first uploaded image as featured image

### Install

Copy the `adapters/wordpress` folder into your WordPress plugins directory and activate the plugin.

To get the latest version:

- Download `adapters/roadtocore-adapter-v0.2.0.zip` from the repo
- Upload via WordPress Admin → Plugins → Add New → Upload Plugin

## Astro Adapter

Path:

- `adapters/astro/`

Implemented capabilities:

- Reads payload JSON file
- Renders Markdown with frontmatter
- Writes output in collection folder
- Copies local image assets into Astro public directory

### Setup

```bash
cd adapters/astro
npm install
npm run build
```

### Run

```bash
node dist/index.js \
  --input /absolute/path/to/payload.json \
  --content-dir /absolute/path/to/astro/src/content \
  --public-dir /absolute/path/to/astro/public/images/roadtocore \
  --assets-dir /absolute/path/to/local/assets
```

## Debugging & Monitoring

The workflow is instrumented with **8 checkpoint phases** that emit structured logs, making it easy to identify where anomalies occur when the workflow fails.

### Workflow Phases & Log Markers

Each phase emits logs with a prefix to indicate which step is executing:

| Phase | Marker | What's Happening | Common Failure Points |
|-------|--------|------------------|----------------------|
| 1. Polling Setup | `[POLLING]` | Initialize worker, delete webhook, fetch Telegram updates | TELEGRAM_TOKEN missing/invalid |
| 2. Telegram Download | `[TELEGRAM]` | Download audio and image files from Telegram API | Network timeout, file not found |
| 3. Image Processing | `[IMAGES]` | Extract EXIF, rotate, optimize, and validate images | Corrupted image data, unsupported format |
| 4. AI Pipeline | `[AI]` + `[TRANSCRIPTION]` + `[GENERATION]` | Transcribe audio and generate structured content | API key invalid, quota exceeded, malformed response |
| 5. Validation | `[VALIDATE]` | Normalize and validate payload against schema | Missing required fields, invalid JSON |
| 6. Outbox | `[OUTBOX]` | Queue payload for delivery to destination adapters | Disk write errors |
| 7. Dispatch | `[DISPATCH]` + `[WORDPRESS]` | Upload images to WordPress Media Library, build gallery blocks, send payload | WP auth failed, endpoint unreachable, malformed blocks |
| 8. Completion | `[POLLING]` | Acknowledge Telegram updates, clean up, report completion | Update acknowledgment failed |

### Example Workflow Output

When the workflow runs successfully, you'll see logs like:

```
[POLLING] Starting Telegram polling worker...
[POLLING] Ensuring polling mode (deleting webhook)...
[POLLING] Webhook deleted, polling mode active.
[POLLING] Fetching Telegram updates...
[POLLING] Fetched 2 update(s).
  [POLLING] Processing audio: chat=*** message_id=56
  [TELEGRAM] Downloading audio file (file_id=...)...
  [TELEGRAM] Audio downloaded: 245123 bytes
  [AI] Starting AI pipeline (transcription + generation)...
    [TRANSCRIPTION] Starting transcription (245123 bytes, audio/ogg)...
    [TRANSCRIPTION] Complete (3420ms, 512 chars, tokens in=1234 out=567)
    [GENERATION] Starting content generation (512 chars, 1 image(s))...
    [GENERATION] Complete (2156ms, tokens in=890 out=345)
  [AI] Pipeline complete.
  [IMAGES] Processing 1 image(s) from batch...
    [IMAGES] Image 1: Downloading from Telegram...
    [IMAGES] Image 1: Downloaded (567890 bytes)
    [IMAGES] Image 1: Extracting EXIF metadata...
    [IMAGES] Image 1: EXIF parsed - GPS(45.1234, 7.5678)
    [IMAGES] Image 1: Rotating and optimizing...
    [IMAGES] Image 1: Optimized (234567 bytes)
    [IMAGES] Image 1: Saved to /tmp/roadtocore_outbox/assets/5534984149/uuid/image-1.jpg
  [IMAGES] Processed 1 image(s) with EXIF/GPS extraction.
  [VALIDATE] Normalizing and validating payload...
  [VALIDATE] Payload valid.
  [POLLING] Saved: uuid — Article Title
[DELIVERY] Delivering 1 payload(s) to WordPress...
[OUTBOX] Found 1 pending payload(s) to deliver...
[OUTBOX] Delivering payload 1/1: uuid.json...
[DISPATCH] Dispatching event uuid...
  [DISPATCH] Starting WordPress dispatch for event uuid...
  [DISPATCH] Processing 1 image(s) for upload...
  [DISPATCH] Image 1/1: uploading local file...
    [WORDPRESS] Uploading image: image-1.jpg (234567 bytes, image/jpeg)...
    [WORDPRESS] Image uploaded: https://example.com/wp-content/uploads/2026/06/image-1.jpg (ID: 123)
  [DISPATCH] Building gallery blocks for 1 image(s)...
  [DISPATCH] Gallery blocks appended.
  [DISPATCH] Sending payload to WordPress endpoint...
  [DISPATCH] WordPress dispatch successful (HTTP 200).
[DISPATCH] delivered: /tmp/roadtocore_outbox/.delivered/uuid.json
[POLLING] Acknowledged 2 update(s) up to 797716227.
[POLLING] Workflow complete.
```

### How to Interpret Errors

When the workflow fails, look for:

1. **`[ERROR]` lines** — immediate problem indicator (e.g., missing token, authentication failed).
2. **Phase where logs stop** — indicates which component failed (e.g., logs stop at `[TRANSCRIPTION]` → AI provider issue).
3. **Last successful phase** — helps narrow troubleshooting scope.

Examples:

- **Logs stop after `[TELEGRAM]`**: Check Telegram API key, network connectivity.
- **Logs stop after `[IMAGES]`**: Check image format support, EXIF parsing errors, disk space.
- **Logs stop after `[TRANSCRIPTION]`**: Check Google API key, model availability, quota limits.
- **Logs stop after `[VALIDATE]`**: Check schema compatibility, payload structure.
- **Logs stop after `[WORDPRESS]`**: Check WordPress credentials, endpoint URL, REST API permissions.

## Current Limitations

- Google AI integration requires valid API key and model availability.
- File-based idempotency/outbox state is suitable for single-node deployment. For multi-node production, use Redis/Postgres.
- Polling runs every 5 minutes via GitHub Actions cron. For real-time processing, consider webhook + localtunnel setup (experimental).
- EXIF GPS extraction depends on image having valid GPS tags; images without GPS metadata will have no `gps` field in payload.

## Suggested Next Steps

1. Move idempotency and delivery state to Redis/Postgres for horizontal scaling.
2. Implement CI/CD auto-deploy for WordPress adapter (currently manual zip upload).
3. Add signed payload delivery to adapters.
4. Add automated integration tests for Telegram → core → WordPress/Astro end-to-end.
5. Add observability (structured logs + metrics) per delivery attempt.
6. Support video audio extraction (ffmpeg) for video files.
7. Add interview transcription mode (speaker detection + Q&A format).
