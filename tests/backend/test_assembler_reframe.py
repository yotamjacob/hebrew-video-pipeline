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

    def test_split_chain_geometry(self):
        chain = _fn("_split_chain")({"left": (0.25, 0.4), "right": (0.75, 0.45)}, 1920, 1080, 1080, 1920)
        # tiles 1080x960, source crops 960x853 (9:8 of half the width)
        assert "crop=960:854" in chain or "crop=960:852" in chain
        assert "scale=1080:960[tl]" in chain and "scale=1080:960[tr]" in chain
        assert chain.endswith("[tl][tr]vstack")
        # left speaker centered at x=0.25 -> crop x = 480-480 = 0; right -> 1440-480 = 960;
        # y = face y*1080 - 0.45*852, clamped
        assert "[sl]crop=960:852:0:49," in chain
        assert "[sr]crop=960:852:960:103," in chain


class TestCropExpr:
    def test_constant(self):
        assert _fn("_crop_x_expr")([(0.0, 0.5)], 1920, 1080) == "420"
        assert _fn("_crop_x_expr")([], 1920, 1080) == "420"

    def test_piecewise_linear_and_clamped(self):
        e = _fn("_crop_x_expr")([(0.0, 0.0), (2.0, 1.0)], 1920, 1080)
        assert e == "if(lt(t\\,2.000)\\,0+(840-0)*(t-0.000)/2.000\\,840)"


class TestReframeContracts:
    def test_wired_into_body_parts_only_and_canvas(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 22000]
        assert 'reframe = reframe if reframe in ("9:16", "fit") else None' in block
        assert "def _crop_dims(ci):" in block
        assert "cw, ch = _crop_dims(first_ci) or src_dims[first_ci]" in block
        assert "_reframe_samples(sources[ci], a, b, tmp" in block
        assert "pre = f\"crop={crop_w}:{crop_h}:x='{_crop_x_expr(plan, sw, crop_w)}':y=0\"" in block
        assert "logo=wm_path, pre=pre))" in block
        # intro/outro never get the tracked crop (only the "fit" pre)
        assert "_has_audio(src), fade, fade,\n" in block

    def test_smart_9_16_splits_two_speakers_and_tracks_one(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 26000]
        assert "rf_split[i] = _split_plan(smp" in block
        assert "split_mode = bool(rf_split.get(0))" in block
        assert "if (reframe == \"fit\" or split_mode) and _is_wide(src_dims[first_ci]):" in block
        assert "pre = _split_chain(rf_split[i], sw, sh, cw, ch)" in block
        # hook box moves to the seam so it never covers the top speaker
        assert 'hook["vertical_position"] = 46' in block

    def test_fit_mode_keeps_the_whole_frame_over_a_blurred_fill(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 24000]
        assert 'reframe = reframe if reframe in ("9:16", "fit") else None' in block
        assert "def _fit_dims(dims):" in block and "w = min(1080, sw)" in block
        # blur at 1/8 scale then upscale; foreground fitted to width, centered
        assert "gblur=sigma=4" in block and "[fg]scale={cw}:-2[fgs]" in block
        assert "[bgb][fgs]overlay=(W-w)/2:(H-h)/2" in block
        # applied to body parts and to landscape intro/outro alike
        assert "pre = _pre_for(src_dims[ci])" in block
        assert "pre=_pre_for(_probe_dims(src))" in block
        # every part now goes through -filter_complex (labeled sub-graphs)
        assert 'cmd += ["-filter_complex", f"[0:v]{norm_here}{vf_fade}[vout]", "-map", "[vout]", "-map", a_map]' in block

    def test_probe_dims_is_rotation_aware(self):
        i = MODAL_SRC.index("def _probe_dims")
        block = MODAL_SRC[i:i + 1600]
        assert "rotate" in block and "displaymatrix" in block.lower() or "rotation" in block
        assert "w, h = h, w" in block

    def test_route_forwards_reframe(self):
        i = MODAL_SRC.index('("/assembler/render"')
        block = MODAL_SRC[i:i + 7000]
        assert 'reframe = data.get("reframe") if data.get("reframe") in ("9:16", "fit") else None' in block
        assert "wm,\n                                          reframe)" in block
