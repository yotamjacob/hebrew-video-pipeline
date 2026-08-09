"""The 'save' progress stage: publishing the result (volume copy + commit +
record/prune for terminal runs) is real user-visible time - it must be
reported as its own stage so the checklist never shows everything done while
the worker is still writing (field report: cut-only "hangs for a minute").
Source tripwires - process_video's body is a Modal function."""
from pathlib import Path

SRC = (Path(__file__).parent.parent.parent / "pipeline_fns.py").read_text()


def test_save_stage_wraps_the_publish_step():
    i_stage = SRC.index('_mark(stage="save")')
    i_copy  = SRC.index("shutil.copy(out_file, cut_path)")
    i_prune = SRC.index("prune_volume()")
    i_done  = SRC.index('_mark(done="save")')
    i_pop   = SRC.index("progress_store.pop(upload_key)")
    assert i_stage < i_copy < i_prune < i_done < i_pop
