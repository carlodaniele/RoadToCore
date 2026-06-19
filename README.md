# RoadToCore

RoadToCore is a CMS-agnostic content pipeline that receives Telegram messages, triggers on audio arrival, generates structured editorial payloads via AI, and distributes the result to target adapters (WordPress, Astro, and future CMS/frameworks).

## Architecture

- `core/`: Python webhook + orchestration + AI pipeline + schema validation.
- `shared/schema.json`: versioned contract (`1.1.x`) for all adapters.
- `adapters/wordpress/`: WordPress 7.0+ plugin (REST ingest + Abilities API + block mapping).
- `adapters/astro/`: Node/TypeScript adapter that writes Markdown files.

## Data Contract (V1.1)

The payload envelope includes:

- `schema_version`
- `event_id`
- `idempotency_key`
- `created_at`
- `source`
- `content`
- `assets`
- `meta`
- optional `targets`
- optional `ai_meta`

The authoritative schema is in `shared/schema.json`.

## Core Features Implemented

- Telegram webhook endpoint (`POST /webhook/telegram`).
- Trigger only on audio (`audio` or `voice` Telegram message).
- Image buffering by `chat_id` before audio arrives.
- Telegram file download via Bot API.
- Telegram image download and local asset persistence.
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

Implemented capabilities:

- REST endpoint: `POST /wp-json/roadtocore/v1/receive`
- Idempotent post create/update via `_roadtocore_idempotency_key`
- Content mapping to Gutenberg blocks (`heading`, `paragraph`, `list`)
- Media ingest from `asset_ref` (`http/https` and absolute local path)
- Optional Ability registration: `roadtocore/publish-post`
- Optional AI-based image alt text enrichment when AI Client is available

### Install

Copy the `adapters/wordpress` folder into your WordPress plugins directory and activate the plugin.

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

## Current Limitations

- Google AI integration requires valid API key and model availability.
- File-based idempotency/outbox state is suitable for single-node deployment. For multi-node production, use Redis/Postgres.
- WordPress media ingestion supports `http/https` and absolute local paths; if your adapters are on separate hosts, expose assets through shared storage or URLs.

## Suggested Next Steps

1. Move idempotency and delivery state to Redis/Postgres for horizontal scaling.
2. Add signed payload delivery to adapters.
3. Add automated integration tests for Telegram -> core -> WordPress/Astro end-to-end.
4. Add observability (structured logs + metrics) per delivery attempt.
