"""
Re-editing a previous export (History → edit).

The feature rests on two backend guarantees, both tested here:
  1. a burn records the editor state that produced it, and
  2. the CUT SOURCE that state points at survives the 48h scratch sweep for as
     long as the job record lives (captions are baked into the output, so a
     re-edit can only burn onto the cut again).
"""
import time
from pathlib import Path

from tests.backend.conftest import MODAL_SRC, _extract_fn


def _record_job_fn(jobs_store, sent=None):
    """_record_job with ffprobe + FCM stubbed out."""
    class _Proc:
        stdout = "12.5"

    class _Subprocess:
        @staticmethod
        def run(*a, **k):
            return _Proc()

    ns = {
        "jobs_store": jobs_store,
        "subprocess": _Subprocess,
        "_UID_PREFIX_RE": __import__("re").compile(r"^u[0-9a-f]{32}__"),
        "_send_fcm": lambda *a, **k: (sent if sent is not None else []).append(a),
    }
    return _extract_fn(MODAL_SRC, "_record_job", extra_ns=ns)["_record_job"]


class TestRecordJobEditState:
    def test_burn_state_is_stored_under_edit(self, tmp_path):
        jobs = {}
        out = tmp_path / "out.mp4"
        out.write_bytes(b"x" * 10)
        fn = _record_job_fn(jobs)
        state = {"src_key": "u0__abc_cut.mp4", "captions": [{"text": "שלום"}],
                 "hook": {"text": "היי"}, "broll": [], "font": "Heebo",
                 "font_size": 48, "margin_v_pct": 0.08, "caption_style": {}}
        fn("u0__xyz_out.mp4", "clip.mp4", out, notify=False, edit_state=state)
        rec = jobs["u0__xyz_out.mp4"]
        assert rec["edit"] == state
        assert rec["name"] == "clip.mp4" and rec["duration"] == 12.5

    def test_no_edit_key_when_state_is_absent(self, tmp_path):
        """Cut-only/terminal jobs stay exactly as before - no edit affordance."""
        jobs = {}
        out = tmp_path / "out.mp4"
        out.write_bytes(b"x")
        _record_job_fn(jobs)("u0__xyz_cut.mp4", "clip.mp4", out, notify=False)
        assert "edit" not in jobs["u0__xyz_cut.mp4"]


def _prune_fn(tmp_dir, jobs_store):
    ns = {
        "TMP_DIR": str(tmp_dir),
        "jobs_store": jobs_store,
        "pending_store": {}, "progress_store": {}, "calls_store": {},
        "JOB_RETENTION_DAYS": 30,
        "SCRATCH_RETENTION_HOURS": 48,
        "CALL_RETENTION_SECONDS": 3600,
    }
    return _extract_fn(MODAL_SRC, "prune_volume", extra_ns=ns)["prune_volume"]


def _aged(path: Path, hours: float):
    """Create a file whose mtime is `hours` old."""
    path.write_bytes(b"data")
    old = time.time() - hours * 3600
    import os
    os.utime(path, (old, old))


class TestReEditSourceRetention:
    """The whole feature dies if the scratch sweep eats the cut it burns onto."""

    def test_cut_source_and_broll_survive_the_scratch_sweep(self, tmp_path):
        out_key = "u0__out1_out.mp4"
        src_key = "u0__src1_cut.mp4"
        broll   = "broll_" + "a" * 32 + ".mp4"
        for n in (out_key, src_key, broll):
            _aged(tmp_path / n, 72)          # all well past the 48h scratch window
        jobs = {out_key: {"ts": time.time(), "edit": {
            "src_key": src_key, "broll": [{"video_key": broll}]}}}

        _prune_fn(tmp_path, jobs)()

        assert (tmp_path / out_key).exists()
        assert (tmp_path / src_key).exists(), "re-edit source was swept"
        assert (tmp_path / broll).exists(),   "cached B-roll clip was swept"

    def test_unreferenced_cut_is_still_swept(self, tmp_path):
        """The pin is scoped to referenced files - ordinary scratch still goes."""
        out_key = "u0__out1_out.mp4"
        _aged(tmp_path / out_key, 1)
        _aged(tmp_path / "u0__orphan_cut.mp4", 72)
        jobs = {out_key: {"ts": time.time()}}

        _prune_fn(tmp_path, jobs)()

        assert not (tmp_path / "u0__orphan_cut.mp4").exists()

    def test_source_is_released_once_the_job_expires(self, tmp_path):
        """A 30-day-old job is deleted, and its source stops being protected."""
        out_key = "u0__out1_out.mp4"
        src_key = "u0__src1_cut.mp4"
        _aged(tmp_path / out_key, 24 * 40)
        _aged(tmp_path / src_key, 24 * 40)
        # A second, live job keeps jobs_store non-empty so the loss fail-safe
        # (empty store + outputs present = abort) does not skip the sweep.
        live = "u0__live_out.mp4"
        _aged(tmp_path / live, 1)
        jobs = {out_key: {"ts": time.time() - 31 * 86400,
                          "edit": {"src_key": src_key, "broll": []}},
                live:    {"ts": time.time()}}

        _prune_fn(tmp_path, jobs)()

        assert out_key not in jobs
        assert not (tmp_path / out_key).exists()
        assert not (tmp_path / src_key).exists(), "expired job's source leaked"


class TestBrollCacheFallback:
    """A re-edit can outlive the cached stock clip; the burn must re-fetch it
    from the (host-whitelisted) stock URL instead of silently dropping the
    insert. Guarded at source level - burn_captions_fn is not extractable."""

    def test_missing_cache_falls_through_to_the_url(self):
        body = MODAL_SRC[MODAL_SRC.index("def burn_captions_fn"):]
        body = body[:body.index("\ndef ")]
        i = body.index("cached = None")
        window = body[i:i + 900]
        assert "cached = _c if _c.exists() else None" in window
        assert "elif download_url:" in window
        # The old behaviour skipped the item outright on a cache miss.
        assert "if not src.exists():\n                    continue" not in window

    def test_invalid_broll_key_is_still_rejected_outright(self):
        body = MODAL_SRC[MODAL_SRC.index("def burn_captions_fn"):]
        i = body.index("cached = None")
        window = body[i:i + 600]
        assert "_BROLL_KEY_RE.match(vid_key)" in window
        assert "continue" in window


class TestEditStateIsRecordedAtBurn:
    """Route/worker wiring guard: the burn must actually pass the state through
    (the record helper is generic - nothing else proves the burn uses it)."""

    def test_burn_passes_edit_state_with_the_cut_key(self):
        i = MODAL_SRC.index("def burn_captions_fn")
        body = MODAL_SRC[i:MODAL_SRC.index("def _record_job")]
        call = body[body.index("_record_job("):]
        assert "edit_state={" in call
        assert '"src_key":       video_key' in call
        for field in ("captions", "hook", "broll", "font", "caption_style"):
            assert f'"{field}"' in call

    def test_jobs_list_exposes_the_editable_flag(self):
        assert '"editable": bool(meta.get("edit"))' in MODAL_SRC

    def test_edit_route_verifies_ownership_and_source(self):
        i = MODAL_SRC.index('path.rstrip("/").endswith("/edit")')
        route = MODAL_SRC[i:i + 2200]
        assert "_owned_key(key, uid)" in route
        assert "_owned_key(src_key, uid)" in route      # no cross-tenant source
        assert 'code="source_gone"' in route
