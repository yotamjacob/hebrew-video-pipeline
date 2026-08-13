"""
Shared fixtures and helpers for backend tests.

Functions are extracted from source files without importing them (which
would require Modal/GPU libraries).  The same AST-extraction technique
used by test_stock_helpers.py is applied here.
"""
import ast
import sys
import os
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parent.parent.parent
_MODAL_FILES = ["pipeline_core.py", "pipeline_fns.py", "stock_helpers.py",
                "broll_fns.py", "content_fns.py", "metricool_fns.py",
                "assembler_fns.py", "app_modal.py"]
MODAL_SRC    = "\n".join((ROOT / f).read_text() for f in _MODAL_FILES)


def _build_ns():
    """Shared namespace for extracted code — stdlib + typing stubs."""
    from typing import List, Dict, Tuple, Optional, Any
    import re as _re, collections as _collections, threading as _threading, time as _time
    import dataclasses
    return {
        "List": List, "Dict": Dict, "Tuple": Tuple, "Optional": Optional, "Any": Any,
        "re": _re, "_re": _re,
        "_collections": _collections, "_threading": _threading, "_time": _time,
        "dataclasses": dataclasses,
        "dataclass": dataclasses.dataclass,
        "field": dataclasses.field,
    }


def _node_start(node) -> int:
    """Return the first line of a node including any decorators."""
    if getattr(node, "decorator_list", None):
        return node.decorator_list[0].lineno
    return node.lineno


def _exec_snippet(source: str, lineno_start: int, lineno_end: int, ns: dict):
    lines = source.splitlines()
    snippet = "\n".join(lines[lineno_start - 1: lineno_end])
    exec(compile(snippet, "<extracted>", "exec"), ns)  # noqa: S102


def _extract_fn(source: str, *names: str, extra_ns=None) -> dict:
    """Return {name: fn} for each top-level function, sharing one namespace."""
    ns = _build_ns()
    if extra_ns:
        ns.update(extra_ns)
    tree = ast.parse(source)
    fns = {}
    # Top-level defs only — nested helpers (e.g. a seconds_to_ass closure inside
    # another function) would extract with leading indentation and fail to exec.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            _exec_snippet(source, _node_start(node), node.end_lineno, ns)
            fns[node.name] = ns[node.name]
    return fns


# ─── Extract pure helpers from the Modal source (production code) ─────────────
# The word-level cutter (compute_keep_segments) + filler detection are covered
# directly against pipeline_fns.py in test_silence_cutter.py.

_modal_helpers = _extract_fn(
    MODAL_SRC,
    "seconds_to_ass",
    "_sanitize_transcript",
    "add_clip_window",
)

seconds_to_ass      = _modal_helpers["seconds_to_ass"]
sanitize_transcript = _modal_helpers["_sanitize_transcript"]
add_clip_window     = _modal_helpers["add_clip_window"]
