"""Story Assembler per-clip social caption + hashtags (2026-09-24).

The clip's words are sliced server-side from the persisted transcript to the
clip's CURRENT range (after the trim steppers), regrouped into the segment
shape the shared generate_caption_options reads, and the model's answer is
cleaned by a post-step (dashes, line count, exactly-5 hashtags). Pure
helpers executed from source + route / function contracts."""

import json

from tests.backend.conftest import MODAL_SRC


def _fn(name):
    """Exec one top-level function from source; the slice stops at the next
    top-level def OR decorator (a Modal function may follow directly)."""
    i = MODAL_SRC.index(f"\ndef {name}(") + 1
    j = min(k for k in (MODAL_SRC.find("\ndef ", i), MODAL_SRC.find("\n@", i)) if k != -1)
    ns = {}
    exec(MODAL_SRC[i:j], ns)
    return ns[name]


SEGS = [
    {"start": 10.0, "end": 14.0, "text": "שלום לכולם היום",
     "words": [[10.5, 11.0, " שלום"], [11.2, 12.0, " לכולם"], [12.1, 13.8, " היום"]]},
    {"start": 20.0, "end": 24.0, "text": "נדבר על נשימה",
     "words": [[20.2, 21.0, " נדבר"], [21.1, 21.6, " על"], [21.7, 23.5, " נשימה"]]},
    {"start": 40.0, "end": 42.0, "text": "מחוץ לקליפ",
     "words": [[40.1, 41.0, " מחוץ"], [41.1, 41.9, " לקליפ"]]},
]


class TestWordSlicing:
    def test_slices_to_the_trimmed_range_and_regroups_by_segment(self):
        out = _fn("_social_segments")(SEGS, 11.1, 21.65)
        # first segment loses "שלום" (trimmed start), second loses "נשימה"
        assert out == [{"start": 11.2, "end": 13.8, "text": "לכולם היום"},
                       {"start": 20.2, "end": 21.6, "text": "נדבר על"}]

    def test_end_is_clamped_and_grace_keeps_a_word_starting_on_the_edge(self):
        out = _fn("_social_segments")(SEGS, 10.47, 11.1)          # 30 ms before "שלום"
        assert out == [{"start": 10.5, "end": 11.0, "text": "שלום"}]
        out = _fn("_social_segments")(SEGS, 20.0, 22.0)
        assert out[-1]["end"] == 22.0                              # "נשימה" ends after the trim

    def test_nothing_in_range_is_empty(self):
        assert _fn("_social_segments")(SEGS, 30.0, 35.0) == []
        assert _fn("_social_segments")([], 0, 10) == []
        assert _fn("_social_segments")([{"start": 0, "end": 5, "text": "x"}], 0, 10) == []

    def test_payload_shape_is_what_the_caption_generator_reads(self):
        out = _fn("_social_segments")(SEGS, 10.0, 25.0)
        payload = json.dumps(out, ensure_ascii=False)
        # generate_caption_options joins c.get("text") of json.loads(captions_json)
        transcript = " ".join(c.get("text", "") for c in json.loads(payload)).strip()
        assert transcript == "שלום לכולם היום נדבר על נשימה"
        assert all(set(c) == {"start", "end", "text"} for c in out)


class TestCleanSocial:
    def test_dashes_lines_and_hashtags(self):
        clean = _fn("_clean_social")({
            "caption": "שורה ראשונה — חזקה\nשורה שנייה – עוד\nשורה שלישית\nשורה רביעית\n#יוגה #נשימה",
            "hashtags": ["#יוגה", "מדיטציה", "#Yoga Life", "#נשימה", "#רוגע", "#שקט", "#עוד"],
        })
        assert "—" not in clean["caption"] and "–" not in clean["caption"]
        assert clean["caption"] == "שורה ראשונה - חזקה\nשורה שנייה - עוד\nשורה שלישית"
        assert clean["hashtags"] == ["#יוגה", "#מדיטציה", "#Yoga_Life", "#נשימה", "#רוגע"]

    def test_hashtags_as_a_string_or_only_inside_the_caption(self):
        c = _fn("_clean_social")({"caption": "טקסט\n#אחד #שניים", "hashtags": "#שלוש"})
        assert c == {"caption": "טקסט", "hashtags": ["#שלוש", "#אחד", "#שניים"]}
        assert _fn("_clean_social")("סתם טקסט") == {"caption": "סתם טקסט", "hashtags": []}


class TestPromptAndGenerator:
    def _gen(self):
        i = MODAL_SRC.index("def generate_caption_options")
        return MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]

    def test_assembler_prompt_contract(self):
        p = _fn("_assembler_social_prompt")("טקסט", "כותרת | הוק", "instagram,tiktok", True)
        assert "GENDER-NEUTRAL" in p and "exactly 5" in p and "2-3 short lines" in p
        assert "do NOT copy them into the caption" in p and "כותרת | הוק" in p
        assert "never use em or en dashes" in p and "instagram,tiktok" in p
        assert "CONTEXT" not in _fn("_assembler_social_prompt")("טקסט", "", "", False)

    def test_main_editor_default_is_unchanged(self):
        block = self._gen()
        assert ('def generate_caption_options(captions_json: str, video_key: str = "", platforms: str = "",\n'
                '                             flavor: str = "", context: str = "") -> dict:') in block
        # the default path keeps its prompt, spend tag and return shape
        assert "- Ends with 4-6 hashtags on the final line" in block
        assert '_record_ai_spend(costs_store, "post_caption", SONNET_MODEL, resp.usage)' in block
        assert 'return {"caption": result.get("caption", "")}' in block

    def test_assembler_flavor_spend_and_post_step(self):
        block = self._gen()
        assert 'if flavor == "assembler":' in block
        assert '_record_ai_spend(costs_store, "assembler_social", SONNET_MODEL, resp.usage)' in block
        assert "return _clean_social(parsed)" in block


class TestRoutesAndFunction:
    def _route(self):
        i = MODAL_SRC.index('("/assembler/social-caption"')
        return MODAL_SRC[i:MODAL_SRC.index('("/assembler/render"', i)]

    def test_post_uid_scopes_and_guards(self):
        block = self._route()
        assert "_SAFE_KEY_RE.match(key)" in block
        assert "if vk and not _owned_key(vk, uid):" in block          # frames only from the caller's own clip
        assert "if not (0 <= start < end) or end - start > 600:" in block
        assert 'assembler_social_caption.spawn(f"{uprefix}{key}", start, end, vk, context)' in block
        assert "_record_call(call)" in block

    def test_non_object_bodies_are_a_400(self):
        assert "if not isinstance(data, dict):     # a JSON list / string must be a 400, not a 500" in self._route()
        i = MODAL_SRC.index('("/assembler/render"')
        assert "if not isinstance(data, dict):" in MODAL_SRC[i:MODAL_SRC.index('"/assembler/render-poll/"', i)]

    def test_poll_enforces_call_ownership(self):
        block = self._route()
        i = block.index('"/assembler/social-caption-poll/"')
        assert "if not _call_owned(call_id):" in block[i:i + 400]

    def test_function_slices_then_calls_the_shared_generator(self):
        i = MODAL_SRC.index("def assembler_social_caption")
        block = MODAL_SRC[i:MODAL_SRC.index("\ndef ", i)]
        assert "wp = _asm_words_path(upload_key)" in block
        assert "sliced = _social_segments(segs, start, end)" in block
        assert 'return {"error": "no_transcript"}' in block and 'return {"error": "no_words"}' in block
        assert ('generate_caption_options.local(json.dumps(sliced, ensure_ascii=False), video_key or "",\n'
                '                                          "instagram,tiktok", "assembler"') in block
        # new backend function registered on the light image with the Anthropic secret
        head = MODAL_SRC[MODAL_SRC.rindex("@app.function(", 0, i):i]
        assert "image=light_image" in head and "anthropic-secret" in head
