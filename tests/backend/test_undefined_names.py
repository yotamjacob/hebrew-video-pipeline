"""Every backend module must import every name it uses.

The AST-extraction suite concatenates ALL backend modules into one source
blob, so a helper defined in pipeline_core but never imported into the module
that calls it passes every contract test and then NameErrors in production
(field bug 2026-08-16: `_msg_text` used in content_fns / broll_fns /
pipeline_fns without an import - hooks + B-roll generation 500ed)."""

import pathlib

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes.reporter import Reporter  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES = ["pipeline_core.py", "pipeline_fns.py", "stock_helpers.py", "broll_fns.py",
           "content_fns.py", "metricool_fns.py", "assembler_fns.py", "app_modal.py"]


class _Collect:
    def __init__(self):
        self.lines = []

    def write(self, s):
        self.lines.append(s)

    def flush(self):
        pass


@pytest.mark.parametrize("name", MODULES)
def test_no_undefined_names(name):
    out, err = _Collect(), _Collect()
    src = (ROOT / name).read_text(encoding="utf-8")
    pyflakes_api.check(src, name, Reporter(out, err))
    undefined = [l for l in "".join(out.lines).splitlines() if "undefined name" in l]
    assert not undefined, "\n".join(undefined)
