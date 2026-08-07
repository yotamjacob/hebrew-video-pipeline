"""Transcript proofread tests - the LLM spelling pass must never corrupt
the word list: same count, same order, and any failure keeps the original
words (timestamps depend on it).
"""
import json

from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
# The phonetic guard helpers are called from inside proofread_words - extract
# them into the same namespace or the call NameErrors and chunks keep originals.
proofread_words = _extract_fn(
    MODAL_SRC, "proofread_words", "_consonant_key", "_phonetically_plausible",
    extra_ns=_ns)["proofread_words"]


class _FakeClient:
    """Stand-in for anthropic.Anthropic - returns queued raw responses."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                raw = outer._responses.pop(0)
                if isinstance(raw, Exception):
                    raise raw

                class _Block:
                    text = raw

                class _Resp:
                    content = [_Block()]

                return _Resp()

        self.messages = _Messages()


def test_corrections_applied_in_order():
    words = ["שלום", "עולמ", "טוב"]
    client = _FakeClient([json.dumps(["שלום", "עולם", "טוב"], ensure_ascii=False)])
    out = proofread_words(words, client, "model-x")
    assert out == ["שלום", "עולם", "טוב"]
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "model-x"
    assert client.calls[0]["thinking"] == {"type": "disabled"}


def test_wrong_count_keeps_original():
    words = ["אחת", "שתיים", "שלוש"]
    client = _FakeClient([json.dumps(["אחת", "שתיים"], ensure_ascii=False)])
    assert proofread_words(words, client, "m") == words


def test_api_error_keeps_original():
    words = ["אחת", "שתיים"]
    client = _FakeClient([RuntimeError("boom")])
    assert proofread_words(words, client, "m") == words


def test_garbage_response_keeps_original():
    words = ["אחת", "שתיים"]
    client = _FakeClient(["I cannot help with that."])
    assert proofread_words(words, client, "m") == words


def test_code_fence_is_stripped():
    words = ["עולמ"]
    client = _FakeClient(['```json\n["עולם"]\n```'])
    assert proofread_words(words, client, "m") == ["עולם"]


def test_chunking_and_partial_failure():
    # 5 words, chunk_size=2 -> 3 calls; middle chunk fails, others succeed.
    words = ["א", "ב", "ג", "ד", "ה"]
    client = _FakeClient([
        json.dumps(["A", "B"], ensure_ascii=False),
        RuntimeError("boom"),
        json.dumps(["E"], ensure_ascii=False),
    ])
    out = proofread_words(words, client, "m", chunk_size=2)
    assert out == ["A", "B", "ג", "ד", "E"]
    assert len(client.calls) == 3


def test_empty_or_nonstring_elements_rejected():
    words = ["אחת", "שתיים"]
    client = _FakeClient([json.dumps(["אחת", ""], ensure_ascii=False)])
    assert proofread_words(words, client, "m") == words
    client = _FakeClient(['["אחת", 5]'])
    assert proofread_words(words, client, "m") == words
