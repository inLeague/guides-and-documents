#!/usr/bin/env python3
"""Regenerate `<!-- toc -->` blocks in the guides-and-documents wiki.

Pages that contain a `<!-- toc -->` / `<!-- tocstop -->` marker pair get the
list between the markers rebuilt from the page's own headings.

Anchor slugs use a GitHub-flavored algorithm, validated against every rendered
heading anchor in the wiki (306/306 headings across all 27 pages, 2026-10-26;
re-validate with the same method if GitHub changes its anchor behavior).
Formatting mirrors the convention of the existing blocks:

* indentation is relative to the page's shallowest heading level
  (depth = level - min_level), two spaces per level;
* bullets cycle "-", "*", "+" by depth;
* inline links/images in a heading are flattened to their text in the label
  (nested link markup cannot render as a link at all);
* line endings are preserved per file (the wiki mixes CRLF and LF).

Pages without markers are never touched, and files are edited only between
their markers.

Usage:
    python3 scripts/wiki_toc.py <wiki-dir> [--check]

    --check   Report what would change; write nothing; exit 1 if anything
              would change.
"""
from __future__ import annotations

import argparse
import difflib
import html
import re
import sys
from pathlib import Path

MARKER_OPEN = "<!-- toc -->"
MARKER_CLOSE = "<!-- tocstop -->"
BULLETS = ("-", "*", "+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^(```+|~~~+)")


def gfm_slug(text: str) -> str:
    """Slug that matches GitHub's heading anchors for this corpus.

    Lowercase; keep letters/digits/hyphens/underscores/ASCII spaces; drop
    everything else; spaces become hyphens (consecutive spaces produce
    consecutive hyphens, as on GitHub). Unicode letters/digits survive via
    str.isalnum(). Note: this is why "2.7%" -> "27" and a non-breaking space
    simply disappears (GitHub drops it rather than hyphenating).
    """
    value = "".join(
        ch if (ch.isalnum() or ch in "-_ ") else "" for ch in text.lower()
    )
    return value.replace(" ", "-")


def flatten_inline(text: str) -> str:
    """Inline links/images -> their visible text (no nesting allowed in links)."""
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # images -> alt text
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)       # links -> link text
    return t


def rendered_heading_text(raw: str) -> str:
    """Approximate the text GitHub shows for a heading (the slug input)."""
    return html.unescape(re.sub(r"[*`~]+", "", flatten_inline(raw)))


def collect_headings(text: str):
    """Yield (level, raw_text) for ATX headings outside fenced code blocks."""
    headings = []
    fence = None
    for line in text.splitlines():
        stripped = line.lstrip()
        m = FENCE_RE.match(stripped)
        if m:
            token = m.group(1)
            if fence is None:
                fence = token[0] * 3
            elif stripped.startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        m = HEADING_RE.match(stripped)
        if m:
            headings.append((len(m.group(1)), m.group(2).strip()))
    return headings


def build_block(headings, eol: str) -> str:
    """Build the TOC list, with GitHub-style -N suffixes for duplicate slugs."""
    if not headings:
        return ""
    min_level = min(level for level, _ in headings)
    seen: dict[str, int] = {}
    lines = []
    for level, raw in headings:
        slug = gfm_slug(rendered_heading_text(raw))
        if slug not in seen:
            seen[slug] = 1
        else:
            k = seen[slug]
            candidate = f"{slug}-{k}"
            while candidate in seen:
                k += 1
                candidate = f"{slug}-{k}"
            seen[slug] = k + 1
            seen[candidate] = 1
            slug = candidate
        depth = level - min_level
        bullet = BULLETS[depth % len(BULLETS)]
        # Label = displayed text: inline markup flattened, stray non-breaking
        # spaces shown as spaces. The slug path above must keep the raw char --
        # GitHub drops it from the anchor rather than hyphenating.
        label = flatten_inline(raw).replace("\xa0", " ")
        lines.append(f"{'  ' * depth}{bullet} [{label}](#{slug})")
    return eol.join(lines)


def rebuild(text: str, path: Path) -> tuple[str, bool]:
    """Return (new_text, would_change) for one page."""
    if MARKER_OPEN not in text and MARKER_CLOSE not in text:
        return text, False
    if text.count(MARKER_OPEN) != 1 or text.count(MARKER_CLOSE) != 1:
        raise SystemExit(
            f"{path}: expected exactly one {MARKER_OPEN!r}/{MARKER_CLOSE!r} pair"
        )
    i = text.index(MARKER_OPEN)
    j = text.index(MARKER_CLOSE)
    if j < i or (i > 0 and text[i - 1] != "\n") or (j > 0 and text[j - 1] != "\n"):
        raise SystemExit(f"{path}: toc markers must each start their own line")
    eol = "\r\n" if "\r\n" in text else "\n"
    block = build_block(collect_headings(text), eol)
    if block:
        region = MARKER_OPEN + eol + eol + block + eol + eol + MARKER_CLOSE
    else:
        region = MARKER_OPEN + eol + eol + MARKER_CLOSE
    new_text = text[:i] + region + text[j + len(MARKER_CLOSE):]
    return new_text, new_text != text


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate <!-- toc --> blocks in wiki markdown pages."
    )
    ap.add_argument("wiki_dir", type=Path)
    ap.add_argument(
        "--check",
        action="store_true",
        help="report only; exit 1 if any page would change",
    )
    args = ap.parse_args()

    files = sorted(args.wiki_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"no .md files found in {args.wiki_dir}")

    marked = 0
    changed = []
    for path in files:
        text = path.read_bytes().decode("utf-8")
        if MARKER_OPEN not in text:
            continue
        marked += 1
        new_text, would_change = rebuild(text, path)
        if not would_change:
            print(f"clean    {path.name}")
            continue
        changed.append(path.name)
        print(f"CHANGED  {path.name}")
        if args.check:
            diff = difflib.unified_diff(
                text.splitlines(),
                new_text.splitlines(),
                fromfile=path.name,
                tofile=path.name,
                lineterm="",
            )
            for line in diff:
                print("    " + line)
        else:
            path.write_bytes(new_text.encode("utf-8"))
            print(f"         updated ({len(text)} -> {len(new_text)} chars)")

    summary = ", ".join(changed) if changed else "-"
    print(
        f"{len(files)} pages scanned; {marked} with TOC markers; "
        f"{len(changed)} changed: {summary}"
    )
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
