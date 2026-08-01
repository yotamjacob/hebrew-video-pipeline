"""The Play-review demo login — a FIXED code for exactly one address.

Sign-in is passwordless (Google, or a 6-digit code we email) and a Play
reviewer can read neither, which is why the app came back from review as "not
ready". This is the deliberate bypass that lets them in, so it has to open for
exactly one configured address and stay shut for everything else: a wrong
address, a wrong code, an unset/half-set secret, or a caller who sends nothing
at all. A mistake here is an auth hole on a live product.
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn

_fns = _extract_fn(MODAL_SRC, "_normalize_email", "_is_review_email", "_review_login_ok")
_normalize_email = _fns["_normalize_email"]
_is_review_email = _fns["_is_review_email"]
_review_login_ok = _fns["_review_login_ok"]

EMAIL = "play-review@example.com"
CODE  = "314159"


class TestReviewLoginOpens:
    """The configured pair, and only in the forms a reviewer would actually
    type it (stray case, stray whitespace)."""

    def test_exact_pair_is_accepted(self):
        assert _review_login_ok(EMAIL, CODE, EMAIL, CODE)

    def test_case_and_whitespace_in_the_typed_email_still_match(self):
        assert _review_login_ok("  Play-Review@Example.COM  ", CODE, EMAIL, CODE)

    def test_whitespace_around_the_typed_code_still_matches(self):
        # Copy-paste from the Play Console form drags spaces along.
        assert _review_login_ok(EMAIL, f" {CODE} ", EMAIL, CODE)

    def test_a_configured_gmail_matches_its_dotted_alias(self):
        # Account keys are normalized (dots/+tags stripped for Gmail), so the
        # review address must resolve the same way or the reviewer's typing
        # could miss the account the code unlocks.
        assert _review_login_ok("play.review+x@gmail.com", CODE,
                                "playreview@gmail.com", CODE)


class TestReviewLoginStaysShut:
    """Everything else. Each of these being True would be a live auth hole."""

    def test_wrong_code_is_refused(self):
        assert not _review_login_ok(EMAIL, "000000", EMAIL, CODE)

    def test_wrong_email_with_the_right_code_is_refused(self):
        assert not _review_login_ok("someone@else.com", CODE, EMAIL, CODE)

    def test_unset_secret_disables_the_bypass_entirely(self):
        # A deploy without the secret must have NO demo login, and must not
        # accept an empty code against an empty config.
        assert not _review_login_ok(EMAIL, CODE, "", "")
        assert not _review_login_ok("", "", "", "")
        assert not _review_login_ok(EMAIL, "", EMAIL, CODE)

    def test_half_set_secret_disables_the_bypass(self):
        assert not _review_login_ok(EMAIL, CODE, EMAIL, "")
        assert not _review_login_ok(EMAIL, CODE, "", CODE)

    def test_a_short_review_code_is_refused_even_if_it_matches(self):
        # Guards a typo'd/truncated secret from becoming a trivial bypass.
        assert not _review_login_ok(EMAIL, "12345", EMAIL, "12345")
        assert not _review_login_ok(EMAIL, "1", EMAIL, "1")

    def test_none_inputs_do_not_raise(self):
        assert not _review_login_ok(None, None, EMAIL, CODE)
        assert not _is_review_email(None, EMAIL)


class TestReviewEmailMatch:
    def test_matches_only_the_configured_address(self):
        assert _is_review_email(EMAIL, EMAIL)
        assert not _is_review_email("other@example.com", EMAIL)

    def test_unset_config_matches_nothing(self):
        assert not _is_review_email(EMAIL, "")


class TestReviewLoginWiring:
    """The route bodies are closures inside api() and can't be extracted, so
    the wiring is guarded against the source."""

    def test_request_code_short_circuits_the_review_address(self):
        # No mail is sent (the code is fixed) and BOTH lanes get "ok, existing
        # account", so the reviewer reaches the code screen either way.
        assert 'if _is_review_email(_raw, os.environ.get("REVIEW_EMAIL", "")):' in MODAL_SRC

    def test_the_short_circuit_precedes_the_lane_and_terms_checks(self):
        short = MODAL_SRC.index('if _is_review_email(_raw, os.environ.get("REVIEW_EMAIL", "")):')
        lane  = MODAL_SRC.index('if _mode == "login" and is_new:')
        assert short < lane, (
            "the review address must be answered before the lane mismatch "
            "check, or 'I have an account' 403s before the account exists")

    def test_verify_code_checks_the_fixed_pair(self):
        assert '_review = _review_login_ok(_raw, _code, os.environ.get("REVIEW_EMAIL", ""),' in MODAL_SRC

    def test_the_verify_throttles_still_run_before_the_bypass(self):
        # The bypass must sit BEHIND the per-email/per-IP lockout, or the fixed
        # code becomes freely brute-forceable.
        throttle = MODAL_SRC.index('_okv, _rev = _throttle_allowed(throttle_store, f"codev:{ident}", _now)')
        bypass   = MODAL_SRC.index("_review = _review_login_ok(_raw, _code")
        assert throttle < bypass

    def test_the_reviewer_is_never_made_an_admin(self):
        # The Admin tab lists every user's email, so admin rights would turn a
        # demo login into a data leak.
        assert 'rec["review_account"] = True' in MODAL_SRC
        assert 'rec["role"] = "admin"' not in MODAL_SRC

    def test_credits_are_topped_up_past_what_was_already_spent(self):
        # Raising the limit above the AUTHORITATIVE consumed count is what
        # actually restores credits; resetting videos_used would not, since the
        # gate takes max(record, quota-key scan).
        assert 'rec["video_limit"] = _count_quota_used(quota_store, rec["uid"]) + REVIEW_VIDEO_LIMIT' in MODAL_SRC
