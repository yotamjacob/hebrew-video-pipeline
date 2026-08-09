"""
B-roll Modal functions: stock B-roll moment selection (Sonnet), Haiku clip
scoring, search, and clip windowing.
"""

import modal

from pipeline_core import (
    light_image,
    app, tmp_vol, TMP_DIR,
    SONNET_MODEL, HAIKU_MODEL,
    _sanitize_transcript,
    _anthropic_client, _plain_anthropic_errors,
    costs_store, _record_ai_spend,
)
from stock_helpers import (
    fetch_pexels, fetch_pixabay, sample_frames, extract_disqualify_clause,
    score_clips, add_clip_window,
)

# ---------------------------------------------------------------------------
# Video context — one Sonnet call per video that grounds all downstream steps
# in the video's actual visual register, speaker presence, and topic.
# ---------------------------------------------------------------------------

_video_context_cache: dict = {}  # (video_key, transcript_hash) → context dict


def _get_video_context(video_path: str, transcript: str, client) -> dict:
    """Sample 10 frames from the user's video, send to Sonnet with the transcript,
    and return a structured understanding of the video's genre, register, and B-roll fit.
    Returns {} on any failure — callers treat missing context as graceful degradation.
    """
    import hashlib, json as _json, base64 as _b64
    cache_key = (video_path, hashlib.md5(transcript.encode()).hexdigest())
    if cache_key in _video_context_cache:
        print("[ctx] video context cache hit")
        return _video_context_cache[cache_key]

    frames = sample_frames(video_path, n_frames=10, strategy="evenly_spaced")
    if not frames:
        print(f"[ctx] frame sampling failed for {video_path!r} — skipping video context")
        return {}

    print(f"[ctx] sampled {len(frames)} frames from user video, calling Sonnet for context")
    content = []
    for t, jpeg in frames:
        b64 = _b64.b64encode(jpeg).decode()
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": (
        "Analyze this Hebrew speech video. Examine the frames in order, the speaker's "
        "presence, setting, apparent emotional delivery, and read the corrected transcript. "
        "Produce a structured understanding as JSON.\n\n"
        f"Transcript (Hebrew with timestamps):\n<transcript>\n{transcript[:6000]}\n</transcript>\n\n"
        "Return ONLY a JSON object with these fields:\n"
        '- "video_genre": one of "personal monologue" | "educational" | "interview" | "tutorial" | "story" | "other"\n'
        '- "speaker_presence": one of "central focus" | "partial" | "absent"\n'
        '- "setting_description": 1-2 sentences on visible environment and setting\n'
        '- "speaker_emotional_register": 1 phrase describing the speaker\'s emotional delivery style '
        '(e.g. "intimate confession", "didactic teaching", "warm storytelling", "professional")\n'
        '- "topic_summary": 2-3 sentences on what this video is about and the speaker\'s apparent intent\n'
        '- "cultural_context_notes": anything specific to the content (Hebrew cultural references, '
        'religious context, niche topic) that affects what visual B-roll would feel appropriate vs jarring. '
        'Empty string if nothing notable.\n'
        '- "broll_style_recommendation": what visual register fits this video — '
        '"abstract/symbolic", "literal/concrete", "mood/atmospheric", "narrative" — and a brief reason\n'
        '- "sensitive_topics": array of strings listing any themes requiring care in B-roll selection '
        '(e.g. "discusses intimate relationships — avoid casual or romantic touch imagery"). '
        'Empty array [] if none.\n\n'
        "Raw JSON only, no markdown, no code blocks."
    )})

    try:
        resp = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1400,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": content}],
        )
        _record_ai_spend(costs_store, "broll_context", SONNET_MODEL, resp.usage)
        raw = resp.content[0].text.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{"):
                    raw = part
                    break
        ctx = _json.loads(raw)
        print(f"[ctx] video context: genre={ctx.get('video_genre')!r}, "
              f"register={ctx.get('speaker_emotional_register')!r}, "
              f"sensitive={ctx.get('sensitive_topics')}")
        _video_context_cache[cache_key] = ctx
        return ctx
    except Exception as e:
        print(f"[ctx] _get_video_context failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Stock B-roll analysis — finds moments in transcript + searches Pexels/Pixabay
# ---------------------------------------------------------------------------
def _process_moment(m: dict, pexels_key: str, pixabay_key: str, client,
                    video_context: dict, max_candidates: int = 20,
                    orientation: str = "portrait") -> None:
    """Search stock libraries + score clips for one B-roll moment. Mutates m in place.
    Designed to run in a ThreadPoolExecutor — creates its own requests.Session so
    threads don't share state, and score_clips can safely call asyncio.run()."""
    import requests as _r
    http_session = _r.Session()
    try:
        broll_dur     = m.get("broll_duration_seconds", 3.0)
        variants      = m.get("search_variants") or [m.get("broad_search_prompt", m.get("search_query", ""))]
        variants      = [v for v in variants if v]
        seen_ids      = set()
        clips         = []
        variant_stats = []

        for variant in variants:
            pex = fetch_pexels(variant, 1, pexels_key, http_session, orientation)
            pix = fetch_pixabay(variant, 1, pixabay_key, http_session, orientation)
            new_clips = []
            for i in range(max(len(pex), len(pix))):
                if i < len(pex):
                    c = dict(pex[i]); vid_id = f"pexels_{c['id']}"
                    if vid_id not in seen_ids:
                        seen_ids.add(vid_id); c["_source_variant"] = variant; new_clips.append(c)
                if i < len(pix):
                    c = dict(pix[i]); vid_id = f"pixabay_{c['id']}"
                    if vid_id not in seen_ids:
                        seen_ids.add(vid_id); c["_source_variant"] = variant; new_clips.append(c)
            variant_stats.append({"variant": variant, "count": len(new_clips)})
            clips.extend(new_clips)

        if not clips and variants:
            first = variants[0]; words = first.split()
            short = " ".join(words[:2]) if len(words) > 2 else first
            if short != first:
                print(f"[stock] 0 candidates across all variants for moment@{m['start']:.0f}s, "
                      f"retrying with {short!r}")
                pex2 = fetch_pexels(short, 1, pexels_key, http_session, orientation)
                pix2 = fetch_pixabay(short, 1, pixabay_key, http_session, orientation)
                for i in range(max(len(pex2), len(pix2))):
                    if i < len(pex2): clips.append(pex2[i])
                    if i < len(pix2): clips.append(pix2[i])

        clips = clips[:max_candidates]
        vlog  = ", ".join(f"'{s['variant']}': {s['count']}" for s in variant_stats)
        print(f"[stock] moment@{m['start']:.0f}s: {len(clips)} candidates "
              f"({len(variants)} variant(s)) — {vlog}")

        if not clips:
            print(f"[stock] no {orientation} candidates for moment@{m['start']:.0f}s — weak_match")
            m["clips"] = []; m["weak_match"] = True; m["_variant_stats"] = variant_stats
        else:
            strict_eval  = m.get("strict_eval_prompt") or m.get("search_query", "")
            moment_ctx_s = {
                "transcript_excerpt": m.get("transcript_excerpt", ""),
                "key_insight":        m.get("key_insight", ""),
                "visual_anchor":      m.get("visual_anchor", ""),
                "reasoning":          m.get("reasoning", ""),
            }
            scored = score_clips(clips, strict_eval, client,
                                 video_context=video_context or None,
                                 moment_ctx=moment_ctx_s,
                                 on_usage=lambda u: _record_ai_spend(
                                     costs_store, "broll_vision", HAIKU_MODEL, u))
            m["clips"]          = [add_clip_window(c, broll_dur) for c in scored]
            m["_variant_stats"] = variant_stats
            if scored:
                w = scored[0]
                print(f"[stock] winner moment@{m['start']:.0f}s: score={w.get('score')}, "
                      f"variant='{w.get('_source_variant','?')}', "
                      f"reason={w.get('score_reason','')[:80]!r}")
            if not scored:
                m["weak_match"] = True
    finally:
        http_session.close()


@app.function(
    image=light_image,
    timeout=600,
    volumes={TMP_DIR: tmp_vol},
    secrets=[
        modal.Secret.from_name("anthropic-secret"),
        modal.Secret.from_name("pexels-secret"),
        modal.Secret.from_name("pixabay-secret"),
    ],
)
@_plain_anthropic_errors
def analyze_stock_broll(captions_json: str, video_key: str = "",
                        orientation: str = "portrait") -> list:
    import json, os
    orientation = orientation if orientation in ("portrait", "landscape", "square") else "portrait"

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    pexels_key    = os.environ.get("PEXELS_API_KEY", "")
    pixabay_key   = os.environ.get("PIXABAY_API_KEY", "")

    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY secret not configured.")
    if not pexels_key and not pixabay_key:
        raise ValueError("PEXELS_API_KEY or PIXABAY_API_KEY secret not configured.")

    captions = json.loads(captions_json)
    if not captions:
        return []

    lines = []
    for cap in captions:
        ms = int(cap["start"] // 60); ss = cap["start"] % 60
        me = int(cap["end"]   // 60); se = cap["end"]   % 60
        lines.append(f"[{ms}:{ss:04.1f}–{me}:{se:04.1f}] {cap['text']}")
    transcript = _sanitize_transcript("\n".join(lines))

    from pathlib import Path as _Path
    client = _anthropic_client(api_key=anthropic_key)

    # --- Video context pass (Phase 2) ---
    # Sample frames from the user's video and ask Sonnet to characterise its
    # genre, register, and B-roll style fit. Result grounds all downstream steps.
    video_context = {}
    if video_key:
        tmp_vol.reload()
        _vpath = _Path(TMP_DIR) / video_key
        if _vpath.exists():
            video_context = _get_video_context(str(_vpath), transcript, client)
        else:
            print(f"[ctx] video_key={video_key!r} not found in volume — skipping context")

    # Build system blocks: video context first (if available), then the main instruction block.
    # The context block is NOT marked cache_control so it varies per video while the stable
    # instruction block stays cached across calls.
    _ctx_blocks = []
    if video_context:
        sensitive_str = ("; ".join(video_context.get("sensitive_topics", [])) or "none")
        _ctx_blocks = [{
            "type": "text",
            "text": (
                "VIDEO CONTEXT (analysed from the video frames before this call):\n"
                f"Genre: {video_context.get('video_genre', 'unknown')}\n"
                f"Speaker presence: {video_context.get('speaker_presence', 'unknown')}\n"
                f"Setting: {video_context.get('setting_description', '')}\n"
                f"Speaker register: {video_context.get('speaker_emotional_register', 'unknown')}\n"
                f"Topic: {video_context.get('topic_summary', '')}\n"
                f"Cultural context: {video_context.get('cultural_context_notes', '')}\n"
                f"B-roll style fit: {video_context.get('broll_style_recommendation', '')}\n"
                f"Sensitive topics: {sensitive_str}\n\n"
                "Use this context throughout. Your search_variants and strict_eval_prompts must "
                "reflect the video's actual register and avoid imagery that feels jarring given "
                "the speaker's intent and any sensitive topics listed above.\n"
            ),
        }]

    system_content = _ctx_blocks + [
        {
            "type": "text",
            "text": (
                "You are a professional video editor selecting stock B-roll moments for a Hebrew speech video.\n\n"
                "You select B-roll moments in TWO PASSES:\n\n"
                "PASS 1 — EMPHASIS (primary moments)\n"
                "Identify moments that genuinely deserve B-roll on their own merit: emotional peaks, intensity markers, concrete visualizable subjects, narrative climaxes, key insights. Use all criteria below (TWO FLAVORS, emotional signals, intensity scoring). Label these confidence: 'high' or 'medium' depending on strength.\n\n"
                "PASS 2 — COVERAGE (rhythm moments)\n"
                "After choosing emphasis moments, scan for uncovered gaps. If any stretch between two consecutive emphasis moments, or between the 0:03 opening buffer and the first emphasis moment, or between the last emphasis moment and the video_end−2s closing buffer, exceeds 12 seconds — you MUST add a coverage moment inside that gap.\n"
                "Coverage moment rules:\n"
                "  • Pick the best available moment within the gap, even if it is not a natural emphasis peak.\n"
                "  • Set moment_type: 'coverage'. Confidence: 'low' by default; 'medium' if it has a solid visual anchor.\n"
                "  • Assign a real visual_anchor and both search prompts using whatever concrete or emotional content exists in the gap.\n"
                "  • If the gap contains only pure filler with nothing visualizable, skip coverage for that gap and note it as an extra object: {\"skipped_coverage\": \"gap 0:22–0:45 — no usable content\"}.\n\n"
                "BUFFERS — HARD RULES (non-negotiable):\n"
                "  • Do NOT place any moment whose start_seconds < 3 (opening 3 seconds — speaker establishes presence).\n"
                "  • Do NOT place any moment whose broll_end_seconds > video_end − 2 (closing 2 seconds — conclusions land here).\n\n"
                "TARGET RHYTHM: A B-roll moment every 10–12 seconds across the video is ideal. Maximum density: 1 moment per 8 seconds averaged across the whole video. Minimum 8 seconds between consecutive B-roll windows (broll_end of one → broll_start of next).\n\n"
                "MINIMUM MOMENT COUNTS — use coverage to reach the floor if emphasis falls short:\n"
                "  ≤ 30 s    → at least 1 moment\n"
                "  31–45 s   → at least 2 moments\n"
                "  46–60 s   → at least 3 moments\n"
                "  61–90 s   → at least 4 moments\n"
                "  91–120 s  → at least 5 moments\n"
                "  121–180 s → at least 6 moments\n"
                "  > 180 s   → at least 1 moment per 25 seconds\n"
                "To account for post-filter drops (buffer and spacing rules), aim to return 1–2 moments above the minimum floor. If emphasis alone falls short, fill with coverage moments. If combined they still fall short, lower your strictness threshold and include next-best moments labeled 'low' confidence rather than returning fewer than the minimum.\n\n"
                "GENERAL RULES:\n"
                "  • Filler, throat-clearing, generic statements, and low-information sentences do NOT warrant B-roll.\n"
                "  • Choose moments with a SPECIFIC, NON-OBVIOUS claim — not generic filler.\n"
                "  • Each moment must be distinct.\n"
                "  • reasoning field must be in Hebrew.\n\n"

                "B-ROLL COMES IN TWO FLAVORS — RECOGNIZE BOTH:\n\n"

                "CONCRETE moments have a visualizable subject — the viewer benefits from seeing the thing being discussed "
                "(yoga instructor, pouring coffee, late-night office). moment_type: \"concrete\".\n"
                "First-person ACTION narrations are always CONCRETE — if the speaker narrates something they physically did "
                "('I brushed my teeth', 'I went to the gym', 'I cooked dinner', 'I opened my laptop'), show that action in stock footage. "
                "The action verb + its object is the subject; anchor both in every search variant.\n\n"

                "EMOTIONAL moments have no concrete subject but carry strong narrative weight — the viewer benefits from "
                "a symbolic or emotional visual that amplifies the feeling. These are peaks, confessions, realizations, "
                "vulnerabilities, breakthroughs, moments of struggle or triumph. Good editors ALWAYS cut to B-roll on "
                "these — typically a pensive face, hands, eyes, symbolic imagery (windows, doors, paths, water, light), "
                "or abstract textures. moment_type: \"emotional\".\n\n"

                "HYBRID moments have both: a concrete subject wrapped in emotional framing "
                "(exhausted founder at an empty desk). moment_type: \"hybrid\".\n\n"
                "COVERAGE moments (Pass 2 only) are the best available moments inside uncovered gaps — not peaks on their own merit, but genuine content that fills dead stretches. moment_type: \"coverage\".\n\n"

                "CONCRETE ACTION SIGNALS — any first-person past-tense or habitual action verb is a B-roll trigger:\n"
                "  עשיתי (I did), הלכתי (I went), קמתי (I got up), הכנתי (I made/prepared), פתחתי (I opened),\n"
                "  נסעתי (I drove/traveled), אכלתי (I ate), שתיתי (I drank), ניקיתי (I cleaned), כתבתי (I wrote),\n"
                "  צחצחתי שיניים (I brushed my teeth), הלכתי לחדר כושר (I went to the gym),\n"
                "  בישלתי (I cooked), קניתי (I bought), פגשתי (I met), שיחקתי (I played), רצתי (I ran).\n"
                "  Rule: speaker narrates a physical act → it is CONCRETE, moment_type: 'concrete'. Search for the action being performed.\n\n"
                "EMOTIONAL MOMENT SIGNALS — flag a moment with 2+ signals (or 1 strong intensity marker alone is enough):\n\n"
                "  A. Emotion words:\n"
                "     הרגשתי / אני מרגיש/ה (I felt/feel), כאב (pain), שבור/ה (broken), מנפץ/ת (shattering),\n"
                "     עצוב (sad), שמח (happy), מופתע (surprised), מתוסכל (frustrated), אבוד (lost), חופשי (free),\n"
                "     עמוק (deep), פחד (fear), בושה (shame), כמיהה (longing)\n\n"
                "  B. Intensity markers — superlatives, absolutes, extremes (1 alone raises the score):\n"
                "     מדהים/מדהימה (amazing/incredible), נהדר (wonderful), נורא (terrible), הכי (most),\n"
                "     תמיד (always), אף פעם (never), בחיים (ever / in life), לגמרי (completely),\n"
                "     לחלוטין (absolutely), כל (every / all), ממש (really / truly)\n"
                "     Example: 'יכולת מדהימה' ('incredible ability') → B-roll worthy from 'מדהימה' alone.\n\n"
                "  C. Struggle / transformation language:\n"
                "     לא מצליח/ה (can't / failing to), סתום/ה (blocked), תקוע/ה (stuck),\n"
                "     שיניתי (I changed), גיליתי (I discovered), הבנתי (I understood), לפני / אחרי (before / after)\n\n"
                "  D. First-person vulnerability:\n"
                "     Sentences starting with 'אני' (I) followed by emotional disclosure are narrative peaks.\n\n"
                "  E. Rhetorical peaks:\n"
                "     Sentences that are clearly the climax the speaker has been building toward — often shorter and "
                "punchier than surrounding text, ending a thought or turning a corner.\n\n"
                "RULE: a sentence does NOT need a concrete noun to deserve B-roll. If it hits 2+ signals (or 1 strong "
                "intensity marker), it is an emotional B-roll moment — include it with at least 'medium' confidence.\n\n"

                "For EMOTIONAL moments, visual_anchor and broad_search_prompt use symbolic conventions:\n"
                "  struggle/blocked → broad: 'pensive person face close-up'; visual_anchor: 'person staring blankly, hands over face, narrow dark corridor'\n"
                "  breakthrough/realization → broad: 'person looking up hope light'; visual_anchor: 'person looking upward toward light, window opening, sunrise'\n"
                "  shattered/broken → broad: 'breaking glass slow motion'; visual_anchor: 'breaking glass slow-motion, cracked surface, fragments falling'\n"
                "  amazement/wonder → broad: 'wonder awe face close-up'; visual_anchor: 'wide eyes close-up, awe-struck face, light catching hands'\n"
                "  transformation → broad: 'person walking forward sunrise'; visual_anchor: 'person walking forward into light, sunrise, new beginning'\n"
                "  free/liberated → broad: 'person arms open field sky'; visual_anchor: 'open sky, arms outstretched, wind in hair'\n"
                "For emotional moments, strict_eval_prompt focuses on EMOTIONAL TONE, not a literal scene.\n\n"

                "Assign each moment an intensity_score (1–10) — how emotionally / narratively charged it is, regardless of concreteness:\n"
                "  9–10: unmistakably a peak moment the audience will remember\n"
                "  7–8:  strong — clear emotional weight or important claim\n"
                "  5–6:  moderate — worth including, not transformative\n"
                "  3–4:  weak — borderline, filler-adjacent\n"
                "  1–2:  skip\n\n"

                "Assign each moment a confidence level:\n"
                "  'high'   — intensity_score ≥ 8 AND (concrete subject OR multiple emotional signals with clear narrative weight) — emphasis moments only\n"
                "  'medium' — intensity_score 6–7, OR emotional with one strong signal, OR coverage with a solid visual anchor\n"
                "  'low'    — intensity_score ≤ 5, OR borderline, OR coverage moment in a thin/filler gap\n\n"

                "You output TWO search fields per moment — a retrieval field and a scoring field. They serve different purposes.\n\n"

                "search_variants — an array of conceptually DISTINCT visual concepts that all express the moment's meaning.\n"
                "The principle: stock libraries tag clips with many words for the same concept. A clip showing 'broken mirror' won't appear in results for 'breaking glass' even though both express 'shattered.' You must cover the conceptual space, not just one phrasing.\n\n"
                "VARIANT GENERATION RULES — depend on moment_type:\n\n"
                "For CONCRETE moments (visualizable subject is the point):\n"
                "  Every variant MUST include the concrete subject. Variants differ only in framing, angle, action, or composition.\n"
                "  Subject 'yoga instructor and student':\n"
                "    GOOD: ['yoga instructor student teaching', 'yoga teacher guiding student class', 'yoga session instructor student mat', 'yoga studio one on one lesson']\n"
                "    BAD:  ['helping hand supportive', 'guidance gentle teaching', 'mentoring relationship'] — abstracted away the subject\n\n"
                "For HYBRID moments (concrete subject + emotional framing):\n"
                "  Same rule as concrete: anchor the subject in every variant. Emotional framing modifies HOW the subject is shown, not WHETHER it appears.\n"
                "  Subject 'yoga instructor', framing 'gentle support':\n"
                "    GOOD: ['yoga instructor gentle guidance student', 'yoga teacher supportive student', 'yoga instructor light correction student', 'yoga session careful teaching style']\n"
                "    BAD:  ['helping hand gentle assistance', 'supportive touch relationship'] — yoga context is lost; these match any gentle-contact clip\n\n"
                "For EMOTIONAL moments (no concrete subject — feeling or transformation only):\n"
                "  Conceptual diversity is correct — different visual metaphors all expressing the same feeling. Variants diverge intentionally.\n"
                "  Concept 'shattered/broken':\n"
                "    GOOD: ['broken mirror reflection', 'shattered glass slow motion', 'cracked surface texture', 'broken pieces falling']\n\n"
                "For COVERAGE moments:\n"
                "  Apply the rule of the underlying type — coverage is a pacing label, not a generation rule.\n\n"
                "Universal requirements:\n"
                "  • Each variant: 2-5 words, keyword-style, optimized for retrieval.\n"
                "  • All variants must serve the same strict_eval_prompt (scoring filters quality).\n"
                "  • Count: 2-3 variants for concrete/hybrid; 3-5 for emotional/symbolic.\n\n"
                "Self-check before finalising variants: for concrete and hybrid moments, ask yourself 'Does every variant contain at least one specific noun from key_insight or transcript_excerpt?' If any variant omits the subject, regenerate it before output.\n\n"

                "strict_eval_prompt: a full sentence capturing the specific insight (or emotional tone) AND what would disqualify a clip. Always include a DISQUALIFY clause. Used for scoring only, never for search.\n\n"
                "SUBJECT ANCHORING RULE: if moment_type is concrete or hybrid, strict_eval_prompt MUST name the concrete subject. Without it, scoring loses context and generic-but-on-tone clips will pass.\n\n"
                "  WRONG (hybrid — subject abstracted away):\n"
                "    'supportive, gentle assistance showing helping rather than controlling. DISQUALIFY: forceful manipulation, controlling touch.'\n"
                "    → matches any gentle-contact clip: holding hands, parent-child, friends. The yoga context is gone.\n\n"
                "  RIGHT (hybrid — subject anchored):\n"
                "    'yoga instructor providing gentle guidance to student — light touch supporting student's own movement, pedagogical context. DISQUALIFY: holding hands romantically or casually, parent-child contact, friends or strangers touching, any gentle contact outside a teaching context.'\n"
                "    → scoring now rejects the false-positive clip families explicitly.\n\n"
                "For emotional moments: no concrete subject exists, so strict_eval_prompt focuses on emotional register and visual conventions — existing rules apply.\n\n"
                "DISQUALIFY clause must explicitly name common false-positive categories. Ask: what other contexts share these same gestures or visuals? Name them. For a yoga instructor guiding a student: think holding hands romantically, parent-child contact, friends supporting each other — name all three.\n\n"

                "REAL-WORLD DEFAULT (anti-slop) — applies to EVERY moment, concrete AND emotional:\n"
                "Unless the transcript is LITERALLY about space, physics, energy, technology, data, or abstract art, B-roll must be REAL footage of people, faces, hands, places, or objects relevant to what is being said. Abstract CGI, 3D renders, particle / plasma / nebula / smoke / 'energy' / light-streak animations, glowing orbs, and generic 'motion background' loops are NOT acceptable — they look like filler and cheapen the video. Prefer human, literal, on-topic footage even for EMOTIONAL moments (a real pensive face, real hands, a real doorway or window beats an abstract texture). For an interview / talking-to-camera / advice video, lean on real people, workplaces, conversations, and expressive faces — never cosmic or particle animation.\n"
                "  • search_variants must therefore describe real, filmable scenes — never 'energy', 'abstract', 'particles', 'plasma', 'digital background', or similar.\n"
                "  • Every strict_eval_prompt MUST extend its DISQUALIFY clause with: 'abstract CGI, 3D render, particle/plasma/nebula/smoke/energy/light-streak animation, glowing orb, motion-graphics background' — unless the moment is genuinely about one of those subjects.\n\n"
                "  Examples:\n"
                "    concrete: \"yoga instructor guiding student with minimal or no physical contact, verbal guidance or hand-hovering only. DISQUALIFY: hands-on adjustments, physical corrections, instructor touching student.\"\n"
                "    hybrid:   \"exhausted, defeated-looking founder at desk showing professional burnout — slumped posture, dim or empty environment. DISQUALIFY: energetic, productive, or celebratory work imagery; person looks focused, content, or active.\"\n"
                "    emotional: \"person with frustrated or blocked expression — introspective, searching, unable to find words. DISQUALIFY: happy, relaxed, confident, or productive-looking person.\"\n\n"

                "DURATION RULES — for each moment, set B-roll timing using the transcript timestamps:\n\n"
                "a) ALIGN TO SPEECH. B-roll starts with the phrase introducing the insight and ends at a natural pause (sentence end, clause boundary, or silence gap). Use the actual start/end timestamps from the transcript lines — no invented round numbers.\n\n"
                "b) CLAMP TO 2–5 SECONDS. Under 2s reads as a glitch. Over 5s loses the speaker.\n\n"
                "c) KEEP IT SHORT — 2 to 3 seconds ALWAYS (punchy inserts, never longer):\n"
                "   - simple (one concrete action/object, or a short emotional line): ~2 seconds\n"
                "   - moderate (scene with one main focus, or an emotional passage): ~2.5 seconds\n"
                "   - complex (abstract or multi-element: cityscape, conceptual scene): ~3 seconds (hard max)\n\n"
                "d) RESOLVE CONFLICTS. broll_duration_seconds must be between 2 and 3 seconds. If the speech window is longer, still cap at 3s; if shorter than 2s, extend to 2s minimum.\n\n"
                "e) NEVER CUT MID-WORD. broll_end_seconds must fall on a word boundary — use the end timestamp of the last word in the key phrase, or the start of the next phrase (= silence gap).\n\n"

                "Examples:\n\n"
                "Moment: \"כל בוקר אני קם וצוחצח שיניים לפני הכל\" (CONCRETE — first-person morning action, 2.5s at 0:18.0–0:20.5)\n"
                "  moment_type: \"concrete\", intensity_score: 5, intensity_markers: []\n"
                "  broll_start_seconds: 18.0, broll_end_seconds: 20.5, broll_duration_seconds: 2.5\n"
                "  key_insight: \"speaker brushes teeth every morning as the first thing they do\"\n"
                "  visual_anchor: \"person brushing teeth in bathroom\"\n"
                "  search_variants: [\"person brushing teeth bathroom\", \"morning routine teeth brushing\", \"toothbrush close-up dental hygiene\"]\n"
                "  strict_eval_prompt: \"person actively brushing teeth in bathroom — real action, close-up or medium shot. DISQUALIFY: toothbrush product shot without a person, dentist or clinical context, animated or illustrated teeth.\"\n"
                "  confidence: \"medium\"\n\n"
                "Moment: \"yoga corrections should be gentle, loose contact if at all\" (CONCRETE, 3.2s at 1:14.5–1:17.7)\n"
                "  moment_type: \"concrete\", intensity_score: 6, intensity_markers: []\n"
                "  broll_start_seconds: 74.5, broll_end_seconds: 77.5, broll_duration_seconds: 3.0\n"
                "  key_insight: \"yoga corrections should use minimal or no physical contact — verbal guidance only\"\n"
                "  search_variants: [\"yoga instructor student teaching\", \"yoga teacher demonstrating pose\", \"yoga class verbal instruction\"]\n"
                "  strict_eval_prompt: \"yoga instructor teaching student with minimal or no physical contact, verbal guidance or hand-hovering only. DISQUALIFY: hands-on adjustments, physical corrections, instructor touching student.\"\n"
                "  confidence: \"medium\"\n\n"

                "Moment: \"most founders burn out by year two\" (HYBRID, 2.1s at 0:45.0–0:47.1)\n"
                "  moment_type: \"hybrid\", intensity_score: 8, intensity_markers: [\"burn out\"]\n"
                "  broll_start_seconds: 45.0, broll_end_seconds: 47.1, broll_duration_seconds: 2.1\n"
                "  key_insight: \"most founders burn out before achieving profitability\"\n"
                "  search_variants: [\"tired entrepreneur office late night\", \"founder slumped desk dim light\", \"empty startup office after hours\", \"exhausted professional head in hands\"]\n"
                "  strict_eval_prompt: \"exhausted or defeated-looking founder at desk showing professional burnout — slumped posture, dim or empty environment, end-of-day isolation. DISQUALIFY: energetic, productive, or celebratory work imagery; person looks focused, content, or active.\"\n"
                "  confidence: \"high\"\n\n"

                "Moment: \"המורה שלי תמיד עזרה לי להתיישב בצורה נכונה, לגעת קלות פה ושם\" (HYBRID — yoga instructor gentle correction, 3.1s at 1:22.4–1:25.5)\n"
                "  moment_type: \"hybrid\", intensity_score: 6, intensity_markers: []\n"
                "  broll_start_seconds: 82.4, broll_end_seconds: 85.2, broll_duration_seconds: 2.8\n"
                "  key_insight: \"the yoga instructor used light touch to guide alignment, not control\"\n"
                "  search_variants: [\"yoga instructor gentle touch student\", \"yoga teacher guiding student correction\", \"yoga studio instructor adjustment\", \"yoga class instructor student hands\"]\n"
                "  strict_eval_prompt: \"yoga instructor providing light hands-on alignment correction to student in teaching context. DISQUALIFY: holding hands romantically or casually, parent-child contact, friends embracing, any gentle physical contact outside a yoga or coaching context.\"\n"
                "  confidence: \"medium\"\n\n"

                "Moment: \"וכל הזמן הרגשתי כאילו לא מצליחה להבין\" (EMOTIONAL — feeling blocked/failing, 2.8s at 2:15.0–2:17.8)\n"
                "  moment_type: \"emotional\", intensity_score: 8, intensity_markers: [\"הרגשתי\", \"לא מצליחה\"]\n"
                "  broll_start_seconds: 135.0, broll_end_seconds: 137.8, broll_duration_seconds: 2.8\n"
                "  key_insight: \"feeling perpetually blocked and unable to understand — a state of persistent confusion\"\n"
                "  search_variants: [\"pensive person face close-up\", \"frustrated woman looking down\", \"closed door narrow corridor\", \"fog covering path view\"]\n"
                "  strict_eval_prompt: \"person with frustrated or blocked expression — introspective, searching, unable to find words. DISQUALIFY: happy, relaxed, confident, or productive-looking person.\"\n"
                "  confidence: \"high\"\n\n"

                "Return ONLY a JSON array with AT MOST 14 moments - when more qualify, keep the strongest "
                "(highest intensity, most concrete). Long videos MUST NOT list every candidate: a response "
                "that exceeds the output budget is truncated mid-JSON and the whole analysis is lost. "
                "Each moment has these exact fields:\n"
                "- \"moment_type\": \"concrete\" | \"emotional\" | \"hybrid\" | \"coverage\"\n"
                "- \"intensity_score\": number 1–10, emotional/narrative charge of this moment\n"
                "- \"intensity_markers\": array of specific Hebrew words/phrases from the transcript that signal intensity (empty array [] if none)\n"
                "- \"broll_start_seconds\": when the B-roll starts — use the start timestamp of the phrase introducing the insight (number, from transcript)\n"
                "- \"broll_end_seconds\": per DURATION RULES above, 2–5s after broll_start_seconds (number)\n"
                "- \"broll_duration_seconds\": = broll_end_seconds - broll_start_seconds (number)\n"
                "- \"transcript_excerpt\": the Hebrew text of the moment copied verbatim from the transcript above\n"
                "- \"key_insight\": the specific non-obvious claim or concrete subject being described, in English, one sentence\n"
                "- \"visual_anchor\": for concrete moments — the most searchable scene/object; for emotional moments — the symbolic B-roll convention being targeted (in English)\n"
                "- \"reasoning\": one short sentence in Hebrew (max 15 words) on why this moment deserves B-roll\n"
                "- \"search_variants\": array of 2-5 conceptually distinct stock-search queries (2-5 words each) — see guidance above\n"
                "- \"strict_eval_prompt\": full sentence with DISQUALIFY clause for scoring (see above)\n"
                "- \"confidence\": \"high\" | \"medium\" | \"low\" — based on intensity_score rules above\n\n"
                "No markdown, no code blocks — raw JSON array only."
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    def _call_sonnet_for_moments():
        r = client.messages.create(
            model=SONNET_MODEL,
            # 8000 (was 5000): a LONG video can yield a dozen verbose moment
            # objects (~250-400 tokens each) - at 5000 the array truncated
            # mid-JSON, parse failed, and the user saw "no moments found"
            # (field report: long video, empty B-roll). The prompt also caps
            # the ask at 14 moments now; this is the belt to that suspender.
            max_tokens=8000,
            thinking={"type": "disabled"},
            system=system_content,
            messages=[{
                "role": "user",
                "content": f"Transcript (Hebrew with timestamps):\n<transcript>\n{transcript}\n</transcript>",
            }]
        )
        _record_ai_spend(costs_store, "broll_moments", SONNET_MODEL, r.usage)
        raw = r.content[0].text.strip()
        truncated = getattr(r, "stop_reason", None) == "max_tokens"
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("["):
                    raw = part
                    break
        # Fallback: find first '[' in case Claude prefixed with explanatory text
        if not raw.startswith("["):
            idx = raw.find("[")
            if idx >= 0:
                raw = raw[idx:]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # A truncated array still holds complete leading objects - salvage
            # them instead of throwing the whole analysis away.
            cut = raw.rfind("}")
            if cut > 0:
                try:
                    parsed = json.loads(raw[:cut + 1] + "]")
                    print(f"[stock] salvaged {len(parsed)} moments from a "
                          f"truncated response (stop_reason={'max_tokens' if truncated else '?'})")
                    return parsed
                except json.JSONDecodeError:
                    pass
            print(f"[stock] JSON parse failed: {raw[:300]}")
            return None
        if not isinstance(parsed, list):
            print(f"[stock] unexpected JSON type ({type(parsed).__name__}): {raw[:200]}")
            return None
        return parsed

    moments = _call_sonnet_for_moments()
    if moments is None or len(moments) == 0:
        # Retry once on parse failure or empty response (cold-container / transient Claude issue)
        print(f"[stock] first Sonnet call returned {'None' if moments is None else 'empty []'} — retrying")
        import time as _time
        _time.sleep(2)
        moments = _call_sonnet_for_moments()

    if moments is None:
        return {"moments": [], "total_moments_identified": 0, "moments_processed": 0,
                "cost_limit_hit": False, "video_context": video_context,
                "filter_stats": {"sonnet_moments_raw": 0, "buf_drops": 0,
                                 "spacing_drops": 0, "cost_cap_drops": 0}}
    if not moments:
        return {"moments": [], "total_moments_identified": 0, "moments_processed": 0,
                "cost_limit_hit": False, "video_context": video_context,
                "filter_stats": {"sonnet_moments_raw": 0, "buf_drops": 0,
                                 "spacing_drops": 0, "cost_cap_drops": 0}}

    sonnet_moments_raw = len(moments)

    # Compute video duration from captions for buffer enforcement
    video_end = max((c["end"] for c in captions), default=0.0)

    # Normalize field names and validate
    normalized = []
    buf_drops = 0
    for m in moments:
        if not isinstance(m, dict):
            continue
        # Skip skipped_coverage annotations from Sonnet
        if "skipped_coverage" in m and "start_seconds" not in m and "start" not in m:
            print(f"[stock] {m.get('skipped_coverage', 'skipped coverage note')}")
            continue
        start = float(m.get("broll_start_seconds", m.get("start_seconds", m.get("start", 0))))
        if start < 3.0:
            print(f"[stock] buffer drop: moment at {start:.1f}s before opening buffer (3.0s)")
            buf_drops += 1
            continue

        # B-roll timing: prefer new broll_* fields, fall back to legacy start/end_seconds
        broll_start = float(m.get("broll_start_seconds", m.get("speech_start_seconds", m.get("start_seconds", start))))
        broll_end   = float(m.get("broll_end_seconds",   m.get("speech_end_seconds",   broll_start + 3.0)))
        # Clamp duration to 2–3 seconds (short, punchy inserts).
        dur = broll_end - broll_start
        if dur < 2.0:   broll_end = broll_start + 2.0
        elif dur > 3.0: broll_end = broll_start + 3.0
        broll_start = round(broll_start, 2)
        broll_end   = round(broll_end, 2)
        broll_dur   = round(broll_end - broll_start, 2)

        # Closing buffer: drop any moment whose end falls within the last 2 seconds
        if video_end > 5.0 and broll_end > video_end - 2.0:
            print(f"[stock] buffer drop: moment ending at {broll_end:.1f}s within closing buffer "
                  f"(video_end={video_end:.1f}s)")
            buf_drops += 1
            continue

        # Parse search_variants — the array of conceptually distinct retrieval queries
        raw_variants = m.get("search_variants", [])
        if isinstance(raw_variants, list):
            variants = [v.strip() for v in raw_variants
                        if isinstance(v, str) and v.strip() and len(v.strip().split()) <= 10]
        else:
            variants = []
        variants = variants[:5]  # cap at 5 variants

        # Derive broad_search_prompt from variants (or fall back to legacy field)
        legacy_broad = m.get("broad_search_prompt", m.get("search_prompt", m.get("search_query", ""))).strip()
        if variants:
            broad = variants[0]          # first variant is the primary display query
        elif legacy_broad:
            broad = legacy_broad
            variants = [broad]
        else:
            broad = ""
            variants = []

        if not broad:
            print(f"[stock] WARNING: no search_variants and no broad_search_prompt for moment at {start}s")
        elif len(variants) == 1:
            print(f"[stock] WARNING: only 1 search variant for moment@{start}s — "
                  f"may miss candidates (expected 2-5)")

        strict = m.get("strict_eval_prompt", "").strip()
        if not strict:
            strict = broad

        m["start"]                = broll_start
        m["end"]                  = broll_end
        m["broll_start_seconds"]  = broll_start
        m["broll_end_seconds"]    = broll_end
        m["broll_duration_seconds"] = broll_dur
        m["duration_reasoning"]   = m.get("duration_reasoning", "")
        m["search_variants"]      = variants
        m["broad_search_prompt"]  = broad   # derived from search_variants[0]; kept for UI compat
        m["strict_eval_prompt"]   = strict
        m["search_query"]         = broad
        # Card label: first 6 words of key_insight; fall back to legacy topic/label fields
        _insight = m.get("key_insight", "").strip()
        _topic   = m.get("topic", m.get("label", "")).strip()
        if _insight:
            _words = _insight.split()
            m["label"] = " ".join(_words[:6]) + ("…" if len(_words) > 6 else "")
        else:
            m["label"] = _topic
        m["confidence"]           = m.get("confidence", "medium")
        if m["confidence"] not in ("high", "medium", "low"):
            m["confidence"] = "medium"
        m["moment_type"]          = m.get("moment_type", "concrete")
        if m["moment_type"] not in ("concrete", "emotional", "hybrid", "coverage"):
            m["moment_type"] = "concrete"
        raw_score = m.get("intensity_score", 5)
        try:
            m["intensity_score"]  = max(1, min(10, int(raw_score)))
        except (TypeError, ValueError):
            m["intensity_score"]  = 5
        markers = m.get("intensity_markers", [])
        m["intensity_markers"]    = markers if isinstance(markers, list) else []
        normalized.append(m)
    moments = normalized

    # Debug: two-pass breakdown + buffer summary
    n_emphasis = sum(1 for m in moments if m.get("moment_type") != "coverage")
    n_coverage = sum(1 for m in moments if m.get("moment_type") == "coverage")
    if buf_drops:
        print(f"[stock] buffer enforcement: dropped {buf_drops} moment(s)")
    print(f"[stock] 2-pass breakdown: emphasis={n_emphasis}, coverage={n_coverage}, "
          f"total={len(moments)} (video_end={video_end:.0f}s)")

    # ── Spacing filter ────────────────────────────────────────────────────────
    # 1. Even distribution: divide video into N equal slots, keep best per slot
    # 2. Minimum 6 s gap: drop/replace moments that crowd a neighbour
    n_before_spacing = len(moments)
    if len(moments) > 1:
        moments.sort(key=lambda m: m["broll_start_seconds"])
        # Use pre-computed video_end with fallback for empty-captions edge case
        video_end = video_end if video_end > 0 else moments[-1]["broll_end_seconds"]
        n = len(moments)
        _CONF = {"high": 0, "medium": 1, "low": 2}

        # Step 1 — even spacing
        slot_w = video_end / n
        slots: list = [[] for _ in range(n)]
        for m in moments:
            idx = min(int(m["broll_start_seconds"] / slot_w), n - 1)
            slots[idx].append(m)
        spaced = []
        seen_starts: set = set()
        for slot in slots:
            if not slot:
                continue
            best = min(slot, key=lambda m: (_CONF.get(m["confidence"], 1), m["broll_start_seconds"]))
            k = round(best["broll_start_seconds"], 2)
            if k not in seen_starts:
                spaced.append(best)
                seen_starts.add(k)
        moments = sorted(spaced, key=lambda m: m["broll_start_seconds"])

        # Step 2 — enforce minimum 6 s gap between consecutive B-rolls
        _MIN_GAP = 6.0
        filtered = []
        for m in moments:
            if not filtered:
                filtered.append(m)
                continue
            gap = m["broll_start_seconds"] - filtered[-1]["broll_end_seconds"]
            if gap >= _MIN_GAP:
                filtered.append(m)
            elif _CONF.get(m["confidence"], 1) < _CONF.get(filtered[-1]["confidence"], 1):
                filtered[-1] = m  # replace with higher-confidence neighbour
        moments = filtered
        print(f"[stock] spacing: {n} raw → {len(moments)} after even-spacing + {_MIN_GAP}s gap "
              f"(video_end={video_end:.0f}s, slot_w={slot_w:.0f}s)")

        # Gap analysis
        if len(moments) > 1:
            gaps = [round(moments[i]["broll_start_seconds"] - moments[i-1]["broll_end_seconds"], 1)
                    for i in range(1, len(moments))]
            max_gap = max(gaps)
            print(f"[stock] gap analysis: {gaps}s (max {max_gap:.1f}s)")

    # Min-floor check (warning only — Sonnet is responsible for meeting it)
    def _min_floor(dur):
        if dur <= 30:   return 1
        if dur <= 45:   return 2
        if dur <= 60:   return 3
        if dur <= 90:   return 4
        if dur <= 120:  return 5
        if dur <= 180:  return 6
        return max(6, int(dur / 25))
    floor = _min_floor(video_end) if video_end > 0 else 1
    print(f"[stock] min floor for {video_end:.0f}s video: {floor}, have {len(moments)}"
          + (" — OK" if len(moments) >= floor else " — WARNING: below minimum (spacing may have reduced count)"))

    # Max density cap: 1 moment per 8 s average — prevents over-dense B-roll on long dense videos
    if video_end > 0:
        max_cap = max(1, int(video_end / 8.0))
        if len(moments) > max_cap:
            # Prefer emphasis over coverage; within each tier prefer higher confidence
            _P = {"coverage": 1}   # 0 for concrete/emotional/hybrid
            _C = {"high": 0, "medium": 1, "low": 2}
            moments.sort(key=lambda m: (_P.get(m.get("moment_type", ""), 0),
                                        _C.get(m.get("confidence", "low"), 1)))
            moments = moments[:max_cap]
            moments.sort(key=lambda m: m["broll_start_seconds"])
            print(f"[stock] max density cap (1/8s): trimmed to {max_cap} moments "
                  f"for {video_end:.0f}s video")

    spacing_drops = n_before_spacing - len(moments)

    # Cost guard — cap Haiku fan-out to stay under budget.
    # B-roll is the priciest per-video feature and is non-essential, so the
    # per-job budget and candidate fan-out are kept deliberately tight.
    # Multi-frame mode: 20 candidates × batches of 3 × ~$0.003/batch ≈ $0.02/moment
    # Single-frame mode: 20 candidates × ~$0.002/call ≈ $0.04/moment (batched loosely)
    _HAIKU_COST_PER_MOMENT = 0.02 if video_context else 0.04
    # Sonnet: moment selection (~$0.04) + video context pass if used (~$0.04)
    _SONNET_COST_ESTIMATE  = 0.08 if video_context else 0.04
    _COST_BUDGET           = 0.25
    total_identified = len(moments)
    projected_cost   = _SONNET_COST_ESTIMATE + total_identified * _HAIKU_COST_PER_MOMENT
    cost_limit_hit   = False
    cost_cap_drops   = 0
    if projected_cost > _COST_BUDGET:
        max_m = int((_COST_BUDGET - _SONNET_COST_ESTIMATE) / _HAIKU_COST_PER_MOMENT)
        cost_cap_drops = max(0, len(moments) - max(max_m, 1))
        moments = moments[:max(max_m, 1)]
        cost_limit_hit = True
        print(f"[stock] cost cap hit: {total_identified} moments → {len(moments)} processed "
              f"(projected ${projected_cost:.3f} > ${_COST_BUDGET})")
    h   = sum(1 for m in moments if m.get("confidence") == "high")
    med = sum(1 for m in moments if m.get("confidence") == "medium")
    low = sum(1 for m in moments if m.get("confidence") == "low")
    est = _SONNET_COST_ESTIMATE + len(moments) * _HAIKU_COST_PER_MOMENT
    print(f"[stock] {len(moments)} moments ({h} high / {med} medium / {low} low), est cost ${est:.3f}")

    _MAX_CANDIDATES = 20

    import concurrent.futures as _cf
    # No moments identified → nothing to fetch. Skip the pool entirely:
    # ThreadPoolExecutor(max_workers=0) raises "max_workers must be greater
    # than 0", which surfaced to the user as a B-roll search error.
    _workers = min(5, len(moments))
    if _workers > 0:
        print(f"[stock] processing {len(moments)} moments in parallel (workers={_workers})")
        with _cf.ThreadPoolExecutor(max_workers=_workers) as _pool:
            futs = [
                _pool.submit(_process_moment, m, pexels_key, pixabay_key,
                             client, video_context, _MAX_CANDIDATES, orientation)
                for m in moments
            ]
            for fut in _cf.as_completed(futs):
                try:
                    fut.result()
                except Exception as _exc:
                    print(f"[stock] moment processing error: {_exc}")
    else:
        print("[stock] no moments identified — nothing to fetch")

    return {
        "moments":                 moments,
        "total_moments_identified": total_identified,
        "moments_processed":       len(moments),
        "cost_limit_hit":          cost_limit_hit,
        "video_context":           video_context,
        "filter_stats": {
            "sonnet_moments_raw": sonnet_moments_raw,
            "buf_drops":          buf_drops,
            "spacing_drops":      spacing_drops,
            "cost_cap_drops":     cost_cap_drops,
        },
    }


@app.function(
    image=light_image,
    timeout=120,
    secrets=[
        modal.Secret.from_name("anthropic-secret"),
        modal.Secret.from_name("pexels-secret"),
        modal.Secret.from_name("pixabay-secret"),
    ],
)
@_plain_anthropic_errors
def search_stock_clips(search_query: str, page: int = 2, moment_context: str = "",
                       orientation: str = "portrait") -> list:
    """Fetch a fresh page of clips for 'Find different clips', with Haiku scoring when context is provided."""
    import os, json, requests as _req
    orientation   = orientation if orientation in ("portrait", "landscape", "square") else "portrait"
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    pexels_key    = os.environ.get("PEXELS_API_KEY", "")
    pixabay_key   = os.environ.get("PIXABAY_API_KEY", "")

    http_session = _req.Session()
    pex = fetch_pexels(search_query, page, pexels_key, http_session, orientation)
    pix = fetch_pixabay(search_query, page, pixabay_key, http_session, orientation)
    clips = []
    for i in range(max(len(pex), len(pix))):
        if i < len(pex): clips.append(pex[i])
        if i < len(pix): clips.append(pix[i])
    clips = clips[:12]

    if moment_context:
        try:
            ctx = json.loads(moment_context)
        except Exception:
            ctx = {"label": moment_context}
        broll_dur   = float(ctx.get("broll_duration_seconds", 3.0))
        strict_eval = ctx.get("strict_eval_prompt") or search_query
        ac = _anthropic_client(api_key=anthropic_key) if anthropic_key else None
        clips = [add_clip_window(c, broll_dur) for c in score_clips(
            clips, strict_eval, ac,
            on_usage=lambda u: _record_ai_spend(costs_store, "broll_vision", HAIKU_MODEL, u))]

    return clips


