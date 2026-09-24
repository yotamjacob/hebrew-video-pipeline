"""_captions_for_windows - the assembler's transcript->output-timeline remap.

The captions on a story cut are only right if every kept window's words are
shifted by exactly the accumulated output time before it (storyboard order,
not chronological). Extracted and EXECUTED from source (pure function)."""

from tests.backend.conftest import MODAL_SRC


def _fn():
    """_captions_for_windows with its helpers: _clean_caption_word and the
    REAL _fix_rtl_punct (pipeline_core) - the main pipeline's cue rules."""
    import re
    ns = {"_RTL_LEAD_PUNCT_RE": re.compile(r'^([?!.،؟]+)\s+(.+)$', re.DOTALL)}
    for name in ("_fix_rtl_punct", "_clean_caption_word", "_captions_for_windows"):
        i = MODAL_SRC.index(f"\ndef {name}(") + 1
        exec(MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)], ns)
    return ns["_captions_for_windows"]


SEGS_CLIP0 = [
    {"start": 10.0, "end": 14.0, "text": "שלום לכולם",
     "words": [[10.5, 11.0, "שלום"], [11.2, 12.0, "לכולם"]]},
    {"start": 30.0, "end": 33.0, "text": "מחוץ לחלון",
     "words": [[30.2, 31.0, "מחוץ"], [31.1, 32.0, "לחלון"]]},
]
SEGS_CLIP1 = [
    {"start": 5.0, "end": 8.0, "text": "בין הגפנים",
     "words": [[5.1, 6.0, "בין"], [6.2, 7.0, "הגפנים"]]},
]


class TestCaptionRemap:
    def test_single_window_shifts_to_zero_based_output_time(self):
        events = _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0])
        assert len(events) == 1
        e = events[0]
        assert e["words"][0][0] == 0.5           # 10.5 - 10.0
        assert e["words"][1][1] == 2.0           # 12.0 - 10.0
        assert e["text"] == "שלום לכולם"
        assert e["end"] <= 4.0                   # clamped inside the window

    def test_storyboard_order_accumulates_offsets_across_clips(self):
        # Storyboard: clip1's moment FIRST (5-8), then clip0's (10-14).
        events = _fn()([(1, 5.0, 8.0), (0, 10.0, 14.0)],
                       [SEGS_CLIP0, SEGS_CLIP1])
        assert len(events) == 2
        assert events[0]["words"][0][0] == 0.1               # 5.1 - 5.0
        assert events[1]["words"][0][0] == 3.0 + 0.5         # win1 len + (10.5-10)
        assert events[1]["start"] >= 3.0                      # never overlaps window 1

    def test_words_outside_the_window_are_excluded(self):
        # Window covers only the first segment - the 30s segment must not leak.
        events = _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0])
        assert all("לחלון" not in e["text"] for e in events)

    def test_empty_words_produce_no_event(self):
        events = _fn()([(0, 0.0, 5.0)], [[{"start": 1, "end": 2, "text": "x", "words": []}]])
        assert events == []


# ── Caption style passthrough (2026-09-24) ─────────────────────────────────

def _style_ns():
    """_sanitize_caption_style + friends with their module constants."""
    ns = {}
    i = MODAL_SRC.index("# ── Caption style for the assembler render")
    exec(MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)], ns)
    for name in ("_sanitize_caption_style", "_caption_ass_args", "_apply_hook_style"):
        a = MODAL_SRC.index(f"\ndef {name}(") + 1
        exec(MODAL_SRC[a:MODAL_SRC.index("\ndef ", a)], ns)
    return ns


def _builder():
    """The REAL shared build_caption_ass (as test_ass_generation loads it)."""
    from tests.backend.conftest import _extract_fn, _build_ns
    ns = _build_ns()
    ns.update({'_fix_rtl_punct': lambda s: s, '_censor_caption_text': lambda s: s,
               '_rtl_ass_text': lambda s: s, '_RLE': '', '_PDF': ''})
    return _extract_fn(MODAL_SRC, 'build_caption_ass', extra_ns=ns)['build_caption_ass']


ST = _style_ns()
CW, CH = 1080, 1920
HOOK = {"text": "שורת הוק", "start_seconds": 0.2, "duration_seconds": 4.5}


class TestByteIdenticalDefault:
    def test_no_style_builds_exactly_the_historical_ass(self):
        events = _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0])
        build = _builder()
        # the literal call render_story made before the style work
        before = build(CW, CH, "Heebo", 48, max(25, CW // 14), int(0.08 * CH), events, HOOK)
        f, s, m, cs, hs, w = ST["_sanitize_caption_style"]()
        assert (f, s, m, cs, hs, w) == (None, None, None, None, None, [])
        after = build(*ST["_caption_ass_args"](CW, CH, f, s, m), events, ST["_apply_hook_style"](HOOK, hs))
        assert after == before
        assert ST["_caption_ass_args"](CW, CH) == (CW, CH, "Heebo", 48, max(25, CW // 14), int(0.08 * CH))

    def test_render_story_calls_the_builder_the_old_way_when_nothing_is_chosen(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
        assert "ass_args = _caption_ass_args(cw, ch, font, font_size, margin_v_pct)" in block
        assert "if caption_style is None:\n                    ass_str = build_caption_ass(*ass_args, events, hook)" in block
        assert "hook = _apply_hook_style(hook, hook_style)" in block
        assert '"style_warnings": warnings_out' in block


class TestStylePassthrough:
    def test_profile_style_reaches_the_ass(self):
        f, s, m, cs, hs, w = ST["_sanitize_caption_style"](
            "Rubik", 60, 0.12,
            {"font_color": "#FFE45C", "border_color": "#112233", "border_size": 3,
             "bg_color": "#000000", "bg_opacity": 0.5, "mode": "classic",
             "progress_bar": True, "auto_zoom": True, "zooms": [[1, 2]]},
            {"font": "SecularOne", "font_color": "#C26D4B", "bg_opacity": 0.3, "font_size_pct": 120})
        assert w == []
        assert "progress_bar" not in cs and "auto_zoom" not in cs and "zooms" not in cs   # editor-only effects
        args = ST["_caption_ass_args"](CW, CH, f, s, m)
        assert args[2:4] == ("Rubik", 60) and args[5] == int(0.12 * CH)
        hook = ST["_apply_hook_style"](HOOK, hs)
        assert hook["font"] == "SecularOne" and hook["start_seconds"] == 0.2      # timing untouched
        ass = _builder()(*args, _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0]), hook, caption_style=cs)
        style = [l for l in ass.splitlines() if l.startswith("Style: Default")][0]
        assert "Rubik" in style and "&H005CE4FF" in style                        # font + #FFE45C (ABGR)
        assert "SecularOne" in ass                                                # hook box font

    def test_invalid_values_fall_back_and_are_reported(self):
        f, s, m, cs, hs, w = ST["_sanitize_caption_style"](
            "Comic Sans", 999, "far", {"font_color": "red", "border_size": 50, "mode": "wave",
                                        "bg_opacity": 3, "evil": "x"},
            {"font": "Rubik", "font_size_pct": 10})
        assert (f, s, m) == ("Heebo", 48, 0.08)
        assert cs == {"font_color": "#FFFFFF", "border_size": 2, "mode": "classic", "bg_opacity": 0.0}
        assert hs == {"font": "Heebo", "font_size_pct": 100}                       # Rubik is not a hook font
        fields = {x["field"]: x for x in w}
        assert set(fields) == {"font", "font_size", "margin_v_pct", "caption_style.font_color",
                               "caption_style.border_size", "caption_style.mode",
                               "caption_style.bg_opacity", "hook_style.font", "hook_style.font_size_pct"}
        assert fields["font"] == {"field": "font", "value": "Comic Sans", "fallback": "Heebo"}
        assert ST["_sanitize_caption_style"](caption_style="invalid")[3] is None   # a non-dict body

    def test_margin_range_is_the_editors_drag_range(self):
        # A real saved profile (2026-09-24) sits at 0.3307 - it must pass
        # untouched; the editor drags 0.03-0.80.
        f, s, m, cs, hs, w = ST["_sanitize_caption_style"](margin_v_pct=0.33065051077841956)
        assert m == 0.33065051077841956 and w == []
        assert ST["_sanitize_caption_style"](margin_v_pct=0.8)[2] == 0.8
        _f, _s, m, _cs, _hs, w = ST["_sanitize_caption_style"](margin_v_pct=0.95)
        assert m == 0.08 and w[0]["field"] == "margin_v_pct"

    def test_route_validates_and_forwards_positionally(self):
        i = MODAL_SRC.index('("/assembler/render"')
        block = MODAL_SRC[i:MODAL_SRC.index('"/assembler/render-poll/"', i)]
        assert 'data.get("font"), data.get("font_size"), data.get("margin_v"),' in block
        assert '_opt_dict(data.get("caption_style")), _opt_dict(data.get("hook_style"))' in block
        assert "reframe, st_font, st_size, st_margin, st_cs, st_hs," in block


class TestKaraokeAndWordModesFromAssemblerEvents:
    """The assembler's events carry per-word timings (_captions_for_windows),
    so both word-timed modes render from them - they stay in the selector."""

    def _events(self):
        return _fn()([(0, 10.0, 14.0), (1, 5.0, 8.0)], [SEGS_CLIP0, SEGS_CLIP1])

    def test_every_event_has_one_timing_per_token(self):
        for e in self._events():
            assert len(e["words"]) == len(e["text"].split())

    def test_karaoke_highlights_each_word_on_its_own_timing(self):
        ass = _builder()(CW, CH, "Heebo", 48, max(25, CW // 14), int(0.08 * CH), self._events(), {},
                         caption_style={"mode": "karaoke", "highlight_color": "#FFD400"})
        dia = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(dia) >= 4 and sum("\\c&H00D4FF&" in l for l in dia) >= 4    # 4 words highlighted in turn
        assert any(l.startswith("Dialogue: 0,0:00:00.50") for l in dia)         # the first word's own start

    def test_word_pop_shows_one_word_per_event(self):
        ass = _builder()(CW, CH, "Secular One", 48, max(25, CW // 14), int(0.08 * CH), self._events(), {},
                         caption_style={"mode": "word"})
        dia = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        texts = [l.split(",", 9)[9] for l in dia]
        assert len(dia) == 4 and all(len(t.split()) == 1 for t in texts)


class TestPresetMirror:
    def test_assembler_page_presets_are_app_js_verbatim(self):
        # site/assembler.html loads no app.js, so it mirrors CAPTION_PRESETS -
        # the two array bodies must stay identical (whitespace-normalized).
        from tests.backend.conftest import ROOT
        import re

        def body(path):
            src = (ROOT / path).read_text(encoding="utf-8")
            i = src.index("const CAPTION_PRESETS = [")
            return re.sub(r"\s+", " ", src[i:src.index("];", i)])
        assert body("site/assembler.html") == body("site/app.js")


# ── Main-pipeline cue rules, split-seam placement, karaoke RTL (2026-09-24) ──

def _one(name):
    i = MODAL_SRC.index(f"\ndef {name}(") + 1
    ns = {}
    exec(MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)], ns)
    return ns[name]


class TestPipelineCueRules:
    """The assembler builds caption cues like generate_ass (the main
    pipeline): cleaned words, one line per cue, pause splits, RTL punct."""

    def _seg(self, words):
        return [{"start": words[0][0], "end": words[-1][1], "text": " ".join(w[2] for w in words), "words": words}]

    def test_edge_punctuation_is_cleaned_like_the_pipeline(self):
        clean = _one("_clean_caption_word")
        assert clean("כדת,") == "כדת" and clean("אומרת.") == "אומרת" and clean(" ־-שלום:") == "־-שלום"
        assert clean("באמת?") == "באמת?" and clean("וואו!") == "וואו!" and clean("—") == ""
        ev = _fn()([(0, 0.0, 5.0)], [self._seg([[0.0, 0.4, "זאת"], [0.4, 0.8, "אומרת,"], [0.8, 1.0, "יש"],
                                                [1.0, 1.4, "אנשים"], [1.4, 1.9, "כדת,"]])])
        assert ev[0]["text"] == "זאת אומרת יש אנשים כדת"                  # no trailing comma for karaoke to flip
        assert [w[2] for w in ev[0]["words"]] == ev[0]["text"].split()

    def test_one_line_per_cue_and_a_new_cue_on_every_pause(self):
        words = [[i * 0.3, i * 0.3 + 0.25, w] for i, w in enumerate("אחת שתיים שלוש ארבע חמש שש שבע שמונה".split())]
        words[4] = [words[4][0] + 0.5, words[4][1] + 0.5, words[4][2]]      # 0.55 s pause before "חמש"
        ev = _fn()([(0, 0.0, 10.0)], [self._seg(words)], max_chars=14)
        assert all(len(e["text"]) <= 14 for e in ev)
        assert ev[0]["text"] == "אחת שתיים שלוש" and ev[1]["text"] == "ארבע"   # line full, then the pause
        assert ev[2]["words"][0][2] == "חמש"
        assert ev[0]["end"] == words[2][1]                                  # cue ends on its last word

    def test_cues_never_span_source_segments_and_leading_punct_is_fixed(self):
        # two source segments 50 ms apart: still two cues (generate_ass never
        # merges across segments); the Whisper leading-"?" quirk is repaired
        segs = self._seg([[0.0, 0.3, "שלום"]]) + self._seg([[0.35, 0.6, "?"], [0.6, 0.9, "מה"], [0.9, 1.2, "נשמע"]])
        ev = _fn()([(0, 0.0, 5.0)], [segs])
        assert [e["text"] for e in ev] == ["שלום", "מה נשמע?"]

    def test_max_chars_mirrors_generate_ass(self):
        mc = _one("_caption_max_chars")
        assert mc(1080) == 32 and mc(608) == 18 and mc(100) == 8


class TestSplitSeamPlacement:
    def test_a_one_line_caption_centers_on_the_seam(self):
        seam = _one("_seam_margin_pct")
        assert seam(1080, 1920) == round((960 - 0.6 * 48) / 1920, 4)
        assert seam(1080, 1920, 90) == round((960 - 0.6 * 90) / 1920, 4)
        # bottom margin + half a line = half the canvas
        m = seam(1080, 1920, 90)
        assert abs(m * 1920 + 0.6 * 90 - 960) < 1

    def test_render_story_moves_captions_to_the_seam_and_the_hook_above_it(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
        assert ('if events and any(sg["kind"] == "split" for pl in rf_plans.values() for sg in pl.get("segs") or []):\n'
                '            margin_v_pct = _seam_margin_pct(cw, ch, font_size)') in block
        assert 'hook["vertical_position"] = 36 if events else 46' in block
        assert "events = _captions_for_windows(windows, clip_segments, _caption_max_chars(cw))" in block


class TestKaraokeRtl:
    def test_only_the_caption_style_gets_encoding_minus_one(self):
        fix = _one("_rtl_karaoke_ass")
        ass = ("[V4+ Styles]\nStyle: Default,Heebo,71,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,77,77,153,1\n"
               "Style: Hook,Heebo,81,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,0,0,8,20,20,0,1\n"
               "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,x\n")
        out = fix(ass)
        assert "Style: Default,Heebo,71,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,77,77,153,-1" in out
        assert "Style: Hook,Heebo,81,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,0,0,8,20,20,0,1" in out
        assert out.count("\n") == ass.count("\n")

    def test_applied_only_when_karaoke_is_chosen(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
        assert ('if caption_style.get("mode") == "karaoke":\n'
                '                        ass_str = _rtl_karaoke_ass(ass_str)') in block
        # the default path never reaches it (byte-identical)
        j = block.index("if caption_style is None:")
        assert "_rtl_karaoke_ass" not in block[j:block.index("else:", j)]

    def test_the_real_builder_ass_carries_it(self):
        ev = _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0])
        ass = _one("_rtl_karaoke_ass")(_builder()(CW, CH, "Heebo", 48, max(25, CW // 14), int(0.08 * CH), ev, {},
                                                  caption_style={"mode": "karaoke"}))
        style = [l for l in ass.splitlines() if l.startswith("Style: Default")][0]
        assert style.endswith(",-1")


class TestGereshGlue:
    """Whisper splits loan words at the geresh; the assembler applies the main
    pipeline's _glue_split_tokens rule (user review 2026-09-24: word-pop
    showed "׳ונסון" alone)."""

    def test_glue_rule_mirrors_the_pipeline(self):
        g = _one("_glue_split_words")
        assert g([[0, 1, "ג"], [1, 2, "׳ונסון"]]) == [[0, 2, "ג׳ונסון"]]
        assert g([[0, 1, "ולצ"], [1, 2, "'יקון,"]]) == [[0, 2, "ולצ'יקון,"]]
        assert g([[0, 1, "צ"], [1, 2, "’יפס"]]) == [[0, 2, "צ’יפס"]]
        # not glued: a lone geresh, a non-Hebrew predecessor, gershayim / a quote
        assert g([[0, 1, "ג"], [1, 2, "׳"]]) == [[0, 1, "ג"], [1, 2, "׳"]]
        assert g([[0, 1, "rock"], [1, 2, "'n"]]) == [[0, 1, "rock"], [1, 2, "'n"]]
        assert g([[0, 1, "אמר"], [1, 2, "״שלום"]]) == [[0, 1, "אמר"], [1, 2, "״שלום"]]
        once = g([[0, 1, "ג"], [1, 2, "׳ונסון"]])
        assert g(once) == once                                             # idempotent

    def test_segments_are_repaired_with_their_text(self):
        segs = [{"start": 0, "end": 3, "text": "אמר ג ׳ונסון", "words": [[0, 1, "אמר"], [1, 2, "ג"], [2, 3, "׳ונסון"]]},
                {"start": 4, "end": 5, "text": "שלום", "words": [[4, 5, "שלום"]]}]
        ns = {}
        for name in ("_glue_split_words", "_glued_segments"):
            i = MODAL_SRC.index(f"\ndef {name}(") + 1
            exec(MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)], ns)
        out = ns["_glued_segments"](segs)
        assert out[0]["text"] == "אמר ג׳ונסון" and [w[2] for w in out[0]["words"]] == ["אמר", "ג׳ונסון"]
        assert out[1] is segs[1]                                           # untouched segment passes through
        assert segs[0]["text"] == "אמר ג ׳ונסון"                           # input not mutated

    def test_word_pop_shows_the_whole_name(self):
        ns = {}
        for name in ("_glue_split_words", "_glued_segments"):
            i = MODAL_SRC.index(f"\ndef {name}(") + 1
            exec(MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)], ns)
        segs = ns["_glued_segments"]([{"start": 0, "end": 2, "text": "ג ׳ונסון", "words": [[0.0, 0.4, "ג"], [0.4, 1.2, "׳ונסון"]]}])
        ass = _builder()(CW, CH, "Secular One", 90, max(25, CW // 14), int(0.33 * CH),
                         _fn()([(0, 0.0, 2.0)], [segs]), {}, caption_style={"mode": "word"})
        texts = [l.split(",", 9)[9] for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(texts) == 1 and "ג׳ונסון" in texts[0]

    def test_applied_at_analyze_render_and_social(self):
        assert "segs = _glued_segments(segs)   # the pipeline's geresh repair" in MODAL_SRC
        i = MODAL_SRC.index("def render_story")
        assert "clip_segments.append(_glued_segments(" in MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
        i = MODAL_SRC.index("def assembler_social_caption")
        assert "segs = _glued_segments(json.loads(" in MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
