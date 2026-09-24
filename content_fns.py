"""
Content-generation Modal functions: hook options and social caption options.
"""

import modal

from pipeline_core import (
    light_image,
    app, image, tmp_vol, TMP_DIR,
    SONNET_MODEL,
    _anthropic_client, _plain_anthropic_errors,
    costs_store, _record_ai_spend, _msg_text,
)
from stock_helpers import sample_frames

# ---------------------------------------------------------------------------
# Hook generator — generate 3 Hebrew hook options via Sonnet vision
# ---------------------------------------------------------------------------

@app.function(
    image=light_image,
    timeout=120,
    volumes={TMP_DIR: tmp_vol},
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
@_plain_anthropic_errors
def generate_hook_options(captions_json: str, video_key: str = "") -> dict:
    import json, base64 as _b64
    from pathlib import Path

    client = _anthropic_client()
    captions = json.loads(captions_json)
    transcript = " ".join(c.get("text", "") for c in captions).strip()
    if not transcript:
        return {"hooks": []}

    frames = []
    if video_key:
        tmp_vol.reload()
        vpath = Path(TMP_DIR) / video_key
        if vpath.exists():
            frames = sample_frames(str(vpath), n_frames=6, strategy="evenly_spaced")

    content = []
    for _t, jpeg in frames:
        b64 = _b64.b64encode(jpeg).decode()
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

    content.append({"type": "text", "text": (
        "You are a social-media video editor writing Hebrew hooks — short, punchy opening captions "
        "that appear in the first seconds of a video to stop the scroll and compel viewers to watch.\n\n"
        f"TRANSCRIPT (Hebrew):\n{transcript}\n\n"
        "Generate exactly 3 distinct Hebrew hook options. Each hook:\n"
        "- Maximum 10 words\n"
        "- Must be provocative, intriguing, or emotionally striking — NOT a plain summary\n"
        "- Should create curiosity or tension that only resolves by watching the video\n"
        "- Must be grammatically correct Hebrew\n"
        "- No emojis, no hashtags\n\n"
        "Return JSON only — no markdown, no explanation:\n"
        "{\"hooks\": ["
        "{\"text\": \"...\", \"rationale\": \"one short sentence IN HEBREW explaining why this hook works (e.g. the emotion or curiosity it creates)\"},"
        "{\"text\": \"...\", \"rationale\": \"...\"},"
        "{\"text\": \"...\", \"rationale\": \"...\"}"
        "]}"
    )})

    resp = client.messages.create(
        model=SONNET_MODEL, max_tokens=800, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": content}],
    )
    _record_ai_spend(costs_store, "hook", SONNET_MODEL, resp.usage)
    raw = _msg_text(resp).strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"hooks": []}
    if "hooks" not in result or not isinstance(result["hooks"], list):
        return {"hooks": []}
    return result


# ---------------------------------------------------------------------------
# Caption generator — suggest a Hebrew social caption from the transcript
# ---------------------------------------------------------------------------

def _assembler_social_prompt(transcript, context, platforms, has_frames):
    """The Story Assembler's per-clip social caption prompt (flavor
    "assembler", 2026-09-24): 2-3 Hebrew lines, gender-neutral address, no
    dashes, NO hashtags (user directive 2026-09-24: "no need for hashtag
    generation anywhere"). `context` (the
    clip's title + on-screen hook) steers the model and must not be copied
    into the output. Pure."""
    plat_note = f" It will be posted on: {platforms}." if platforms else ""
    ctx = (f"CONTEXT (the clip's working title and on-screen hook - for your "
           f"understanding only, do NOT copy them into the caption):\n{context}\n\n") if context else ""
    return (
        "You are a social-media manager writing ONE caption in Hebrew for a short "
        "vertical video clip cut from a longer recording, based on the transcript"
        + (" and the video frames" if has_frames else "") + "." + plat_note + "\n\n"
        + ctx +
        f"TRANSCRIPT (Hebrew):\n{transcript}\n\n"
        "Infer the clip's real topic and voice from the transcript - never assume a "
        "niche the transcript does not support. Then write:\n"
        "- caption: 2-3 short lines in Hebrew. The first line stops the scroll; the "
        "last line is a call to action that fits the content (save / share / comment).\n"
        "- Address the audience in a GENDER-NEUTRAL way: plural imperative or "
        "infinitive (e.g. \"שמרו\", \"תייגו מישהו ש...\", \"לשמור לפעם הבאה\"), never "
        "a masculine or feminine singular form.\n"
        "- No hashtags at all, at most 2 emojis, and never use em or en "
        "dashes - use a plain hyphen (-) if you need one.\n\n"
        "Return JSON only, no markdown, no explanation:\n"
        "{\"caption\": \"line 1\\nline 2\"}"
    )


def _strip_hashtags(text):
    """Remove every #tag token (the prompts say "no hashtags", the model is
    not trusted to obey - user directive 2026-09-24: "no need for hashtag
    generation anywhere"). Lines left empty are dropped; other lines keep
    their words, single-spaced. Pure."""
    import re as _re2
    out = []
    for ln in str(text or "").splitlines():
        had = "#" in ln
        ln = _re2.sub(r"(?<!\S)#[^\s#]+", "", ln)
        if had:
            ln = _re2.sub(r"[ \t]+", " ", ln).strip()
        if ln.strip() or not had:
            out.append(ln)
    return "\n".join(out).strip()


def _clean_social(result):
    """Post-step for the assembler caption (the prompt is not trusted to
    obey): em / en dashes -> "-", hashtags stripped, at most 3 non-empty
    caption lines. Returns {"caption": str}. Pure."""
    raw = result.get("caption") if isinstance(result, dict) else result
    text = _strip_hashtags(str(raw or "").replace("\u2014", "-").replace("\u2013", "-"))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {"caption": "\n".join(lines[:3])}


@app.function(
    image=light_image,
    timeout=120,
    volumes={TMP_DIR: tmp_vol},
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
@_plain_anthropic_errors
def generate_caption_options(captions_json: str, video_key: str = "", platforms: str = "",
                             flavor: str = "", context: str = "") -> dict:
    """Suggested social caption. Default flavor = the main editor's caption
    button (prompt, `post_caption` spend and return shape unchanged).
    flavor="assembler" (Story Assembler clip tiles, 2026-09-24): the
    _assembler_social_prompt, spend under `assembler_social`, returns
    {"caption"} through _clean_social. Neither flavor generates hashtags
    (2026-09-24) - both outputs pass _strip_hashtags."""
    import json, base64 as _b64
    from pathlib import Path

    client = _anthropic_client()
    captions = json.loads(captions_json)
    transcript = " ".join(c.get("text", "") for c in captions).strip()
    if not transcript:
        return {"caption": ""}

    frames = []
    if video_key:
        tmp_vol.reload()
        vpath = Path(TMP_DIR) / video_key
        if vpath.exists():
            frames = sample_frames(str(vpath), n_frames=6, strategy="evenly_spaced")

    content = []
    for _t, jpeg in frames:
        b64 = _b64.b64encode(jpeg).decode()
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

    if flavor == "assembler":
        content.append({"type": "text", "text": _assembler_social_prompt(
            transcript, str(context or "")[:400], platforms, bool(frames))})
        resp = client.messages.create(
            model=SONNET_MODEL, max_tokens=1000, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": content}],
        )
        _record_ai_spend(costs_store, "assembler_social", SONNET_MODEL, resp.usage)
        raw = _msg_text(resp).strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{"):
                    raw = part
                    break
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"caption": raw}
        return _clean_social(parsed)

    plat_note = f" It will be posted on: {platforms}." if platforms else ""
    content.append({"type": "text", "text": (
        "You are a social-media manager writing ONE caption in Hebrew for a short "
        "vertical video (Reel), based on the transcript"
        + (" and the video frames" if frames else "") + "." + plat_note + "\n\n"
        f"TRANSCRIPT (Hebrew):\n{transcript}\n\n"
        "First, infer THIS video's actual topic, niche and tone from the transcript"
        + (" and frames" if frames else "") + ". Then write a caption that:\n"
        "- Matches the video's real subject and voice - do NOT assume any particular "
        "niche, brand, or profession (e.g. do not default to yoga/wellness). If the "
        "video is about cars, cooking, business, comedy, etc., the caption and tone "
        "must reflect THAT.\n"
        "- Opens with a scroll-stopping first line\n"
        "- Is 2-4 short sentences\n"
        "- Ends with a call to action (save / follow / comment) that fits the content\n"
        "- Uses a few tasteful emojis, not many\n"
        "- Has NO hashtags at all\n\n"
        "Return JSON only — no markdown, no explanation:\n"
        "{\"caption\": \"...\"}"
    )})

    resp = client.messages.create(
        model=SONNET_MODEL, max_tokens=1000, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": content}],
    )
    _record_ai_spend(costs_store, "post_caption", SONNET_MODEL, resp.usage)
    raw = _msg_text(resp).strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"caption": _strip_hashtags(raw)}  # fallback: hand back the raw text
    return {"caption": _strip_hashtags(result.get("caption", "") if isinstance(result, dict) else result)}


