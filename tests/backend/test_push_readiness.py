"""The completion push is readiness-gated (2026-08-12).

Firing "your video is ready" from inside the GPU worker meant the push could
land while the result was not yet reachable: the FunctionCall hadn't returned
(/process_poll still 202), the api() container could be cold, and its volume
reload can lag the worker's commit. Both completion call sites must therefore
route through the spawned notifier, which waits for the call to finish and
probes the real /download route before pushing. These pins keep the gate from
being quietly bypassed.
"""
from tests.backend.conftest import MODAL_SRC


def _notifier_body():
    i = MODAL_SRC.index("def notify_when_previewable(")
    return MODAL_SRC[i:MODAL_SRC.index("def _notify_ready(")]


class TestCompletionSitesUseTheGate:
    def test_record_job_routes_through_the_notifier(self):
        i = MODAL_SRC.index("def _record_job(")
        body = MODAL_SRC[i:MODAL_SRC.index("def prune_volume(")]
        assert "_notify_ready(" in body
        assert "_send_push(" not in body     # no direct, ungated push

    def test_edit_ready_routes_through_the_notifier(self):
        i = MODAL_SRC.index('"edit_ready", upload_key=upload_key')
        window = MODAL_SRC[i - 400:i]
        assert "_notify_ready(" in window

    def test_the_processing_push_stays_direct(self):
        """Push (1) fires when processing STARTS - there is nothing to gate on,
        and routing it through the notifier would delay the 'upload done'
        signal behind a probe of a file that does not exist yet."""
        i = MODAL_SRC.index('kind="processing"')
        window = MODAL_SRC[i - 300:i + 50]
        assert "_send_push(" in window


class TestNotifierGates:
    def test_waits_for_the_function_call_before_probing(self):
        body = _notifier_body()
        assert body.index("_poll_fn_call(") < body.index("Range")

    def test_probes_the_real_download_route_with_a_media_token(self):
        body = _notifier_body()
        assert "/download/" in body
        assert "_sign_media_token(" in body
        assert '"Range": "bytes=0-1"' in body

    def test_probe_runs_before_the_push(self):
        body = _notifier_body()
        assert body.index("Range") < body.index("_send_push(")

    def test_gates_are_bounded_and_the_push_always_fires(self):
        """A dead probe must never eat the notification - the push is the last
        statement, outside every gate's try/except."""
        body = _notifier_body()
        assert body.count("deadline = time.time() + ") == 2
        assert "pushing anyway" in body
        # The push is the last ACTION (only the timeline telemetry print follows it),
        # and it comes after both gates have run out.
        send = body.index('_send_push(uid, title, body, kind=kind, tag="hebpipe-job")')
        assert send > body.index("pushing anyway")
        assert "_send_push(" not in body[send + 10:]
        assert '[notify] {kind}' in body[send:]          # timeline line logged after the send

    def test_spawn_failure_falls_back_to_a_direct_push(self):
        i = MODAL_SRC.index("def _notify_ready(")
        body = MODAL_SRC[i:i + 700]
        assert "notify_when_previewable.spawn(" in body
        assert "_send_push(" in body
