"""The caption-font contract: every family offered by the editor must be
loaded by the page's Google Fonts stylesheet AND installed on the Modal
images. A missing @font-face makes document.fonts.load() a silent no-op, so
the caption overlay renders a fallback font while the burn / exact preview
frame renders the real one - the "font changes when the preview pauses" bug
(2026-07-17): the <link> had been trimmed to the two UI fonts only.
"""
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parent.parent.parent

CAPTION_FONTS = [
    "Heebo", "Assistant", "Frank Ruhl Libre", "Secular One", "Rubik",
    "Suez One", "Karantina", "Playpen Sans Hebrew", "Miriam Libre",
]


def _fonts_link_families():
    html = (ROOT / "site/index.html").read_text()
    m = re.search(r'href="(https://fonts\.googleapis\.com/css2\?[^"]+)"', html)
    assert m, "Google Fonts css2 <link> missing from index.html"
    url = unquote(m.group(1)).replace("+", " ")
    return url


def test_every_caption_font_is_in_the_google_fonts_link():
    url = _fonts_link_families()
    missing = [f for f in CAPTION_FONTS if f"family={f}" not in url]
    assert not missing, f"caption fonts missing from the Google Fonts <link>: {missing}"


def test_caption_fonts_match_app_js_list():
    js = (ROOT / "site/app.js").read_text()
    m = re.search(r"const CAPTION_FONTS = \[(.*?)\];", js, re.DOTALL)
    assert m, "CAPTION_FONTS not found in app.js"
    js_fonts = re.findall(r"'([^']+)'", m.group(1))
    assert sorted(js_fonts) == sorted(CAPTION_FONTS), (
        f"app.js CAPTION_FONTS {js_fonts} != test contract {CAPTION_FONTS} - "
        "update BOTH plus the index.html link and the Modal image font layers")


def test_every_caption_font_installed_on_modal_images():
    core = (ROOT / "pipeline_core.py").read_text()
    # Font files are wget'd per family (spaces stripped in filenames).
    missing = [f for f in CAPTION_FONTS
               if f.replace(" ", "").lower() not in core.replace("%5B", "[").lower()]
    assert not missing, f"fonts missing from pipeline_core image layers: {missing}"
