"""Markdown -> styled print HTML -> PDF (via headless Chrome). No dependencies.

Regenerate the technical reference after editing docs/TECHNICAL_REFERENCE.md:

    venv\\Scripts\\python.exe tools\\md2pdf.py docs\\TECHNICAL_REFERENCE.md ^
        docs\\MapHarvest-Technical-Reference.pdf "MapHarvest" "<subtitle>"

Usage:  python md2pdf.py <input.md> <output.pdf> "<Doc title>" "<Subtitle>"

Handles exactly the Markdown subset used in the MapHarvest technical reference:
ATX headings, pipe tables (with escaped pipes), fenced code blocks, blockquotes,
ordered/unordered lists (one nesting level), horizontal rules, inline code,
bold, italic and inline links.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import unicodedata

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }

* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Segoe UI", "DM Sans", system-ui, sans-serif;
  font-size: 9.1pt; line-height: 1.5; color: #1a1c1f; margin: 0;
}

/* ---------- cover ---------- */
.cover { page-break-after: always; padding-top: 46mm; }
.cover .kicker { font-size: 8.5pt; letter-spacing: .18em; text-transform: uppercase;
  color: #22A559; font-weight: 700; }
.cover h1 { font-size: 30pt; line-height: 1.1; margin: 6mm 0 4mm; letter-spacing: -.01em;
  color: #101214; border: 0; padding: 0; }
.cover .sub { font-size: 11.5pt; color: #4a5058; max-width: 132mm; line-height: 1.45; }
.cover .rule { height: 3px; width: 34mm; background: #22A559; margin: 8mm 0; border-radius: 2px; }
.cover dl { display: grid; grid-template-columns: 34mm 1fr; gap: 2mm 6mm; margin: 10mm 0 0;
  font-size: 9pt; }
.cover dt { color: #7b828b; text-transform: uppercase; letter-spacing: .08em; font-size: 7.6pt;
  padding-top: .6mm; }
.cover dd { margin: 0; color: #23262a; }
.cover dd code { background: none; border: 0; padding: 0; font-size: 8.6pt; }

/* ---------- headings ---------- */
h1 { font-size: 17pt; margin: 0 0 5mm; padding-bottom: 2.5mm; letter-spacing: -.01em;
  border-bottom: 2px solid #22A559; page-break-after: avoid; page-break-before: always; }
h2 { font-size: 13.2pt; margin: 9mm 0 3.5mm; padding-bottom: 2mm; letter-spacing: -.005em;
  border-bottom: 1px solid #dfe3e8; page-break-after: avoid; }
h2.flow { page-break-before: always; color: #0f1113; }
h2.flow .fnum { color: #22A559; }
h3 { font-size: 10.6pt; margin: 6mm 0 2.5mm; color: #23262a; page-break-after: avoid; }
h4 { font-size: 9.4pt; margin: 4.5mm 0 2mm; color: #3a4048; page-break-after: avoid; }
h1:first-of-type { page-break-before: avoid; }

p { margin: 0 0 3mm; }
strong { color: #101214; font-weight: 650; }
a { color: #1d6f3f; text-decoration: none; }
hr { border: 0; border-top: 1px solid #e6e9ed; margin: 6mm 0; }

/* ---------- code ---------- */
code {
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 8.1pt; background: #f4f6f8; border: 1px solid #e4e8ec;
  border-radius: 3px; padding: 0 2px; color: #0f3d24;
}
pre {
  background: #fafbfc; border: 1px solid #e2e6ea; border-left: 3px solid #22A559;
  border-radius: 4px; padding: 2.8mm 3.4mm; margin: 0 0 4mm; overflow: hidden;
  page-break-inside: avoid;
}
pre code {
  background: none; border: 0; padding: 0; color: #23262a; font-size: 7.5pt;
  line-height: 1.42; white-space: pre; display: block;
}
pre.wide code { font-size: 6.6pt; line-height: 1.36; }
pre.xwide code { font-size: 5.9pt; line-height: 1.32; }

/* ---------- tables ---------- */
table {
  width: 100%; border-collapse: collapse; margin: 0 0 4.5mm; font-size: 7.9pt;
  page-break-inside: auto;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th {
  text-align: left; background: #f2f4f6; color: #3c424a; font-weight: 650;
  font-size: 7.2pt; text-transform: uppercase; letter-spacing: .045em;
  padding: 1.9mm 2.2mm; border: 1px solid #dde2e7; border-top: 0;
}
td { padding: 1.7mm 2.2mm; border: 1px solid #e6eaee; vertical-align: top; }
tbody tr:nth-child(even) td { background: #fbfcfd; }
td code, th code { font-size: 7.2pt; padding: 0 1.5px; }

/* ---------- lists / quotes ---------- */
ul, ol { margin: 0 0 3.5mm; padding-left: 6mm; }
li { margin-bottom: 1.4mm; }
li > ul, li > ol { margin: 1.4mm 0 0; }
blockquote {
  margin: 0 0 4mm; padding: 2.6mm 3.4mm; background: #f3f9f5;
  border-left: 3px solid #22A559; border-radius: 0 4px 4px 0; color: #24402f;
}
blockquote p:last-child { margin-bottom: 0; }

/* ---------- severity pills ---------- */
.sev { display: inline-block; padding: 0 1.4mm; border-radius: 2px; font-size: 6.9pt;
  font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
.sev-high { background: #fdeaea; color: #a3231f; border: 1px solid #f3c9c7; }
.sev-medium { background: #fff3e0; color: #8a5300; border: 1px solid #f6dcb4; }
.sev-low { background: #eef3f8; color: #3f5a75; border: 1px solid #d6e0ea; }
"""

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
SEV = re.compile(r"\b(HIGH|MEDIUM|LOW)\b")


def slug(text: str) -> str:
    text = INLINE_CODE.sub(r"\1", text)
    text = "".join(c for c in text if unicodedata.category(c)[0] not in ("S", "C") or c == " ")
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    # GitHub's rule: each whitespace char becomes one hyphen (no collapsing), so
    # "FLOW 1 — Cold start" -> "flow-1--cold-start".
    return re.sub(r"[\s_]", "-", text)


def inline(text: str) -> str:
    """Inline formatting with code spans protected from every other rule."""
    slots: list[str] = []

    def stash(m: re.Match) -> str:
        slots.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(slots) - 1}\x00"

    text = INLINE_CODE.sub(stash, text)
    text = html.escape(text, quote=False)
    text = LINK.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', text)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = ITALIC.sub(r"<em>\1</em>", text)
    text = text.replace("\\|", "|").replace("\\_", "_")
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


def split_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    parts = re.split(r"(?<!\\)\|", line)
    # Unescape before inline processing so a `\|` inside a code span renders as "|".
    return [p.strip().replace("\\|", "|") for p in parts]


def pre_class(lines: list[str]) -> str:
    width = max((len(l) for l in lines), default=0)
    if width > 104:
        return " class=\"xwide\""
    if width > 86:
        return " class=\"wide\""
    return ""


FLOW_RE = re.compile(r"^FLOW (\d+) — (.+)$")


def convert(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code
        if stripped.startswith("```"):
            i += 1
            body: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(body))
            out.append(f"<pre{pre_class(body)}><code>{code}</code></pre>")
            continue

        # table
        if (stripped.startswith("|") and i + 1 < n
                and re.fullmatch(r"\|[\s:|-]+\|?", lines[i + 1].strip())):
            head = split_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i].strip()))
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            trs = []
            for r in rows:
                r = (r + [""] * len(head))[:len(head)]
                tds = []
                for c in r:
                    cell = inline(c)
                    cell = SEV.sub(
                        lambda m: f'<span class="sev sev-{m.group(1).lower()}">{m.group(1)}</span>',
                        cell)
                    tds.append(f"<td>{cell}</td>")
                trs.append(f"<tr>{''.join(tds)}</tr>")
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>")
            continue

        # headings
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            raw = m.group(2).strip()
            anchor = slug(raw)
            fm = FLOW_RE.match(raw)
            if level == 2 and fm:
                text = f'<span class="fnum">FLOW {fm.group(1)}</span> — {inline(fm.group(2))}'
                out.append(f'<h2 class="flow" id="{anchor}">{text}</h2>')
            else:
                out.append(f'<h{level} id="{anchor}">{inline(raw)}</h{level}>')
            i += 1
            continue

        # horizontal rule
        if re.fullmatch(r"-{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            body = []
            while i < n and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip())
                i += 1
            paras = "".join(f"<p>{inline(p)}</p>"
                            for p in re.split(r"\n\s*\n", "\n".join(body)) if p.strip())
            out.append(f"<blockquote>{paras}</blockquote>")
            continue

        # lists (one nesting level)
        if re.match(r"^\s*([-*]|\d+\.)\s+\S", line):
            ordered = bool(re.match(r"^\s*\d+\.\s", line))
            tag = "ol" if ordered else "ul"
            items: list[tuple[int, str]] = []
            while i < n and (re.match(r"^\s*([-*]|\d+\.)\s+\S", lines[i])
                             or (lines[i].strip() and lines[i].startswith("  ") and items)):
                cur = lines[i]
                lm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", cur)
                if lm:
                    items.append((len(lm.group(1)), lm.group(3).strip()))
                else:
                    depth, text = items[-1]
                    items[-1] = (depth, text + " " + cur.strip())
                i += 1
            buf: list[str] = []
            open_nested = False
            base = items[0][0] if items else 0
            for depth, text in items:
                if depth > base:
                    if not open_nested:
                        buf.append(f"<{tag}>")
                        open_nested = True
                    buf.append(f"<li>{inline(text)}</li>")
                else:
                    if open_nested:
                        buf.append(f"</{tag}>")
                        open_nested = False
                    buf.append(f"<li>{inline(text)}</li>")
            if open_nested:
                buf.append(f"</{tag}>")
            out.append(f"<{tag}>{''.join(buf)}</{tag}>")
            continue

        if not stripped:
            i += 1
            continue

        # paragraph
        para = [stripped]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^(\s*[-*]\s|\s*\d+\.\s|#{1,4}\s|\||>|```|-{3,}$)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    return "\n".join(out)


def cover(title: str, subtitle: str, meta: list[tuple[str, str]]) -> str:
    dl = "".join(f"<dt>{html.escape(k)}</dt><dd>{inline(v)}</dd>" for k, v in meta)
    return (
        '<section class="cover">'
        '<div class="kicker">Technical Reference &amp; System Audit</div>'
        f"<h1>{html.escape(title)}</h1>"
        '<div class="rule"></div>'
        f'<div class="sub">{inline(subtitle)}</div>'
        f"<dl>{dl}</dl>"
        "</section>"
    )


def main() -> int:
    src, dest = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "Technical Reference"
    subtitle = sys.argv[4] if len(sys.argv) > 4 else ""

    with open(src, encoding="utf-8") as f:
        md = f.read()

    # Drop the markdown title block; the cover page replaces it.
    lines = md.split("\n")
    start = 0
    for idx, line in enumerate(lines):
        if line.startswith("## Table of contents"):
            start = idx
            break
    meta_lines = [l for l in lines[:start] if l.strip().startswith("**")]
    meta: list[tuple[str, str]] = []
    for l in meta_lines:
        m = re.match(r"^\*\*(.+?):\*\*\s*(.*)$", l.strip())
        if m:
            meta.append((m.group(1), m.group(2)))
    body = convert("\n".join(lines[start:]))

    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"{cover(title, subtitle, meta)}{body}</body></html>"
    )

    html_path = os.path.splitext(dest)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"html  -> {html_path}  ({os.path.getsize(html_path):,} bytes)")

    chrome = next((p for p in CHROME_CANDIDATES if os.path.isfile(p)), "")
    if not chrome:
        print("ERROR: no Chrome/Edge binary found for PDF printing")
        return 1

    url = "file:///" + os.path.abspath(html_path).replace("\\", "/").replace(" ", "%20")
    cmd = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-first-run", "--no-default-browser-check",
        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=12000",
        "--no-pdf-header-footer", f"--print-to-pdf={os.path.abspath(dest)}", url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if not os.path.isfile(dest):
        print("ERROR: Chrome did not produce a PDF")
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        return 1
    print(f"pdf   -> {dest}  ({os.path.getsize(dest):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
