#!/usr/bin/env python3
"""Daily brief generator.

Fetches feeds, asks Claude to pick and summarize items, writes site/brief.json.
The page is a front page (one lead story, a few front stories, 2 to 3 items per topic) plus one tab per
topic group. Call 1 to Claude writes the front page. Call 2 picks the longer list for the topic tabs.
Call 3 picks the best YouTube videos of the past two days for the YouTube tab.

Usage:
    ANTHROPIC_API_KEY=... python brief.py
    python brief.py --dry-run      # no network, uses fixtures/ (also writes a demo site/private.enc)
    python brief.py --fetch-only   # fetch + normalize (news items and videos), write out/items.json
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
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
LISTEN_CHARS = 4000      # the spoken script, about 450 words with headroom
ARCHIVE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
CHANNEL_ID = re.compile(r"UC[\w-]{22}", re.ASCII)
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
VIDEO_MAX_TOKENS = 3000   # call 3: at most 12 short notes
YOUTUBE_RETRY_WAIT = 2.0  # seconds before the one retry of a failed YouTube feed
YOUTUBE_FOLD_AFTER = 3    # more channel failures than this are folded into one source error
RECENT_DAYS = 3           # lead and front stories from this many days back count as "seen recently"
RECENT_LINES = 20         # at most this many recent headlines go to call 1
REPEAT_STOP = set("a an the and or but of to in on at for by with from as is are was were be been has have had it its "
                  "this that these those after before over under into about says said say new will would can could "
                  "not no than more most up out off amid via vs".split())
REPEAT_MIN_SHARED = 3     # two headlines are one story when they share this many words...
REPEAT_MIN_RATIO = 0.5    # ...and the shared words are this share of the shorter headline

SYSTEM_PROMPT = """You are the editor of a short personal daily brief, laid out like a newspaper front page, for one reader.
Pick the few items that are truly worth their attention today, and say why in plain words.

Rules:
- Be selective. Prefer substance over hype. No clickbait.
- Never invent facts. Use only what is in the item title and summary.
- Refer to items by their id only. Never write URLs.
- Sports items only if they are real news (trades, injuries, results, fights).
- A team source also carries stories about other teams and players. Name a team only when the item title
  or summary names it. Never take the team from the source name.
- World items must be straight news, never opinion or analysis pieces.
- Politics items must be news.
  Use neutral wording. No adjectives that take a side.
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
- Items with seen_recently=true were on the front page in the last 3 days.
  Do not use them for "lead" or "front" unless the item reports a major new development
  (a new fact, decision, result, or number).
  When you do use one, fill "whats_new" with one short sentence, at most 15 words, that says what is new.
  Make the headline (lead) or the note (front) say what is new.
  They may still go in "sections".
- "sections": one entry for EVERY topic listed in the message that has items left after "lead" and "front".
  Give 2 to 3 items per topic. No repeats of the lead or front items.
  Use the topic name exactly as listed. Do not leave "sections" empty.
  Note: 1 sentence, at most 25 words, saying why the item matters to this reader.
- has_image only tells you whether a picture is available. Never mention pictures in notes.
- "listen": a spoken version of today's paper, for a phone voice to read aloud. 300 to 450 words.
  Start with these exact words: Good morning. This is The Daily Brief for <weekday, month day>.
  Use today's date from the message.
  Cover the lead story, then the front stories, then one short sentence for each section.
  Write for the ear: short plain sentences, no URLs, no lists or bullets, no symbols, no headlines in all caps.
  Spell out abbreviations a voice reads badly. Use only facts from the item titles and summaries. Do not invent any.
  Use neutral wording for World and for politics.
  End with these exact words: That is the brief. Have a good day."""

_NOTE_ITEM = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
    "required": ["id", "note"],
}

_WHATS_NEW = {"type": "string",
              "description": "Only for an item with seen_recently=true: one short sentence on what is new. "
                             "Else omit."}
# Front stories may carry whats_new; section notes may not, so they keep _NOTE_ITEM.
_FRONT_ITEM = {**_NOTE_ITEM, "properties": {**_NOTE_ITEM["properties"], "whats_new": _WHATS_NEW}}


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
                        "whats_new": _WHATS_NEW,
                    },
                    "required": ["id", "headline", "note", "decks"],
                },
                "front": {"type": "array", "minItems": 2, "maxItems": 4, "items": _FRONT_ITEM,
                          "description": "Other front-page stories, from any section."},
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "enum": list(section_names)},
                            "items": {"type": "array", "minItems": 2, "maxItems": 3, "items": _NOTE_ITEM},
                        },
                        "required": ["topic", "items"],
                    },
                },
                "listen": {"type": "string",
                           "description": "Spoken version of today's paper for a phone voice, 300 to 450 words. Short "
                                          "plain sentences, no URLs, lists or symbols. No invented facts."},
            },
            "required": ["lead", "front", "sections", "listen"],
        },
    }


DESK_PROMPT = """You are the editor of the topic pages of a short personal daily newspaper, for one reader.
The front page is already done. Now you fill the page of each topic.

Rules:
- For every topic that has items in the message, pick the 6 to 10 best items. Pick fewer only if the topic
  has fewer. Put the best first. You may pick items that are also on the front page.
- Be selective. Prefer substance over hype. No clickbait.
- Never invent facts. Use only what is in the item title and summary.
- Refer to items by id only. Never write URLs.
- Note: 1 to 2 sentences, at most 35 words, saying what the story is and why it matters to this reader.
- Sports items only if they are real news (trades, injuries, results, fights).
- A team source also carries stories about other teams and players. Name a team only when the item title
  or summary names it. Never take the team from the source name.
- World items must be straight news, never opinion or analysis pieces.
- Politics items must be news.
- Notes must be neutral and factual for World and for anything political. No adjectives that take a side.
- Use the topic name exactly as listed. Do not leave "sections" empty.
- has_image only tells you whether a picture is available. Never mention pictures in notes."""

DESK_MIN_ITEMS, DESK_MAX_ITEMS = 4, 10
DESK_FALLBACK_ITEMS = 8


def desk_tool(section_names: list[str]) -> dict[str, Any]:
    """The forced tool for call 2 (the topic tabs)."""
    return {
        "name": "publish_desk",
        "description": "Publish the topic pages by listing the chosen item ids with a short note each.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "enum": list(section_names)},
                            "items": {"type": "array", "minItems": DESK_MIN_ITEMS, "maxItems": DESK_MAX_ITEMS,
                                      "items": {
                                          "type": "object",
                                          "properties": {"id": {"type": "string"},
                                                         "note": {"type": "string",
                                                                  "description": "1 to 2 sentences, at most 35 words. "
                                                                                 "No invented facts."}},
                                          "required": ["id", "note"]}},
                        },
                        "required": ["topic", "items"],
                    },
                },
            },
            "required": ["sections"],
        },
    }


VIDEO_PROMPT = """You are the video editor of a short personal daily newspaper, for one reader.
You fill the YouTube page. The reader has YouTube Premium and wants to watch only videos that are worth the time.

Rules:
- Pick the best {lo} to {hi} videos in total from the list. Pick fewer only if the list has fewer.
- Put each pick under the group of its item. Use the group name exactly as listed.
- Put the best video first within each group.
- Spread the picks across the groups. Pick at least one video from every group that has any, if the quality allows.
- Be selective. Prefer substance over hype. No clickbait.
- Skip a clip that is only a promo, a trailer, or a sponsor read. Skip a livestream replay when the title or
  description says it is over 3 hours long. Skip near-duplicates: of videos that cover the same thing, pick the best one.
- Note: 1 sentence, at most 25 words, saying what the video covers. Write it only from the title and description.
  Never invent facts.
- Use neutral wording for politics. No adjectives that take a side.
- A channel also posts about other teams and players. Name a team only when the title or description names it.
  Never take the team from the channel name.
- Refer to videos by id only. Never write URLs."""


def videos_tool(group_names: list[str], pick_max: int) -> dict[str, Any]:
    """The forced tool for call 3 (the YouTube tab). Group names come from sources.yaml, so the enum always matches."""
    return {
        "name": "publish_videos",
        "description": "Publish the YouTube page by listing the chosen video ids with a short note each, by group.",
        "input_schema": {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "group": {"type": "string", "enum": list(group_names)},
                            "items": {"type": "array", "minItems": 1, "maxItems": pick_max,
                                      "items": {
                                          "type": "object",
                                          "properties": {"id": {"type": "string"},
                                                         "note": {"type": "string",
                                                                  "description": "1 sentence, at most 25 words, "
                                                                                 "from the title and description only. "
                                                                                 "No invented facts."}},
                                          "required": ["id", "note"]}},
                        },
                        "required": ["group", "items"],
                    },
                },
            },
            "required": ["groups"],
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
    """Fill missing images on selected items (lead, front, sections, then the tab pages) from the article page.
    The same story on the front page and on a tab is fetched once and shares the picture.
    At most OG_MAX_FETCHES fetches, run concurrently, front page first. Returns how many images were found."""
    s = session or requests.Session()
    if session is None:
        s.headers["User-Agent"] = USER_AGENT
    fetcher = fetcher or (lambda u: fetch_og_image(s, u))
    ordered = ([out["lead"]] if out.get("lead") else []) + list(out.get("front") or []) + \
        [i for sec in out.get("sections") or [] for i in sec["items"]] + \
        [i for tab in out.get("tabs") or [] for sec in tab["sections"] for i in sec["items"]]
    known = {i["url"]: i["image"] for i in ordered if i.get("image")}
    for i in ordered:                     # a copy of a story that already has a picture gets it for free
        if not i.get("image") and i["url"] in known:
            i["image"] = known[i["url"]]
    todo, seen = [], set()
    for i in ordered:
        if not i.get("image") and i["source"] not in no_image_sources and i["url"] not in seen:
            seen.add(i["url"])
            todo.append(i)
    todo = todo[:OG_MAX_FETCHES]
    if not todo:
        return 0
    found: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for it, img in zip(todo, pool.map(lambda i: fetcher(i["url"]), todo)):
            if img:
                found[it["url"]] = img
    for i in ordered:
        if not i.get("image") and i["url"] in found:
            i["image"] = found[i["url"]]
    return len(found)


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


# ---------- YouTube (its own tab; the videos never go to calls 1 and 2) ----------

URL_IN_TEXT = re.compile(r"https?://\S+")


def _count(value: Any, default: int, lo: int = 1, hi: int = 1000) -> int:
    """A whole number inside [lo, hi] from the config, else `default`."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def youtube_settings(cfg: dict) -> dict | None:
    """The `youtube:` block, checked. Returns {"max_age_days", "per_channel", "pick_min", "pick_max",
    "groups": [{"name", "channels": [{"name", "id", "url"}]}], "bad": [names of skipped channels]}, or None
    when there is nothing to fetch. A channel id must be UC plus 22 letters, digits, "_" or "-"; a channel with
    a bad or repeated id, or no name, is skipped. A group with no good channel is dropped. Never raises."""
    raw = cfg.get("youtube") if isinstance(cfg, dict) else None
    if not isinstance(raw, dict):
        return None
    groups, bad, seen_ids, seen_names = [], [], set(), set()
    for g in raw.get("groups") if isinstance(raw.get("groups"), list) else []:
        if not isinstance(g, dict):
            continue
        gname = clean_text(str(g.get("name") or ""), 60)
        if not gname or gname in seen_names:
            continue
        channels = []
        for ch in g.get("channels") if isinstance(g.get("channels"), list) else []:
            if not isinstance(ch, dict):
                continue
            cid, cname = str(ch.get("id") or "").strip(), clean_text(str(ch.get("name") or ""), 80)
            if not CHANNEL_ID.fullmatch(cid) or not cname or cid in seen_ids:
                bad.append(cname or "unnamed channel")
                continue
            seen_ids.add(cid)
            channels.append({"name": cname, "id": cid, "url": YOUTUBE_FEED + cid})
        if channels:
            seen_names.add(gname)
            groups.append({"name": gname, "channels": channels})
    if not groups:
        return None
    pick_max = _count(raw.get("pick_max"), 12, 1, 50)
    return {"max_age_days": _count(raw.get("max_age_days"), 2, 1, 30),
            "per_channel": _count(raw.get("per_channel"), 3, 1, 15),
            "pick_min": min(_count(raw.get("pick_min"), 8, 1, 50), pick_max), "pick_max": pick_max,
            "groups": groups, "bad": bad}


def is_short(title: str, link: str, summary: str = "") -> bool:
    """A YouTube Short: the link path starts with /shorts/, or the title or summary has #shorts (any case)."""
    try:
        path = urlsplit(link or "").path
    except ValueError:
        path = ""
    return path.startswith("/shorts/") or "#shorts" in f"{title or ''} {summary or ''}".lower()


def fetch_channel(s: requests.Session, ch: dict, group: str, yt: dict, summary_chars: int,
                  now: datetime) -> list[dict]:
    """One channel's newest videos: no Shorts, none older than max_age_days, only real watch links,
    newest first, at most per_channel. Topic is the group name, source is the channel name.
    The picture is the feed thumbnail, else the standard YouTube still. A feed with 0 views (an upcoming
    premiere or scheduled live stream) is skipped. A 404, 429 or 5xx is tried once more after a short wait.
    Raises on a bad feed."""
    r = s.get(ch["url"], timeout=TIMEOUT)
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:   # YouTube feeds often fail once and work a moment later
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if not isinstance(code, int) or not (code in (404, 429) or 500 <= code < 600):
            raise
        time.sleep(YOUTUBE_RETRY_WAIT)
        r = s.get(ch["url"], timeout=TIMEOUT)
        r.raise_for_status()
    feed = feedparser.parse(r.content)
    if not feed.entries:
        raise ValueError("feed has no entries")
    cutoff = now - timedelta(days=yt["max_age_days"])
    items = []
    for e in feed.entries:
        link, title = e.get("link", ""), clean_text(e.get("title", ""))
        vid = youtube_id(link)
        if str((e.get("media_statistics") or {}).get("views", "")).strip() == "0":
            continue   # an upcoming premiere or a scheduled live stream; missing stats = keep
        published = to_iso(e.get("published_parsed") or e.get("updated_parsed"))
        if not vid or is_short(title, link, e.get("summary", "")) or not published or datetime.fromisoformat(published) < cutoff:
            continue
        image = entry_image(e, link) or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        it = make_item(group, ch["name"], title, link, published, URL_IN_TEXT.sub("", e.get("summary", "")),
                       summary_chars, image)
        if it:
            items.append(it)
    items.sort(key=lambda i: i["published"], reverse=True)
    return items[: yt["per_channel"]]


def fetch_youtube(cfg: dict, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Fetch every YouTube channel in parallel. Returns (items, source_errors); items are in group order,
    then channel order, newest first within a channel. A bad channel is a source error, never a stop."""
    yt = youtube_settings(cfg)
    if not yt:
        return [], []
    errors = [{"source": name, "error": "ValueError: bad channel id"} for name in yt["bad"]]
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    now = now or datetime.now(timezone.utc)
    summary_chars = int(cfg.get("summary_chars", 300))
    jobs = [(g["name"], ch) for g in yt["groups"] for ch in g["channels"]]

    def run(job: tuple[str, dict]) -> tuple[list[dict], dict | None]:
        group, ch = job
        try:
            return fetch_channel(s, ch, group, yt, summary_chars, now), None
        except Exception as exc:  # one bad channel must never stop the run
            return [], {"source": ch["name"], "error": f"{type(exc).__name__}: {exc}"[:140]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, jobs))
    items, seen, failed = [], set(), []
    for found, err in results:
        if err:
            failed.append(err)
        for it in found:
            if it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    if failed:
        print(f"youtube: {len(failed)} of {len(jobs)} channels failed")
    if len(failed) > YOUTUBE_FOLD_AFTER:   # one line instead of a wall of errors
        names = ", ".join(e["source"] for e in failed)[:200]
        errors.append({"source": "YouTube", "error": f"{len(failed)} of {len(jobs)} channels failed: {names}"})
    else:
        errors += failed
    return items, errors


def fetch_yesterday(site_url: str) -> dict:
    """Yesterday's live brief.json as a dict, or {} on any failure. Never raises."""
    try:
        r = requests.get(site_url.rstrip("/") + "/brief.json", timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        old = r.json()
        return old if isinstance(old, dict) else {}
    except Exception:
        return {}


def drop_seen_yesterday(items: list[dict], site_url: str, old: dict | None = None) -> list[dict]:
    """Remove items whose URL was in yesterday's published brief. Ignore any failure.
    `old` is the live brief when the caller already fetched it; else it is fetched here."""
    try:
        if old is None:
            old = fetch_yesterday(site_url)
        urls = {i["url"].rstrip("/") for i in old.get("front", [])}
        if old.get("lead"):
            urls.add(old["lead"]["url"].rstrip("/"))
        for sec in old.get("sections", []):
            urls.update(i["url"].rstrip("/") for i in sec.get("items", []))
        return [i for i in items if i["url"].rstrip("/") not in urls]
    except Exception:
        return items


def headline_words(text: Any) -> set[str]:
    """The words of a headline that carry meaning: lower case, no stop words, no short words, plural 's' cut."""
    w = re.findall(r"[a-z0-9]+", str(text or "").lower().replace("’", "'").replace("'s", ""))
    out = set()
    for x in w:
        if x in REPEAT_STOP or len(x) < 3:
            continue
        if len(x) > 4 and x.endswith("s") and not x.endswith("ss"):
            x = x[:-1]
        out.add(x)
    return out


def same_story(a: Any, b: Any) -> bool:
    """True when two headlines share enough words to be the same story, even from different outlets."""
    wa, wb = headline_words(a), headline_words(b)
    if not wa or not wb:
        return False
    shared = wa & wb
    return len(shared) >= REPEAT_MIN_SHARED and len(shared) / min(len(wa), len(wb)) >= REPEAT_MIN_RATIO


def recent_front_headlines(folder: Path | str, today: Any, old: dict | None, days: int = RECENT_DAYS) -> list[str]:
    """Lead title, lead headline and front titles of the papers from `today` back `days` days (archive files),
    then of the live brief `old`. No repeats, order kept, no blanks. Never raises: any problem gives []."""
    try:
        # load_archive_papers starts one day back, so ask from tomorrow to include today's own file (a reprint)
        papers = [p for _, p in load_archive_papers(folder, today + timedelta(days=1), days + 1)]
        if isinstance(old, dict):
            papers.append(old)
        out: list[str] = []
        for paper in papers:
            lead, front = paper.get("lead"), paper.get("front")
            found = [lead.get("title"), lead.get("headline")] if isinstance(lead, dict) else []
            found += [f.get("title") for f in front if isinstance(f, dict)] if isinstance(front, list) else []
            for text in found:
                text = clean_text(text) if isinstance(text, str) else ""
                if text and text not in out:
                    out.append(text)
        return out
    except Exception:
        return []


def mark_recent_repeats(items: list[dict], headlines: list[str]) -> list[dict]:
    """Copies of `items` where each item that tells a story from `headlines` has "seen_recently": True.
    Never raises: on any problem the items come back unchanged."""
    try:
        return [{**i, "seen_recently": True} if any(same_story(i["title"], h) for h in headlines) else i
                for i in items]
    except Exception:
        return items


def drop_seen_videos(videos: list[dict], old: dict | None) -> list[dict]:
    """Remove fetched videos that were on yesterday's YouTube tab (old["videos"][*]["items"][*]["url"]).
    Compared by YouTube id when the link has one, else by URL. Ignore any failure."""
    try:
        def key(url: str) -> str:
            return youtube_id(url) or url.rstrip("/")
        seen = {key(i["url"]) for g in (old or {}).get("videos", []) for i in g.get("items", [])}
        return [v for v in videos if key(v["url"]) not in seen]
    except Exception:
        return videos


# ---------- one paper per day ----------

def printed_today(brief: dict | None, now: datetime) -> bool:
    """True if brief was generated on the same New York calendar day as now. Bad data is False."""
    try:
        made = datetime.fromisoformat(brief["generated_at"])
        if made.tzinfo is None:
            return False
        return made.astimezone(TZ).date() == now.astimezone(TZ).date()
    except Exception:
        return False


def fetch_live_brief(site_url: str) -> dict | None:
    """The published brief.json, or None on any problem."""
    try:
        r = requests.get(site_url.rstrip("/") + "/brief.json", timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def check_today(cfg: dict) -> bool:
    """Print whether today's paper is out. Write skip=true/false for GitHub Actions. Never fails."""
    done = printed_today(fetch_live_brief(cfg["site_url"]), datetime.now(TZ))
    print(f"printed_today={'true' if done else 'false'}")
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        try:
            with open(path, "a") as f:
                f.write(f"skip={'true' if done else 'false'}\n")
        except OSError:
            pass
    return done


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
    """One team's schedule from ESPN. `last`, `last_date`, `next` feed the small scores ear.
    `recent` (up to 3 results, newest first) and `upcoming` (up to 3 games, soonest first) feed the Sports tab."""
    out = {"team": cfg["team"], "last": None, "last_date": None, "next": None, "url": None,
           "recent": [], "upcoming": []}
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

    def day(p: dict) -> str:
        return p["when"].strftime("%b ") + str(p["when"].day)

    def link(ev: dict) -> str | None:
        return next((l.get("href") for l in ev.get("links") or [] if is_http(l.get("href"))), None)

    results = []                     # (result text, date text, event), newest first
    for p, ev in reversed(done):
        mine, theirs = _score(p["mine"]), _score(p["other"])
        if mine is None or theirs is None:
            continue
        res = "W" if mine > theirs else "L" if mine < theirs else "T"
        results.append((f"{res} {mine}–{theirs} {where(p)}", day(p), ev))
    if results:
        text, date_text, ev = results[0]
        out["last"], out["last_date"], out["url"] = text, date_text, link(ev)
        out["recent"] = [f"{t} · {d}" for t, d, _ in results[:3]]
    for p, ev in future[:3]:
        out["upcoming"].append(f"{p['when']:%a %b} {p['when'].day} {where(p)}")
    if out["upcoming"]:
        out["next"] = out["upcoming"][0]
        out["url"] = out["url"] or link(future[0][1])
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


# ---------- recalls and the daily quote ----------

def parse_recalls(content: bytes | str, cfg: dict, now: datetime | None = None) -> list[dict]:
    """FDA recall feed -> [{"title", "url", "published"}], newest first, at most max_items,
    only items inside max_age_days. `cfg` is the `recalls:` block. Items without a usable date are left out."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=float(cfg.get("max_age_days", 4)))
    feed = feedparser.parse(content)
    if not feed.entries:
        raise ValueError("feed has no entries")
    rows, seen = [], set()
    for e in feed.entries:
        published = to_iso(e.get("published_parsed") or e.get("updated_parsed"))
        title, url = clean_text(e.get("title", ""), 160), e.get("link", "")
        if not title or not is_http(url) or not published or url in seen:
            continue
        if datetime.fromisoformat(published) < cutoff:
            continue
        seen.add(url)
        rows.append({"title": title, "url": url, "published": published})
    rows.sort(key=lambda r: r["published"], reverse=True)
    return rows[: int(cfg.get("max_items", 3))]


def fetch_recalls(cfg: dict, now: datetime | None = None, session: requests.Session | None = None) -> list[dict]:
    """Download the FDA recall feed. Raises on any failure; the caller records it as a skipped source."""
    s = session or requests.Session()
    if session is None:
        s.headers["User-Agent"] = USER_AGENT
    r = s.get(cfg["recalls"]["url"], timeout=TIMEOUT)
    r.raise_for_status()
    return parse_recalls(r.content, cfg["recalls"], now)


QUOTE_MIN, QUOTE_MAX = 20, 240
QUOTE_EPOCH = date(2026, 1, 1)   # day 0 of the cycle


def norm_text(text: str) -> str:
    """Case-folded text with runs of whitespace collapsed, used to spot duplicates and to order quotes."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def load_quotes(path: Path) -> list[dict]:
    """The curated quotes file -> [{"text", "who", "about", "source"}], in file order.
    An entry needs a text of 20 to 240 characters and a who; anything else is dropped, and so is a repeat of
    an earlier text (ignoring case and spacing). Raises if the file is missing or is not a YAML list;
    the caller records that as a skipped source."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("quotes file is not a list")
    out, seen = [], set()
    for e in data:
        if not isinstance(e, dict):
            continue
        text, who = e.get("text"), e.get("who")
        if not isinstance(text, str) or not isinstance(who, str):
            continue
        text, who = text.strip(), who.strip()
        if not who or not (QUOTE_MIN <= len(text) <= QUOTE_MAX):
            continue
        key = norm_text(text)
        if key in seen:
            continue
        seen.add(key)
        about, source = e.get("about"), e.get("source")
        out.append({"text": text, "who": who,
                    "about": about.strip() if isinstance(about, str) else "",
                    "source": source.strip() if isinstance(source, str) else ""})
    return out


def quote_order(quotes: list[dict]) -> list[int]:
    """A fixed order for the whole list: indices sorted by the hash of each quote's text, so it does not depend
    on the order of the file. Then two neighbors with the same `who` (the last and first count as neighbors)
    are separated by swapping with the nearest later position that fixes it without making a new clash.
    The last/first pair has no later position, so it swaps with the nearest earlier one. A clash that cannot
    be fixed (one person owns over half the list) is left alone."""
    n = len(quotes)
    who = [q["who"].strip().casefold() for q in quotes]
    order = sorted(range(n), key=lambda i: (hashlib.sha256(norm_text(quotes[i]["text"]).encode("utf-8")).hexdigest(),
                                            norm_text(quotes[i]["text"]), who[i]))
    if n < 3:
        return order

    def clashes(pairs: set[int]) -> set[int]:    # pair p is positions p and p + 1 (wrapping)
        return {p for p in pairs if who[order[p]] == who[order[(p + 1) % n]]}

    def try_swap(target: int, k: int, cands: range) -> None:
        for j in cands:
            if j == k:
                continue
            pairs = {(x - 1) % n for x in (k, j)} | {k % n, j % n}
            before = clashes(pairs)
            order[k], order[j] = order[j], order[k]
            after = clashes(pairs)
            if target not in after and after <= before:
                return
            order[k], order[j] = order[j], order[k]

    for i in range(n - 1):
        if who[order[i]] == who[order[i + 1]]:
            try_swap(i, i + 1, range(i + 2, n))
    if who[order[-1]] == who[order[0]]:
        try_swap(n - 1, n - 1, range(n - 2, 0, -1))
    return order


def pick_quote(quotes: list[dict], cfg_block: dict, now: datetime) -> dict | None:
    """Today's quote. One quote per day, walking the whole fixed order, so nothing repeats until the list is used up.
    The day is the Eastern date, so it stays the same all day. None for an empty list."""
    if not quotes:
        return None
    n = (now.astimezone(TZ).date() - QUOTE_EPOCH).days
    order = quote_order(quotes)
    q = quotes[order[n % len(order)]]
    return {"text": q["text"], "who": q["who"], "about": q["about"],
            "title": (cfg_block or {}).get("title", "Words to Live By")}


def fetch_extras(cfg: dict, now: datetime) -> tuple[list[dict], dict | None, list[dict]]:
    """Recalls and the daily quote, each failing soft. Returns (recalls, quote, source_errors).
    The quote comes from the local quotes file, with no network."""
    errors, recalls, quote = [], [], None
    if cfg.get("recalls"):
        try:
            recalls = fetch_recalls(cfg, now)
        except Exception as exc:
            errors.append({"source": "Recalls", "error": f"{type(exc).__name__}: {exc}"[:140]})
    if cfg.get("quotes"):
        try:
            quote = pick_quote(load_quotes(ROOT / cfg["quotes"]["file"]), cfg["quotes"], now)
        except Exception as exc:
            errors.append({"source": "Quotes", "error": f"{type(exc).__name__}: {exc}"[:140]})
    return recalls, quote, errors


# ---------- Claude ----------

def slim_items(items: list[dict]) -> list[dict]:
    """The records the model sees. No urls and no image links; only whether a picture exists."""
    return [{**{k: i[k] for k in ("id", "topic", "source", "title", "summary", "published")},
             "has_image": bool(i.get("image")), **({"seen_recently": True} if i.get("seen_recently") else {})}
            for i in items]


def _forced_call(cfg: dict, system: str, tool: dict, user: str, max_tokens: int,
                 model: str | None = None, timeout: float = 180.0, max_retries: int = 2) -> tuple[dict, dict]:
    """One Claude call that must answer through `tool`. Returns (tool input, usage counts).
    `model` defaults to cfg["model"]. `timeout` (seconds) and `max_retries` go to the client.

    Some models reject a forced tool_choice ("tool" or "any") with a 400. For those models the call uses
    tool_choice "auto" and a system line that says to call the tool. If the answer still has no tool call,
    it asks one more time. Usage counts add up over all calls."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=timeout, max_retries=max_retries)
    model = model or cfg["model"]
    usage = {"input_tokens": 0, "output_tokens": 0}

    def ask(sys_text: str, choice: dict):
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, system=sys_text, tools=[tool], tool_choice=choice,
            messages=[{"role": "user", "content": user}])
        usage["input_tokens"] += msg.usage.input_tokens
        usage["output_tokens"] += msg.usage.output_tokens
        return next((b for b in msg.content if b.type == "tool_use" and b.name == tool["name"]), None), msg

    if model not in _NO_FORCED_TOOL:
        try:
            block, msg = ask(system, {"type": "tool", "name": tool["name"]})
            if block is None:
                raise RuntimeError(f"no tool call in response (stop_reason={msg.stop_reason})")
            return dict(block.input), usage
        except Exception as exc:
            if "tool_choice" not in str(exc) or "not supported" not in str(exc):
                raise
            _NO_FORCED_TOOL.add(model)
            print(f"{model}: forced tool_choice not supported, using auto", file=sys.stderr)
    nudge = f"{system}\n\nAnswer only by calling the {tool['name']} tool. Do not answer in plain text."
    for _ in range(2):
        block, msg = ask(nudge, {"type": "auto"})
        if block is not None:
            return dict(block.input), usage
    raise RuntimeError(f"no tool call in response (stop_reason={msg.stop_reason})")


# Models that answered a forced tool_choice with a 400 in this run. They use tool_choice "auto".
_NO_FORCED_TOOL: set[str] = set()


def front_model(cfg: dict) -> str:
    """The model for call 1 (front page and spoken script): front_model, else the main model."""
    return cfg.get("front_model") or cfg["model"]


def week_model(cfg: dict) -> str:
    """The model for the Sunday week in review: week_model, else the main model."""
    return cfg.get("week_model") or cfg["model"]


def reader_block(cfg: dict, now: datetime | None = None) -> str:
    """The reader profile, then (when the likes log has recent entries) the liked stories. Ends with a blank line."""
    text = f"Reader profile:\n{cfg['reader_profile'].strip()}\n\n"
    try:
        lines = likes_prompt(load_likes(data_dir()), now or datetime.now(TZ))
    except Exception:   # the likes log must never stop the run
        lines = ""
    if lines:
        text += ("Stories this reader liked recently. Use them only as a guide to the reader's taste. "
                 f"They are not items to pick:\n{lines}\n\n")
    return text


def ask_claude(cfg: dict, items: list[dict], today: str, recent: list[str] | None = None) -> tuple[dict, dict]:
    """Call 1: the front page (lead, front stories, 2 to 3 items per topic) and the spoken script.
    `recent` are the front-page headlines of the last few days; the model must not repeat them."""
    slim = slim_items(items)
    topics = section_names(cfg, items)
    lines = [clean_text(h, 140) for h in (recent or []) if isinstance(h, str)]
    lines = [l for l in lines if l][:RECENT_LINES]
    seen = (f"Front-page stories from the last {RECENT_DAYS} days (do not repeat them unless there is a major "
            "new development):\n" + "".join(f"- {l}\n" for l in lines) + "\n") if lines else ""
    user = (f"{reader_block(cfg)}Today is {today}.\n\n"
            f"Topics (make a section for each): {', '.join(topics)}\n\n{seen}"
            f"Items (JSON):\n{json.dumps(slim, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Call publish_brief with your picks.")
    return _forced_call(cfg, SYSTEM_PROMPT, publish_tool([t["name"] for t in cfg["topics"]]), user,
                        int(cfg["max_output_tokens"]), model=front_model(cfg))


def ask_desk(cfg: dict, items: list[dict], today: str) -> tuple[dict, dict]:
    """Call 2: the longer list for each topic tab. Same items as call 1. Uses the main (cheaper) model."""
    slim = slim_items(items)
    topics = section_names(cfg, items)
    user = (f"{reader_block(cfg)}Today is {today}.\n\n"
            f"Topics (one entry for each): {', '.join(topics)}\n\n"
            f"Items (JSON):\n{json.dumps(slim, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Call publish_desk with your picks.")
    return _forced_call(cfg, DESK_PROMPT, desk_tool([t["name"] for t in cfg["topics"]]), user,
                        int(cfg.get("desk_max_output_tokens", 8000)), model=cfg["model"])


def section_names(cfg: dict, items: list[dict]) -> list[str]:
    """Configured section names, in order, that have at least one item."""
    have = {i["topic"] for i in items}
    return [t["name"] for t in cfg["topics"] if t["name"] in have]


def make_story(it: dict, note: Any) -> dict:
    """The shape of every story in brief.json (lead, front, sections and tabs)."""
    return {"title": it["title"], "url": it["url"], "source": it["source"],
            "topic": it["topic"], "note": clean_text(str(note or ""), 300), "image": it.get("image"),
            "video": youtube_id(it["url"])}


def build_output(choice: dict, items: list[dict], order: list[str]) -> dict:
    """Map model-chosen ids back to real items. Unknown or repeated ids are dropped.
    Returns {"lead": item|None, "front": [...], "sections": [{"topic", "items"}]} with
    sections in the configured `order`."""
    by_id = {i["id"]: i for i in items}
    used: set[str] = set()

    make = make_story

    def pick(entries: Any, limit: int | None = None, front_page: bool = False) -> list[dict]:
        out = []
        for e in entries if isinstance(entries, list) else []:
            iid = e.get("id") if isinstance(e, dict) else None
            if iid not in by_id or iid in used:
                continue
            new = ""
            if front_page and by_id[iid].get("seen_recently"):
                new = clean_text(str(e.get("whats_new") or ""), 140)
                if not new:
                    continue    # a recent repeat with nothing new stays off the front page (not marked used)
            used.add(iid)
            story = make(by_id[iid], e.get("note"))
            if new:
                story["follow_up"] = new
            out.append(story)
        return out[:limit] if limit else out

    raw_lead = choice.get("lead")
    lead_list = pick([raw_lead], 1, front_page=True)
    lead = lead_list[0] if lead_list else None
    if lead:
        lead["note"] = clean_text(str(raw_lead.get("note") or ""), LEAD_NOTE_CHARS)
        lead["headline"] = clean_text(str(raw_lead.get("headline") or ""), 120) or lead["title"]
        raw_decks = raw_lead.get("decks")
        decks = [clean_text(str(d), 100) for d in raw_decks if isinstance(d, str)] \
            if isinstance(raw_decks, list) else []
        lead["decks"] = [d for d in decks if d][:3]
    front = pick(choice.get("front"), 4, front_page=True)
    if lead is None and front and isinstance(raw_lead, dict) and by_id.get(raw_lead.get("id"), {}).get("seen_recently"):
        # The lead was a repeat with nothing new: the first front story takes its place.
        lead = {**front.pop(0)}
        lead["headline"], lead["decks"] = lead["title"], []

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
        for it in rest[:2]:
            used.add(it["id"])
            buckets[topic] = buckets.get(topic, []) + [make(it, "")]
    names = [n for n in order if buckets.get(n)] + [n for n in buckets if n not in order and buckets[n]]
    sections = [{"topic": n, "items": buckets[n][:3]} for n in names]
    return {"lead": lead, "front": front, "sections": sections}


def desk_has_picks(choice: Any, items: list[dict]) -> bool:
    """True if the desk answer names at least one real item id."""
    ids = {i["id"] for i in items}
    return any(isinstance(e, dict) and e.get("id") in ids
               for sec in (choice.get("sections") if isinstance(choice, dict) else None) or []
               if isinstance(sec, dict) for e in (sec.get("items") if isinstance(sec.get("items"), list) else []))


def build_tabs(desk_choice: Any, items: list[dict], cfg: dict) -> list[dict]:
    """The topic tabs: [{"id", "name", "sections": [{"topic", "items": [story...]}]}], in the configured tab
    order. Model-chosen ids are mapped back to real items; unknown and repeated ids are dropped, and an item
    always sits under its own topic. A topic the model skipped (or all of them, when call 2 failed and
    desk_choice is None) is filled with its newest items, no notes. Empty sections are dropped, and a tab
    is dropped only when all its sections are empty."""
    by_id = {i["id"]: i for i in items}
    picked: dict[str, list[dict]] = {}
    seen: set[str] = set()
    sections = desk_choice.get("sections") if isinstance(desk_choice, dict) else None
    for sec in sections if isinstance(sections, list) else []:
        entries = sec.get("items") if isinstance(sec, dict) else None
        for e in entries if isinstance(entries, list) else []:
            iid = e.get("id") if isinstance(e, dict) else None
            if iid not in by_id or iid in seen:
                continue
            seen.add(iid)
            it = by_id[iid]
            picked.setdefault(it["topic"], []).append(make_story(it, e.get("note")))
    for topic in list(picked):
        picked[topic] = picked[topic][:DESK_MAX_ITEMS]
    for t in cfg["topics"]:
        name = t["name"]
        if picked.get(name):
            continue
        newest = sorted((i for i in items if i["topic"] == name), key=lambda i: i.get("published") or "",
                        reverse=True)
        if newest:
            picked[name] = [make_story(i, "") for i in newest[:DESK_FALLBACK_ITEMS]]
    tabs = []
    for tab in cfg.get("tabs") or []:
        secs = [{"topic": topic, "items": picked[topic]} for topic in tab.get("topics") or [] if picked.get(topic)]
        if secs:
            tabs.append({"id": tab["id"], "name": tab["name"], "sections": secs})
    return tabs


# ---------- call 3: the YouTube tab ----------

def ask_videos(cfg: dict, items: list[dict], today: str) -> tuple[dict, dict]:
    """Call 3: the best videos for the YouTube tab, by group. Uses the main (cheaper) model."""
    yt = youtube_settings(cfg)
    if not yt:
        raise ValueError("no youtube config")
    names = [g["name"] for g in yt["groups"]]
    have = {i["topic"] for i in items}
    slim = [{"id": i["id"], "group": i["topic"], "channel": i["source"], "title": i["title"],
             "summary": i["summary"], "published": i["published"]} for i in items]
    user = (f"{reader_block(cfg)}Today is {today}.\n\n"
            f"Groups (in this order): {', '.join(n for n in names if n in have)}\n\n"
            f"Videos (JSON):\n{json.dumps(slim, ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"Pick {yt['pick_min']} to {yt['pick_max']} videos in total. Call publish_videos with your picks.")
    system = VIDEO_PROMPT.replace("{lo}", str(yt["pick_min"])).replace("{hi}", str(yt["pick_max"]))
    # The YouTube tab is optional, so a slow API gets a short leash: one try plus one retry, 60 seconds each.
    return _forced_call(cfg, system, videos_tool(names, yt["pick_max"]), user, VIDEO_MAX_TOKENS, model=cfg["model"],
                        timeout=60.0, max_retries=1)


def _video_groups(picked: dict[str, list[dict]], yt: dict) -> list[dict]:
    """[{"group", "items"}] in the configured group order, trimmed to pick_max in that order. No empty groups."""
    out, room = [], yt["pick_max"]
    for g in yt["groups"]:
        rows = picked.get(g["name"], [])[:room]
        if rows:
            out.append({"group": g["name"], "items": rows})
            room -= len(rows)
        if room <= 0:
            break
    return out


def build_videos(choice: Any, items: list[dict], yt: dict) -> list[dict]:
    """The "videos" list of brief.json: [{"group", "items": [story...]}] in configured group order.
    Model-chosen ids map back to real items; unknown and repeated ids are dropped, and a video always sits
    under its own group. The best-first order of the model is kept inside a group. The total is trimmed to
    pick_max (in group order). Empty groups are left out."""
    by_id = {i["id"]: i for i in items}
    picked: dict[str, list[dict]] = {}
    seen: set[str] = set()
    groups = choice.get("groups") if isinstance(choice, dict) else None
    for grp in groups if isinstance(groups, list) else []:
        entries = grp.get("items") if isinstance(grp, dict) else None
        for e in entries if isinstance(entries, list) else []:
            iid = e.get("id") if isinstance(e, dict) else None
            if not isinstance(iid, str) or iid not in by_id or iid in seen:
                continue
            seen.add(iid)
            it = by_id[iid]
            picked.setdefault(it["topic"], []).append(make_story(it, e.get("note")))
    return _video_groups(picked, yt)


def fallback_videos(items: list[dict], yt: dict) -> list[dict]:
    """When call 3 fails: the newest 2 videos of each group, in group order, up to pick_max, with no notes."""
    picked: dict[str, list[dict]] = {}
    for g in yt["groups"]:
        newest = sorted((i for i in items if i["topic"] == g["name"]), key=lambda i: i.get("published") or "",
                        reverse=True)
        if newest:
            picked[g["name"]] = [make_story(i, "") for i in newest[:2]]
    return _video_groups(picked, yt)


def make_videos(cfg: dict, items: list[dict], today: str,
                dry_run: bool = False) -> tuple[list[dict], dict, bool]:
    """The YouTube tab, as (videos, usage, from_model). It never stops the run: when the call fails or gives
    nothing usable, the newest videos of each group are used (no notes) and from_model is False. Prints counts
    only. Dry-run uses fixtures/sample_videos_response.json."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        yt = youtube_settings(cfg)
        if not yt or not items:
            return [], usage, False
        choice = None
        try:
            if dry_run:
                choice = json.loads((ROOT / "fixtures/sample_videos_response.json").read_text(encoding="utf-8"))
            else:
                choice, usage = ask_videos(cfg, items, today)
        except Exception as exc:
            print(f"videos: call failed ({type(exc).__name__})")
        videos, from_model = build_videos(choice, items, yt), True
        if not videos:
            videos, from_model = fallback_videos(items, yt), False
            print("videos: fallback")
        print(f"videos: {sum(len(g['items']) for g in videos)} in {len(videos)} groups, "
              f"{len(items)} fetched")
        return videos, usage, from_model
    except Exception as exc:
        print(f"videos: skipped ({type(exc).__name__})")
        return [], usage, False


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


# ---------- data branch: likes log and back issues ----------

def data_dir() -> Path:
    """Where the `data` branch is checked out. BRIEF_DATA_DIR overrides it (tests). The folder may not exist."""
    return Path(os.environ.get("BRIEF_DATA_DIR") or ROOT / "data")


def load_likes(folder: Path | str) -> list[dict]:
    """Read data/likes.jsonl. Bad lines and bad records are skipped; a missing file gives []."""
    out: list[dict] = []
    try:
        lines = (Path(folder) / "likes.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines[-5000:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        url, title = rec.get("u"), rec.get("t")
        if isinstance(url, str) and url.lower().startswith(("http://", "https://")) \
                and isinstance(title, str) and title.strip():
            out.append(rec)
    return out


def _parse_at(value: Any) -> datetime | None:
    """A UTC-aware datetime from an ISO string, or None."""
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def recent_likes(likes: list[dict], now: datetime, days: int) -> list[dict]:
    """Likes with a valid "at" inside the last `days` days, newest first."""
    cutoff = now - timedelta(days=days)
    dated = [(when, rec) for rec in likes if (when := _parse_at(rec.get("at"))) and cutoff <= when <= now]
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [rec for _, rec in dated]


def like_line(rec: dict) -> str:
    """One like as a single line: "- [topic] source: title". Every field is cleaned and capped."""
    topic, source = clean_text(str(rec.get("c") or ""), 40), clean_text(str(rec.get("s") or ""), 60)
    title = clean_text(str(rec.get("t") or ""), 160)
    return "- " + (f"[{topic}] " if topic else "") + (f"{source}: " if source else "") + title


def likes_prompt(likes: list[dict], now: datetime, days: int = 60, limit: int = 30) -> str:
    """The most recent likes inside `days`, newest first, up to `limit`, one line each. "" when there are none."""
    return "\n".join(like_line(r) for r in recent_likes(likes, now, days)[:limit])


def write_archive(site_dir: Path, folder: Path | str, brief: dict, now: datetime, keep: int = 60) -> None:
    """Publish back issues: copy the newest `keep` papers from data/archive into site/archive, add today's paper,
    and write site/archive/index.json {"dates": [newest first]}. Never fails the run."""
    try:
        today = now.astimezone(TZ).date().isoformat()
        out = Path(site_dir) / "archive"
        out.mkdir(parents=True, exist_ok=True)
        kept: dict[str, str] = {}
        src = Path(folder) / "archive"
        names = sorted((p.name for p in src.iterdir() if ARCHIVE_NAME.match(p.name)), reverse=True) \
            if src.is_dir() else []
        for name in names:
            if len(kept) >= keep:
                break
            date = name[:-5]
            if date == today:
                continue
            try:
                text = (src / name).read_text(encoding="utf-8")
                if isinstance(json.loads(text), dict):
                    kept[date] = text
            except (OSError, ValueError):
                continue
        for old in out.iterdir():       # drop leftovers from an earlier local run
            if ARCHIVE_NAME.match(old.name) and old.name[:-5] not in kept and old.name[:-5] != today:
                old.unlink()
        for date, text in kept.items():
            (out / f"{date}.json").write_text(text, encoding="utf-8")
        (out / f"{today}.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        dates = [today] + list(kept)
        (out / "index.json").write_text(json.dumps({"dates": dates}) + "\n", encoding="utf-8")
        print(f"archive: {len(dates)} back issues")
    except Exception as exc:   # never stop the run
        print(f"archive: skipped ({type(exc).__name__})")


# ---------- the spoken script ----------

def _sentence(text: Any) -> str:
    """Clean text that ends in . ! or ? (empty stays empty)."""
    t = clean_text(str(text or ""))
    return t if not t or t[-1] in ".!?" else t + "."


def clean_listen(raw: Any) -> str:
    """The model's spoken script as plain text: tags removed, whitespace collapsed, capped at LISTEN_CHARS
    (cut at a sentence end when there is one in the second half)."""
    text = clean_text(raw) if isinstance(raw, str) else ""
    if len(text) > LISTEN_CHARS:
        text = text[:LISTEN_CHARS]
        end = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
        text = text[:end + 1] if end > LISTEN_CHARS // 2 else text.rstrip()
    return text


def fallback_listen(lead: dict | None, front: list[dict], today: str) -> str:
    """A plain spoken script built in Python (headline and note of the lead, then each front title),
    used when the model gives none. `today` is like "Monday, October 12, 2026"."""
    day = re.sub(r",?\s*\d{4}$", "", today or "").strip()
    parts = [f"Good morning. This is The Daily Brief for {day}." if day else "Good morning. This is The Daily Brief."]
    if lead:
        parts.append("The top story. " + _sentence(lead.get("headline") or lead.get("title")))
        parts.append(_sentence(lead.get("note")))
    titles = [_sentence(f.get("title")) for f in front or [] if f.get("title")]
    if titles:
        parts.append("Also on the front page. " + " ".join(titles))
    parts.append("That is the brief. Have a good day.")
    return " ".join(p for p in parts if p)


# ---------- Sunday edition: the week in review ----------

WEEK_PROMPT = """You are the weekly editor of a personal daily newspaper, for one reader. It is Sunday.
You write the week in review from the stories of the past days and the stories the reader liked.

Rules:
- Use only facts that are in the story titles and notes. Never invent a fact, name, number or date.
- Refer to stories by id only. Never write URLs.
- Be neutral and factual on politics, World news and anything contested. No adjectives that take a side.
- "headline": at most 10 words.
- "summary": 180 to 260 words in 2 or 3 paragraphs separated by a blank line. What mattered this week, in plain words.
- "top": 3 to 5 stories of the week, best first. Note: 1 sentence, at most 25 words.
- "suggestions": 3 to 5 proposals for next week's paper. Each is one concrete change: add or drop a topic,
  add or drop a named source, or change a line of the reader profile. The reason is one sentence that cites
  what the likes or the week's coverage show. Suggestions are proposals the reader approves by hand.
  Nothing changes automatically.
- Base the suggestions on the likes and on which sources gave stories. If there are few likes, say so in the
  reason instead of guessing."""

WEEK_MIN_PAPERS = 3
WEEK_MAX_STORIES = 220
WEEK_SUMMARY_CHARS = 2000


def week_tool() -> dict[str, Any]:
    """The forced tool for the Sunday edition."""
    return {
        "name": "publish_week",
        "description": "Publish the week in review: headline, summary, top stories by id, and suggestions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string", "description": "At most 10 words."},
                "summary": {"type": "string",
                            "description": "180 to 260 words, 2 or 3 paragraphs separated by a blank line."},
                "top": {"type": "array", "minItems": 3, "maxItems": 5,
                        "items": {"type": "object",
                                  "properties": {"id": {"type": "string"},
                                                 "note": {"type": "string",
                                                          "description": "1 sentence, at most 25 words."}},
                                  "required": ["id", "note"]}},
                "suggestions": {"type": "array", "minItems": 3, "maxItems": 5,
                                "items": {"type": "object",
                                          "properties": {"change": {"type": "string",
                                                                    "description": "One concrete sentence."},
                                                         "reason": {"type": "string",
                                                                    "description": "One sentence citing the "
                                                                                   "likes or the coverage."}},
                                          "required": ["change", "reason"]}},
            },
            "required": ["headline", "summary", "top", "suggestions"],
        },
    }


def week_wanted(now: datetime) -> bool:
    """True on a New York Sunday, or when BRIEF_FORCE_WEEK=1. BRIEF_FORCE_WEEK=0 turns it off (tests)."""
    force = os.environ.get("BRIEF_FORCE_WEEK")
    if force in ("0", "1"):
        return force == "1"
    return now.astimezone(TZ).weekday() == 6


def load_archive_papers(folder: Path | str, today: Any, days: int = 6) -> list[tuple[str, dict]]:
    """(date, paper) for the `days` days before `today` (a date), from data/archive by file name, oldest first.
    Missing or unreadable files are skipped."""
    out = []
    for back in range(days, 0, -1):
        date = (today - timedelta(days=back)).isoformat()
        try:
            paper = json.loads((Path(folder) / "archive" / f"{date}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(paper, dict):
            out.append((date, paper))
    return out


def _paper_stories(paper: dict) -> list[tuple[int, dict]]:
    """(priority, story) for the lead and front (0) and the sections (1) of one paper. Only dicts with a
    title and an http(s) url are kept."""
    found: list[tuple[int, Any]] = []
    front, sections = paper.get("front"), paper.get("sections")
    if isinstance(paper.get("lead"), dict):
        found.append((0, paper["lead"]))
    found += [(0, f) for f in front] if isinstance(front, list) else []
    for sec in sections if isinstance(sections, list) else []:
        if isinstance(sec, dict) and isinstance(sec.get("items"), list):
            found += [(1, i) for i in sec["items"]]
    return [(prio, st) for prio, st in found if isinstance(st, dict) and isinstance(st.get("title"), str)
            and st["title"].strip() and isinstance(st.get("url"), str) and is_http(st["url"])]


def week_stories(papers: list[tuple[str, dict]], cap: int = WEEK_MAX_STORIES) -> list[dict]:
    """The week's stories as records {id: "w1", date, topic, source, title, note, url, image}, oldest paper first.
    The same url is kept once. Over the cap, section stories of the oldest papers go first.
    `url` and `image` stay in Python; they are never sent to the model."""
    entries, seen = [], set()
    for p_idx, (date, paper) in enumerate(papers):
        for prio, st in _paper_stories(paper):
            key = st["url"].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            entries.append((p_idx, prio, len(entries), {
                "date": date, "topic": clean_text(str(st.get("topic") or ""), 60),
                "source": clean_text(str(st.get("source") or ""), 100), "title": clean_text(st["title"], 300),
                "note": clean_text(str(st.get("note") or ""), 200), "url": st["url"],
                "image": clean_image(st.get("image"))}))
    if len(entries) > cap:
        entries = sorted(sorted(entries, key=lambda e: (e[1], -e[0], e[2]))[:cap], key=lambda e: e[2])
    return [{"id": f"w{n}", **rec} for n, (_, _, _, rec) in enumerate(entries, 1)]


def _count_by(likes: list[dict], key: str, blank: str) -> dict[str, int]:
    """Likes counted by a field, biggest first."""
    counts = Counter(clean_text(str(r.get(key) or ""), 60) or blank for r in likes)
    return dict(counts.most_common(15))


def configured_sources(cfg: dict) -> dict[str, list[str]]:
    """Enabled source names per topic, from sources.yaml."""
    return {t["name"]: [s["name"] for s in t.get("sources", []) if s.get("enabled", True)] for t in cfg["topics"]}


def week_user_message(cfg: dict, stories: list[dict], week_likes: list[dict], frm: str, to: str, n_papers: int) -> str:
    """What the model sees: the stories (no urls), this week's likes, like counts, the profile and the sources."""
    slim = [{k: s[k] for k in ("id", "date", "topic", "source", "title", "note")} for s in stories]
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))   # noqa: E731
    lines = "\n".join(like_line(r) for r in week_likes[:60]) or "(none this week)"
    return (f"Reader profile:\n{cfg['reader_profile'].strip()}\n\n"
            f"The week: {frm} to {to}, from {n_papers} papers.\n\n"
            f"Configured sources by topic (JSON):\n{dump(configured_sources(cfg))}\n\n"
            f"Stories the reader liked this week ({len(week_likes)}):\n{lines}\n\n"
            f"Likes by topic (JSON): {dump(_count_by(week_likes, 'c', 'Other'))}\n"
            f"Likes by source (JSON): {dump(_count_by(week_likes, 's', 'Unknown'))}\n\n"
            f"Stories of the week (JSON):\n{dump(slim)}\n\n"
            "Call publish_week.")


def ask_week(cfg: dict, user: str) -> tuple[dict, dict]:
    """The Sunday call, on week_model."""
    return _forced_call(cfg, WEEK_PROMPT, week_tool(), user, 4000, model=week_model(cfg))


def clean_paragraphs(raw: Any, limit: int) -> str:
    """Plain text kept as paragraphs (blank line between), each cleaned, capped at `limit` characters."""
    paras = [clean_text(p) for p in re.split(r"\n\s*\n", str(raw or ""))]
    text = "\n\n".join(p for p in paras if p)
    return text if len(text) <= limit else text[:limit].rstrip()


def build_week(choice: Any, stories: list[dict], week_likes: list[dict], frm: str, to: str) -> dict | None:
    """The "week" object of brief.json. Model ids map back to the archived stories (unknown and repeated ids
    are dropped); urls and images come from the archive. None when there is no headline and no summary."""
    if not isinstance(choice, dict):
        return None
    headline = clean_text(str(choice.get("headline") or ""), 120)
    summary = clean_paragraphs(choice.get("summary"), WEEK_SUMMARY_CHARS)
    if not headline and not summary:
        return None
    by_id = {s["id"]: s for s in stories}
    top, used = [], set()
    for e in choice.get("top") if isinstance(choice.get("top"), list) else []:
        sid = e.get("id") if isinstance(e, dict) else None
        if sid in by_id and sid not in used:
            used.add(sid)
            top.append(make_story(by_id[sid], e.get("note")))
    suggestions = []
    for e in choice.get("suggestions") if isinstance(choice.get("suggestions"), list) else []:
        change = clean_text(str(e.get("change") or ""), 300) if isinstance(e, dict) else ""
        reason = clean_text(str(e.get("reason") or ""), 300) if isinstance(e, dict) else ""
        if change and reason:
            suggestions.append({"change": change, "reason": reason})
    liked = [{"title": clean_text(r["t"], 300), "url": r["u"], "source": clean_text(str(r.get("s") or ""), 100),
              "topic": clean_text(str(r.get("c") or ""), 60)} for r in week_likes[:10]]
    return {"from": frm, "to": to, "headline": headline, "summary": summary, "top": top[:5],
            "liked": liked, "suggestions": suggestions[:5]}


def week_listen(week: dict) -> str:
    """The Sunday addition to the spoken script, as plain text."""
    return (" This is the Sunday edition. Here is the week in review. " + _sentence(week.get("headline"))
            + " " + clean_text(str(week.get("summary") or ""))).rstrip()


def make_week(cfg: dict, today_paper: dict, now: datetime, dry_run: bool = False) -> tuple[dict | None, dict]:
    """The week in review, or None. Returns (week, usage). It never stops the run: on any problem it prints a
    short reason (no story text) and returns None. Dry-run uses fixtures/sample_week_response.json and today's
    paper only."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        if not week_wanted(now):
            return None, usage
        today = now.astimezone(TZ).date()
        papers = [] if dry_run else load_archive_papers(data_dir(), today)
        papers.append((today.isoformat(), today_paper))
        if not dry_run and len(papers) < WEEK_MIN_PAPERS:
            print(f"week: skipped, only {len(papers)} papers")
            return None, usage
        stories = week_stories(papers)
        if not stories:
            print("week: skipped, no stories")
            return None, usage
        week_likes = recent_likes(load_likes(data_dir()), now, 7)
        if dry_run:
            choice = json.loads((ROOT / "fixtures/sample_week_response.json").read_text())
        else:
            user = week_user_message(cfg, stories, week_likes, papers[0][0], today.isoformat(), len(papers))
            choice, usage = ask_week(cfg, user)
        week = build_week(choice, stories, week_likes, papers[0][0], today.isoformat())
        if not week:
            print("week: skipped, no usable answer")
            return None, usage
        print(f"week: {len(papers)} papers, {len(stories)} stories, {len(week['top'])} top, "
              f"{len(week['suggestions'])} suggestions, {len(week_likes)} likes")
        return week, usage
    except Exception as exc:
        print(f"week: skipped ({type(exc).__name__})")
        return None, usage


# ---------- main ----------

def load_recalls_fixture(path: Path, cfg: dict, now: datetime) -> list[dict]:
    """Dry-run recalls. @D0@ ... @D9@ in the fixture become dates that many days before `now`."""
    from email.utils import format_datetime
    text = path.read_text()
    for n in range(10):
        text = text.replace(f"@D{n}@", format_datetime((now - timedelta(days=n)).astimezone(timezone.utc)))
    return parse_recalls(text, cfg["recalls"], now)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the daily brief.")
    ap.add_argument("--dry-run", action="store_true", help="no network; use fixtures/")
    ap.add_argument("--fetch-only", action="store_true", help="fetch items, write out/items.json, skip Claude")
    ap.add_argument("--check-today", action="store_true", help="say whether today's paper is already out, then exit")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    if args.check_today:
        check_today(cfg)
        return 0
    now = datetime.now(TZ)
    today = f"{now:%A, %B} {now.day}, {now.year}"
    errors: list[dict] = []
    video_items: list[dict] = []
    recent: list[str] = []   # front-page headlines of the last few days (live runs only)

    if not (args.dry_run or args.fetch_only or os.environ.get("ANTHROPIC_API_KEY")):
        print("error: ANTHROPIC_API_KEY is not set. Set it, or use --dry-run.", file=sys.stderr)
        return 1

    if args.dry_run:
        fixture = json.loads((ROOT / "fixtures/sample_items.json").read_text())
        items, scores = fixture["items"], fixture.get("scores", [])
        recalls = load_recalls_fixture(ROOT / "fixtures/sample_recalls.xml", cfg, now)
        quote = pick_quote(load_quotes(ROOT / cfg["quotes"]["file"]), cfg["quotes"], now)
        video_items = json.loads((ROOT / "fixtures/sample_videos.json").read_text(encoding="utf-8"))["items"]
    else:
        items, errors = fetch_all(cfg)
        scores, score_errors = fetch_scores(cfg)
        recalls, quote, extra_errors = fetch_extras(cfg, now)
        errors += score_errors + extra_errors
        try:   # the YouTube channels must never stop the run
            video_items, video_errors = fetch_youtube(cfg)
        except Exception as exc:
            video_items, video_errors = [], [{"source": "YouTube", "error": f"{type(exc).__name__}: {exc}"[:140]}]
        errors += video_errors
        if args.fetch_only:
            (ROOT / "out").mkdir(exist_ok=True)
            (ROOT / "out/items.json").write_text(json.dumps(
                {"items": items, "videos": video_items, "scores": scores, "recalls": recalls, "quote": quote,
                 "source_errors": errors}, indent=2, ensure_ascii=False))
            print(f"fetched {len(items)} items, {len(video_items)} videos, {len(scores)} scores, "
                  f"{len(recalls)} recalls, {len(errors)} errors -> out/items.json")
            for e in errors:
                print(f"  skipped {e['source']}: {e['error']}")
            return 0
        old = fetch_yesterday(cfg["site_url"])   # one live fetch for the news items and the videos
        items = drop_seen_yesterday(items, cfg["site_url"], old)
        recent = recent_front_headlines(data_dir(), now.astimezone(TZ).date(), old)
        items = mark_recent_repeats(items, recent)
        print(f"repeats: {sum(1 for i in items if i.get('seen_recently'))} items seen in the last {RECENT_DAYS} days")
        video_items = drop_seen_videos(video_items, old)

    if not items:
        print("error: no items to summarize", file=sys.stderr)
        return 1

    if args.dry_run:
        choice = json.loads((ROOT / "fixtures/sample_response.json").read_text())
        if isinstance(choice.get("listen"), str):    # the fixture cannot know today's date
            choice["listen"] = choice["listen"].replace("@DATE@", re.sub(r",?\s*\d{4}$", "", today))
        usage = {"input_tokens": 0, "output_tokens": 0}
    else:
        try:
            choice, usage = ask_claude(cfg, items, today, recent)
        except Exception as exc:
            print(f"error: Claude call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            if front_model(cfg) == cfg["model"]:
                return 1
            # The stronger front-page model failed. Print the paper with the main model rather than no paper.
            print(f"front page: retrying with {cfg['model']}", file=sys.stderr)
            try:
                choice, usage = ask_claude({**cfg, "front_model": cfg["model"]}, items, today, recent)
            except Exception as exc2:
                print(f"error: Claude call failed again: {type(exc2).__name__}: {exc2}", file=sys.stderr)
                return 1

    order = [t["name"] for t in cfg["topics"]]
    out_items = build_output(choice, items, order)
    lead, front, sections = out_items["lead"], out_items["front"], out_items["sections"]
    if not lead and not front and not sections:
        print("error: Claude returned no usable items", file=sys.stderr)
        return 1

    # Call 2: the topic tabs. It must never stop the run: on any problem each topic uses its newest items.
    desk_choice = None
    if args.dry_run:
        desk_choice = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    else:
        try:
            desk_choice, desk_usage = ask_desk(cfg, items, today)
            usage = {k: usage[k] + desk_usage[k] for k in usage}
        except Exception as exc:
            print(f"desk: call failed ({type(exc).__name__})")
    if not desk_has_picks(desk_choice, items):
        desk_choice = None
        print("desk: fallback")
    out_items["tabs"] = build_tabs(desk_choice, items, cfg)

    if not args.dry_run:   # dry-run never touches the network
        no_image = {src["name"] for t in cfg["topics"] for src in t.get("sources", []) if src.get("no_image")}
        found = resolve_og_images(out_items, no_image)
        print(f"og:image: found {found} extra images")
    if swap_lead_if_imageless(out_items):
        lead, front = out_items["lead"], out_items["front"]
        print("lead had no image; swapped with a front story that has one")
    tabs = out_items["tabs"]

    # Call 3: the YouTube tab. Videos are not sent to calls 1 and 2. It must never stop the run.
    videos, video_usage, videos_from_model = make_videos(cfg, video_items, today, dry_run=args.dry_run)
    usage = {k: usage[k] + video_usage[k] for k in usage}

    # Private tabs (Today and Mail). Skipped without BRIEF_PASSPHRASE. Prints counts only, never private text.
    extra = private.run_private(cfg, now, SITE_DIR, ROOT, dry_run=args.dry_run)
    usage = {k: usage[k] + extra[k] for k in ("input_tokens", "output_tokens")}

    # The spoken script: the model's text, or a plain one built here when it is missing.
    listen = clean_listen(choice.get("listen"))
    if not listen:
        listen = fallback_listen(lead, front, today)
        print("listen: fallback")

    # Sunday edition (or BRIEF_FORCE_WEEK=1): the week in review. Never stops the run.
    week, week_usage = make_week(cfg, {"lead": lead, "front": front, "sections": sections}, now,
                                 dry_run=args.dry_run)
    usage = {k: usage[k] + week_usage[k] for k in usage}
    models = {"front": front_model(cfg), "desk": cfg["model"]}
    if videos and videos_from_model:
        models["videos"] = cfg["model"]
    if week:
        models["week"] = week_model(cfg)
        if now.weekday() == 6:
            listen += week_listen(week)

    brief = {"masthead": cfg.get("masthead", "CMRLM"), "tagline": cfg.get("tagline", "The Daily Brief"),
             "edition": cfg.get("edition", "Atlanta Edition"), "date_label": today,
             "generated_at": now.isoformat(timespec="seconds"), "model": front_model(cfg), "models": models,
             "listen": listen,
             "lead": lead, "front": front, "sections": sections, "tabs": tabs,
             "recalls": recalls, "quote": quote,
             "scores": scores, "source_errors": errors, "usage": usage}
    if videos:
        brief["videos"] = videos
    if week:
        brief["week"] = week
    out = SITE_DIR / "brief.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n")
    write_archive(SITE_DIR, data_dir(), brief, now)
    in_sections = sum(len(s["items"]) for s in sections)
    in_tabs = sum(len(s["items"]) for t in tabs for s in t["sections"])
    with_img = sum(1 for i in ([lead] if lead else []) + front + [i for s in sections for i in s["items"]]
                   if i.get("image"))
    print(f"wrote {out.name}: lead={'yes' if lead else 'no'}, {len(front)} front, "
          f"{in_sections} in {len(sections)} sections, {with_img} with images, "
          f"{in_tabs} in {len(tabs)} tabs, {len(recalls)} recalls, quote={'yes' if quote else 'no'}, "
          f"{len(scores)} scores, {len(errors)} sources skipped | "
          f"tokens in={usage['input_tokens']} out={usage['output_tokens']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
