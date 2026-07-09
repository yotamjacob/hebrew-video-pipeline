"""
Guard: the user-facing frontend must stay emoji-free (design system uses
thin-stroke SVG line-icons + clean typographic text, no emoji in chrome).

Scans the strings that actually reach the DOM:
  - site/i18n.js  — every translation string (what renders as button/label text)
  - site/index.html — full markup (SVG icons only; no emoji text)

app.js is intentionally NOT scanned: its ICON strings are inline SVG (no
emoji) and its code COMMENTS legitimately use arrows like "step -> value".
"""
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Emoji / decorative-symbol codepoint ranges (arrows included — buttons must
# not carry ▶ ↻ ↩ etc.), plus the standalone star glyphs.
_RANGES = [
    (0x1F000, 0x1FAFF),  # pictographs, emoji
    (0x2600, 0x27BF),    # misc symbols + dingbats
    (0x2B00, 0x2BFF),    # arrows/stars block
    (0x2190, 0x21FF),    # arrows
    (0x2300, 0x23FF),    # technical (⏳ ⌄ …)
    (0x25A0, 0x25FF),    # geometric shapes (▶ ■ ○ …)
    (0xFE00, 0xFE0F),    # variation selectors (emoji presentation)
]


def _emoji_chars(text):
    out = []
    for ch in text:
        o = ord(ch)
        if any(a <= o <= b for a, b in _RANGES) or ch in "★☆":
            out.append(ch)
    return sorted(set(out))


def test_i18n_strings_have_no_emoji():
    src = open(os.path.join(_ROOT, "site", "i18n.js"), encoding="utf-8").read()
    offenders = {}
    # Each entry: 'key': { en: '...', he: '...' }
    for m in re.finditer(r"'([\w.]+)':\s*\{\s*en:\s*'((?:[^'\\]|\\.)*)'\s*,\s*he:\s*'((?:[^'\\]|\\.)*)'", src):
        key, en, he = m.groups()
        hits = _emoji_chars(en + he)
        if hits:
            offenders[key] = "".join(hits)
    assert not offenders, f"emoji found in i18n strings: {offenders}"


def test_index_html_has_no_emoji():
    src = open(os.path.join(_ROOT, "site", "index.html"), encoding="utf-8").read()
    hits = _emoji_chars(src)
    assert not hits, f"emoji found in index.html: {hits}"
