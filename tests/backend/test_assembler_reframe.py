"""Auto-reframe 16:9 -> 9:16 (2026-08-16): the pure planner and crop
expression executed from source + wiring contracts."""

from tests.backend.conftest import MODAL_SRC


def _fn(name):
    i = MODAL_SRC.index(f"def {name}")
    j = MODAL_SRC.index("\ndef ", i)
    ns = {}
    exec(MODAL_SRC[i:j], ns)
    ns.setdefault("REFRAME_MAX_KEYFRAMES", 30)
    return ns[name]


def _plan():
    f = _fn("_reframe_plan")
    return f


CROP = 9 / 16 / (16 / 9)   # crop_frac of a 16:9 frame -> ~0.316


class TestReframePlan:
    def test_static_speaker_is_a_single_constant_track(self):
        faces = [(0.7, 0.4, 0.08, 0.14)]
        samples = [(i / 3, faces) for i in range(90)]      # 30 s, face at x=0.7
        kf = _plan()(samples, CROP)
        assert kf[0] == (0.0, 0.7) and kf[-1][1] == 0.7
        assert len(kf) <= 3                                # start (+ end), no wobble

    def test_micro_motion_inside_the_dead_zone_does_not_pan(self):
        samples = [(i / 3, [(0.6 + 0.02 * ((-1) ** i), 0.4, 0.08, 0.14)]) for i in range(60)]
        kf = _plan()(samples, CROP)
        assert all(abs(c - kf[0][1]) < 1e-6 for _t, c in kf)

    def test_speaker_change_pans_smoothly_and_rate_limited(self):
        left = [(0.25, 0.4, 0.08, 0.14)]
        right = [(0.75, 0.4, 0.08, 0.14)]
        samples = [(i / 3, left) for i in range(30)] + [(10 + i / 3, right) for i in range(30)]
        kf = _plan()(samples, CROP, max_speed=0.30)
        # Ends on the right speaker (clamped so the crop stays inside the frame).
        assert abs(kf[-1][1] - 0.75) < 0.02
        # A 0.5-wide move at 0.30/s takes >= 1.6 s: not an instant cut. The
        # keyframes are line ENDPOINTS: the pan runs from the last point still
        # on the left speaker to the first point on the right one.
        t_start = max(t for t, c in kf if c < 0.26)
        t_done = min(t for t, c in kf if c > 0.74)
        assert t_done - t_start >= 1.5

    def test_two_shot_that_fits_stays_a_group_center(self):
        # Two faces 0.2 apart fit inside a 0.316-wide crop -> center between them.
        faces = [(0.4, 0.4, 0.06, 0.1), (0.6, 0.4, 0.06, 0.1)]
        kf = _plan()([(i / 3, faces) for i in range(30)], CROP)
        assert abs(kf[0][1] - 0.5) < 1e-6

    def test_far_apart_two_shot_follows_the_largest_face(self):
        faces = [(0.15, 0.4, 0.06, 0.1), (0.85, 0.4, 0.10, 0.16)]   # right one larger
        kf = _plan()([(i / 3, faces) for i in range(30)], CROP)
        assert abs(kf[0][1] - (1 - CROP / 2)) < 1e-3              # clamped to the right edge (4-dp rounding)

    def test_lost_face_holds_then_drifts_to_center(self):
        samples = [(i / 3, [(0.8, 0.4, 0.08, 0.14)]) for i in range(15)] + [(5 + i / 3, []) for i in range(90)]
        kf = _plan()(samples, CROP)                 # default hold = 6 s
        def at(tt):
            # piecewise-linear value at tt
            for (t0, c0), (t1, c1) in zip(kf, kf[1:]):
                if t0 <= tt <= t1:
                    return c0 + (c1 - c0) * (tt - t0) / max(1e-6, t1 - t0)
            return kf[-1][1]
        # Still on the speaker 5 s after losing the face (hold, no drift).
        assert abs(at(10.0) - 0.8) < 0.02
        # Well after `hold`, back around the middle.
        assert abs(at(34.9) - 0.5) < 0.05

    def test_detection_gap_is_bridged_by_interpolation(self):
        # Face at 0.3 for 3 s, lost for 4 s, found again at 0.7: the crop
        # pans across the gap instead of freezing then jumping.
        samples = ([(i / 3, [(0.3, 0.4, 0.08, 0.14)]) for i in range(9)]
                   + [(3 + i / 3, []) for i in range(12)]
                   + [(7 + i / 3, [(0.7, 0.4, 0.08, 0.14)]) for i in range(9)])
        kf = _plan()(samples, CROP)
        def at(tt):
            for (t0, c0), (t1, c1) in zip(kf, kf[1:]):
                if t0 <= tt <= t1:
                    return c0 + (c1 - c0) * (tt - t0) / max(1e-6, t1 - t0)
            return kf[-1][1]
        assert 0.4 < at(5.0) < 0.6          # mid-gap: mid-way
        assert abs(at(9.5) - 0.7) < 0.06        # within the dead zone

    def test_glitch_frame_is_ignored_and_keyframes_are_capped(self):
        good = [(0.3, 0.4, 0.08, 0.14)]
        samples = [(i / 3, good) for i in range(60)]
        samples[20] = (20 / 3, [(0.9, 0.4, 0.08, 0.14)])          # one-frame glitch
        kf = _plan()(samples, CROP)
        assert all(abs(c - 0.3) < 1e-6 for _t, c in kf)
        wild = [(i / 3, [(0.2 + 0.6 * ((i // 4) % 2), 0.4, 0.08, 0.14)]) for i in range(300)]
        assert len(_plan()(wild, CROP)) <= 30

    def test_empty_samples(self):
        assert _plan()([], CROP) == [(0.0, 0.5)]


class TestSplitPlan:
    def _two(self, l=0.25, r=0.75, n=30, miss_right_every=0):
        out = []
        for i in range(n):
            faces = [(l, 0.4, 0.08, 0.14)]
            if not miss_right_every or i % miss_right_every:
                faces.append((r, 0.45, 0.08, 0.14))
            out.append((i / 3, faces))
        return out

    def test_two_far_speakers_split(self):
        p = _fn("_split_plan")(self._two(), CROP)
        assert p and abs(p["left"][0] - 0.25) < 1e-6 and abs(p["right"][0] - 0.75) < 1e-6
        assert abs(p["left"][1] - 0.4) < 1e-6 and abs(p["right"][1] - 0.45) < 1e-6

    def test_two_close_faces_prefer_the_group_crop(self):
        assert _fn("_split_plan")(self._two(0.42, 0.58), CROP) is None   # fit in one crop

    def test_one_speaker_or_flaky_second_is_not_a_split(self):
        one = [(i / 3, [(0.5, 0.4, 0.08, 0.14)]) for i in range(30)]
        assert _fn("_split_plan")(one, CROP) is None
        # right speaker seen in only ~1/3 of samples -> below presence floor
        flaky = self._two(miss_right_every=1)
        flaky = [(t, [f for f in faces if f[0] < 0.5] + ([f for f in faces if f[0] >= 0.5] if i % 3 == 0 else []))
                 for i, (t, faces) in enumerate(self._two())]
        assert _fn("_split_plan")(flaky, CROP) is None


class TestCropExpr:
    def test_constant(self):
        assert _fn("_crop_x_expr")([(0.0, 0.5)], 1920, 1080) == "420"
        assert _fn("_crop_x_expr")([], 1920, 1080) == "420"

    def test_piecewise_linear_and_clamped(self):
        e = _fn("_crop_x_expr")([(0.0, 0.0), (2.0, 1.0)], 1920, 1080)
        assert e == "if(lt(t\\,2.000)\\,0+(840-0)*(t-0.000)/2.000\\,840)"




# ── Shot-aware reframe v2 (2026-09-24) ─────────────────────────────────────

_V2 = ["_reframe_plan", "_crop_x_expr", "_split_plan", "_rf_median", "_rf_floor",
       "_zoom_box", "_group_box", "_center_spec", "_shot_cuts", "_face_tracks",
       "_mouth_score", "_speaker_timeline", "_drift_runs", "_hold_runs",
       "_track_pieces", "_plan_shot", "_reframe_confidence", "_seg_upscale",
       "_merge_segments", "_speaker_stability", "_plan_window", "_report_segments",
       "_reframe_summary", "_chrome_line", "_find_pane", "_fit_tile_in_pane", "_rf_ts",
       "_fit_graph", "_seg_filter", "_segments_chain"]


def _v2_ns():
    """Every v2 planner + the REFRAME_* constants in ONE namespace (they
    call each other and use the constants as defaults)."""
    ns = {}
    i = MODAL_SRC.index("# ── Auto-reframe")
    exec(MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)], ns)
    for name in _V2:
        a = MODAL_SRC.index(f"\ndef {name}(") + 1
        exec(MODAL_SRC[a:MODAL_SRC.index("\ndef ", a)], ns)
    return ns


NS = _v2_ns()
SW, SH = 1920, 1080
FPS = 3


def face(cx, cy=0.4, w=0.08, h=0.14, talk=None):
    """A detection; talk=True/False adds mouth/upper diffs (speaking or
    listening), None = no mouth data."""
    if talk is None:
        return (cx, cy, w, h)
    return (cx, cy, w, h, 8.0 if talk else 1.5, 1.0)


def box_cx(seg):
    x, _y, w, _h = seg["box"]
    return (x + w / 2.0) / SW


def seconds(n, fps=FPS):
    return [i / float(fps) for i in range(int(n * fps))]


class TestShotCuts:
    def test_cut_is_the_exact_frame_and_flashes_collapse(self):
        scores = [(i / 30.0, 0.02) for i in range(300)]
        scores += [(5.033333, 0.8), (7.0, 0.5), (7.1, 0.6), (0.2, 0.9), (9.8, 0.9)]
        assert NS["_shot_cuts"](scores, 10.0) == [5.033333, 7.1]

    def test_head_turn_scores_are_not_cuts(self):
        assert NS["_shot_cuts"]([(i / 30.0, 0.12) for i in range(300)], 10.0) == []


class TestTwoShotHardCut:
    CUT = 5.033333

    def _samples(self):
        # Shot 1: wide, face at x=0.3; shot 2 (a camera cut): close-up at x=0.7.
        return [(t, [face(0.3)] if t < self.CUT else [face(0.7, 0.45, 0.2, 0.40)])
                for t in seconds(10)]

    def test_crop_jumps_exactly_at_the_cut_with_nothing_in_between(self):
        segs, rep = NS["_plan_window"](self._samples(), [self.CUT], 10.0, SW, SH)
        assert len(segs) == 2 and rep["shots"] == 2
        assert segs[0]["t1"] == self.CUT == segs[1]["t0"] and segs[1]["cut"]
        assert all(s["kind"] == "crop" for s in segs)          # no ease / pan keyframes
        assert abs(box_cx(segs[0]) - 0.3) < 0.01
        assert segs[1]["box"][3] == SH                          # close-up -> full height
        assert abs(box_cx(segs[1]) - 0.7) < 0.01
        chain = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 10.0, [self.CUT])
        assert "[r0]trim=end=5.0332," in chain and "[r1]trim=start=5.0332," in chain
        assert "(t-" not in chain                               # no interpolation anywhere
        assert chain.endswith("[g0][g1]concat=n=2:v=1:a=0,tpad=stop_mode=clone:stop_duration=0.5,trim=duration=10.0000")

    def test_samples_straddling_the_cut_never_leak_into_the_other_shot(self):
        smp = self._samples()
        smp[15] = (5.0, [face(0.7, 0.45, 0.2, 0.40)])           # the fps grid caught the new shot early
        segs, _rep = NS["_plan_window"](smp, [self.CUT], 10.0, SW, SH)
        assert abs(box_cx(segs[0]) - 0.3) < 0.01


class TestStaticSpeaker:
    def test_zero_pan_keyframes_one_static_crop(self):
        smp = [(t, [face(0.7 + 0.01 * ((-1) ** i), talk=True)]) for i, t in enumerate(seconds(30))]
        segs, rep = NS["_plan_window"](smp, [], 30.0, SW, SH)
        assert len(segs) == 1 and segs[0]["kind"] == "crop" and segs[0]["role"] == "track"
        assert rep["speaker_switches"] == 0 and rep["mode"] == "track"
        chain = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 30.0)
        assert "if(lt(t" not in chain and "trim=start" not in chain and "trim=end" not in chain

    def test_re_aim_is_a_hard_cut_after_leaving_the_band_for_1_5_s(self):
        smp = [(t, [face(0.3 if t < 6 else 0.6)]) for t in seconds(14)]
        smp += [(14 + t, [face(0.6)]) for t in seconds(6)]
        segs, _rep = NS["_plan_window"](smp, [], 20.0, SW, SH)
        assert [s["kind"] for s in segs] == ["crop", "crop"]
        assert segs[1]["t0"] == 6.0                             # cut where it LEFT the band
        assert abs(box_cx(segs[0]) - 0.3) < 0.01 and abs(box_cx(segs[1]) - 0.6) < 0.01

    def test_brief_excursion_under_1_5_s_does_not_re_aim(self):
        smp = [(t, [face(0.55 if 5 <= t < 6.2 else 0.3)]) for t in seconds(12)]
        segs, _rep = NS["_plan_window"](smp, [], 12.0, SW, SH)
        assert len(segs) == 1

    def test_steady_drift_still_eases(self):
        # The yoga case: the subject walks 0.3 -> 0.6 over 9 s.
        smp = [(t, [face(0.3 + 0.3 * min(1.0, max(0.0, (t - 1.5) / 9.0)))]) for t in seconds(12)]
        segs, _rep = NS["_plan_window"](smp, [], 12.0, SW, SH)
        eases = [s for s in segs if s["kind"] == "ease"]
        assert len(eases) == 1
        cs = [c for _t, c in eases[0]["kf"]]
        assert cs == sorted(cs) and cs[-1] - cs[0] > 0.2


class TestAlternatingSpeakers:
    A, B = 0.38, 0.62          # close enough that no split, too far for one group crop

    def _samples(self, talks_a):
        return [(t, [face(self.A, talk=talks_a(i, t)), face(self.B, talk=not talks_a(i, t))])
                for i, t in enumerate(seconds(12))]

    def test_switches_follow_mouth_motion_and_ignore_a_blip_inside_the_hold(self):
        # A talks to 4 s, B to 8 s (with a 0.67 s A interjection 0.67 s
        # after B took over - inside the 1.5 s hold), then A again.
        smp = self._samples(lambda i, t: t < 4 - 1e-9 or 14 <= i <= 15 or t >= 8 - 1e-9)
        ids, _n = NS["_face_tracks"](smp)
        tl = NS["_speaker_timeline"](smp, ids, [0, 1], 12.0)
        assert [(round(a, 3), k) for a, _b, k in tl["spans"]] == [(0.0, 0), (4.0, 1), (8.0, 0)]
        assert tl["switches"] == 2 and tl["suppressed"] >= 1 and tl["margin"] > 0.3

        segs, rep = NS["_plan_window"](smp, [], 12.0, SW, SH)
        assert [s["role"] for s in segs] == ["speaker"] * 3 and rep["speaker_switches"] == 2
        assert [s["t0"] for s in segs] == [0.0, 4.0, 8.0]
        assert [round(box_cx(s), 2) for s in segs] == [self.A, self.B, self.A]
        assert all(s["kind"] == "crop" for s in segs)          # hard cuts between speakers

    def test_upper_face_motion_never_counts_as_talking(self):
        # Field (2026-09-24 podcast): blinks / glasses glints in the upper
        # third made "lower - upper" crown the listener. The lower third
        # alone decides; values are on the sampler's normalized scale.
        smp = [(t, [(0.38, 0.4, 0.08, 0.14, 0.25, 0.05), (0.62, 0.4, 0.08, 0.14, 0.10, 0.40)])
                for t in seconds(8)]
        ids, _n = NS["_face_tracks"](smp)
        tl = NS["_speaker_timeline"](smp, ids, [0, 1], 8.0)
        assert tl["spans"] == [(0.0, 8.0, 0)]
        # a still pair (both at the listener floor) is no evidence at all
        still = [(t, [(0.38, 0.4, 0.08, 0.14, 0.10, 0.0), (0.62, 0.4, 0.08, 0.14, 0.09, 0.0)])
                 for t in seconds(8)]
        ids, _n = NS["_face_tracks"](still)
        assert NS["_speaker_timeline"](still, ids, [0, 1], 8.0)["switches"] == 0

    def test_a_wanted_switch_waits_for_the_hold(self):
        # B talks only 1 s; A resumes at 5 s -> the switch back lands at 4 + 1.5.
        smp = self._samples(lambda i, t: not (4 - 1e-9 <= t < 5 - 1e-9))
        ids, _n = NS["_face_tracks"](smp)
        tl = NS["_speaker_timeline"](smp, ids, [0, 1], 12.0, excursion=0.0)   # hold mechanics alone
        starts = [a for a, _b, _k in tl["spans"]]
        assert starts == [0.0, 4.0, 5.5]
        assert all(b - a >= 1.5 - 1e-9 for a, b in zip(starts, starts[1:]))

    def test_split_tiles_are_zoomed_and_the_speaker_is_emphasized(self):
        smp = [(t, [face(0.25, h=0.12, talk=t < 6), face(0.75, h=0.12, talk=t >= 6)])
               for t in seconds(12)]
        segs, rep = NS["_plan_window"](smp, [], 12.0, SW, SH)
        assert len(segs) == 1 and segs[0]["kind"] == "split" and rep["speaker_switches"] == 1
        (_xl, _yl, wl, hl), (_xr, _yr, wr, hr) = segs[0]["tiles"]
        assert hl == hr == 492 and abs(wl / hl - 9 / 8) < 0.01          # 0.12 x 3.8 x 1080, 9:8
        assert rep["segments"][0]["upscale_factor"] == round(960 / 492, 2)
        chain = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 12.0)
        assert chain.count("scale=1080:960") == 2 and "vstack" in chain
        assert "eq=eval=frame" in chain                                  # listener dimmed
        plain = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 12.0, dim=False)
        assert "eq=eval=frame" not in plain


class TestLowConfidence:
    def test_sparse_detections_fall_back_to_fit(self):
        smp = [(t, [face(0.5)] if i % 4 == 0 else []) for i, t in enumerate(seconds(20))]
        segs, rep = NS["_plan_window"](smp, [], 20.0, SW, SH)
        assert rep["mode"] == "fit" and rep["reason"] == "low_confidence"
        assert rep["confidence"] < 0.45 and rep["coverage"] == 0.25
        assert segs == [{"kind": "fit", "role": "fit", "t0": 0.0, "t1": 20.0, "cut": False}]
        chain = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 20.0)
        assert "gblur=sigma=4" in chain and "overlay=(W-w)/2:(H-h)/2" in chain

    def test_confidence_terms(self):
        conf = NS["_reframe_confidence"]
        assert conf(1.0, 3, 30.0) == 1.0
        assert conf(1.0, 30, 30.0) == 0.75               # 1 s shots -> frantic edit
        assert conf(0.9, 1, 30.0, stability=0.5) == 0.45


class TestZoom:
    def test_face_is_about_a_quarter_of_the_crop_and_above_center(self):
        x, y, w, h = NS["_zoom_box"](0.14, 0.5, 0.4, SW, SH)
        assert 0.22 <= 0.14 * SH / h <= 0.30
        assert abs(w / h - 9 / 16) < 0.01 and h % 2 == 0 and w % 2 == 0
        assert abs((0.4 * SH - y) / h - 0.40) < 0.01

    def test_floor_cap_and_upscale_guard(self):
        assert NS["_zoom_box"](0.05, 0.5, 0.4, SW, SH)[3] == 486          # 0.45 x 1080 floor
        assert NS["_zoom_box"](0.40, 0.5, 0.4, SW, SH)[3] == 1080         # full height cap
        assert NS["_rf_floor"](720) == 0.60 and NS["_rf_floor"](1080) == 0.45
        smp = [(t, [face(0.5, h=0.05)]) for t in seconds(6)]
        segs, rep = NS["_plan_window"](smp, [], 6.0, 1280, 720)
        assert segs[0]["box"][3] == 432                                   # 0.60 x 720
        assert rep["segments"][0]["upscale_factor"] == round(1920 / 432, 2)

    def test_close_group_is_zoomed_not_full_height(self):
        smp = [(t, [face(0.45, w=0.06, h=0.1), face(0.55, w=0.06, h=0.1)]) for t in seconds(6)]
        segs, rep = NS["_plan_window"](smp, [], 6.0, SW, SH)
        assert rep["mode"] == "group"
        x, y, w, h = segs[0]["box"]
        assert h < SH and abs(w / h - 9 / 16) < 0.01
        assert x <= (0.42 - 0.04) * SW + 1 and x + w >= (0.58 + 0.04) * SW - 1   # 25% margin kept


class TestSegmentIntegrity:
    def _seg(self, t0, t1, **kw):
        return dict({"kind": "crop", "role": "track", "box": (0, 0, 608, 1080), "t0": t0, "t1": t1}, **kw)

    def test_rejects_gaps_overlaps_uncovered_and_missed_cuts(self):
        import pytest
        chain = NS["_segments_chain"]
        with pytest.raises(ValueError):
            chain([self._seg(0.0, 4.0), self._seg(4.1, 10.0)], SW, SH, 1080, 1920, 10.0)   # gap
        with pytest.raises(ValueError):
            chain([self._seg(0.0, 4.2), self._seg(4.0, 10.0)], SW, SH, 1080, 1920, 10.0)   # overlap
        with pytest.raises(ValueError):
            chain([self._seg(0.0, 9.0)], SW, SH, 1080, 1920, 10.0)                          # uncovered
        with pytest.raises(ValueError):
            chain([self._seg(0.0, 10.0)], SW, SH, 1080, 1920, 10.0, cuts=[5.033333])       # cut lost
        with pytest.raises(ValueError):
            chain([], SW, SH, 1080, 1920, 10.0)

    def test_every_cut_boundary_lands_on_the_cuts_own_frame(self):
        ts = NS["_rf_ts"]
        for cut, fps in ((5.033333, 30), (2.5, 60), (1.004167, 240), (3.0, 25)):
            b = float(ts(cut))
            assert cut - 1.0 / fps < b <= cut        # trim start <= cut frame, previous frame excluded
        segs = [self._seg(0.0, 5.033333), self._seg(5.033333, 10.0, cut=True)]
        out = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 10.0, cuts=[5.033333])
        assert "trim=end=5.0332," in out and "trim=start=5.0332," in out

    def test_a_cut_inside_a_fit_fallback_is_allowed(self):
        segs = [{"kind": "fit", "role": "fit", "t0": 0.0, "t1": 10.0}]
        out = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 10.0, cuts=[5.033333])
        assert "gblur" in out

    def test_every_kind_ends_in_concat_clone_pad_and_exact_trim(self):
        # ffmpeg 5.1 dropped the last frame after a vstack/overlay (split /
        # fit) at 24/25/30 fps sources without the concat + tpad + trim tail.
        for segs in ([{"kind": "fit", "role": "fit", "t0": 0.0, "t1": 4.0}],
                     [self._seg(0.0, 4.0)],
                     [self._seg(0.0, 1.2), {"kind": "split", "role": "split", "t0": 1.2, "t1": 4.0,
                                            "tiles": [(0, 0, 553, 492), (1000, 0, 553, 492)], "speak": []}]):
            out = NS["_segments_chain"](segs, SW, SH, 1080, 1920, 4.0)
            assert out.endswith(f"concat=n={len(segs)}:v=1:a=0,tpad=stop_mode=clone:stop_duration=0.5,trim=duration=4.0000")

    def test_merge_absorbs_slivers_but_never_a_cut(self):
        segs = [self._seg(0.0, 3.0), self._seg(3.0, 3.1), self._seg(3.1, 6.0, cut=True),
                self._seg(6.0, 6.05, cut=True), self._seg(6.05, 9.0)]
        out = NS["_merge_segments"](segs, [3.1, 6.0])
        assert [(s["t0"], s["t1"]) for s in out] == [(0.0, 3.1), (3.1, 6.0), (6.0, 9.0)]


class TestReframeContracts:
    def _render(self):
        i = MODAL_SRC.index("def render_story")
        return MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]

    def test_canvas_is_always_1080x1920_and_windows_use_the_segment_chain(self):
        block = self._render()
        assert 'reframe = reframe if reframe in ("9:16", "fit") else None' in block
        assert "cw, ch = REFRAME_CANVAS" in block
        assert "rf_plans[i] = _reframe_window(sources[ci], a, b, sw, sh, tmp" in block
        assert 'pre = _segments_chain(pl["segs"], sw, sh, cw, ch, b - a, pl["cuts"])' in block
        assert "logo=wm_path, pre=pre))" in block
        # intro/outro never get the tracked crop (only the "fit" pre)
        assert "_has_audio(src), fade, fade,\n" in block
        assert "pre=_pre_for(_probe_dims(src))" in block
        assert NS["REFRAME_CANVAS"] == (1080, 1920)

    def test_report_rides_the_render_result(self):
        block = self._render()
        assert 'result["reframe_report"] = _reframe_summary(' in block
        # split: captions go ON the seam, the hook just above it (2026-09-24)
        assert 'hook["vertical_position"] = 36 if events else 46' in block and 'sg["kind"] == "split"' in block
        assert "margin_v_pct = _seam_margin_pct(cw, ch, font_size)" in block

    def test_fit_mode_keeps_the_whole_frame_over_a_blurred_fill(self):
        block = self._render()
        assert "def _fit_dims(dims):" in block and "w = min(1080, sw)" in block
        assert "return _fit_graph(cw, ch)" in block
        g = NS["_fit_graph"](1080, 1920)
        assert "gblur=sigma=4" in g and "[fg]scale=1080:-2[fgs]" in g
        assert g.endswith("[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
        # every part goes through -filter_complex (labeled sub-graphs)
        assert 'cmd += ["-filter_complex", f"[0:v]{norm_here}{vf_fade}[vout]", "-map", "[vout]", "-map", a_map]' in block

    def test_sampler_is_one_decode_with_frame_accurate_scene_scores(self):
        i = MODAL_SRC.index("def _reframe_samples")
        block = MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
        assert "[fa]fps={fps},scale=960:-2[fs]" in block
        assert "select='gte(scene\\\\,0)',metadata=print:file={scene_txt}" in block
        assert "def _reframe_samples(src, a, b, workdir, tag, fps=REFRAME_SAMPLE_FPS):" in block

    def test_probe_dims_is_rotation_aware(self):
        i = MODAL_SRC.index("def _probe_dims")
        block = MODAL_SRC[i:i + 1600]
        assert "rotate" in block and "displaymatrix" in block.lower() or "rotation" in block
        assert "w, h = h, w" in block

    def test_route_forwards_reframe(self):
        i = MODAL_SRC.index('("/assembler/render"')
        block = MODAL_SRC[i:MODAL_SRC.index('"/assembler/render-poll/"', i)]
        assert 'reframe = data.get("reframe") if data.get("reframe") in ("9:16", "fit") else None' in block
        # reframe, then the caption style (2026-09-24) - positional, in
        # render_story's parameter order
        assert ("wm,\n                                          reframe, st_font, st_size, st_margin, st_cs, st_hs,"
                "\n                                          st_warn)") in block


# ── Pane finder / hysteresis / stability (2026-09-24, first real podcast) ──

def _two_pane_frame(divider=True, W=320, H=180):
    """Branded Zoom-style layout: black title bar with text (rows 0-29),
    two textured panes (rows 30-149, split at x=158-159 by a black divider
    when `divider`), a blown-out white window above the left face, and a
    black name bar with text (rows 150-179). RGB24 bytes."""
    buf = bytearray(W * H * 3)
    for y in range(H):
        for x in range(W):
            if 30 <= y < 150:
                if divider and 158 <= x < 160:
                    c = (0, 0, 0)
                elif x < 158 and 32 <= y < 46 and 10 <= x < 150:
                    c = (255, 255, 255)                          # overexposed window = content
                elif x < 159:
                    v = (x * 37 + y * 91) % 120 + 60
                    c = (v, v - 20, v - 40)
                else:
                    v = (x * 53 + y * 29) % 110 + 70
                    c = (v - 30, v, v - 10)
            elif (10 <= y < 20 and 100 <= x < 220) or (160 <= y < 170 and 60 <= x < 260):
                c = (0, 220, 255) if (x // 3) % 2 else (0, 0, 0)   # bar text
            else:
                c = (0, 0, 0)
            i = (y * W + x) * 3
            buf[i:i + 3] = bytes(max(0, min(255, v)) for v in c)
    return bytes(buf), W, H


class TestPaneFinder:
    LEFT_FACE = (0.25, 0.5, 0.1, 0.2)

    def test_two_pane_frame_with_name_bar_and_divider(self):
        buf, W, H = _two_pane_frame()
        l, t, r, b = NS["_find_pane"](buf, W, H, self.LEFT_FACE)
        assert (round(l * W), round(t * H), round(r * W), round(b * H)) == (0, 30, 158, 150)
        l, t, r, b = NS["_find_pane"](buf, W, H, (0.75, 0.5, 0.1, 0.2))
        assert (round(l * W), round(t * H), round(r * W), round(b * H)) == (160, 30, 320, 150)

    def test_no_divider_runs_to_the_frame_edge_and_white_is_not_chrome(self):
        buf, W, H = _two_pane_frame(divider=False)
        l, t, r, b = NS["_find_pane"](buf, W, H, self.LEFT_FACE)
        assert round(t * H) == 30 and round(b * H) == 150       # the blown-out window did not stop it
        assert l == 0.0 and r == 1.0

    def test_chrome_line_calibration_from_the_real_podcast(self):
        line = NS["_chrome_line"]
        # its black bars (luma 0-10, std 0-4 with compression noise)
        assert line([(v, v, v) for v in [0, 3, 8, 1, 6, 0, 2, 9] * 8])
        # a black stove pipe in the guest's room (luma ~28, std ~7.5) is content
        assert not line([(v, v, v) for v in [20, 36, 22, 34, 28, 18, 38, 28] * 8])
        # a flat brand-colour bar is chrome; a blown-out white window is not
        assert line([(30, 90, 200)] * 60) and not line([(255, 255, 255)] * 60)

    def test_a_dark_object_spanning_the_pane_is_not_a_pane_edge(self):
        buf, W, H = _two_pane_frame()
        b = bytearray(buf)
        for y in range(30, 150):                     # stove pipe at x=30-33, full pane height
            for x in range(30, 34):
                v = 28 + (-9, -3, 4, 9, -6, 2, 7, -4)[(3 * y + x) % 8]
                b[(y * W + x) * 3:(y * W + x) * 3 + 3] = bytes((v, v, v))
        l, t, r, b_ = NS["_find_pane"](bytes(b), W, H, self.LEFT_FACE)
        assert l == 0.0 and round(r * W) == 158

    def test_detection_failure_returns_none(self):
        W, H = 64, 36
        black = bytes(W * H * 3)
        assert NS["_find_pane"](black, W, H, (0.5, 0.5, 0.2, 0.3)) is None

    def test_tile_is_constrained_to_the_pane_and_fills_when_short(self):
        pane = (0.0, 30 / 180.0, 158 / 320.0, 150 / 180.0)      # 1280x720 source: y 120..600
        face = (0.25, 0.75, 0.08, 0.12)                          # low in the pane
        (x, y, w, h), fill = NS["_fit_tile_in_pane"]((120, 360, 405, 360), pane, face, 1280, 720)
        assert not fill and (w, h) == (405, 360)
        assert 120 <= y and y + h <= 600 and 0 <= x and x + w <= 632
        short = (0.0, 0.3, 0.5, 0.3 + 300 / 720.0)
        (x, y, w, h), fill = NS["_fit_tile_in_pane"]((120, 200, 405, 360), short, face, 1280, 720)
        assert fill and h == 300 and y >= 216 and y + h <= 516
        assert NS["_fit_tile_in_pane"]((1, 2, 404, 360), None, face, 1280, 720) == ((1, 2, 404, 360), False)

    def test_filled_tile_uses_a_blurred_copy_never_black(self):
        seg = {"kind": "split", "role": "split", "t0": 0.0, "t1": 6.0, "speak": [],
               "tiles": [(0, 216, 405, 300), (700, 100, 405, 360)], "fill": [True, False]}
        chain = NS["_segments_chain"]([seg], 1280, 720, 1080, 1920, 6.0)
        assert "[s0a]crop=405:300:0:216,split=2[s0ab][s0af]" in chain
        assert "gblur=sigma=4,eq=brightness=-0.06,scale=1080:960[s0abb]" in chain
        assert "force_original_aspect_ratio=decrease:force_divisible_by=2[s0afs]" in chain
        assert "[s0b]crop=405:360:700:100,scale=1080:960[s0tb]" in chain
        assert "pad=" not in chain.replace("tpad=", "")           # no black padding anywhere

    def test_report_carries_pane_bounds_and_fill(self):
        seg = {"kind": "split", "role": "split", "t0": 0.0, "t1": 6.0, "speak": [],
               "tiles": [(0, 216, 405, 300), (700, 100, 405, 360)], "fill": [True, False],
               "panes": [(0.0, 0.1667, 0.4938, 0.8333), None]}
        row = NS["_report_segments"]([seg], 1280, 720, 1080, 1920)[0]
        assert row["pane_bounds"] == [[0.0, 0.1667, 0.4938, 0.8333], None]
        assert row["tile_fill"] == [True, False]


class TestSpeakerHysteresis:
    def _pair(self, a_score, b_score, n=12):
        return [(t, [(0.38, 0.4, 0.08, 0.14, a_score(t), 0.0), (0.62, 0.4, 0.08, 0.14, b_score(t), 0.0)])
                for t in seconds(n, fps=6)]

    def test_a_laugh_that_barely_outscores_the_talker_never_flips(self):
        # Talker steady at 0.20; the listener laughs to 0.24 for 1.2 s: a
        # normalized lead of 0.09 < 0.15 -> no switch.
        smp = self._pair(lambda t: 0.20, lambda t: 0.24 if 5 <= t < 6.2 else 0.10)
        ids, _n = NS["_face_tracks"](smp)
        tl = NS["_speaker_timeline"](smp, ids, [0, 1], 12.0)
        assert tl["switches"] == 0 and tl["suppressed"] == 0

    def test_a_short_excursion_over_ongoing_speech_is_merged_back(self):
        # The 30:00 case: the talker keeps going (0.17) while the listener
        # laughs / interjects hard (0.30) for 2 s -> no tile flip at all...
        smp = self._pair(lambda t: 0.17, lambda t: 0.30 if 5 <= t < 7 else 0.10)
        ids, _n = NS["_face_tracks"](smp)
        tl = NS["_speaker_timeline"](smp, ids, [0, 1], 12.0)
        assert tl["switches"] == 0 and tl["suppressed"] >= 1
        # ...while a 4 s turn is a real exchange and switches out and back.
        smp = self._pair(lambda t: 0.17 if not 4 <= t < 8 else 0.08, lambda t: 0.30 if 4 <= t < 8 else 0.10)
        ids, _n = NS["_face_tracks"](smp)
        assert NS["_speaker_timeline"](smp, ids, [0, 1], 12.0)["switches"] == 2

    def test_a_real_handover_still_switches(self):
        smp = self._pair(lambda t: 0.22 if t < 6 else 0.10, lambda t: 0.10 if t < 6 else 0.25)
        ids, _n = NS["_face_tracks"](smp)
        tl = NS["_speaker_timeline"](smp, ids, [0, 1], 12.0)
        assert [k for _a, _b, k in tl["spans"]] == [0, 1] and abs(tl["spans"][1][0] - 6.0) < 0.6


class TestSpeakerStability:
    def test_margin_alone_never_pushes_a_good_window_into_fit(self):
        stab = NS["_speaker_stability"]
        assert stab(0.0, 10) == 0.7 and stab(0.3, 0) == 1.0 and stab(None, 0) == 0.7
        assert NS["_reframe_confidence"](1.0, 3, 30.0, stab(0.0, 10)) >= 0.45
        # Plan level: two speaker-tracked faces with IDENTICAL mouth scores
        # (margin 0) and full coverage stay tracked.
        smp = [(t, [(0.38, 0.4, 0.08, 0.14, 0.2, 0.0), (0.62, 0.4, 0.08, 0.14, 0.2, 0.0)])
               for t in seconds(20)]
        segs, rep = NS["_plan_window"](smp, [], 20.0, SW, SH)
        assert rep["mode"] == "speaker" and rep["margin"] == 0.0 and rep["confidence"] >= 0.45
