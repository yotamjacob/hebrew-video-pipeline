"""Reframe plan preview (debug) - judge the 9:16 planner without full renders.

Runs the SAME sampler + planner render_story uses (`_reframe_window`) on one
or more windows of an uploaded source and writes, per window:
  - a contact sheet (one row per shot: mid-shot frame with every detected
    face box + its mouth score, the chosen crop rectangle / split tiles,
    the cropped result, and the shot's segments with kind / upscale);
  - a low-res mp4 (360x640) of the crop plan ONLY - no captions, hook,
    watermark or branding; audio kept so speaker switches can be judged.
It prints each window's reframe_report and the top scene scores (for tuning
REFRAME_SCENE_THR). Ephemeral: nothing is added to the deployed app.

    modal run scripts/reframe_preview.py --key <upload key> --start 120 --end 180
    modal run scripts/reframe_preview.py --key <key> --windows "120-180,610-655" --fps 3
    options: --fps 6 (face sample rate, the render default)  --thr 0.3 (scene cut
             threshold)  --no-dim (no listener dimming)
             --out DIR (default: $TMPDIR/reframe_preview)
"""
import json
import os
import tempfile
from pathlib import Path

from pipeline_core import app, burn_image, tmp_vol, TMP_DIR


@app.function(image=burn_image, cpu=4, memory=4096, timeout=1800, volumes={TMP_DIR: tmp_vol})
def reframe_preview_fn(key: str, windows: list, fps: int = 6, thr: float = 0.30, dim: bool = True):
    import subprocess
    import cv2
    import numpy as np
    import assembler_fns as af

    key = key[:-len("_src.mp4")] if key.endswith("_src.mp4") else key
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = af._resolve_source(key, tmp)
        sw, sh = af._probe_dims(src)
        cw, ch = af.REFRAME_CANVAS
        for wi, (a, b) in enumerate(windows):
            a, b = float(a), min(float(b), af._probe_duration(src))
            length = b - a
            pl = af._reframe_window(src, a, b, sw, sh, tmp, f"pv{wi:02d}", cw, ch, fps=fps, thr=thr)
            segs, cuts, report = pl["segs"], pl["cuts"], pl["report"]
            bounds = [0.0] + [c for c in cuts if 0 < c < length] + [length]

            # ── contact sheet ────────────────────────────────────────────
            FW, FH, PW, PH, TW = 640, 360, 180, 320, 460
            rows = []
            head = np.full((70, FW + PW + TW + 20, 3), 245, np.uint8)
            summ = (f"{key}  [{a:.1f}-{b:.1f}s]  fps={fps} thr={thr}  mode={report['mode']}  "
                    f"conf={report['confidence']}  shots={report['shots']}  "
                    f"switches={report['speaker_switches']}  margin={report.get('margin')}")
            cv2.putText(head, summ[:150], (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1, cv2.LINE_AA)
            cv2.putText(head, f"source {sw}x{sh} -> canvas {cw}x{ch}   coverage={report['coverage']}  "
                        f"stability={report.get('stability')}  {report.get('reason', '')}",
                        (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1, cv2.LINE_AA)
            rows.append(head)
            for si, (s0, s1) in enumerate(zip(bounds, bounds[1:])):
                tm = (s0 + s1) / 2.0
                fp = tmp / f"pv_{wi}_{si}.jpg"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a + tm:.3f}", "-i", str(src),
                                "-frames:v", "1", "-q:v", "3", str(fp)], check=False)
                img = cv2.imread(str(fp))
                if img is None:
                    img = np.zeros((sh, sw, 3), np.uint8)
                full = cv2.resize(img, (sw, sh)) if img.shape[:2] != (sh, sw) else img
                disp = cv2.resize(full, (FW, FH))
                sx, sy = FW / float(sw), FH / float(sh)
                near = min(pl["samples"], key=lambda s: abs(s[0] - tm)) if pl["samples"] else (tm, [])
                for f in near[1]:
                    x0, y0 = int((f[0] - f[2] / 2) * FW), int((f[1] - f[3] / 2) * FH)
                    x1, y1 = int((f[0] + f[2] / 2) * FW), int((f[1] + f[3] / 2) * FH)
                    cv2.rectangle(disp, (x0, y0), (x1, y1), (60, 200, 60), 2)
                    ms = af._mouth_score(f)
                    if ms is not None:
                        cv2.putText(disp, f"m{ms:.1f}", (x0, max(12, y0 - 4)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 200, 60), 1, cv2.LINE_AA)
                seg = next((s for s in segs if s["t0"] <= tm < s["t1"]), segs[-1])
                panel = np.zeros((PH, PW, 3), np.uint8)
                if seg["kind"] == "split":
                    speaking = next((sd for x, y, sd in seg.get("speak") or []
                                     if x <= tm - seg["t0"] < y), None)
                    for ti, (x, y, w, h) in enumerate(seg["tiles"]):
                        pb = (seg.get("panes") or [None, None])[ti]
                        if pb:     # detected pane (cyan)
                            cv2.rectangle(disp, (int(pb[0] * FW), int(pb[1] * FH)),
                                          (int(pb[2] * FW) - 1, int(pb[3] * FH) - 1), (255, 220, 0), 1)
                        col = (0, 140, 255) if speaking == ti else (40, 40, 230)
                        cv2.rectangle(disp, (int(x * sx), int(y * sy)), (int((x + w) * sx), int((y + h) * sy)), col, 2)
                        crop = full[y:y + h, x:x + w]
                        if (seg.get("fill") or [False, False])[ti]:
                            th2 = PH // 2
                            tile = (cv2.GaussianBlur(cv2.resize(crop, (PW, th2)), (0, 0), 6) * 0.9).astype(np.uint8)
                            k = min(PW / float(w), th2 / float(h))
                            fg = cv2.resize(crop, (max(1, int(w * k)), max(1, int(h * k))))
                            oy, ox = (th2 - fg.shape[0]) // 2, (PW - fg.shape[1]) // 2
                            tile[oy:oy + fg.shape[0], ox:ox + fg.shape[1]] = fg
                        else:
                            tile = cv2.resize(crop, (PW, PH // 2))
                        if dim and speaking is not None and speaking != ti:
                            tile = (tile * 0.88).astype(np.uint8)
                        panel[ti * (PH // 2):(ti + 1) * (PH // 2)] = tile
                elif seg["kind"] == "fit":
                    fg = cv2.resize(full, (PW, int(PW * sh / float(sw))))
                    bg = cv2.GaussianBlur(cv2.resize(full, (PH * sw // sh, PH))[:, :PW], (0, 0), 6)
                    panel[:] = (bg * 0.9).astype(np.uint8)
                    oy = (PH - fg.shape[0]) // 2
                    panel[oy:oy + fg.shape[0]] = fg
                    cv2.putText(disp, "FIT", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 40, 230), 2)
                else:
                    x, y, w, h = seg["box"]
                    if seg["kind"] == "ease":
                        kf = seg["kf"]
                        rt = tm - seg["t0"]
                        c = kf[-1][1]
                        for (t0, c0), (t1, c1) in zip(kf, kf[1:]):
                            if t0 <= rt < t1:
                                c = c0 + (c1 - c0) * (rt - t0) / max(1e-6, t1 - t0)
                                break
                        x = int(round(min(max(0.0, c * sw - w / 2.0), sw - w)))
                    cv2.rectangle(disp, (int(x * sx), int(y * sy)), (int((x + w) * sx), int((y + h) * sy)), (40, 40, 230), 2)
                    panel = cv2.resize(full[y:y + h, x:x + w], (PW, PH))
                txt = np.full((FH, TW, 3), 250, np.uint8)
                lines = [f"shot {si + 1}/{len(bounds) - 1}   {s0:.2f} - {s1:.2f} s ({s1 - s0:.1f}s)",
                         f"{'CUT at ' + format(s0, '.3f') if s0 > 0 else 'window start'}   faces@mid: {len(near[1])}",
                         "segments:"]
                for s in segs:
                    if s["t0"] >= s0 - 1e-9 and s["t1"] <= s1 + 1e-9:
                        up = af._seg_upscale(s, sw, sh, cw, ch)
                        extra = f" spk={len(s.get('speak') or [])}" if s["kind"] == "split" else ""
                        lines.append(f"  {s['t0']:.2f}-{s['t1']:.2f} {s['kind']}/{s['role']} up={up}{extra}")
                        if s["kind"] == "split":
                            for ti, nm in enumerate(("top", "bottom")):
                                pb = (s.get("panes") or [None, None])[ti]
                                fl = (s.get("fill") or [False, False])[ti]
                                pbs = "frame-bounded" if pb is None else "pane " + ",".join(f"{v:.3f}" for v in pb)
                                lines.append(f"    {nm} tile: {pbs}{'  +fill' if fl else ''}")
                for li, line in enumerate(lines[:16]):
                    cv2.putText(txt, line, (10, 24 + li * 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (30, 30, 30), 1, cv2.LINE_AA)
                row = np.full((FH, FW + PW + TW + 20, 3), 255, np.uint8)
                row[:, :FW] = disp
                row[20:20 + PH, FW + 10:FW + 10 + PW] = panel
                row[:, FW + PW + 20:] = txt
                rows.append(row)
                rows.append(np.full((6, row.shape[1], 3), 200, np.uint8))
            sheet = np.vstack(rows)
            ok, jpg = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])

            # ── low-res mp4 of the crop plan only ───────────────────────
            mp4 = tmp / f"pv_{wi}.mp4"
            chain = af._segments_chain(segs, sw, sh, 360, 640, length, cuts, dim=dim)
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-to", f"{b:.2f}",
                                "-i", str(src), "-filter_complex", f"[0:v]{chain},fps=30[v]",
                                "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                                "-crf", "27", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                                str(mp4)], capture_output=True, text=True)
            if r.returncode:
                print(f"[preview] mp4 failed: {r.stderr[-1500:]}")
            results.append({
                "window": [a, b], "report": report,
                "cuts": [round(c, 3) for c in cuts],
                "scene_top": sorted(((round(t, 3), round(s, 3)) for t, s in pl["scene"]),
                                    key=lambda x: -x[1])[:12],
                "sheet": jpg.tobytes() if ok else b"",
                "mp4": mp4.read_bytes() if mp4.exists() else b"",
            })
    return results


@app.local_entrypoint()
def main(key: str, start: float = 0.0, end: float = 60.0, windows: str = "",
         fps: int = 6, thr: float = 0.30, dim: bool = True, out: str = ""):
    wins = ([[float(x) for x in w.split("-", 1)] for w in windows.split(",") if w.strip()]
            if windows else [[start, end]])
    out_dir = Path(out or os.path.join(tempfile.gettempdir(), "reframe_preview"))
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[-40:]
    for res in reframe_preview_fn.remote(key, wins, fps, thr, dim):
        a, b = res["window"]
        stem = out_dir / f"{safe}_{a:.0f}-{b:.0f}_fps{fps}"
        Path(f"{stem}_sheet.jpg").write_bytes(res["sheet"])
        Path(f"{stem}_plan.mp4").write_bytes(res["mp4"])
        print(f"\n=== window {a:.1f}-{b:.1f}s  fps={fps} ===")
        print(f"cuts: {res['cuts']}")
        print(f"top scene scores: {res['scene_top']}")
        print("reframe_report: " + json.dumps(res["report"], indent=1))
        print(f"sheet: {stem}_sheet.jpg\nplan mp4: {stem}_plan.mp4")
