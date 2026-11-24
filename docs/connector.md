# Claude connector (remote MCP server) - /mcp/<key>, /claude

**Read this file fully before changing** `connector_fns.py`, the `/mcp/` and
`/auth/connector-key` routes in `app_modal.py`, or `site/claude.html`.

Added 2026-11-24 (owner request: "make sure every feature has an API so Alina
can use it via Claude easily - choose whatever is simplest"). The app already had
a route behind every feature; what a chat lacked was (1) a login it can hold,
(2) a way to hand over a video, (3) a tool surface. A custom connector is the
only shape that works in claude.ai chat, desktop and mobile alike.

## How a person uses it

1. Signed in to the app, open `/claude` and press "create link".
2. In Claude: Settings > Connectors > Add custom connector, paste the link.
3. In a chat: "here is a Drive link, cut it, add karaoke captions and a hook".

## Architecture

- **Transport:** stateless Streamable HTTP. One JSON-RPC message per `POST
  /mcp/<key>`, answered as `application/json`. No sessions, no SSE stream
  (`GET` answers 405, which the spec allows). Notifications answer 202 empty.
  Supported protocol versions are echoed back; anything else gets the newest.
- **Auth = the link.** The key rides in the URL path because a pasted URL is
  the only thing Claude's custom-connector box takes without an OAuth server.
  `secrets.token_urlsafe(32)`; **only its SHA-256 is stored** (`hebpipe-mcp`
  Dict: `key:<sha256>` -> `{uid, ts}`, `ukey:<uid>` -> sha256). Consequences:
  the link is shown ONCE, one live link per account, creating a new one kills
  the old one, and account deletion removes both entries (`_purge_user_data`).
  The route also requires the `uid:` index to exist, so a deleted account's
  link is dead even if a stale `key:` entry survived.
- **Loopback, not duplication.** Every tool calls the router's OWN routes
  in-process (`_loop` in the `/mcp/` branch builds a scope with a 5-minute
  session token for the key's owner and re-enters `app`). Credits, ownership
  checks, refunds, error alerts and validation therefore live in exactly one
  place. **Never re-implement a route's logic inside a tool** - add or extend
  the route, then call it.
- **Link fetch** (`fetch_url_fn`, light image): streams the file to
  `{uprefix}{upload_key}_src.mp4`, the single-file source form
  `process_video` already accepts, then `/process` runs as for any upload.
- **Job state** lives in `hebpipe-mcp` as `job:<uid>:<job_id>`:
  `fetching -> processing -> ready -> exporting -> done` (+ `error`).
  `get_video_status` advances the chain lazily (fetch done -> it calls
  `/process`) and long-polls up to ~35 s so a chat needs few round trips.

## Tools (MVP)

| Tool | Route(s) behind it |
|------|--------------------|
| `start_video(url, name?, cut_silences?, captions?, enhance_audio?)` | `/auth/me`, `fetch_url_fn.spawn` |
| `get_video_status(job_id)` | `/process`, `/process_poll`, `/burn_poll`, `/auth/media-token` |
| `generate_hooks(job_id)` | `/generate-hook`, `/generate-hook-poll` |
| `export_video(job_id, style?, caption_edits?, hook_text?, font?, ...)` | `/burn` |
| `list_videos()` | `/jobs`, `/auth/media-token` |
| `get_account()` | `/auth/me` |

Not exposed yet (the routes exist): B-roll, punch-in zoom, Metricool
scheduling, Story Assembler, style profiles, AI upscale. Add them tool by tool.

## Contracts / gotchas

- **SSRF:** only `https`, and EVERY redirect hop is re-checked - every address
  the host resolves to must be public (`_host_is_public`). Redirects are
  followed manually (`allow_redirects=False`) for exactly this reason.
- **Share links:** Drive `/file/d/<id>` and `?id=` become
  `drive.usercontent.google.com/download?...&confirm=t` (skips the large-file
  virus-scan interstitial); Dropbox gets `dl=1`. An HTML response means the
  link is not public - reported as such, never processed.
- **Limits** mirror the upload card: 1 GB, 20 minutes, must have an audio
  stream. Checked in the fetcher, BEFORE a credit is spent.
- **Credits:** `start_video` fails fast when none are left; the real charge
  happens in `/process` (so a 10+ minute video costs 2, as in the app), and a
  failed run is refunded by `/process_poll` as usual. Exports are free.
- **Captions:** the chat only sees `{index, start, end, text}`; per-word
  timings stay server-side. An edited line drops its `words` (they describe
  the old text), so karaoke falls back to even spacing for that line.
- **Style defaults** mirror `_captionStylePayload()` in `site/app.js` and the
  hook defaults mirror `doBurn`. The hook is sent WITHOUT `lines`, so
  `build_caption_ass` wraps it itself.
- **Download links** carry a media token (1 hour). `get_video_status` /
  `list_videos` mint a fresh one on every call.
- **Errors:** `ConnectorError` text is shown to the person verbatim - write
  it as an instruction they can act on. Anything else is logged and masked.
- **Not in the metadata backup:** a link is re-minted in one click and job
  state is only useful while its video lives (30 days).

## Tests

`tests/backend/test_connector.py` (link normalization, SSRF guard, JSON-RPC
envelope, the whole job flow through a fake loopback, router ordering) and
`tests/frontend/connector.spec.js` (the `/claude` page states + layout).
