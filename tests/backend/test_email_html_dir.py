"""_email_html direction auto-detection (2026-08-16): Hebrew content -> rtl
card; English -> ltr. Extracted and EXECUTED from source."""
from tests.backend.conftest import MODAL_SRC


def _fn():
    i = MODAL_SRC.index("def _email_html")
    j = MODAL_SRC.index("\ndef ", i)
    ns = {}
    exec(MODAL_SRC[i:j], ns)
    return ns["_email_html"]


def test_hebrew_body_is_rtl():
    html = _fn()("התקלה תוקנה", "היי, הכל עובד שוב.", "לפתיחה", "https://x")
    assert html.startswith("<div dir='rtl'")
    assert html.count("dir='rtl'") == 2

def test_english_stays_ltr():
    html = _fn()("Verify your email", "Click below.", "Verify", "https://x")
    assert html.startswith("<div dir='ltr'")
    assert "rtl" not in html

def test_hebrew_only_in_title_is_enough():
    assert _fn()("שלום", "plain", "Go", "https://x").startswith("<div dir='rtl'")
