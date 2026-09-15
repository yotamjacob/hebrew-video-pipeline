#!/usr/bin/env python3
"""Build a branded, Gmail-safe Pipeline email from a small JSON spec.

    python3 tools/mail/build.py tools/mail/specs/hp100.json            # build + preview screenshot
    python3 tools/mail/build.py tools/mail/specs/hp100.json --deploy   # also copy assets to site/img and deploy

Outputs (tools/mail/out/<name>/):
    email.html    send version; images point at https://hebrew-pipeline.app/img/<name>-*.png
    email.txt     plain-text alternative
    preview.html  same email with local image paths; open it in a browser
    preview.png   screenshot of preview.html at 720px (needs Google Chrome)
    <name>-title.png / -steps.png / -cta.png   display type rendered in Karantina at 2x

Why images for the display type: Gmail strips web fonts, @import, position and
animation. Body copy is plain Arial; only the headline, section title, button
and hero carry the brand look, and those are images.

Spec keys (see specs/hp100.json): name, subject, preheader, opener, headline
{text, dir, size}, lede, body, hero {src, width, height, alt} (src is a file in
site/img or a path to copy there), steps_heading, steps [{html, text, accent}],
note, cta {text, url}, signoff, signoff_text, body_text (plain-text override, e.g.
to spell out a value the hero image carries), links {token: html}. Any "{token}"
inside step html is replaced from links.

Requires: Google Chrome, Homebrew ffmpeg. Type images are cached by content
hash, so re-running with only copy changes is instant.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SITE_IMG = REPO / "site" / "img"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
IMG_BASE = "https://hebrew-pipeline.app/img/"

SANS = "font-family:Arial,Helvetica,sans-serif;"


# ---------------------------------------------------------------- type images
TYPE_PAGE = """<!DOCTYPE html><html lang="he"><head><meta charset="utf-8"><style>
@import url('{fonts}');
html,body{{margin:0;padding:0;background:transparent;}}
.el{{position:absolute;top:0;left:0;white-space:nowrap;font-family:Karantina,sans-serif;font-weight:700;line-height:1;padding:6px 4px;}}
#headline{{font-size:{hsize}px;color:#3A3630;direction:{hdir};letter-spacing:.5px;}}
#headline b{{color:#C4703F;font-weight:700;}}
#steps{{font-size:36px;color:#3A3630;direction:rtl;}}
#button{{font-size:30px;color:#FBF4EA;direction:rtl;background:#3A3630;border-radius:16px;padding:17px 46px 15px;letter-spacing:.5px;}}
</style></head><body>
<div class="el" id="headline">{headline}</div>
<div class="el" id="steps">{steps}</div>
<div class="el" id="button">{button}</div>
<script>
const only=location.hash.slice(1);
if(only){{for(const id of ["headline","steps","button"]){{if(id!==only)document.getElementById(id).style.display="none";}}}}
addEventListener("load",()=>{{const o={{}};for(const id of ["headline","steps","button"]){{const r=document.getElementById(id).getBoundingClientRect();o[id]=[Math.ceil(r.width),Math.ceil(r.height)];}}document.body.setAttribute("data-sizes",JSON.stringify(o));}});
</script></body></html>"""


def chrome(args, timeout=15, wait_for: Path = None):
    """Run headless Chrome. It reliably does its work in a few seconds and then
    hangs on exit, so stop it as soon as the artifact exists (or stdout arrives)."""
    import time
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             "--virtual-time-budget=2500", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    t0 = time.time()
    if wait_for is not None:
        while time.time() - t0 < timeout:
            if wait_for.exists() and wait_for.stat().st_size > 0 and time.time() - wait_for.stat().st_mtime > 0.5:
                break
            time.sleep(0.2)
        proc.kill()
        return ""
    out = ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out or ""


def render_type(out: Path, name: str, spec: dict) -> dict:
    """Render headline / steps heading / button as transparent 2x PNGs. Returns {id: (file, w, h)}."""
    page = out / "_type.html"
    page.write_text(TYPE_PAGE.format(fonts=(HERE / "fonts" / "fonts.css").as_uri(),
                                     hsize=spec["headline"].get("size", 72),
                                     hdir=spec["headline"].get("dir", "rtl"),
                                     headline=spec["headline"]["text"],
                                     steps=spec.get("steps_heading", ""),
                                     button=spec.get("cta", {}).get("text", "")), encoding="utf-8")
    cache = out / "_type.json"
    cached = json.loads(cache.read_text()) if cache.exists() else {}
    key = hashlib.sha1(page.read_bytes()).hexdigest()
    results = {}
    if cached.get("key") == key and all((out / v[0]).exists() for v in cached.get("files", {}).values()):
        return {k: tuple(v) for k, v in cached["files"].items()}
    profile = out / "_chrome"
    dom = chrome([f"--user-data-dir={profile}", "--window-size=900,300", "--dump-dom", page.as_uri()])
    m = re.search(r'data-sizes="([^"]*)"', dom)
    if not m:
        sys.exit("could not measure type (is Google Chrome installed?)")
    sizes = json.loads(m.group(1).replace("&quot;", '"'))
    for el, suffix in (("headline", "title"), ("steps", "steps"), ("button", "cta")):
        w, h = sizes[el]
        if w == 0:
            continue
        raw = out / f"_{el}.png"
        raw.unlink(missing_ok=True)
        chrome([f"--user-data-dir={profile}-{el}", "--force-device-scale-factor=2",
                "--default-background-color=00000000", f"--window-size={w},{h}",
                f"--screenshot={raw}", f"{page.as_uri()}#{el}"], wait_for=raw)
        final = out / f"{name}-{suffix}.png"
        # Chrome enforces a minimum window, so crop to the measured box at 2x.
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(raw),
                        "-vf", f"crop={w * 2}:{h * 2}:0:0", "-pix_fmt", "rgba", str(final)], check=True)
        raw.unlink(missing_ok=True)
        results[el] = (final.name, w, h)
    cache.write_text(json.dumps({"key": key, "files": results}))
    for d in out.glob("_chrome*"):
        shutil.rmtree(d, ignore_errors=True)
    return results


# ---------------------------------------------------------------- html pieces
def img(src, w, h, alt, extra=""):
    return (f'<img src="{src}" width="{w}" height="{h}" alt="{alt}" '
            f'style="display:block;max-width:100%;height:auto;border:0;{extra}">')


def steps_html(spec, links, base):
    rows = []
    steps = spec.get("steps", [])
    for i, st in enumerate(steps, 1):
        last = i == len(steps)
        accent = st.get("accent")
        bg, fg, cls = ("#C4703F", "#FBF4EA", "tile-b") if accent else ("#A3B196", "#3A3630", "tile-a")
        pad = "0" if last else "18px"
        body = st["html"].format(**links)
        rows.append(
            f'            <tr>\n'
            f'              <td width="48" valign="top" style="padding:0 0 {pad} 14px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td class="{cls}" bgcolor="{bg}" width="38" height="38" align="center" style="background-color:{bg};border-radius:12px;{SANS}font-size:18px;font-weight:700;color:{fg};line-height:38px;">{i}</td></tr></table></td>\n'
            f'              <td valign="top" class="txt" dir="rtl" style="padding:7px 0 {pad};font-size:17px;line-height:1.6;color:#3A3630;text-align:right;">{body}</td>\n'
            f'            </tr>')
    if not rows:
        return ""
    heading = ""
    if "steps" in base:
        f, w, h = base["steps"]
        heading = img(IMG_BASE + f, w, h, spec.get("steps_heading", ""), "margin:0 0 14px;")
    note = spec.get("note", "")
    note_html = (f'          <p class="muted" dir="rtl" style="margin:26px 0 0;font-size:15px;line-height:1.6;color:#8A8175;text-align:right;">{note}</p>'
                 if note else "")
    return (f'<tr><td dir="rtl" style="padding:26px 0 6px;{SANS}text-align:right;">\n'
            f'          {heading}\n'
            f'          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" dir="rtl">\n'
            + "\n".join(rows) + "\n          </table>\n" + note_html + "\n        </td></tr>")


def build(spec_path: Path, deploy: bool, preview: bool):
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    name = spec["name"]
    out = HERE / "out" / name
    out.mkdir(parents=True, exist_ok=True)
    links = spec.get("links", {})

    types = render_type(out, name, spec)

    # hero: a file already in site/img, or a path to copy there
    hero_html = ""
    hero = spec.get("hero")
    if hero:
        src = Path(hero["src"])
        if not src.exists():
            src = SITE_IMG / hero["src"]
        if src.exists() and src.resolve() != (out / src.name).resolve():
            shutil.copy(src, out / src.name)
        hero_html = (f'<tr><td align="center" style="padding:26px 0 4px;">\n'
                     f'          {img(IMG_BASE + src.name, hero["width"], hero["height"], hero.get("alt", ""))}\n'
                     f'        </td></tr>')

    f, w, h = types["headline"]
    headline_html = img(IMG_BASE + f, w, h, re.sub("<[^>]+>", "", spec["headline"]["text"]), "margin:2px 0 12px;")
    opener_html = f'<p class="muted" style="margin:0 0 6px;font-size:17px;color:#6F675D;">{spec["opener"]}</p>' if spec.get("opener") else ""
    lede_html = f'<p class="txt" style="margin:0 0 14px;font-size:20px;line-height:1.5;font-weight:bold;color:#3A3630;">{spec["lede"]}</p>' if spec.get("lede") else ""
    body_html = f'<p class="muted" style="margin:0;font-size:17px;line-height:1.75;color:#6F675D;">{spec["body"]}</p>' if spec.get("body") else ""

    cta_html = ""
    if spec.get("cta") and "button" in types:
        f, w, h = types["button"]
        cta_html = (f'<tr><td align="center" style="padding:32px 0 6px;">\n'
                    f'          <a href="{spec["cta"]["url"]}" style="display:inline-block;text-decoration:none;border:0;">'
                    f'{img(IMG_BASE + f, w, h, spec["cta"]["text"], "display:block;")}</a>\n'
                    f'        </td></tr>')

    html = (HERE / "template.html").read_text(encoding="utf-8")
    for k, v in {"title": re.sub("<[^>]+>", "", spec["headline"]["text"]), "preheader": spec.get("preheader", ""),
                 "opener_html": opener_html, "headline_html": headline_html, "lede_html": lede_html,
                 "body_html": body_html, "hero_html": hero_html, "steps_html": steps_html(spec, links, types),
                 "cta_html": cta_html, "signoff_html": spec.get("signoff", "")}.items():
        html = html.replace("{{" + k + "}}", v)
    (out / "email.html").write_text(html, encoding="utf-8")
    (out / "preview.html").write_text(html.replace(IMG_BASE, "./"), encoding="utf-8")

    # plain text
    strip = lambda s: re.sub("<[^>]+>", "", s.replace("<br>", "\n"))
    lines = [spec.get("opener", ""), "", strip(spec["headline"]["text"]), spec.get("lede", ""), "",
             spec.get("body_text") or strip(spec.get("body", "")), ""]
    if spec.get("steps"):
        lines += [spec.get("steps_heading", "")] + [f"{i}. {st.get('text') or strip(st['html'].format(**links))}"
                                                     for i, st in enumerate(spec["steps"], 1)] + [""]
    if spec.get("note"):
        lines += [strip(spec["note"]), ""]
    if spec.get("cta"):
        lines += [f"{spec['cta']['text']}: {spec['cta']['url']}", ""]
    lines += [spec.get("signoff_text") or strip(spec.get("signoff", "")), "", "hebrew-pipeline.app · hebrewpipeline@gmail.com"]
    (out / "email.txt").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    assets = [out / v[0] for v in types.values()] + ([out / Path(hero["src"]).name] if hero else [])
    if deploy:
        for a in assets:
            shutil.copy(a, SITE_IMG / a.name)
        print("copied to site/img:", ", ".join(a.name for a in assets))
        subprocess.run(["vercel", "deploy", "--prod", "--yes"], cwd=REPO)
    else:
        print("assets to host (run with --deploy, or commit + `vercel deploy --prod`):", ", ".join(a.name for a in assets))

    if preview and Path(CHROME).exists():
        (out / "preview.png").unlink(missing_ok=True)
        chrome([f"--user-data-dir={out / '_chrome-preview'}", "--window-size=720,1500",
                f"--screenshot={out / 'preview.png'}", (out / "preview.html").as_uri()], wait_for=out / "preview.png")
        shutil.rmtree(out / "_chrome-preview", ignore_errors=True)
    print(f"built {out.relative_to(REPO)}/  (email.html, email.txt, preview.html{', preview.png' if preview else ''})")
    print(f"subject: {spec['subject']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--deploy", action="store_true", help="copy assets to site/img and run `vercel deploy --prod`")
    ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args()
    build(Path(a.spec), a.deploy, not a.no_preview)
