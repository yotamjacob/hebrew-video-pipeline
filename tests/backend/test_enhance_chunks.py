"""
_enhance_in_chunks - long audio goes through DeepFilterNet in overlapping
chunks (cuDNN's RNN kernels reject very long sequences; a 15-min clip failed
with CUDNN_STATUS_NOT_SUPPORTED, 2026-09-28). The slicing is pure, so it is
tested here with a list-backed stand-in for the torch tensor (no numpy/torch
in the unit-test venv).
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn

_fn = _extract_fn(MODAL_SRC, "_enhance_in_chunks")["_enhance_in_chunks"]


class T:
    """[channels][frames] tensor look-alike: shape, [:, a:b] slicing, contiguous()."""
    def __init__(self, rows): self.rows = [list(r) for r in rows]
    @property
    def shape(self): return (len(self.rows), len(self.rows[0]) if self.rows else 0)
    def __getitem__(self, idx):
        ch, fr = idx
        return T([r[fr] for r in self.rows[ch]])
    def contiguous(self): return T(self.rows)
    def __eq__(self, other): return self.rows == other.rows


def cat(parts):
    ch = len(parts[0].rows)
    return T([sum((p.rows[c] for p in parts), []) for c in range(ch)])


def _audio(seconds, sr, ch=1):
    n = int(seconds * sr)
    return T([[c * 10_000 + i for i in range(n)] for c in range(ch)])


def test_short_input_is_one_call():
    calls = []
    def run(seg): calls.append(seg.shape[1]); return seg
    x = _audio(30, 100)
    out = _fn(x, run, 100, cat, chunk_s=60.0, overlap_s=1.0)
    assert calls == [3000]
    assert out == x


def test_long_input_is_reassembled_exactly():
    # 7.3 s at 100 Hz, 2 s chunks, 0.5 s context: identity model -> identity output
    x = _audio(7.3, 100)
    calls = []
    def run(seg): calls.append(seg.shape[1]); return seg
    out = _fn(x, run, 100, cat, chunk_s=2.0, overlap_s=0.5)
    assert out.shape == x.shape
    assert out == x                                   # no sample lost, doubled or shifted
    assert len(calls) == 4                            # 2+2+2+1.3 s
    assert max(calls) <= 200 + 2 * 50                 # never more than chunk + both margins
    assert calls[0] == 250 and calls[-1] == 130 + 50  # first: no left margin; last: no right margin


def test_margins_are_discarded_not_kept():
    # A model that poisons its first/last `ov` samples: those must never reach
    # the output, because every chunk's margins are trimmed away.
    x = _audio(5.0, 100)
    def run(seg):
        rows = [list(r) for r in seg.rows]
        for r in rows:
            for i in range(50): r[i] = -1; r[-1 - i] = -1
        return T(rows)
    out = _fn(x, run, 100, cat, chunk_s=2.0, overlap_s=0.5)
    inner = out.rows[0][50:-50]                       # only the recording's true edges lack context
    assert -1 not in inner
    assert out.shape == x.shape


def test_stereo_channels_kept():
    x = _audio(5.0, 100, ch=2)
    out = _fn(x, lambda s: s, 100, cat, chunk_s=2.0, overlap_s=0.5)
    assert out.shape == (2, 500)
    assert out == x
