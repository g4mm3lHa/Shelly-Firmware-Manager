"""Workspace helper (not shipped): mockups of UI proposals in one command.

  tools/dev/run.sh python3 tools/dev/mockup.py new DIR
      Scratch copy of the committed repository (git archive HEAD) in DIR; change ui-src there.
  tools/dev/run.sh python3 tools/dev/mockup.py sheet OUT.png LABEL=DIR [LABEL=DIR ...] [options]
      Per variant: rebuild app/static from DIR/ui-src, capture every shot at 1440 px, 412 px and
      360 px embedded (like the app inside HA), then write ONE labelled contact sheet OUT.png
      (a row per shot and variant, columns = viewports). Only this sheet needs to be looked at.

Options of "sheet" (capture.py semantics): --shot NAME=STEPS (repeatable; default: the four
tabs), --scenario NAME, --scheme light|dark, --long-data, --route PAT=FILE (repeatable).
Several variants give a side-by-side choice; never commit mockup code."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
# (capture.py options, file tag, scale of the sheet relative to CSS pixels)
VIEWPORTS = (("--widths 1440", "1440", 0.7), ("--widths 412", "412", 1.0), ("--widths 360 --embedded", "360-embedded", 1.0))
GAP, LABEL_H, BG, FG = 16, 22, (245, 245, 245), (20, 20, 20)


def new(target):
    """Copy the committed repository to target (a scratch folder, never committed)."""
    Path(target).mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(["git", "-C", str(ROOT), "archive", "HEAD"], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", target], stdin=archive.stdout, check=True)
    archive.wait()
    print(target)


def build(copy):
    """Build the UI of a scratch copy (offline esbuild stand-in on PATH when present)."""
    env = dict(os.environ)
    if os.access(os.environ.get("ESBUILD_BIN", "/opt/npm-tools/node_modules/esbuild/bin/esbuild"), os.X_OK):
        env["PATH"] = f"{ROOT / 'tools' / 'dev' / 'bin'}{os.pathsep}{env['PATH']}"  # same rule as check.sh
    subprocess.run([sys.executable, "tools/build_ui.py"], cwd=copy, env=env, check=True, capture_output=True)


def capture(copy, shots_dir, extra):
    """Run capture.py of the scratch copy for the three viewports."""
    for options, _tag, _scale in VIEWPORTS:
        proc = subprocess.run([sys.executable, str(ROOT / "qa" / "capture.py"), str(shots_dir), "--root", str(copy),
                               *options.split(), *extra], capture_output=True, text=True)
        if proc.returncode:  # overflow or page error: show the FAIL lines, the sheet is still made
            print("\n".join(line for line in proc.stdout.splitlines() if line.startswith("FAIL")) or proc.stderr[-600:])


def sheet(out, variants, shot_names, extra):
    """Capture all variants and compose the labelled contact sheet."""
    font = ImageFont.load_default(size=14)
    rows = []  # (label, [images])
    with tempfile.TemporaryDirectory(prefix="sfm-mock-") as tmp:
        for label, copy in variants:
            build(copy)
            shots = Path(tmp) / label
            capture(copy, shots, extra)
            for name in shot_names:
                images = []
                for _options, tag, scale in VIEWPORTS:
                    img = Image.open(shots / f"{tag}-{name}.png").convert("RGB")
                    css_width = 1440 if tag == "1440" else int(tag[:3])
                    factor = scale * css_width / img.width
                    images.append(img.resize((round(img.width * factor), round(img.height * factor)), Image.LANCZOS))
                rows.append((f"{name} – {label}", images))
        width = max(sum(i.width for i in imgs) + GAP * (len(imgs) + 1) for _, imgs in rows)
        height = sum(max(i.height for i in imgs) + LABEL_H + GAP for _, imgs in rows) + GAP
        canvas = Image.new("RGB", (width, height), BG)
        draw, y = ImageDraw.Draw(canvas), GAP
        for label, imgs in rows:
            draw.text((GAP, y), label, fill=FG, font=font)
            x = GAP
            for img in imgs:
                canvas.paste(img, (x, y + LABEL_H))
                x += img.width + GAP
            y += max(i.height for i in imgs) + LABEL_H + GAP
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out)
    print(out)


def main():
    """Parse the sub-command and run it."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new").add_argument("dir")
    sp = sub.add_parser("sheet")
    sp.add_argument("out")
    sp.add_argument("variants", nargs="+", help="LABEL=DIR")
    sp.add_argument("--shot", action="append", default=[])
    sp.add_argument("--scenario")
    sp.add_argument("--scheme")
    sp.add_argument("--long-data", action="store_true")
    sp.add_argument("--route", action="append", default=[])
    args = ap.parse_args()
    if args.cmd == "new":
        new(args.dir)
        return 0
    extra = [a for s in args.shot for a in ("--shot", s)] + [a for r in args.route for a in ("--route", r)]
    extra += ["--long-data"] * args.long_data
    for flag, value in (("--scenario", args.scenario), ("--scheme", args.scheme)):
        extra += [flag, value] if value else []
    shot_names = [s.split("=", 1)[0] for s in args.shot] or ["devices", "firmware", "logs", "settings"]
    sheet(args.out, [tuple(v.split("=", 1)) for v in args.variants], shot_names, extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
