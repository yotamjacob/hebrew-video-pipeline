"""
Stock-footage helpers (pure, no Modal decorators): Pexels/Pixabay fetch,
frame sampling, Haiku clip scoring, clip windowing.
Unit-tested by test_stock_helpers.py without Modal installed.
"""

from pipeline_core import (
    HAIKU_MODEL, HAIKU_SCORING_TEMPERATURE, HAIKU_SANITY_TEMPERATURE, HAIKU_SCORING_MAX_TOKENS,
    HAIKU_SCORING_BATCH_SIZE, HAIKU_SCORING_CONCURRENCY, HAIKU_SANITY_MAX_TOKENS, HAIKU_SANITY_MIN_SCORE,
    HAIKU_MULTIFRAME_BATCH_SIZE, HAIKU_MULTIFRAME_MAX_TOKENS,
)

# ---------------------------------------------------------------------------
# Stock search helpers — module-level so both analyze_stock_broll and
# search_stock_clips can share them without duplication.
# ---------------------------------------------------------------------------

_frame_cache: dict = {}  # (source, n_frames, strategy) → list[(t, bytes)], per-container


def _orient_ok(w, h, orientation: str = "portrait") -> bool:
    """True if a clip sized w×h matches the requested orientation.
    orientation ∈ {"portrait", "landscape", "square"} (unknown → portrait)."""
    try:
        w = float(w or 0); h = float(h or 0)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    if orientation == "landscape":
        return w > h
    if orientation == "square":
        return abs(w - h) <= 0.15 * max(w, h)
    return h > w  # portrait (default)


def fetch_pexels(query: str, page: int, pexels_key: str, session=None,
                 orientation: str = "portrait") -> list:
    if not pexels_key:
        return []
    import requests as _req
    _get = (session or _req).get
    # Pexels supports orientation filtering server-side; keep to its known values.
    _pex_orient = orientation if orientation in ("portrait", "landscape", "square") else "portrait"
    try:
        r = _get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 15, "page": page,
                    "size": "medium", "orientation": _pex_orient},
            headers={"Authorization": pexels_key},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[stock] Pexels {r.status_code}: {r.text[:100]}")
            return []
        clips = []
        for v in r.json().get("videos", []):
            files = v.get("video_files", [])
            oriented_files = [f for f in files if _orient_ok(f.get("width", 0), f.get("height", 0), orientation)]
            sd_files = [f for f in files if f.get("quality") == "sd"]
            preview = (oriented_files[0] if oriented_files else None) or \
                      (sd_files[0] if sd_files else None) or \
                      (files[0] if files else None)
            if not preview:
                continue
            user = v.get("user", {})
            clips.append({
                "source": "pexels",
                "id": v["id"],
                "preview_url": preview["link"],
                "thumbnail": v.get("image", ""),
                "page_url": v.get("url", ""),
                "author": user.get("name", ""),
                "author_url": user.get("url", ""),
                "attribution": f"Video by {user.get('name', 'Unknown')} on Pexels",
                "title": v.get("alt", ""),
                "tags": [],
                "duration": v.get("duration", 0),
            })
        return clips
    except Exception as e:
        print(f"[stock] Pexels error: {e}")
        return []


def fetch_pixabay(query: str, page: int, pixabay_key: str, session=None,
                  orientation: str = "portrait") -> list:
    if not pixabay_key:
        return []
    import requests as _req
    _get = (session or _req).get
    try:
        r = _get(
            "https://pixabay.com/api/videos/",
            params={"key": pixabay_key, "q": query, "per_page": 15, "page": page, "video_type": "film"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[stock] Pixabay {r.status_code}: {r.text[:100]}")
            return []
        clips = []
        for v in r.json().get("hits", []):
            videos = v.get("videos", {})
            # Pixabay has no orientation query param; filter each rendition by size.
            preview = None
            for size in ("small", "medium", "large", "tiny"):
                candidate = videos.get(size, {})
                if candidate.get("url") and _orient_ok(candidate.get("width", 0), candidate.get("height", 0), orientation):
                    preview = candidate
                    break
            if not preview:
                continue
            user = v.get("user", "")
            uid  = v.get("user_id", "")
            clips.append({
                "source": "pixabay",
                "id": v["id"],
                "preview_url": preview["url"],
                "thumbnail": f"https://i.vimeocdn.com/video/{v.get('picture_id', '')}_295x166.jpg"
                             if v.get("picture_id") else "",
                "page_url": v.get("pageURL", ""),
                "author": user,
                "author_url": f"https://pixabay.com/users/{user}-{uid}/",
                "attribution": f"Video by {user or 'Unknown'} on Pixabay (CC0)",
                "title": v.get("tags", "").split(",")[0].strip().title() if v.get("tags") else "",
                "tags": [t.strip() for t in v.get("tags", "").split(",") if t.strip()],
                "duration": v.get("duration", 0),
            })
        return clips
    except Exception as e:
        print(f"[stock] Pixabay error: {e}")
        return []


# ---------------------------------------------------------------------------
# Frame sampling — ffmpeg-based, used for video context analysis and
# multi-frame clip scoring. Never raises: returns [] on any failure so
# callers can degrade to thumbnail-only scoring.
# ---------------------------------------------------------------------------

def sample_frames(source: str, n_frames: int = 4, strategy: str = "skip_intro") -> list:
    """Sample n_frames from a video source (local path or HTTP URL).

    strategy: 'evenly_spaced' — evenly across full duration.
              'skip_intro'    — skip first 0.5 s and last 0.3 s (avoids static
                                intro/outro frames common in stock clips).
    Returns: list of (timestamp_sec: float, jpeg_bytes: bytes).
             Returns [] on any failure — callers fall back to thumbnail.
    Caches by (source, n_frames, strategy) for the container lifetime.
    """
    cache_key = (source, n_frames, strategy)
    if cache_key in _frame_cache:
        return _frame_cache[cache_key]
    result = _sample_frames_impl(source, n_frames, strategy)
    _frame_cache[cache_key] = result
    return result


def _sample_frames_impl(source: str, n_frames: int, strategy: str) -> list:
    import subprocess as _sp, tempfile as _tf, os as _os
    tmp_path = None
    try:
        is_url = source.startswith("http://") or source.startswith("https://")
        if is_url:
            tmp_fd, tmp_path = _tf.mkstemp(suffix=".mp4")
            _os.close(tmp_fd)
            _download_video_partial(source, tmp_path, max_bytes=8 * 1024 * 1024)
            source_path = tmp_path
        else:
            source_path = source

        duration = _probe_video_duration(source_path)
        if not duration or duration <= 0:
            return []

        frames = []
        for t in _frame_timestamps(duration, n_frames, strategy):
            data = _extract_jpeg_frame(source_path, t)
            if data:
                frames.append((t, data))
        return frames
    except Exception as e:
        print(f"[frames] sample_frames failed {source[:80]!r}: {e}")
        return []
    finally:
        if tmp_path:
            try:
                import os as _os2
                _os2.unlink(tmp_path)
            except OSError:
                pass


def _download_video_partial(url: str, dest: str, max_bytes: int) -> None:
    import requests as _rq
    resp = _rq.get(url, headers={"Range": f"bytes=0-{max_bytes - 1}"},
                   stream=True, timeout=20)
    downloaded = 0
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            fh.write(chunk)
            downloaded += len(chunk)
            if downloaded >= max_bytes:
                break


def _probe_video_duration(path: str) -> float:
    import subprocess as _sp
    try:
        r = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        val = r.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def _frame_timestamps(duration: float, n: int, strategy: str) -> list:
    if strategy == "skip_intro":
        start = min(0.5, duration * 0.1)
        end   = max(start + 0.1, duration - 0.3)
    else:
        start, end = 0.0, duration
    if n == 1:
        return [round((start + end) / 2, 3)]
    span = end - start
    return [round(start + span * i / (n - 1), 3) for i in range(n)]


def _extract_jpeg_frame(video_path: str, timestamp: float) -> bytes:
    import subprocess as _sp
    try:
        r = _sp.run(
            ["ffmpeg", "-ss", str(timestamp), "-i", video_path,
             "-frames:v", "1", "-vf", "scale=min(768,iw):-2",
             "-f", "image2", "-vcodec", "mjpeg", "pipe:1",
             "-loglevel", "error"],
            capture_output=True, timeout=15,
        )
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def extract_disqualify_clause(strict_eval_prompt: str) -> str:
    """Return just the DISQUALIFY clause text from a strict_eval_prompt, or '' if absent."""
    upper = strict_eval_prompt.upper()
    idx = upper.find("DISQUALIFY:")
    if idx == -1:
        return ""
    return strict_eval_prompt[idx + len("DISQUALIFY:"):].strip()


def score_clips(clips: list, strict_eval: str, anthropic_client,
                video_context: dict = None, moment_ctx: dict = None,
                on_usage=None) -> list:
    """Score clips against strict_eval via Haiku vision. Returns clips scored >= 6, sorted desc.

    When video_context is provided (set by analyze_stock_broll after the video-context pass),
    switches to multi-frame mode: downloads each clip's preview_url, extracts 4 frames with
    skip_intro strategy, and uses a richer 2-step scoring prompt that checks DISQUALIFY
    violations (STEP 1) and tonal register fit (STEP 2). Batch size reduces to 3 clips.

    Falls back to thumbnail-only mode when video_context is None (e.g. search_stock_clips).
    Frame sampling failures degrade gracefully to thumbnail fallback per clip.
    """
    import asyncio, json as _json, base64 as _b64

    if not clips or not anthropic_client:
        return clips

    has_thumb = [(i, c) for i, c in enumerate(clips) if c.get("thumbnail")]
    if not has_thumb:
        return clips

    use_multiframe = bool(video_context)
    batch_size = HAIKU_MULTIFRAME_BATCH_SIZE if use_multiframe else HAIKU_SCORING_BATCH_SIZE
    max_tokens = HAIKU_MULTIFRAME_MAX_TOKENS if use_multiframe else HAIKU_SCORING_MAX_TOKENS

    # Pre-build context prefixes (empty strings when context absent)
    _vc_prefix = ""
    if video_context:
        sensitive_str = "; ".join(video_context.get("sensitive_topics", [])) or "none"
        _vc_prefix = (
            "VIDEO CONTEXT (the user's video):\n"
            f"Genre: {video_context.get('video_genre', '')}\n"
            f"Speaker register: {video_context.get('speaker_emotional_register', '')}\n"
            f"Topic: {video_context.get('topic_summary', '')}\n"
            f"Sensitive topics: {sensitive_str}\n\n"
        )
    _mc_prefix = ""
    if moment_ctx:
        _mc_prefix = (
            "MOMENT CONTEXT (where this clip would appear):\n"
            f"Transcript: {moment_ctx.get('transcript_excerpt', '')}\n"
            f"Key insight: {moment_ctx.get('key_insight', '')}\n"
            f"Visual anchor: {moment_ctx.get('visual_anchor', '')}\n"
            f"Why this moment: {moment_ctx.get('reasoning', '')}\n\n"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _call_haiku_sync(content, mt, temp):
        r = anthropic_client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=mt,
            temperature=temp,
            messages=[{"role": "user", "content": content}],
        )
        if on_usage:
            # Cost metering callback (kept as a callback so this module stays
            # pure - no Modal imports). Best-effort by contract.
            try:
                on_usage(r.usage)
            except Exception:
                pass
        # Inline (not pipeline_core._msg_text - this module stays import-pure):
        # Sonnet 5 may emit a ThinkingBlock before the text block; join
        # text-type blocks, never index content[0] (field bug 2026-08-13).
        return "".join(getattr(b, "text", "") for b in (r.content or [])
                       if getattr(b, "type", "") == "text").strip()

    def _parse_json_array(raw):
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("["):
                    raw = part
                    break
        return _json.loads(raw)

    def _apply_scores(scores, local_to_orig):
        for rank, orig_i in enumerate(local_to_orig):
            if rank < len(scores):
                s = scores[rank]
                clips[orig_i]["score"] = max(0, min(10, int(s.get("score", 0))))
                clips[orig_i]["score_reason"] = s.get("reason", "")
                if s.get("frames_observed"):
                    clips[orig_i]["frames_observed"] = s["frames_observed"]
                if s.get("step1_disqualified"):
                    clips[orig_i]["step1_disqualified"] = True

    def _build_content_thumbnail(batch_clips):
        """Thumbnail-only scoring (no video_context). Original behaviour."""
        content = []
        local_valid = []
        for li, clip in enumerate(batch_clips):
            if not clip.get("thumbnail"):
                continue
            content.append({"type": "image", "source": {"type": "url", "url": clip["thumbnail"]}})
            local_valid.append(li)
            title    = clip.get("title", "")
            tags_str = ", ".join(clip.get("tags", [])) if clip.get("tags") else ""
            dur      = clip.get("duration", "")
            parts    = []
            if title:    parts.append(f"Title: {title}")
            if tags_str: parts.append(f"Tags: {tags_str}")
            if dur:      parts.append(f"Duration: {dur}s")
            content.append({"type": "text",
                             "text": f"[Clip {len(local_valid)}: {' | '.join(parts) or '(no metadata)'}]"})
        if not local_valid:
            return content, local_valid
        n = len(local_valid)
        content.append({"type": "text", "text": (
            f"You are scoring stock video clips for B-roll relevance.\n\n"
            f"TARGET: {strict_eval}\n\n"
            f"Score each of the {n} clips (1–10) for how well they match TARGET.\n\n"
            "SCORING GUIDE:\n"
            "- 7-10: clip clearly shows what TARGET describes\n"
            "- 4-6: generically related but doesn't clearly satisfy the specific criteria\n"
            "- 1-3: metadata or thumbnail matches a DISQUALIFY clause in TARGET, OR the clip is "
            "abstract CGI / 3D render / particle / plasma / nebula / smoke / energy / light-streak "
            "animation / glowing orb / motion-graphics background (unless TARGET is genuinely about "
            "such a subject) — these read as filler even if visually 'energetic'.\n\n"
            "CRITICAL: if the metadata (title/tags) clearly contradicts what TARGET requires "
            "(especially any DISQUALIFY clause), score ≤ 3 regardless of thumbnail. "
            "Metadata describes the full clip across all frames, not just the poster frame.\n\n"
            f"Return a JSON array of exactly {n} objects in order:\n"
            '[{"score": number, "reason": "short reason referencing both thumbnail AND metadata"}, ...]\n\n'
            "Raw JSON only, no markdown."
        )})
        return content, local_valid

    def _build_content_multiframe(batch_clips):
        """Multi-frame scoring with video + moment context and STEP 1/STEP 2 evaluation."""
        content = []
        local_valid = []
        for li, clip in enumerate(batch_clips):
            frames = clip.get("_frames", [])
            if not frames and not clip.get("thumbnail"):
                continue
            local_valid.append(li)
            title    = clip.get("title", "")
            tags_str = ", ".join(clip.get("tags", [])) if clip.get("tags") else ""
            dur      = clip.get("duration", "")
            if frames:
                for _t, jpeg in frames:
                    b64 = _b64.b64encode(jpeg).decode()
                    content.append({"type": "image",
                                     "source": {"type": "base64",
                                                "media_type": "image/jpeg", "data": b64}})
                frames_note = f"{len(frames)} frames sampled across clip"
            else:
                content.append({"type": "image",
                                 "source": {"type": "url", "url": clip["thumbnail"]}})
                frames_note = "1 thumbnail (frame sampling failed)"
            parts = []
            if title:    parts.append(f"Title: {title}")
            if tags_str: parts.append(f"Tags: {tags_str}")
            if dur:      parts.append(f"Duration: {dur}s")
            meta = " | ".join(parts) or "(no metadata)"
            content.append({"type": "text",
                             "text": f"[Clip {len(local_valid)}: {meta} | {frames_note}]"})
        if not local_valid:
            return content, local_valid
        n = len(local_valid)
        content.append({"type": "text", "text": (
            "You are scoring stock video clips for B-roll relevance.\n\n"
            f"{_vc_prefix}"
            f"{_mc_prefix}"
            f"EVALUATION TARGET: {strict_eval}\n\n"
            f"Score each of the {n} clips (1–10). For each clip:\n\n"
            "STEP 1 (mandatory first): Examine ALL frames, not just the first. Does the clip's "
            "actual content trigger any DISQUALIFY clause in EVALUATION TARGET? Also check if "
            "title/tags clearly contradict what the target requires. "
            "If yes → score 1–3, set step1_disqualified: true. STOP.\n\n"
            "STEP 2 (only if STEP 1 passes): How well does the clip serve the moment given the "
            "video's register and topic? Penalise tonal mismatch even if surface match exists "
            "(e.g. cheerful office footage in an intimate-confession video → ≤4 even if "
            "technically on-topic). ANTI-SLOP: unless the moment's topic is genuinely about "
            "space, physics, energy, technology, data, or abstract art, score ≤3 any abstract "
            "CGI, 3D render, particle/plasma/nebula/smoke/energy/light-streak animation, glowing "
            "orb, or generic motion-graphics background — these read as filler regardless of how "
            "'energetic' or 'exciting' they look. Real people/places/objects only. "
            "Score 4–10 for varying degrees of fit.\n\n"
            f"Return a JSON array of exactly {n} objects:\n"
            '[{"score": number, "step1_disqualified": boolean, '
            '"frames_observed": "1 sentence on what frames actually show across the clip", '
            '"reason": "specific reason citing frames AND metadata"}, ...]\n\n'
            "Raw JSON only, no markdown."
        )})
        return content, local_valid

    # ── Async frame sampling ──────────────────────────────────────────────────

    async def _sample_clip_frames_async(clip, ffmpeg_sem):
        """Download preview_url and extract 4 frames. Attaches _frames to clip dict."""
        url = clip.get("preview_url") or clip.get("thumbnail", "")
        if not url:
            return
        cache_key = (url, 4, "skip_intro")
        if cache_key in _frame_cache:
            clip["_frames"] = _frame_cache[cache_key]
            return
        async with ffmpeg_sem:
            try:
                frames = await asyncio.to_thread(sample_frames, url, 4, "skip_intro")
                clip["_frames"] = frames
                if not frames:
                    clip["frame_sample_failed"] = True
            except Exception as e:
                clip["_frames"] = []
                clip["frame_sample_failed"] = True
                print(f"[frames] async sampling error clip id={clip.get('id')}: {e}")

    # ── Primary scoring (parallel batches) ───────────────────────────────────

    async def _score_batch(orig_indices, batch_clips, haiku_sem):
        if use_multiframe:
            content, local_valid = _build_content_multiframe(batch_clips)
        else:
            content, local_valid = _build_content_thumbnail(batch_clips)
        if not local_valid:
            return
        local_to_orig = [orig_indices[li] for li in local_valid]

        async with haiku_sem:
            raw_response = None
            try:
                raw_response = await asyncio.to_thread(
                    _call_haiku_sync, content, max_tokens, HAIKU_SCORING_TEMPERATURE
                )
                _apply_scores(_parse_json_array(raw_response), local_to_orig)
                return
            except Exception as e:
                print(
                    f"[stock] Haiku batch ({len(local_valid)} clips) failed (attempt 1): {e}\n"
                    f"RAW: {repr(raw_response)[:300]}"
                )

            # Retry with first half of the batch
            half        = max(1, len(local_valid) // 2)
            retry_local = local_valid[:half]
            retry_orig  = [orig_indices[li] for li in retry_local]
            retry_clips = [batch_clips[li] for li in retry_local]
            if use_multiframe:
                retry_content, retry_lv = _build_content_multiframe(retry_clips)
            else:
                retry_content, retry_lv = _build_content_thumbnail(retry_clips)
            if not retry_lv:
                return
            retry_l2o = [retry_orig[li] for li in retry_lv]
            raw2 = None
            try:
                raw2 = await asyncio.to_thread(
                    _call_haiku_sync, retry_content, max_tokens, HAIKU_SCORING_TEMPERATURE
                )
                _apply_scores(_parse_json_array(raw2), retry_l2o)
            except Exception as e2:
                skipped = [clips[i].get("id", i) for i in retry_l2o]
                print(
                    f"[stock] Batch retry failed: {e2}\n"
                    f"RAW2: {repr(raw2)[:200]}\n"
                    f"Skipping clip IDs: {skipped}"
                )

    # ── Disqualify sanity-check (one call per high-scoring clip) ──────────────

    async def _check_sanity(orig_i, clip, disqualify_clause, haiku_sem):
        title    = clip.get("title", "")
        tags_str = ", ".join(clip.get("tags", [])) if clip.get("tags") else ""
        prompt   = (
            f"Quick check. A clip has these properties:\n"
            f"  Title: {title}\n"
            f"  Tags: {tags_str}\n\n"
            f"An evaluation target says to disqualify clips matching: '{disqualify_clause}'\n\n"
            "Does the title or tags clearly indicate this clip matches the disqualifier? "
            "Examples of clear contradiction:\n"
            "  - Disqualify 'welcoming gestures': title 'high five' or tag 'celebration' → YES contradiction\n"
            "  - Disqualify 'happy energetic': title 'tired exhausted' → NO contradiction\n"
            "  - Disqualify 'physical contact': tag 'embrace' → YES contradiction\n\n"
            'Respond ONLY with JSON: {"contradicts": boolean, "reason": "short reason citing specific title or tag"}'
        )
        async with haiku_sem:
            try:
                msg = await asyncio.to_thread(
                    _call_haiku_sync,
                    [{"type": "text", "text": prompt}],
                    HAIKU_SANITY_MAX_TOKENS,
                    HAIKU_SANITY_TEMPERATURE,
                )
                raw_text = msg
                if "```" in raw_text:
                    for part in raw_text.split("```"):
                        part = part.strip().lstrip("json").strip()
                        if part.startswith("{"):
                            raw_text = part
                            break
                result = _json.loads(raw_text)
                if result.get("contradicts"):
                    orig_score = clips[orig_i]["score"]
                    clips[orig_i]["score"] = 2
                    clips[orig_i]["score_reason"] = f"[OVERRIDE] {clips[orig_i].get('score_reason', '')}"
                    print(
                        f"[stock] sanity-check override: clip '{title}' was {orig_score}/10 "
                        f"but title/tags contradict disqualifier '{disqualify_clause}': "
                        f"{result.get('reason', '')}"
                    )
            except Exception as e:
                print(f"[stock] sanity-check failed for clip '{title}': {e}")

    # ── Orchestrate ───────────────────────────────────────────────────────────

    async def _run_all():
        haiku_sem = asyncio.Semaphore(HAIKU_SCORING_CONCURRENCY)
        all_orig  = [i for i, _ in has_thumb]
        all_clips = [c for _, c in has_thumb]

        # Phase 0 (multi-frame only): sample frames for all clips concurrently
        if use_multiframe:
            ffmpeg_sem = asyncio.Semaphore(8)  # cap concurrent ffmpeg + download ops
            await asyncio.gather(*[
                _sample_clip_frames_async(c, ffmpeg_sem) for c in all_clips
            ])
            n_ok   = sum(1 for c in all_clips if c.get("_frames"))
            n_fail = sum(1 for c in all_clips if c.get("frame_sample_failed"))
            print(f"[frames] {n_ok} clips sampled OK, {n_fail} fell back to thumbnail")

        # Phase 1: primary scoring
        primary_tasks = [
            _score_batch(
                all_orig[b:b + batch_size],
                all_clips[b:b + batch_size],
                haiku_sem,
            )
            for b in range(0, len(all_orig), batch_size)
        ]
        await asyncio.gather(*primary_tasks)

        # Phase 2: disqualify override for high scorers
        disqualify_clause = extract_disqualify_clause(strict_eval)
        if disqualify_clause:
            high_scorers = [
                (i, c) for i, c in enumerate(clips)
                if c.get("score", 0) >= HAIKU_SANITY_MIN_SCORE and c.get("thumbnail")
            ]
            if high_scorers:
                await asyncio.gather(*[
                    _check_sanity(i, c, disqualify_clause, haiku_sem)
                    for i, c in high_scorers
                ])

    asyncio.run(_run_all())

    # Strip raw frame bytes — not JSON-serialisable and not needed downstream
    for c in clips:
        c.pop("_frames", None)

    passing = [c for c in clips if c.get("score", 0) >= 6]
    return sorted(passing, key=lambda c: c["score"], reverse=True)


def add_clip_window(clip: dict, broll_duration: float) -> dict:
    """Select the middle window of a stock clip to avoid static start/end frames."""
    stock_dur = float(clip.get("duration") or 0)
    if stock_dur <= 0 or stock_dur <= broll_duration:
        clip["clip_use_start_seconds"] = 0.0
        clip["clip_use_end_seconds"]   = round(stock_dur, 2) if stock_dur > 0 else round(broll_duration, 2)
        clip["clip_window_strategy"]   = "padded" if 0 < stock_dur < broll_duration else "whole"
    else:
        mid = round((stock_dur - broll_duration) / 2, 2)
        clip["clip_use_start_seconds"] = mid
        clip["clip_use_end_seconds"]   = round(mid + broll_duration, 2)
        clip["clip_window_strategy"]   = "middle"
    return clip


