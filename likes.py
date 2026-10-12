#!/usr/bin/env python3
"""Save liked stories sent from the phone.

Reads env LIKES_JSON (a JSON array) and appends valid, new entries to data/likes.jsonl.
The folder is BRIEF_DATA_DIR, else ./data. Standard library only.

Each entry: {"u": url, "t": title, "s": source (optional), "c": topic (optional)}.
Prints counts only. Exit 1 on invalid JSON or a non-list; exit 0 otherwise (0 added is fine).
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ENTRIES = 50
URL_MAX, TITLE_MAX, SOURCE_MAX, TOPIC_MAX = 1000, 300, 100, 60


def clean(value: Any, limit: int) -> str:
    """Text with control characters removed (tabs and newlines become spaces), whitespace collapsed, capped."""
    if not isinstance(value, str):
        return ""
    out = []
    for ch in value:
        cat = unicodedata.category(ch)
        if ch.isspace():
            out.append(" ")
        elif cat[0] != "C":          # other control, format, surrogate and unassigned characters are dropped
            out.append(ch)
    return " ".join("".join(out).split())[:limit].rstrip()


def valid_url(value: Any) -> str | None:
    """The url if it is http(s), at most 1000 characters, with no whitespace; else None."""
    if not isinstance(value, str) or len(value) > URL_MAX or any(c.isspace() for c in value):
        return None
    if not value.lower().startswith(("http://", "https://")) or len(value.split("://", 1)[1]) == 0:
        return None
    return value


def clean_entry(raw: Any) -> dict | None:
    """One accepted like as {"u", "t", "s", "c"}, or None."""
    if not isinstance(raw, dict):
        return None
    url, title = valid_url(raw.get("u")), clean(raw.get("t"), TITLE_MAX)
    if not url or not title:
        return None
    return {"u": url, "t": title, "s": clean(raw.get("s"), SOURCE_MAX), "c": clean(raw.get("c"), TOPIC_MAX)}


def norm(url: str) -> str:
    return url.rstrip("/")


def existing_urls(path: Path) -> set[str]:
    """The urls already in the log. Bad lines are ignored."""
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return seen
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("u"), str):
            seen.add(norm(rec["u"]))
    return seen


def save(entries: list, folder: Path, now: datetime | None = None) -> tuple[int, int]:
    """Append the valid, new entries. Returns (received, added)."""
    received = len(entries)
    path = folder / "likes.jsonl"
    seen = existing_urls(path)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    lines = []
    for raw in entries[:MAX_ENTRIES]:
        rec = clean_entry(raw)
        if not rec or norm(rec["u"]) in seen:
            continue
        seen.add(norm(rec["u"]))
        lines.append(json.dumps({"at": stamp, **rec}, ensure_ascii=False, separators=(",", ":")))
    if lines:
        folder.mkdir(parents=True, exist_ok=True)
        lead = ""
        try:
            if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b"\n"):
                lead = "\n"
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as f:
            f.write(lead + "\n".join(lines) + "\n")
    return received, len(lines)


def main() -> int:
    try:
        entries = json.loads(os.environ.get("LIKES_JSON", ""))
    except (ValueError, RecursionError):
        print("likes: invalid JSON", file=sys.stderr)
        return 1
    if not isinstance(entries, list):
        print("likes: expected a JSON list", file=sys.stderr)
        return 1
    folder = Path(os.environ.get("BRIEF_DATA_DIR") or "data")
    received, added = save(entries, folder)
    print(f"likes: received {received}, added {added}, skipped {received - added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
