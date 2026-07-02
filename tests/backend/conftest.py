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
PIPELINE_SRC = (ROOT / "hebrew_video_pipeline.py").read_text()
_MODAL_FILES = ["pipeline_core.py", "pipeline_fns.py", "stock_helpers.py",
                "broll_fns.py", "content_fns.py", "metricool_fns.py", "app_modal.py"]
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


def _extract_class(source: str, name: str, ns=None):
    if ns is None:
        ns = _build_ns()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            _exec_snippet(source, _node_start(node), node.end_lineno, ns)
            return ns[name]
    return None


def _extract_fn(source: str, *names: str, extra_ns=None) -> dict:
    """Return {name: fn} for each top-level function, sharing one namespace."""
    ns = _build_ns()
    if extra_ns:
        ns.update(extra_ns)
    tree = ast.parse(source)
    fns = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            _exec_snippet(source, _node_start(node), node.end_lineno, ns)
            fns[node.name] = ns[node.name]
    return fns


# ─── Extract pipeline dataclasses and helpers from hebrew_video_pipeline.py ──

_pipeline_ns = _build_ns()
Word        = _extract_class(PIPELINE_SRC, "Word",        _pipeline_ns)
KeepSegment = _extract_class(PIPELINE_SRC, "KeepSegment", _pipeline_ns)
_pipeline_ns["Word"]        = Word
_pipeline_ns["KeepSegment"] = KeepSegment

_pipeline = _extract_fn(
    PIPELINE_SRC,
    "compute_keep_segments",
    "seconds_to_ass",
    extra_ns=_pipeline_ns,
)

compute_keep_segments = _pipeline["compute_keep_segments"]
seconds_to_ass        = _pipeline["seconds_to_ass"]


# ─── Extract app_modal helpers ───────────────────────────────────────────────

_modal_helpers = _extract_fn(
    MODAL_SRC,
    "_sanitize_transcript",
    "add_clip_window",
)

sanitize_transcript = _modal_helpers["_sanitize_transcript"]
add_clip_window     = _modal_helpers["add_clip_window"]


def make_word(text: str, start: float, end: float) -> object:
    return Word(text=text, start=start, end=end)
