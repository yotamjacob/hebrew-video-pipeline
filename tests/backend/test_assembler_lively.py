"""Lively-conversation focus + badge (clips mode, 2026-09-24) - "focus more
on parts where the conversation is lively, ping pong, less monologue, and
badge them". The pure speaker-turn helpers are extracted and EXECUTED from
source (_speaker_labels, _turn_stats, _lively_badge, _voice_features), then
_pick_clips runs end-to-end against a fake Anthropic client."""

import json
import random
import sys
import types

import pytest

from tests.backend.conftest import MODAL_SRC


def _fn(name, ns=None):
    i = MODAL_SRC.index(f"def {name}")
    j = MODAL_SRC.index("\ndef ", i)
    ns = {} if ns is None else ns
    exec(MODAL_SRC[i:j], ns)
    return ns[name]


def _voices(n_a, n_b, sep=4.0, dim=12, seed=1):
    """Two synthetic fingerprint clouds, A then B alternating in blocks."""
    rnd = random.Random(seed)
    ca = [rnd.gauss(0, 1) for _ in range(dim)]
    cb = [c + (sep if j < 8 else 0.0) for j, c in enumerate(ca)]
    pt = lambda c: [x + rnd.gauss(0, 0.6) for x in c]
    feats, truth = [], []
    for k in range(max(n_a, n_b)):
        if k < n_a:
            feats.append(pt(ca)); truth.append("a")
        if k < n_b:
            feats.append(pt(cb)); truth.append("b")
    return feats, truth


class TestSpeakerLabels:
    def test_two_voices_split_cleanly_and_a_is_the_first_voice(self):
        feats, truth = _voices(20, 20)
        labels, meta = _fn("_speaker_labels")(feats, [3.0] * len(feats))
        assert meta["ok"] and meta["separation"] >= 1.3
        assert labels[0] == "A"
        want = {"a": labels[0], "b": "B" if labels[0] == "A" else "A"}
        assert labels == [want[t] for t in truth]
        assert 0.4 < meta["share_b"] < 0.6

    def test_one_voice_never_invents_a_second_speaker(self):
        rnd = random.Random(3)
        feats = [[rnd.gauss(0, 1) for _ in range(12)] for _ in range(40)]
        labels, meta = _fn("_speaker_labels")(feats, [3.0] * 40)
        assert not meta["ok"] and labels == [None] * 40

    def test_a_tiny_minority_voice_is_rejected(self):
        feats, _ = _voices(60, 2)          # 2 of 62 segments = 3% < 5%
        labels, meta = _fn("_speaker_labels")(feats, [3.0] * len(feats))
        assert not meta["ok"] and set(labels) == {None}

    def test_short_segments_are_labelled_but_not_fitted(self):
        feats, truth = _voices(12, 12)
        durs = [3.0] * len(feats)
        durs[1] = 0.9                      # too short to FIT on, still assigned
        feats.insert(2, None); durs.insert(2, 0.5); truth.insert(2, None)
        labels, meta = _fn("_speaker_labels")(feats, durs)
        assert meta["ok"]
        assert labels[2] is None           # no fingerprint -> no label
        assert labels[1] == "B" and labels[0] == "A"

    def test_too_few_segments_is_not_attempted(self):
        feats, _ = _voices(3, 3)
        labels, meta = _fn("_speaker_labels")(feats, [3.0] * 6)
        assert labels == [None] * 6 and meta["separation"] is None


def _segs(spec):
    """spec: [(speaker, duration)] back to back from t=0."""
    t, out = 0.0, []
    for k, d in spec:
        out.append({"start": t, "end": t + d, "text": "x", "spk": k})
        t += d
    return out


class TestTurnStats:
    def test_counts_speaker_changes_between_runs(self):
        segs = _segs([("A", 5), ("A", 4), ("B", 8), ("A", 6), ("B", 7)])   # 30 s, A B A B
        st = _fn("_turn_stats")(segs, 0, 30)
        assert st["turns"] == 3 and st["turns_per_min"] == 6.0 and st["labelled"]
        assert st["balance"] == round(15 / 30, 3)

    def test_a_short_blip_is_noise_not_two_turns(self):
        segs = _segs([("A", 10), ("B", 0.6), ("A", 10), ("B", 9)])
        st = _fn("_turn_stats")(segs, 0, 29.6)
        assert st["turns"] == 1            # A(20) -> B(9); the 0.6 s B blip dropped

    def test_a_monologue_has_no_turns(self):
        st = _fn("_turn_stats")(_segs([("B", 20), ("B", 20), ("B", 20)]), 0, 60)
        assert st["turns"] == 0 and st["balance"] == 0.0

    def test_clipped_to_the_window_and_unlabelled_segments_ignored(self):
        segs = _segs([("A", 10), (None, 5), ("B", 10), ("A", 10)])
        st = _fn("_turn_stats")(segs, 5, 30)     # A 5 s, B 10 s, A 5 s
        assert st["turns"] == 2 and st["balance"] == 0.5

    def test_no_labels_says_so(self):
        st = _fn("_turn_stats")(_segs([(None, 10), (None, 10)]), 0, 20)
        assert st == {"turns": 0, "turns_per_min": 0.0, "balance": 0.0, "labelled": False}


class TestLivelyBadge:
    def test_needs_the_model_and_the_audio(self):
        f = _fn("_lively_badge")
        good = {"labelled": True, "turns": 3, "turns_per_min": 4.0, "balance": 0.4}
        assert f(True, good)
        assert not f(False, good)                                   # the model said monologue
        assert not f(True, {**good, "turns": 1})                    # a single reply is no ping-pong
        assert not f(True, {**good, "turns_per_min": 1.0})          # two turns across 2 minutes
        assert not f(True, {**good, "balance": 0.1})                # one side barely talks

    def test_the_real_podcast_46_min_exchange_passes(self):
        # 2 turns in the 60 s window, balance 0.41 - the "יוגה לדת" back and
        # forth the stricter 3/min draft missed.
        assert _fn("_lively_badge")(True, {"labelled": True, "turns": 2, "turns_per_min": 2.0, "balance": 0.414})

    def test_without_labels_the_model_decides(self):
        f = _fn("_lively_badge")
        assert f(True, {"labelled": False})
        assert not f(False, {"labelled": False})


class TestVoiceFeatures:
    def test_two_synthetic_voices_fingerprint_apart(self, tmp_path):
        np = pytest.importorskip("numpy")
        import wave
        sr = 16000
        rng = np.random.default_rng(0)
        t = np.arange(int(sr * 3)) / sr

        def voice(f0):   # a harmonic buzz with a fixed spectral tilt per "speaker"
            x = sum(np.sin(2 * np.pi * f0 * k * t) / k ** (1.5 if f0 < 150 else 0.6) for k in range(1, 20))
            return x / np.abs(x).max() * 0.5 + rng.normal(0, 0.01, t.size)
        chunks, segs, t0 = [], [], 0.0
        for i in range(12):
            chunks.append(voice(110 if i % 2 == 0 else 230))
            segs.append({"start": t0, "end": t0 + 3.0})
            t0 += 3.0
        segs.append({"start": t0, "end": t0 + 0.5})    # under 0.8 s -> None
        chunks.append(np.zeros(int(sr * 0.5)))
        pcm = (np.concatenate(chunks) * 32767).astype(np.int16)
        p = tmp_path / "a.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
        feats = _fn("_voice_features")(p, segs)
        assert feats[-1] is None and all(len(f) == 12 for f in feats[:-1])
        labels, meta = _fn("_speaker_labels")(feats[:-1], [3.0] * 12)
        assert meta["ok"]
        assert labels == ["A", "B"] * 6


class TestPickClipsLively:
    """_pick_clips end-to-end with a fake model: the speaker tags reach the
    prompt, and the badge needs the model's flag AND measured turns."""

    def _run(self, pass1, segs):
        ns = {"json": json, "SONNET_MODEL": "m", "costs_store": None,
              "MAX_CANDIDATES": 12, "MIN_CANDIDATES": 3, "CLIP_MIN_SECONDS": 8, "CLIP_MAX_SECONDS": 90,
              "_record_ai_spend": lambda *a, **k: None,
              "_msg_text": lambda r: r.content[0].text}
        for n in ("_guidance_block", "_salvage_objects", "_parse_answer", "_snap_to_words", "_clamp_end",
                  "_virality_score", "_no_dash", "_turn_stats", "_lively_badge", "_lively_windows",
                  "_refine_batch", "_pick_clips"):
            _fn(n, ns)
        prompts = []

        class Msgs:
            def create(self, **kw):
                prompts.append(kw["messages"][0]["content"])
                text = pass1 if len(prompts) == 1 else json.dumps({"clips": []})
                return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)], usage=None, stop_reason="end")
        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda **kw: types.SimpleNamespace(messages=Msgs())
        old = sys.modules.get("anthropic")
        sys.modules["anthropic"] = fake
        import os
        os.environ.setdefault("ANTHROPIC_API_KEY", "x")
        try:
            clips = [{"name": "pod.mp4", "duration": 200.0, "segs": segs}]
            return ns["_pick_clips"](clips, 200.0), prompts
        finally:
            if old is None:
                del sys.modules["anthropic"]
            else:
                sys.modules["anthropic"] = old

    SEGS = ([{"start": i * 5.0, "end": i * 5.0 + 5.0, "text": f"מקטע {i}", "words": [],
              "spk": "AB"[(i // 2) % 2]} for i in range(12)]            # 0-60: A A B B A A ... ping-pong
            + [{"start": 60 + i * 5.0, "end": 65 + i * 5.0, "text": f"הרצאה {i}", "words": [],
                "spk": "B"} for i in range(12)])                         # 60-120: B monologue

    def test_tags_reach_the_prompt_and_the_badge_needs_both(self):
        pass1 = json.dumps({"summary": "s", "candidates": [
            {"clip": 0, "from_seg": 0, "to_seg": 11, "title": "פינג", "angle": "a",
             "lively": True, "lively_line": "אלינה ודריה בוויכוח ער — על מקורות היוגה"},
            {"clip": 0, "from_seg": 12, "to_seg": 23, "title": "מונו", "angle": "b",
             "lively": True, "lively_line": "טעות של המודל"},
            {"clip": 0, "from_seg": 2, "to_seg": 9, "title": "לא סומן", "angle": "c", "lively": False}]})
        (summary, out, errs), prompts = self._run(pass1, self.SEGS)
        assert "[0:0] 0.0-5.0: (A) מקטע 0" in prompts[0]
        assert "[0:2] 10.0-15.0: (B) מקטע 2" in prompts[0]
        assert "שיחה ערה קודמת למונולוג" in prompts[0] and '"lively": true' in prompts[0]
        by = {c["title"]: c for c in out}
        ping = by["פינג"]
        assert ping["lively"] is True
        assert ping["lively_line"] == "אלינה ודריה בוויכוח ער - על מקורות היוגה"   # no em dash
        assert ping["turns"]["measured"] and ping["turns"]["count"] >= 2
        # The model called the monologue lively - the measured turns veto it.
        assert by["מונו"]["lively"] is False and by["מונו"]["lively_line"] == ""
        assert by["מונו"]["turns"]["count"] == 0
        # Measured ping-pong but the model said no -> no badge.
        assert by["לא סומן"]["lively"] is False

    def test_unlabelled_transcript_has_no_tags_and_trusts_the_model(self):
        segs = [{k: v for k, v in s.items() if k != "spk"} for s in self.SEGS]
        pass1 = json.dumps({"summary": "s", "candidates": [
            {"clip": 0, "from_seg": 0, "to_seg": 11, "title": "t", "angle": "a", "lively": True, "lively_line": ""}]})
        (summary, out, errs), prompts = self._run(pass1, segs)
        assert "[0:0] 0.0-5.0: מקטע 0" in prompts[0]
        assert out[0]["lively"] is True and out[0]["turns"]["measured"] is False
        assert out[0]["lively_line"] == "שיחה הלוך ושוב בין הדוברים"   # a fallback, never empty

    def test_a_string_true_is_not_a_flag(self):
        pass1 = json.dumps({"summary": "s", "candidates": [
            {"clip": 0, "from_seg": 0, "to_seg": 11, "title": "t", "angle": "a", "lively": "yes"}]})
        (summary, out, errs), _ = self._run(pass1, self.SEGS)
        assert out[0]["lively"] is False


    def test_measured_exchanges_are_listed_and_pass2_sees_speaker_changes(self):
        segs = [dict(s, words=[[s["start"] + 0.1, s["start"] + 1.0, "מילה"]]) for s in self.SEGS]
        pass1 = json.dumps({"summary": "s", "candidates": [
            {"clip": 0, "from_seg": 0, "to_seg": 11, "title": "t", "angle": "a", "lively": True, "lively_line": "x"}]})
        (summary, out, errs), prompts = self._run(pass1, segs)
        assert "זוהו לפי חילופי הקול קטעי הלוך ושוב (שניות): 0-" in prompts[0]
        # Pass 2: a "(B)" mark where the voice changes + the keep-the-exchange note.
        assert "(B) [10.10]מילה" in prompts[1] and "(A) [0.10]מילה" in prompts[1]
        assert "החיתוך צריך לשמור על חילופי הדוברים" in prompts[1]


class TestLivelyWindows:
    def test_merges_overlapping_exchange_windows_and_skips_monologue(self):
        segs = TestPickClipsLively.SEGS          # 0-60 ping-pong, 60-120 monologue
        w = _fn("_lively_windows", {"_turn_stats": _fn("_turn_stats"), "_lively_badge": _fn("_lively_badge")})(segs)
        assert len(w) == 1 and w[0][0] == 0.0 and w[0][1] <= 105
        assert _fn("_lively_windows", {})([{k: v for k, v in s.items() if k != "spk"} for s in segs]) == []


class TestAnalyzeWiring:
    def test_clips_mode_labels_speakers_best_effort_and_persists_them(self):
        i = MODAL_SRC.index("def analyze_story")
        block = MODAL_SRC[i:MODAL_SRC.index("_asm_words_path(key).write_text", i)]
        assert "if clips_mode and len(segs) >= 8:" in block
        assert "_speaker_labels(" in block and "_voice_features(wav, segs)" in block
        assert 'sg["spk"] = lb' in block
        assert "speaker labels skipped" in block      # never fails the analysis
