"""Regenerate the submission PDF from the technical document.

    python backend/scripts/make_technical_pdf.py

Markdown -> standalone HTML with `pandoc`, then HTML -> PDF with the installed Chrome in headless
print mode. Both steps are deterministic and both binaries are checked for, because a submitter
re-running this the night before a deadline needs to know which one is missing rather than staring
at a half-written file. The HTML is written beside the markdown so the browser view and the PDF
come from one render.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "TECHNICAL_DOCUMENT.md"
HTML = ROOT / "docs" / "TECHNICAL_DOCUMENT.html"
CSS = ROOT / "docs" / "doc.css"
PDF = ROOT / "docs" / "TECHNICAL_DOCUMENT.pdf"
TITLE = "FanCode Gap Receipt and Dream11 Watch Along - Technical Document"

CHROME = [
    Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    shutil.which("chrome"), shutil.which("chromium"), shutil.which("google-chrome"),
]


def chrome() -> Path:
    for c in CHROME:
        if c and Path(c).is_file():
            return Path(c)
    sys.exit("no Chrome/Chromium found - the PDF step needs a headless browser. "
             "The HTML is still regenerated.")


def main() -> int:
    if not DOC.exists():
        sys.exit(f"missing {DOC}")
    pandoc = shutil.which("pandoc")
    if not pandoc:
        sys.exit("pandoc not on PATH")
    args = [pandoc, str(DOC), "-o", str(HTML), "--standalone",
            "--metadata", f"title={TITLE}", "--metadata", "lang=en", "--toc", "--toc-depth=2"]
    if CSS.exists():
        args += ["--css", CSS.name]
    subprocess.run(args, check=True)
    if CSS.exists():
        # Inlining the stylesheet keeps the HTML self-contained: a reviewer who mails the single
        # file gets the same typography as the PDF.
        html = HTML.read_text(encoding="utf-8")
        inlined, n = re.subn(r'<link\s+rel="stylesheet"\s+href="[^"]*"\s*/?>',
                             lambda m: f"<style>\n{CSS.read_text(encoding='utf-8')}\n</style>",
                             html, count=1)
        if not n:
            sys.exit("the generated HTML has no stylesheet link to inline")
        HTML.write_text(inlined, encoding="utf-8")

    subprocess.run([str(chrome()), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", HTML.as_uri()], check=True,
                   capture_output=True, text=True, timeout=180)

    text = PDF.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page[^s]", text))
    sections = len(re.findall(r"^## ", DOC.read_text(encoding="utf-8"), re.M))
    print(f"html: {HTML} ({HTML.stat().st_size:,} B)")
    print(f"pdf : {PDF} ({len(text):,} B) ~{pages} pages from {sections} numbered sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
