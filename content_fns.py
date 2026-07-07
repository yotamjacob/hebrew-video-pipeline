"""
Content-generation Modal functions: hook options and social caption options.
"""

import modal

from pipeline_core import (
    light_image,
    app, image, tmp_vol, TMP_DIR,
    SONNET_MODEL,
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
def generate_hook_options(captions_json: str, video_key: str = "") -> dict:
    import os, json, base64 as _b64, anthropic as _anthropic
    from pathlib import Path

    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
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
    raw = resp.content[0].text.strip()
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

@app.function(
    image=light_image,
    timeout=120,
    volumes={TMP_DIR: tmp_vol},
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
def generate_caption_options(captions_json: str, video_key: str = "", platforms: str = "") -> dict:
    import os, json, base64 as _b64, anthropic as _anthropic
    from pathlib import Path

    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
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

    plat_note = f" It will be posted on: {platforms}." if platforms else ""
    content.append({"type": "text", "text": (
        "You are the social-media manager for a Hebrew-speaking yoga teacher (Yogalina). "
        "Write ONE caption in Hebrew for a short vertical video (Reel), based on the transcript"
        + (" and the video frames" if frames else "") + "." + plat_note + "\n\n"
        f"TRANSCRIPT (Hebrew):\n{transcript}\n\n"
        "The caption must:\n"
        "- Be warm, calm and inviting — a yoga / wellness brand voice\n"
        "- Open with a scroll-stopping first line\n"
        "- Be 2-4 short sentences\n"
        "- End with a gentle call to action (save / follow / comment)\n"
        "- Use a few tasteful emojis, not many\n"
        "- Put 4-6 relevant hashtags (Hebrew + a couple of English) on the final line\n\n"
        "Return JSON only — no markdown, no explanation:\n"
        "{\"caption\": \"...\"}"
    )})

    resp = client.messages.create(
        model=SONNET_MODEL, max_tokens=1000, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": content}],
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"caption": raw}  # fallback: hand back the raw text
    return {"caption": result.get("caption", "")}


