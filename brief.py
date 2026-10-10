#!/usr/bin/env python3
"""Daily brief generator.

Fetches feeds, asks Claude to pick and summarize items, writes site/brief.json.

Usage:
    ANTHROPIC_API_KEY=... python brief.py
    python brief.py --dry-run      # no network, uses fixtures/
    python brief.py --fetch-only   # fetch + normalize, write out/items.json
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml

ROOT = Path(__file__).parent
TZ = ZoneInfo("America/New_York")
USER_AGENT = "daily-brief/1.0 (+https://github.com/cmrlmatthews-max/daily-brief)"
TIMEOUT = 15
MAX_AGE_DAYS = 4

SYSTEM_PROMPT = """You are the editor of a short personal daily brief for one reader.
Pick the few items that are truly worth their attention today, and say why in one plain sentence each.

Rules:
- Be selective. Prefer substance over hype. No clickbait.
- Sports items only if they are real news: trades, injuries, results.
- Never invent facts. Use only what is in the item title and summary.
- Refer to items by their id only. Never write URLs.
- Each note is one plain sentence, at most 25 words, saying why the item matters to this reader.
- "top": 5 to 7 items across all topics. "sections": 2 to 4 items per topic, no repeats of top items.
  Leave out any topic with nothing worthwhile."""

PUBLISH_TOOL: dict[str, Any] = {
    "name": "publish_brief",
    "description": "Publish today's brief by listing the chosen item ids with a short note each.",
    "input_schema": {
        "type": "object",
        "properties": {
            "top": {
                "type": "array",
                "minItems": 5,
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["id", "note"],
                },
            },
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
                                "required": ["id", "note"],
                            },
                        },
                    },
                    "required": ["topic", "items"],
                },
            },
        },
        "required": ["top", "sections"],
    },
}


# ---------- helpers ----------

def clean_text(raw: str | None, limit: int | None = None) -> str:
    """Strip HTML, unescape entities, collapse whitespace, optionally truncate."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def is_http(url: str | None) -> bool:
    return bool(url) and url.lower().startswith(("http://", "https://"))


def to_iso(value: Any) -> str | None:
    """Convert a struct_time or ISO string to a UTC ISO string, or None."""
    try:
        if hasattr(value, "tm_year"):
            return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    return None


def make_item(topic: str, source: str, title: str, url: str, published: str | None,
              summary: str, cap: int) -> dict | None:
    title = clean_text(title)
    if not title or not is_http(url):
        return None
    return {"id": item_id(url), "topic": topic, "source": source, "title": title, "url": url,
            "published": published, "summary": clean_text(summary, cap)}


class _NewsLinks(HTMLParser):
    """Collect (href, text) for every <a href="/news/...">."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        href = dict(attrs).get("href") or ""
        if tag == "a" and href.startswith("/news/"):
            self._href, self._text = href, []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean_text(" ".join(self._text))))
            self._href = None


# ---------- source fetchers (each returns a list of items; may raise) ----------

def fetch_rss(s: requests.Session, src: dict, topic: str, cap: int) -> list[dict]:
    r = s.get(src["url"], timeout=TIMEOUT)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if not feed.entries:
        raise ValueError("feed has no entries")
    items = []
    for e in feed.entries:
        published = to_iso(e.get("published_parsed") or e.get("updated_parsed"))
        it = make_item(topic, src["name"], e.get("title", ""), e.get("link", ""), published,
                       e.get("summary", ""), cap)
        if it:
            items.append(it)
    return items


def fetch_html_anthropic(s: requests.Session, src: dict, topic: str, cap: int) -> list[dict]:
    r = s.get(src["url"], timeout=TIMEOUT)
    r.raise_for_status()
    parser = _NewsLinks()
    parser.feed(r.text)
    items, seen = [], set()
    for href, text in parser.links:
        url = urljoin(src["url"], href.split("#")[0].split("?")[0])
        if href.rstrip("/") == "/news" or url in seen or not text:
            continue
        seen.add(url)
        it = make_item(topic, src["name"], text, url, None, "", cap)
        if it:
            items.append(it)
    if not items:
        raise ValueError("no /news/ links found")
    return items


def fetch_github_search(s: requests.Session, src: dict, topic: str, cap: int) -> list[dict]:
    since = (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = s.get(src["url"].replace("{since}", since), headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    items = []
    for repo in r.json().get("items", []):
        extra = ", ".join(str(x) for x in (f"★ {repo.get('stargazers_count', 0)}", repo.get("language")) if x)
        summary = f"{repo.get('description') or ''} ({extra})".strip()
        it = make_item(topic, src["name"], repo.get("full_name", ""), repo.get("html_url", ""),
                       to_iso(repo.get("created_at")), summary, cap)
        if it:
            items.append(it)
    return items


def fetch_espn_news(s: requests.Session, src: dict, topic: str, cap: int) -> list[dict]:
    r = s.get(src["url"], timeout=TIMEOUT)
    r.raise_for_status()
    items = []
    for a in r.json().get("articles") or []:
        url = ((a.get("links") or {}).get("web") or {}).get("href", "")
        it = make_item(topic, src["name"], a.get("headline", ""), url, to_iso(a.get("published")),
                       a.get("description", ""), cap)
        if it:
            items.append(it)
    return items


FETCHERS = {"rss": fetch_rss, "html_anthropic": fetch_html_anthropic,
            "github_search": fetch_github_search, "espn_news": fetch_espn_news}


def select_items(items: list[dict], src: dict, topic: str, cap: int) -> list[dict]:
    """Drop stale items (not for GitHub), sort newest first, keep the per-source cap."""
    if topic.lower() != "github" and src["type"] != "github_search":
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        items = [i for i in items if not i["published"] or datetime.fromisoformat(i["published"]) >= cutoff]
    dated = sorted((i for i in items if i["published"]), key=lambda i: i["published"], reverse=True)
    return dated + [i for i in items if not i["published"]]


def fetch_all(cfg: dict) -> tuple[list[dict], list[dict]]:
    """Fetch every enabled source in parallel. Returns (items, source_errors)."""
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    per_source = int(cfg.get("per_source_item_cap", 8))
    summary_chars = int(cfg.get("summary_chars", 300))
    jobs = [(t["name"], src) for t in cfg["topics"] for src in t.get("sources", [])
            if src.get("enabled", True)]

    def run(job: tuple[str, dict]) -> tuple[list[dict], dict | None]:
        topic, src = job
        try:
            items = FETCHERS[src["type"]](s, src, topic, summary_chars)
            return select_items(items, src, topic, per_source)[:per_source], None
        except Exception as exc:  # one bad source must never stop the run
            return [], {"source": src["name"], "error": f"{type(exc).__name__}: {exc}"[:140]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, jobs))
    errors = [e for _, e in results if e]
    # Round-robin across sources so the total cap trims evenly.
    queues = [list(r[0]) for r in results if r[0]]
    items: list[dict] = []
    limit = int(cfg.get("max_total_items", 90))
    while queues and len(items) < limit:
        for q in list(queues):
            if len(items) >= limit:
                break
            items.append(q.pop(0))
            if not q:
                queues.remove(q)
    return items, errors


def drop_seen_yesterday(items: list[dict], site_url: str) -> list[dict]:
    """Remove items whose URL was in yesterday's published brief. Ignore any failure."""
    try:
        r = requests.get(site_url.rstrip("/") + "/brief.json", timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        old = r.json()
        urls = {i["url"].rstrip("/") for i in old.get("top", [])}
        for sec in old.get("sections", []):
            urls.update(i["url"].rstrip("/") for i in sec.get("items", []))
        return [i for i in items if i["url"].rstrip("/") not in urls]
    except Exception:
        return items


# ---------- scores ----------

def _score(comp: dict) -> int | None:
    sc = comp.get("score")
    sc = sc.get("value", sc.get("displayValue")) if isinstance(sc, dict) else sc
    try:
        return int(float(sc))
    except (TypeError, ValueError):
        return None


def _parse_event(event: dict, team_id: str) -> dict | None:
    comp = (event.get("competitions") or [{}])[0]
    mine = other = None
    for c in comp.get("competitors") or []:
        if str((c.get("team") or {}).get("id")) == team_id:
            mine = c
        else:
            other = c
    when = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(TZ)
    return {"when": when, "mine": mine or {}, "other": other or {},
            "done": bool((((comp.get("status") or {}).get("type")) or {}).get("completed"))}


def team_score(s: requests.Session, cfg: dict) -> dict:
    out = {"team": cfg["team"], "last": None, "last_date": None, "next": None, "url": None}
    base = f"https://site.api.espn.com/apis/site/v2/sports/{cfg['league_path']}/teams/{cfg['team_id']}/schedule"
    events: list = []
    for suffix in ("", "?seasontype=2") if cfg["league_path"].endswith("nfl") else ("",):
        r = s.get(base + suffix, timeout=TIMEOUT)
        r.raise_for_status()
        events = r.json().get("events") or []
        if events:
            break
    team_id, now = str(cfg["team_id"]), datetime.now(TZ)
    parsed = []
    for ev in events:
        try:
            p = _parse_event(ev, team_id)
            if p:
                parsed.append((p, ev))
        except Exception:
            continue
    parsed.sort(key=lambda x: x[0]["when"])
    done = [p for p in parsed if p[0]["done"]]
    future = [p for p in parsed if not p[0]["done"] and p[0]["when"] > now]

    def where(p: dict) -> str:
        return ("vs" if p["mine"].get("homeAway") == "home" else "at") + " " + \
            str((p["other"].get("team") or {}).get("abbreviation") or "?")

    if done:
        p, ev = done[-1]
        mine, theirs = _score(p["mine"]), _score(p["other"])
        if mine is not None and theirs is not None:
            res = "W" if mine > theirs else "L" if mine < theirs else "T"
            out["last"] = f"{res} {mine}–{theirs} {where(p)}"
            out["last_date"] = p["when"].strftime("%b ") + str(p["when"].day)
            out["url"] = next((l.get("href") for l in ev.get("links") or [] if is_http(l.get("href"))), None)
    if future:
        p, ev = future[0]
        out["next"] = f"{p['when']:%a %b} {p['when'].day} {where(p)}"
        out["url"] = out["url"] or next((l.get("href") for l in ev.get("links") or []
                                         if is_http(l.get("href"))), None)
    return out


def fetch_scores(cfg: dict) -> tuple[list[dict], list[dict]]:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    scores, errors = [], []
    for sc in cfg.get("scores", []):
        try:
            scores.append(team_score(s, sc))
        except Exception as exc:
            errors.append({"source": f"Scores: {sc.get('team')}", "error": f"{type(exc).__name__}: {exc}"[:140]})
    return scores, errors


# ---------- Claude ----------

def ask_claude(cfg: dict, items: list[dict], today: str) -> tuple[dict, dict]:
    import anthropic

    key = os.environ["ANTHROPIC_API_KEY"]
    slim = [{k: i[k] for k in ("id", "topic", "source", "title", "summary", "published")} for i in items]
    user = (f"Reader profile:\n{cfg['reader_profile'].strip()}\n\nToday is {today}.\n\n"
            f"Items (JSON):\n{json.dumps(slim, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Call publish_brief with your picks.")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=cfg["model"], max_tokens=int(cfg["max_output_tokens"]), system=SYSTEM_PROMPT,
        tools=[PUBLISH_TOOL], tool_choice={"type": "tool", "name": "publish_brief"},
        messages=[{"role": "user", "content": user}])
    block = next((b for b in msg.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError(f"no tool call in response (stop_reason={msg.stop_reason})")
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    return dict(block.input), usage


def build_output(choice: dict, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Map model-chosen ids back to real items. Unknown or repeated ids are dropped."""
    by_id = {i["id"]: i for i in items}
    used: set[str] = set()

    def pick(entries: Any) -> list[dict]:
        out = []
        for e in entries if isinstance(entries, list) else []:
            iid = e.get("id") if isinstance(e, dict) else None
            if iid not in by_id or iid in used:
                continue
            used.add(iid)
            it = by_id[iid]
            out.append({"title": it["title"], "url": it["url"], "source": it["source"],
                        "topic": it["topic"], "note": clean_text(str(e.get("note", "")), 220)})
        return out

    top = pick(choice.get("top"))
    sections = []
    for sec in choice.get("sections") or []:
        picked = pick(sec.get("items")) if isinstance(sec, dict) else []
        if picked:
            sections.append({"topic": str(sec.get("topic", "More")), "items": picked})
    return top, sections


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the daily brief.")
    ap.add_argument("--dry-run", action="store_true", help="no network; use fixtures/")
    ap.add_argument("--fetch-only", action="store_true", help="fetch items, write out/items.json, skip Claude")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    now = datetime.now(TZ)
    today = f"{now:%A, %B} {now.day}"
    errors: list[dict] = []

    if not (args.dry_run or args.fetch_only or os.environ.get("ANTHROPIC_API_KEY")):
        print("error: ANTHROPIC_API_KEY is not set. Set it, or use --dry-run.", file=sys.stderr)
        return 1

    if args.dry_run:
        fixture = json.loads((ROOT / "fixtures/sample_items.json").read_text())
        items, scores = fixture["items"], fixture.get("scores", [])
    else:
        items, errors = fetch_all(cfg)
        scores, score_errors = fetch_scores(cfg)
        errors += score_errors
        if args.fetch_only:
            (ROOT / "out").mkdir(exist_ok=True)
            (ROOT / "out/items.json").write_text(json.dumps(
                {"items": items, "scores": scores, "source_errors": errors}, indent=2, ensure_ascii=False))
            print(f"fetched {len(items)} items, {len(scores)} scores, {len(errors)} errors -> out/items.json")
            for e in errors:
                print(f"  skipped {e['source']}: {e['error']}")
            return 0
        items = drop_seen_yesterday(items, cfg["site_url"])

    if not items:
        print("error: no items to summarize", file=sys.stderr)
        return 1

    if args.dry_run:
        choice = json.loads((ROOT / "fixtures/sample_response.json").read_text())
        usage = {"input_tokens": 0, "output_tokens": 0}
    else:
        try:
            choice, usage = ask_claude(cfg, items, today)
        except Exception as exc:
            print(f"error: Claude call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    top, sections = build_output(choice, items)
    if not top and not sections:
        print("error: Claude returned no usable items", file=sys.stderr)
        return 1

    brief = {"generated_at": now.isoformat(timespec="seconds"), "date_label": today,
             "model": cfg["model"], "top": top, "sections": sections, "scores": scores,
             "source_errors": errors, "usage": usage}
    out = ROOT / "site/brief.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n")
    n = len(top) + sum(len(s["items"]) for s in sections)
    print(f"wrote {out.relative_to(ROOT)}: {len(top)} top, {n - len(top)} in sections, "
          f"{len(scores)} scores, {len(errors)} sources skipped | "
          f"tokens in={usage['input_tokens']} out={usage['output_tokens']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
