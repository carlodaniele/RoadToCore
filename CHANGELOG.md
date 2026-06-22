# Changelog

All notable changes to RoadToCore are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versioned using [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-22

### Added

#### Image Processing (EXIF)
- EXIF metadata extraction from images (GPS coordinates, orientation)
- Automatic image rotation based on EXIF orientation tag
- Image optimization (resize to 1600px max, JPEG compression quality 85)
- GPS coordinates embedded in image asset payload as `gps` field (latitude/longitude)

#### WordPress Integration
- Direct image upload to WordPress Media Library via `/wp/v2/media` endpoint
- Gutenberg gallery block generation with embedded GPS coordinates in captions (📍 lat, lon)
- Lightbox support for images in gallery blocks
- Support for single-image and multi-image galleries

#### Workflow Logging & Debugging
- **8-phase checkpoint system** with structured log markers:
  - `[POLLING]` — Polling initialization and update management
  - `[TELEGRAM]` — Telegram file downloads with byte counts
  - `[IMAGES]` — Image processing (EXIF, rotation, optimization) with per-image tracking
  - `[AI]` — AI pipeline orchestration
  - `[TRANSCRIPTION]` — Audio transcription with timing (ms) and token usage
  - `[GENERATION]` — Content generation with timing (ms) and token usage
  - `[VALIDATE]` — Payload validation against schema
  - `[DISPATCH]` — Delivery to WordPress/Astro with retry tracking
  - `[WORDPRESS]` — WordPress Media Library uploads with media IDs
  - `[OUTBOX]` — Outbox worker with progress counters (N/M)
- Performance metrics logged per operation (elapsed time, file sizes, token counts)
- Comprehensive debugging guide in README with workflow phase breakdown

#### Data Contract
- Schema version updated to `1.1.x`
- Added optional `gps` field to image assets with `latitude` and `longitude` properties

### Changed

#### Python Core
- `core/polling.py`: Refactored image download to include EXIF extraction and GPS tracking
- `core/ai/pipeline.py`: Added detailed logging to transcription and generation steps with timing and token usage metrics
- `core/delivery/dispatch.py`: 
  - Direct image upload to WordPress before payload delivery
  - Gallery block generation integrated into dispatch flow
  - wp_media_id returned to PHP adapter
  - Comprehensive error handling and logging

#### WordPress Adapter
- `roadtocore.php`: Version bumped to 0.2.0
- `includes/rest-api.php`: Modified to use `wp_media_id` for images already in Media Library (no re-download)
- `includes/block-builder.php`: Gallery block generation with GPS coordinates in captions

#### GitHub Actions
- `poll.yml`: 
  - Fixed token handling: replaced custom secrets with built-in `github.token` and `github.repository`
  - Workflow now executes successfully on manual `workflow_dispatch` trigger

### Fixed

- Image upload pipeline: Previously failing because local asset paths weren't accessible from GitHub Actions. Now images are uploaded directly to WordPress Media Library during dispatch.
- GitHub Actions workflow: Custom secrets couldn't be created due to authorization constraints. Replaced with built-in variables.
- Gallery block display: Previously not appearing in WordPress posts. Now properly appended to post content after image upload.

### Documentation

- Updated README with v0.2.0 features
- Added comprehensive "Debugging & Monitoring" section with:
  - 8-phase workflow breakdown table
  - Example successful workflow output
  - Error diagnosis guide with common failure patterns
  - Phase-specific troubleshooting tips

### DevOps

- WordPress plugin packaged as `roadtocore-adapter-v0.2.0.zip` in `adapters/` folder
- Version consistency across core, adapter, and schema

## [0.1.1] - 2026-06-10

### Added

- Initial release with basic Telegram polling and WordPress delivery
- Audio transcription via Google Gemini API
- Structured content generation
- Schema validation

---

## Migration Guide

### From 0.1.x to 0.2.0

#### Breaking Changes

- **Image handling**: Images are now uploaded to WordPress Media Library automatically. If you were handling image uploads externally, that logic is now integrated into the dispatch phase.
- **Schema v1.1**: New `gps` field in image assets (optional). Existing payloads without GPS will continue to work.

#### Required Actions

1. **Update WordPress Plugin**: Upload `roadtocore-adapter-v0.2.0.zip` to WordPress admin → Plugins → Upload Plugin, or replace the plugin directory.
2. **Verify WordPress REST API**: Ensure `/wp-json/wp/v2/media` endpoint is accessible with your user credentials.
3. **Verify Bot Token**: Google Gemini API should already be configured from v0.1.x.

#### Optional Enhancements

- If images have GPS EXIF tags, coordinates will now be automatically extracted and displayed in gallery captions.
- Gallery blocks now support lightbox mode (enabled by default).

---

## Known Limitations

- **Polling model**: 5-minute interval via GitHub Actions cron (not real-time). Webhook mode possible but requires localtunnel or public server.
- **File-based state**: Idempotency and outbox state stored in filesystem. For production multi-node deployment, migrate to Redis/Postgres.
- **Manual WordPress updates**: Plugin updates still require manual zip upload. CI/CD auto-deploy planned for future versions.

---

## Planned for Future Releases

- [ ] CI/CD auto-deploy for WordPress plugin
- [ ] Video audio extraction (ffmpeg)
- [ ] Interview transcription mode (Q&A format with diarization)
- [ ] Redis/Postgres state migration for horizontal scaling
- [ ] Signed payload delivery to adapters
- [ ] End-to-end integration tests
- [ ] Structured observability (metrics + tracing)
