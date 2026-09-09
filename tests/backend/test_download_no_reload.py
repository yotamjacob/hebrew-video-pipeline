"""/download serves an already-visible file WITHOUT a volume reload (2026-09-09).

Volume.reload() raises "open files" while any request is mid-stream from the
volume, and the stream-first preview player keeps one open for its whole
play. With a reload on EVERY request, each seek and each prefetch chunk burned
up to 10x1s retries and then 500 - the "preview failed to load" report. Output
files are written once and never rewritten, so a file the container already
sees is the final file and needs no reload. These pins keep the exists-first
check ahead of the reload in the media routes.
"""
import re

from tests.backend.conftest import MODAL_SRC


def _route_body(prefix: str) -> str:
    i = MODAL_SRC.index(f'if path.startswith("{prefix}") and method == "GET":')
    return MODAL_SRC[i:i + 4000]


def _retry_loop(body: str) -> str:
    i = body.index("for _attempt in range(")
    return body[i:i + 1400]


class TestVisibleFileSkipsReload:
    def test_download_checks_exists_before_reload(self):
        loop = _retry_loop(_route_body("/download/"))
        assert loop.index("file_path.exists()") < loop.index("tmp_vol.reload()")

    def test_media_checks_exists_before_reload(self):
        loop = _retry_loop(_route_body("/media/"))
        assert loop.index("file_path.exists()") < loop.index("tmp_vol.reload()")

    def test_thumbnail_checks_exists_before_reload(self):
        loop = _retry_loop(_route_body("/thumbnail/"))
        assert loop.index("file_path.exists()") < loop.index("tmp_vol.reload()")

    def test_download_still_reloads_for_a_not_yet_visible_file(self):
        """The commit-lag case (worker committed, api() container stale) must
        keep its reload + 1s retry loop - only a VISIBLE file skips it."""
        loop = _retry_loop(_route_body("/download/"))
        assert "tmp_vol.reload()" in loop
        assert re.search(r"_asyncio\.sleep\(1\)", loop)
