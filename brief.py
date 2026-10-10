#!/usr/bin/env python3
"""Daily brief generator.

Fetches feeds, asks Claude to pick and summarize items, writes site/brief.json.
The page is a front page: one lead story, a few front stories, then four sections.

Usage:
    ANTHROPIC_API_KEY=... python brief.py
    python brief.py --dry-run      # no network, uses fixtures/ (also writes a demo site/private.enc)
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
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml

import private

ROOT = Path(__file__).parent
SITE_DIR = Path(os.environ.get("BRIEF_SITE_DIR") or ROOT / "site")   # tests point this at a scratch folder
TZ = ZoneInfo("America/New_York")
USER_AGENT = "daily-brief/1.0 (+https://github.com/cmrlmatthews-max/daily-brief)"
TIMEOUT = 15
MAX_AGE_DAYS = 4
OG_TIMEOUT = 8
OG_MAX_BYTES = 200_000
OG_MAX_FETCHES = 25
LEAD_NOTE_CHARS = 1200   # about 150 words with headroom

SYSTEM_PROMPT = """You are the editor of a short personal daily brief, laid out like a newspaper front page, for one reader.
Pick the few items that are truly worth their attention today, and say why in plain words.

Rules:
- Be selective. Prefer substance over hype. No clickbait.
- Never invent facts. Use only what is in the item title and summary.
- Refer to items by their id only. Never write URLs.
- Sports items only if they are real news: trades, injuries, results.
- World items must be straight news, never opinion or analysis pieces.
- Notes must be neutral and factual for World and for anything political. No adjectives that take a side.
- "lead": the single most important story today. Strongly prefer an item with has_image=true,
  because the lead is shown with a large picture.
  "headline": a SHORT punchy newspaper headline rewritten from the item title. At most 8 words.
  Use only facts from the title and summary. Do not invent any.
  "note": the lead story write-up. 4 to 6 sentences, between 120 and 150 words. Write it ONLY from the item
  title and summary. Do not invent any fact, name, number, quote or date. If the summary is short, restate
  what it says in plain words and give plain context for the reader, but add no new facts.
  "decks": 2 or 3 short deck lines, like the summary lines printed under an old newspaper headline.
  Each at most 12 words. Write them only from the item title and summary. Do not invent any facts.
- "front": 2 to 4 other front-page stories, from any section. Note: 1 sentence, at most 25 words.
- "sections": one entry for EVERY topic listed in the message that has items left after "lead" and "front".
  Give 3 to 5 items per topic. No repeats of the lead or front items.
  Use the topic name exactly as listed. Do not leave "sections" empty.
  Note: 1 sentence, at most 25 words, saying why the item matters to this reader.
- has_image only tells you whether a picture is available. Never mention pictures in notes."""

_NOTE_ITEM = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
    "required": ["id", "note"],
}


def publish_tool(section_names: list[str]) -> dict[str, Any]:
    """The forced tool. Section names come from sources.yaml, so the enum always matches."""
    return {
        "name": "publish_brief",
        "description": "Publish today's brief by listing the chosen item ids with a short note each.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead": {
                    "type": "object",
                    "description": "The single most important story today. Strongly prefer has_image=true.",
                    "properties": {
                        "id": {"type": "string"},
                        "headline": {"type": "string",
                                     "description": "Short punchy newspaper headline, at most 8 words, no invented facts."},
                        "note": {"type": "string",
                                 "description": "4 to 6 sentences, 120 to 150 words, written only from the item "
                                                "title and summary. No invented facts."},
                        "decks": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "string"},
                                  "description": "Short deck lines under the headline. Each at most 12 words, "
                                                 "only from the item title and summary, no invented facts."},
                    },
                    "required": ["id", "headline", "note", "decks"],
                },
                "front": {"type": "array", "minItems": 2, "maxItems": 4, "items": _NOTE_ITEM,
                          "description": "Other front-page stories, from any section."},
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "enum": list(section_names)},
                            "items": {"type": "array", "minItems": 3, "maxItems": 5, "items": _NOTE_ITEM},
                        },
                        "required": ["topic", "items"],
                    },
                },
            },
            "required": ["lead", "front", "sections"],
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


YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url: str | None) -> str | None:
    """The 11-character video id of a YouTube watch URL (youtube.com/watch?v=ID or youtu.be/ID), else None."""
    if not url:
        return None
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https"):
        return None
    host = (u.hostname or "").lower()
    vid = None
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com") and u.path.rstrip("/") == "/watch":
        vid = (parse_qs(u.query).get("v") or [None])[0]
    elif host in ("youtu.be", "www.youtu.be"):
        vid = u.path.lstrip("/").split("/")[0] or None
    return vid if vid and YOUTUBE_ID.match(vid) else None


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
              summary: str, cap: int, image: str | None = None) -> dict | None:
    title = clean_text(title)
    if not title or not is_http(url):
        return None
    return {"id": item_id(url), "topic": topic, "source": source, "title": title, "url": url,
            "published": published, "summary": clean_text(summary, cap), "image": clean_image(image)}


# ---------- images ----------

IMG_EXT = re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:[?#].*)?$", re.I)
GITHUB_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?(?:[?#].*)?$", re.I)
TRACKER_HOSTS = ("feeds.feedburner.com", "feedburner.com", "doubleclick.net")
# Tracking images in feeds (for example NPR's npr-rss-pixel.png). These are not photos.
TRACKER_PATH = re.compile(r"(?:/tracking/|[-_/.]pixel[-_./?]|/beacon|spacer\.gif|/1x1[._-])", re.I)


def clean_image(url: Any, base: str | None = None) -> str | None:
    """Return an https image URL, or None. http is upgraded to https. Anything else is dropped."""
    if not isinstance(url, str):
        return None
    url = html.unescape(url).strip()
    if base:
        url = urljoin(base, url)
    if not is_http(url) or len(url) > 1000 or any(c.isspace() for c in url):
        return None
    if url.lower().startswith("http://"):
        url = "https://" + url[7:]
    if TRACKER_PATH.search(url) or any(h in url.lower() for h in TRACKER_HOSTS):
        return None
    return url


def _fix_image_url(url: str) -> str:
    """Size tweaks for known hosts: YouTube hqdefault, BBC 800px."""
    url = re.sub(r"/default\.jpg(?=$|\?)", "/hqdefault.jpg", url)
    if re.match(r"https://[^/]*\b(?:bbci\.co\.uk|bbc\.co\.uk)/", url):
        url = re.sub(r"/(standard|news)/\d{2,4}/", r"/\1/800/", url)
    return url


def _is_image_entry(d: dict) -> bool:
    typ = str(d.get("type") or "").lower()
    medium = str(d.get("medium") or "").lower()
    if medium == "image" or typ.startswith("image/"):
        return True
    if medium or typ:        # video, audio, flash, etc.
        return False
    return bool(IMG_EXT.search(str(d.get("url") or d.get("href") or "")))


def _width(d: dict) -> int:
    try:
        return int(d.get("width") or 0)
    except (TypeError, ValueError):
        return 0


def _img_tags(markup: str) -> list[str]:
    """src values of <img> tags, skipping 1px trackers and feed-tracking hosts."""
    out = []
    for tag in re.findall(r"<img\b[^>]*>", markup or "", flags=re.I):
        m = re.search(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, flags=re.I)
        if not m:
            continue
        src = html.unescape(m.group(1) or m.group(2) or "").strip()
        dims = re.findall(r"""\b(?:width|height)\s*=\s*["']?(\d+)""", tag, flags=re.I)
        if any(int(x) <= 2 for x in dims) or any(h in src.lower() for h in TRACKER_HOSTS):
            continue
        out.append(src)
    return out


def entry_image(e: Any, link: str = "") -> str | None:
    """Best image for a feedparser entry. See the order below."""
    gh = GITHUB_REPO.match(link or "")
    if gh:
        return github_image(gh.group(1))
    # 1. media:content, images only, largest width wins
    cands = [d for d in (e.get("media_content") or []) if isinstance(d, dict) and _is_image_entry(d)]
    cands.sort(key=lambda d: (str(d.get("medium") or "").lower() == "image", _width(d)), reverse=True)
    pool: list[str] = [d.get("url") for d in cands]
    # 2. media:thumbnail
    pool += [d.get("url") for d in (e.get("media_thumbnail") or []) if isinstance(d, dict)]
    # 3. enclosures, 4. links rel=enclosure
    pool += [d.get("href") or d.get("url") for d in (e.get("enclosures") or [])
             if isinstance(d, dict) and _is_image_entry({**d, "url": d.get("href") or d.get("url")})]
    pool += [d.get("href") for d in (e.get("links") or [])
             if isinstance(d, dict) and d.get("rel") == "enclosure" and _is_image_entry(d)]
    # 5. first <img> in summary / content
    html_bits = [e.get("summary") or ""] + [c.get("value", "") for c in (e.get("content") or [])
                                              if isinstance(c, dict)]
    for bit in html_bits:
        pool += _img_tags(bit)
    for raw in pool:
        img = clean_image(raw, link or None)
        if img:
            return _fix_image_url(img)
    return None


def github_image(full_name: str) -> str | None:
    full_name = (full_name or "").strip().strip("/")
    return f"https://opengraph.githubassets.com/1/{full_name}" if re.fullmatch(r"[\w.-]+/[\w.-]+", full_name) else None


def espn_image(article: dict) -> str | None:
    imgs = article.get("images") or []
    first = imgs[0] if imgs and isinstance(imgs[0], dict) else {}
    return clean_image(first.get("url"))


def apply_source_options(items: list[dict], src: dict) -> list[dict]:
    """Per-source options: title_suffix_strip and no_image."""
    suffix = src.get("title_suffix_strip")
    for i in items:
        if suffix and i["title"].lower().endswith(suffix.lower()):
            i["title"] = i["title"][: -len(suffix)].rstrip()
        if src.get("no_image"):
            i["image"] = None
    return [i for i in items if i["title"]]


def filter_titles(items: list[dict], patterns: list[str]) -> list[dict]:
    """Drop items whose title matches any exclude pattern (case-insensitive regex)."""
    rx = [re.compile(p, re.I) for p in patterns or []]
    return [i for i in items if not any(r.search(i["title"]) for r in rx)]


OG_META = re.compile(r"<meta\b[^>]*>", re.I)
OG_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def og_image_from_html(markup: str, base: str) -> str | None:
    """og:image, else twitter:image, from <meta> tags."""
    found: dict[str, str] = {}
    for tag in OG_META.findall(markup):
        attrs = {k.lower(): (v1 or v2) for k, v1, v2 in OG_ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src") and attrs.get("content"):
            found.setdefault("og" if key.startswith("og:") else "tw", attrs["content"])
    for k in ("og", "tw"):
        img = clean_image(found.get(k), base)
        if img:
            return img
    return None


def fetch_og_image(s: requests.Session, url: str) -> str | None:
    """Read the first 200 KB of an article page and return its og:image. None on any failure."""
    try:
        deadline = time.monotonic() + OG_TIMEOUT
        r = s.get(url, timeout=OG_TIMEOUT, stream=True, headers={"Accept": "text/html"})
        try:
            if r.status_code != 200:
                return None
            buf = b""
            for chunk in r.iter_content(16384):
                buf += chunk
                if len(buf) >= OG_MAX_BYTES or time.monotonic() > deadline:
                    break
        finally:
            r.close()
        return og_image_from_html(buf[:OG_MAX_BYTES].decode(r.encoding or "utf-8", errors="replace"), r.url)
    except Exception:
        return None


def resolve_og_images(out: dict, no_image_sources: set[str], session: requests.Session | None = None,
                      fetcher: Any = None) -> int:
    """Fill missing images on selected items (lead, front, sections) from the article page.
    At most OG_MAX_FETCHES fetches, run concurrently. Returns how many images were found."""
    s = session or requests.Session()
    if session is None:
        s.headers["User-Agent"] = USER_AGENT
    fetcher = fetcher or (lambda u: fetch_og_image(s, u))
    ordered = ([out["lead"]] if out.get("lead") else []) + list(out.get("front") or []) + \
        [i for sec in out.get("sections") or [] for i in sec["items"]]
    todo = [i for i in ordered if not i.get("image") and i["source"] not in no_image_sources][:OG_MAX_FETCHES]
    if not todo:
        return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for it, img in zip(todo, pool.map(lambda i: fetcher(i["url"]), todo)):
            if img:
                it["image"] = img
    return sum(1 for i in todo if i.get("image"))


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
                       e.get("summary", ""), cap, entry_image(e, e.get("link", "")))
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
                       to_iso(repo.get("created_at")), summary, cap, github_image(repo.get("full_name", "")))
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
                       a.get("description", ""), cap, espn_image(a))
        if it:
            items.append(it)
    return items


FETCHERS = {"rss": fetch_rss, "html_anthropic": fetch_html_anthropic,
            "github_search": fetch_github_search, "espn_news": fetch_espn_news}


def select_items(items: list[dict], src: dict, topic: str, cap: int) -> list[dict]:
    """Drop stale items (unless the source sets ignore_age), sort newest first."""
    if not src.get("ignore_age") and src["type"] != "github_search":
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
            items = filter_titles(apply_source_options(items, src), cfg.get("exclude_title_patterns") or [])
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
        urls = {i["url"].rstrip("/") for i in old.get("front", [])}
        if old.get("lead"):
            urls.add(old["lead"]["url"].rstrip("/"))
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
    slim = [{**{k: i[k] for k in ("id", "topic", "source", "title", "summary", "published")},
             "has_image": bool(i.get("image"))} for i in items]
    topics = section_names(cfg, items)
    user = (f"Reader profile:\n{cfg['reader_profile'].strip()}\n\nToday is {today}.\n\n"
            f"Topics (make a section for each): {', '.join(topics)}\n\n"
            f"Items (JSON):\n{json.dumps(slim, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Call publish_brief with your picks.")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=cfg["model"], max_tokens=int(cfg["max_output_tokens"]), system=SYSTEM_PROMPT,
        tools=[publish_tool([t["name"] for t in cfg["topics"]])],
        tool_choice={"type": "tool", "name": "publish_brief"},
        messages=[{"role": "user", "content": user}])
    block = next((b for b in msg.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError(f"no tool call in response (stop_reason={msg.stop_reason})")
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    return dict(block.input), usage


def section_names(cfg: dict, items: list[dict]) -> list[str]:
    """Configured section names, in order, that have at least one item."""
    have = {i["topic"] for i in items}
    return [t["name"] for t in cfg["topics"] if t["name"] in have]


def build_output(choice: dict, items: list[dict], order: list[str]) -> dict:
    """Map model-chosen ids back to real items. Unknown or repeated ids are dropped.
    Returns {"lead": item|None, "front": [...], "sections": [{"topic", "items"}]} with
    sections in the configured `order`."""
    by_id = {i["id"]: i for i in items}
    used: set[str] = set()

    def make(it: dict, note: Any) -> dict:
        return {"title": it["title"], "url": it["url"], "source": it["source"],
                "topic": it["topic"], "note": clean_text(str(note or ""), 300), "image": it.get("image"),
                "video": youtube_id(it["url"])}

    def pick(entries: Any, limit: int | None = None) -> list[dict]:
        out = []
        for e in entries if isinstance(entries, list) else []:
            iid = e.get("id") if isinstance(e, dict) else None
            if iid not in by_id or iid in used:
                continue
            used.add(iid)
            out.append(make(by_id[iid], e.get("note")))
        return out[:limit] if limit else out

    raw_lead = choice.get("lead")
    lead_list = pick([raw_lead], 1)
    lead = lead_list[0] if lead_list else None
    if lead:
        lead["note"] = clean_text(str(raw_lead.get("note") or ""), LEAD_NOTE_CHARS)
        lead["headline"] = clean_text(str(raw_lead.get("headline") or ""), 120) or lead["title"]
        raw_decks = raw_lead.get("decks")
        decks = [clean_text(str(d), 100) for d in raw_decks if isinstance(d, str)] \
            if isinstance(raw_decks, list) else []
        lead["decks"] = [d for d in decks if d][:3]
    front = pick(choice.get("front"), 4)

    # Group by each item's own topic so a section only holds its own sources, then order them.
    buckets: dict[str, list[dict]] = {name: [] for name in order}
    for sec in choice.get("sections") or []:
        for it in pick(sec.get("items") if isinstance(sec, dict) else None):
            buckets.setdefault(it["topic"], []).append(it)

    # Fallback: any section the model skipped gets its newest unused items, with no note.
    for topic in order:
        if buckets.get(topic):
            continue
        rest = [i for i in items if i["topic"] == topic and i["id"] not in used]
        rest.sort(key=lambda i: i.get("published") or "", reverse=True)
        for it in rest[:3]:
            used.add(it["id"])
            buckets[topic] = buckets.get(topic, []) + [make(it, "")]
    names = [n for n in order if buckets.get(n)] + [n for n in buckets if n not in order and buckets[n]]
    sections = [{"topic": n, "items": buckets[n][:5]} for n in names]
    return {"lead": lead, "front": front, "sections": sections}


def swap_lead_if_imageless(out: dict) -> bool:
    """If the lead has no image and a front story has one, swap them. Returns True if swapped.
    The new lead uses its own title as headline and has no decks.
    The old lead loses its headline and decks."""
    lead = out.get("lead")
    if not lead or lead.get("image"):
        return False
    for idx, f in enumerate(out.get("front") or []):
        if f.get("image"):
            demoted = {k: v for k, v in lead.items() if k not in ("headline", "decks")}
            out["lead"] = {**f, "headline": f["title"], "decks": []}
            out["front"][idx] = demoted
            return True
    return False


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the daily brief.")
    ap.add_argument("--dry-run", action="store_true", help="no network; use fixtures/")
    ap.add_argument("--fetch-only", action="store_true", help="fetch items, write out/items.json, skip Claude")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    now = datetime.now(TZ)
    today = f"{now:%A, %B} {now.day}, {now.year}"
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

    order = [t["name"] for t in cfg["topics"]]
    out_items = build_output(choice, items, order)
    lead, front, sections = out_items["lead"], out_items["front"], out_items["sections"]
    if not lead and not front and not sections:
        print("error: Claude returned no usable items", file=sys.stderr)
        return 1

    if not args.dry_run:   # dry-run never touches the network
        no_image = {src["name"] for t in cfg["topics"] for src in t.get("sources", []) if src.get("no_image")}
        found = resolve_og_images(out_items, no_image)
        print(f"og:image: found {found} extra images")
    if swap_lead_if_imageless(out_items):
        lead, front = out_items["lead"], out_items["front"]
        print("lead had no image; swapped with a front story that has one")

    # Private tabs (Plans and Mail). Skipped without BRIEF_PASSPHRASE. Prints counts only, never private text.
    extra = private.run_private(cfg, now, SITE_DIR, ROOT, dry_run=args.dry_run)
    usage = {k: usage[k] + extra[k] for k in ("input_tokens", "output_tokens")}

    brief = {"masthead": cfg.get("masthead", "CMRLM"), "tagline": cfg.get("tagline", "The Daily Brief"),
             "edition": cfg.get("edition", "Atlanta Edition"), "date_label": today,
             "generated_at": now.isoformat(timespec="seconds"), "model": cfg["model"],
             "lead": lead, "front": front, "sections": sections,
             "scores": scores, "source_errors": errors, "usage": usage}
    out = SITE_DIR / "brief.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n")
    in_sections = sum(len(s["items"]) for s in sections)
    with_img = sum(1 for i in ([lead] if lead else []) + front + [i for s in sections for i in s["items"]]
                   if i.get("image"))
    print(f"wrote {out.name}: lead={'yes' if lead else 'no'}, {len(front)} front, "
          f"{in_sections} in {len(sections)} sections, {with_img} with images, "
          f"{len(scores)} scores, {len(errors)} sources skipped | "
          f"tokens in={usage['input_tokens']} out={usage['output_tokens']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
