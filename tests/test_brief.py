"""Unit checks for brief.py. Run with: python -m pytest -q"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import brief  # noqa: E402


def entry(**kw):
    return kw


# ---------- image extraction ----------

def test_media_content_prefers_image_and_largest_width():
    e = entry(media_content=[
        {"url": "https://x.test/video.mp4", "type": "video/mp4", "width": "1920"},
        {"url": "https://x.test/small.jpg", "medium": "image", "width": "300"},
        {"url": "https://x.test/big.jpg", "medium": "image", "width": "1200"},
    ])
    assert brief.entry_image(e, "https://x.test/a") == "https://x.test/big.jpg"


def test_media_thumbnail():
    e = entry(media_thumbnail=[{"url": "https://x.test/t.jpg"}])
    assert brief.entry_image(e) == "https://x.test/t.jpg"


def test_enclosure_image_only():
    e = entry(enclosures=[{"href": "https://x.test/a.mp3", "type": "audio/mpeg"},
                          {"href": "https://x.test/pic.png", "type": "image/png"}])
    assert brief.entry_image(e) == "https://x.test/pic.png"


def test_link_rel_enclosure():
    e = entry(links=[{"rel": "alternate", "href": "https://x.test/a"},
                     {"rel": "enclosure", "href": "https://x.test/p.jpg", "type": "image/jpeg"}])
    assert brief.entry_image(e) == "https://x.test/p.jpg"


def test_npr_img_only_in_content_encoded():
    e = entry(summary="Plain text summary.",
              content=[{"type": "text/html",
                        "value": '<p><img src="https://npr.test/media/story.jpg" alt=""/>Hello</p>'}])
    assert brief.entry_image(e) == "https://npr.test/media/story.jpg"


def test_img_in_summary_skips_tracking_pixels():
    e = entry(summary='<img src="https://t.test/p.gif" width="1" height="1"><img src="https://x.test/real.jpg">')
    assert brief.entry_image(e) == "https://x.test/real.jpg"


def test_http_upgraded_and_non_http_rejected():
    assert brief.entry_image(entry(media_thumbnail=[{"url": "http://x.test/t.jpg"}])) == "https://x.test/t.jpg"
    assert brief.entry_image(entry(media_thumbnail=[{"url": "javascript:alert(1)"}])) is None
    assert brief.entry_image(entry(media_thumbnail=[{"url": "/relative.jpg"}])) is None
    assert brief.entry_image(entry()) is None


def test_bbc_240_to_800():
    e = entry(media_thumbnail=[{"url": "https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/abc/x.jpg", "width": "240"}])
    assert brief.entry_image(e) == "https://ichef.bbci.co.uk/ace/standard/800/cpsprodpb/abc/x.jpg"
    e = entry(media_thumbnail=[{"url": "http://c.files.bbci.co.uk/standard/976/x.jpg"}])
    assert brief.entry_image(e) == "https://c.files.bbci.co.uk/standard/800/x.jpg"


def test_youtube_prefers_hqdefault():
    e = entry(media_thumbnail=[{"url": "https://i2.ytimg.com/vi/abc/default.jpg"}])
    assert brief.entry_image(e) == "https://i2.ytimg.com/vi/abc/hqdefault.jpg"
    e = entry(media_thumbnail=[{"url": "https://i2.ytimg.com/vi/abc/hqdefault.jpg"}])
    assert brief.entry_image(e) == "https://i2.ytimg.com/vi/abc/hqdefault.jpg"


def test_espn_image():
    assert brief.espn_image({"images": [{"url": "https://a.espncdn.com/p.jpg"}, {"url": "https://b/x.jpg"}]}) \
        == "https://a.espncdn.com/p.jpg"
    assert brief.espn_image({"images": []}) is None
    assert brief.espn_image({}) is None


def test_github_images():
    assert brief.github_image("owner/repo") == "https://opengraph.githubassets.com/1/owner/repo"
    assert brief.github_image("not a repo") is None
    e = entry(summary="trending")
    assert brief.entry_image(e, "https://github.com/acme/pocket-sql") == \
        "https://opengraph.githubassets.com/1/acme/pocket-sql"


def test_og_image_parsing():
    page = '<html><head><meta name="twitter:image" content="https://x.test/tw.jpg">' \
           '<meta property="og:image" content="/img/og.jpg"></head></html>'
    assert brief.og_image_from_html(page, "https://x.test/a/b") == "https://x.test/img/og.jpg"
    assert brief.og_image_from_html('<meta name="twitter:image" content="https://x.test/tw.jpg">', "https://x.test/") \
        == "https://x.test/tw.jpg"
    assert brief.og_image_from_html("<html></html>", "https://x.test/") is None


def test_resolve_og_images_limits_and_skips():
    out = {"lead": {"url": "https://a/0", "image": None, "source": "S"},
           "front": [{"url": "https://a/1", "image": "https://a/i.jpg", "source": "S"}],
           "sections": [{"topic": "T", "items": [{"url": f"https://a/{n}", "image": None, "source": "S"}
                                                 for n in range(2, 40)]
                         + [{"url": "https://a/ap", "image": None, "source": "NoImg"}]}]}
    calls = []

    def fake(u):
        calls.append(u)
        return "https://img/" + u[-2:].strip("/") + ".jpg" if u != "https://a/3" else None

    found = brief.resolve_og_images(out, {"NoImg"}, session=object(), fetcher=fake)
    assert len(calls) == brief.OG_MAX_FETCHES
    assert "https://a/1" not in calls and "https://a/ap" not in calls
    assert out["lead"]["image"]
    assert found == len(calls) - 1


# ---------- filters ----------

def mk(title, image="https://x/i.jpg"):
    return {"id": "x", "topic": "World", "source": "S", "title": title, "url": "https://x", "image": image}


def test_exclude_title_patterns():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    bad = ["Opinion: Why this matters", "Opinion | The vote", "ANALYSIS: markets wobble",
           "A Commentary on trade", "Op-Ed from the mayor", "Editorial board speaks",
           "The week: opinion: nope"]
    good = ["Flood waters recede", "Analysts expect rates to hold", "Opinionated chef opens a cafe"]
    kept = [i["title"] for i in brief.filter_titles([mk(t) for t in bad + good], cfg["exclude_title_patterns"])]
    assert kept == good


def test_title_suffix_strip_and_no_image():
    items = [mk("Big storm hits coast - AP News"), mk("Another - Reuters"), mk("Plain title")]
    out = brief.apply_source_options(items, {"title_suffix_strip": " - AP News", "no_image": True})
    assert [i["title"] for i in out] == ["Big storm hits coast", "Another - Reuters", "Plain title"]
    assert all(i["image"] is None for i in out)
    out = brief.apply_source_options([mk("T - AP News")], {})
    assert out[0]["title"] == "T - AP News" and out[0]["image"]


# ---------- build_output ----------

ORDER = ["Tech & AI", "Sports", "Architecture", "World"]       # the small set the build_output tests use
TOPICS = ["Tech & AI", "Politics", "World", "Sports", "Atlanta", "Architecture", "Science & Nature"]


def items_fixture():
    def it(n, topic, image):
        return {"id": f"id{n}", "topic": topic, "source": f"src{n}", "title": f"Title {n}",
                "url": f"https://x.test/{n}", "published": f"2026-10-0{n % 9 + 1}T00:00:00+00:00",
                "summary": "s", "image": image}
    return [it(1, "Tech & AI", None), it(2, "Tech & AI", "https://x/2.jpg"), it(3, "Sports", None),
            it(4, "Architecture", "https://x/4.jpg"), it(5, "World", None), it(6, "World", None),
            it(7, "World", None), it(8, "World", None)]


def test_unknown_and_repeated_ids_dropped():
    choice = {"lead": {"id": "id2", "headline": "Short Head", "note": "n"},
              "front": [{"id": "nope", "note": "x"}, {"id": "id2", "note": "dup"}, {"id": "id4", "note": "ok"}],
              "sections": [{"topic": "World", "items": [{"id": "id5", "note": "a"}, {"id": "id5", "note": "again"},
                                                          {"id": "zzz", "note": "b"}, {"id": "id6", "note": "c"}]}]}
    out = brief.build_output(choice, items_fixture(), ORDER)
    assert out["lead"]["title"] == "Title 2" and out["lead"]["headline"] == "Short Head"
    assert [f["title"] for f in out["front"]] == ["Title 4"]
    world = next(s for s in out["sections"] if s["topic"] == "World")
    assert [i["title"] for i in world["items"]] == ["Title 5", "Title 6"]
    urls = [out["lead"]["url"]] + [f["url"] for f in out["front"]] + [i["url"] for s in out["sections"] for i in s["items"]]
    assert len(urls) == len(set(urls))
    assert all(u.startswith("https://x.test/") for u in urls)


def test_sections_in_configured_order_with_fallback():
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n"}, "front": [],
              "sections": [{"topic": "World", "items": [{"id": "id5", "note": "a"}]}]}
    out = brief.build_output(choice, items_fixture(), ORDER)
    topics = [s["topic"] for s in out["sections"]]
    assert topics == [t for t in ORDER if t in topics] and set(topics) == set(ORDER)
    sports = next(s for s in out["sections"] if s["topic"] == "Sports")
    assert sports["items"][0]["note"] == ""


def test_missing_headline_falls_back_to_title():
    out = brief.build_output({"lead": {"id": "id1", "note": "n"}, "front": [], "sections": []}, items_fixture(), ORDER)
    assert out["lead"]["headline"] == "Title 1"


def test_lead_front_swap_when_lead_has_no_image():
    choice = {"lead": {"id": "id1", "headline": "Custom Head", "note": "lead note"},
              "front": [{"id": "id3", "note": "f1"}, {"id": "id4", "note": "f2"}], "sections": []}
    out = brief.build_output(choice, items_fixture(), ORDER)
    assert out["lead"]["image"] is None
    assert brief.swap_lead_if_imageless(out) is True
    assert out["lead"]["title"] == "Title 4" and out["lead"]["headline"] == "Title 4"
    assert out["lead"]["image"]
    old = next(f for f in out["front"] if f["title"] == "Title 1")
    assert "headline" not in old
    assert brief.swap_lead_if_imageless(out) is False        # lead now has an image


def test_no_swap_when_nothing_has_image():
    choice = {"lead": {"id": "id1", "headline": "H", "note": "n"}, "front": [{"id": "id3", "note": "f"}], "sections": []}
    out = brief.build_output(choice, items_fixture(), ORDER)
    assert brief.swap_lead_if_imageless(out) is False
    assert out["lead"]["title"] == "Title 1"


def test_decks_copied_stripped_and_limited():
    long = "x" * 150
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n",
                       "decks": ["  First deck  ", "", "   ", "Second", long, "Fourth dropped", 7]},
              "front": [], "sections": []}
    lead = brief.build_output(choice, items_fixture(), ORDER)["lead"]
    assert lead["decks"][:2] == ["First deck", "Second"]
    assert len(lead["decks"]) == 3
    assert len(lead["decks"][2]) <= 100


def test_lead_note_allows_long_summary_and_is_capped():
    note = " ".join(["word"] * 140) + "."
    choice = {"lead": {"id": "id2", "headline": "H", "note": note}, "front": [], "sections": []}
    lead = brief.build_output(choice, items_fixture(), ORDER)["lead"]
    assert lead["note"] == note                      # 140 words survive untouched
    choice["lead"]["note"] = "w" * 5000
    assert len(brief.build_output(choice, items_fixture(), ORDER)["lead"]["note"]) <= brief.LEAD_NOTE_CHARS


def test_lead_prompt_and_schema_ask_for_long_grounded_note():
    desc = brief.publish_tool(ORDER)["input_schema"]["properties"]["lead"]["properties"]["note"]["description"]
    assert "120 to 150 words" in desc and "4 to 6 sentences" in desc
    assert "120 and 150 words" in brief.SYSTEM_PROMPT and "Do not invent any fact" in brief.SYSTEM_PROMPT
    assert "at most 25 words" in brief.SYSTEM_PROMPT       # front and section notes stay short


def test_fixture_lead_note_is_about_130_words():
    resp = json.loads((ROOT / "fixtures" / "sample_response.json").read_text())
    assert 120 <= len(resp["lead"]["note"].split()) <= 150


def test_page_keeps_safety_switches_and_fill_engine():
    html = (ROOT / "site" / "index.html").read_text()
    assert "var DEMO_DATA = null;" in html
    assert "Today’s paper has not been printed yet." in html
    assert "innerHTML" not in html and "document.write" not in html
    assert "MIN_W = 600" in html and "document.fonts" in html and "8000" in html


def test_decks_missing_or_bad_become_empty():
    for bad in (None, "a string", {"a": 1}, []):
        choice = {"lead": {"id": "id2", "headline": "H", "note": "n", "decks": bad}, "front": [], "sections": []}
        assert brief.build_output(choice, items_fixture(), ORDER)["lead"]["decks"] == []
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n"}, "front": [], "sections": []}
    assert brief.build_output(choice, items_fixture(), ORDER)["lead"]["decks"] == []


def test_decks_empty_after_swap():
    choice = {"lead": {"id": "id1", "headline": "Custom", "note": "n", "decks": ["One", "Two"]},
              "front": [{"id": "id4", "note": "f"}], "sections": []}
    out = brief.build_output(choice, items_fixture(), ORDER)
    assert out["lead"]["decks"] == ["One", "Two"]
    assert brief.swap_lead_if_imageless(out) is True
    assert out["lead"]["decks"] == []
    assert "decks" not in out["front"][0]


def test_youtube_id_extraction():
    yid = brief.youtube_id
    assert yid("https://www.youtube.com/watch?v=abc123DEF45") == "abc123DEF45"
    assert yid("https://youtube.com/watch?v=abc123DEF45&t=5s") == "abc123DEF45"
    assert yid("https://www.youtube.com/watch?feature=x&v=a-c_23DEF45") == "a-c_23DEF45"
    assert yid("https://youtu.be/abc123DEF45") == "abc123DEF45"
    assert yid("https://youtu.be/abc123DEF45?si=zzz") == "abc123DEF45"
    for bad in ("https://www.youtube.com/watch?v=short", "https://www.youtube.com/watch?v=abc123DEF45XX",
                "https://www.youtube.com/watch?v=abc123DE<45", "https://youtu.be/bad id", "https://youtu.be/",
                "https://www.youtube.com/watch", "https://www.youtube.com/playlist?list=abc123DEF45",
                "https://evil.test/watch?v=abc123DEF45", "https://notyoutube.com/watch?v=abc123DEF45",
                "javascript:alert(1)", "", None):
        assert yid(bad) is None, bad


def test_item_video_field_set_only_for_youtube():
    items = items_fixture()
    items[1]["url"] = "https://youtu.be/abc123DEF45"
    items[3]["url"] = "https://www.youtube.com/watch?v=tooshort"
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n", "decks": ["a", "b"]},
              "front": [{"id": "id4", "note": "f"}], "sections": []}
    out = brief.build_output(choice, items, ORDER)
    assert out["lead"]["video"] == "abc123DEF45"        # YouTube items keep their thumbnail image too
    assert out["lead"]["image"] == "https://x/2.jpg"
    assert out["front"][0]["video"] is None
    assert all(i["video"] is None for s in out["sections"] for i in s["items"])


# ---------- tool schema and config ----------

def test_tool_schema_shape():
    t = brief.publish_tool(ORDER)
    props = t["input_schema"]["properties"]
    assert set(t["input_schema"]["required"]) == {"lead", "front", "sections", "listen"}
    assert props["listen"]["type"] == "string"
    assert set(props["lead"]["required"]) == {"id", "headline", "note", "decks"}
    decks = props["lead"]["properties"]["decks"]
    assert decks["type"] == "array" and decks["minItems"] == 2 and decks["maxItems"] == 3
    assert props["front"]["minItems"] == 2 and props["front"]["maxItems"] == 4
    sec = props["sections"]
    assert sec["minItems"] == 1
    assert sec["items"]["properties"]["topic"]["enum"] == ORDER
    assert sec["items"]["properties"]["items"]["minItems"] == 2
    assert sec["items"]["properties"]["items"]["maxItems"] == 3
    assert "image" not in json.dumps(t).replace("has_image", "")


def test_sources_config():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    assert [t["name"] for t in cfg["topics"]] == TOPICS
    assert cfg["max_total_items"] == 180
    assert cfg["max_output_tokens"] == 6000 and cfg["desk_max_output_tokens"] == 8000
    assert (cfg["model"], cfg["front_model"], cfg["week_model"]) == (
        "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
    assert (cfg["masthead"], cfg["tagline"], cfg["edition"]) == ("CMRLM", "The Daily Brief", "Atlanta Edition")
    world = next(t for t in cfg["topics"] if t["name"] == "World")
    assert [s["name"] for s in world["sources"]] == ["NPR World", "NPR News", "BBC World"]
    names = {t["name"]: [s["name"] for s in t["sources"]] for t in cfg["topics"]}
    assert names["Politics"] == ["The Lever", "Drop Site News", "NPR Politics"]
    assert names["Atlanta"] == ["Rough Draft Atlanta", "Urbanize Atlanta", "SaportaReport"]
    assert "AEC Magazine" in names["Architecture"] and "Architosh" not in names["Architecture"]   # Architosh blocks the GitHub runner (403)
    assert "Urbanize Atlanta" not in names["Architecture"]
    assert names["Science & Nature"] == ["Science News", "NASA", "Mongabay"]
    assert names["Sports"] == ["Houston Rockets", "Atlanta Braves", "San Francisco 49ers", "UFC (ESPN)"]
    every = [s for t in cfg["topics"] for s in t["sources"]]
    assert {s["type"] for s in every} <= set(brief.FETCHERS)
    assert len({s["url"] for s in every}) == len(every)
    assert [(t["id"], t["name"], t["topics"]) for t in cfg["tabs"]] == [
        ("news", "News", ["Politics", "World"]), ("tech", "Tech & AI", ["Tech & AI"]),
        ("sports", "Sports", ["Sports"]), ("atlanta", "Atlanta", ["Atlanta", "Architecture"]),
        ("science", "Science", ["Science & Nature"])]
    assert set(topic for t in cfg["tabs"] for topic in t["topics"]) == set(TOPICS)
    assert cfg["recalls"]["max_items"] == 3 and cfg["recalls"]["max_age_days"] == 4
    assert cfg["recalls"]["url"].startswith("https://www.fda.gov/")
    assert cfg["quotes"] == {"file": "quotes.yaml", "title": "Words to Live By"}
    assert "stoic" not in cfg
    for word in ("The Lever", "Drop Site News", "UFC", "space", "Atlanta local news"):
        assert word in cfg["reader_profile"]


# ---------- dry run ----------

def test_dry_run_writes_new_schema(tmp_path):
    env = {**os.environ, "BRIEF_SITE_DIR": str(tmp_path)}
    env.pop("BRIEF_PASSPHRASE", None)
    r = subprocess.run([sys.executable, str(ROOT / "brief.py"), "--dry-run"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    b = json.loads((tmp_path / "brief.json").read_text())
    assert list(b)[:6] == ["masthead", "tagline", "edition", "date_label", "generated_at", "model"]
    assert "top" not in b
    assert b["lead"]["headline"] and len(b["front"]) == 3
    assert 2 <= len(b["lead"]["decks"]) <= 3
    assert [s["topic"] for s in b["sections"]] == TOPICS
    assert all(2 <= len(s["items"]) <= 3 for s in b["sections"])
    every = [b["lead"]] + b["front"] + [i for s in b["sections"] for i in s["items"]]
    assert all(set(i) >= {"title", "url", "source", "topic", "note", "image", "video"} for i in every)
    assert "does-not-exist-123" not in json.dumps(b)
    assert not any(i["video"] for i in every)               # videos live only in the YouTube tab
    assert not any(i["video"] for t in b["tabs"] for s in t["sections"] for i in s["items"])
    # tabs, recalls, quote and the longer score lines
    assert [t["id"] for t in b["tabs"]] == ["news", "tech", "sports", "atlanta", "science"]
    for t in b["tabs"]:
        assert t["name"] and t["sections"]
        for sec in t["sections"]:
            assert 4 <= len(sec["items"]) <= 10 and sec["topic"] in TOPICS
            assert all(set(i) >= {"title", "url", "source", "topic", "note", "image", "video"} for i in sec["items"])
    assert [s["topic"] for s in b["tabs"][3]["sections"]] == ["Atlanta", "Architecture"]
    assert "not-a-real-id" not in json.dumps(b)
    assert len(b["recalls"]) == 3 and all(set(r) == {"title", "url", "published"} for r in b["recalls"])
    assert b["quote"]["text"] and b["quote"]["who"] and b["quote"]["title"] == "Words to Live By"
    assert "stoic" not in b
    for sc in b["scores"]:
        assert len(sc["recent"]) <= 3 and len(sc["upcoming"]) <= 3 and "last" in sc and "next" in sc
    assert b["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert b["models"]["videos"] == "claude-haiku-5-5"


# ---------- real feedparser parsing ----------

RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>t</title>
<item><title>NPR story</title><link>https://npr.test/1</link><description>plain</description>
 <content:encoded><![CDATA[<p><img src="https://npr.test/a.jpg"/>text</p>]]></content:encoded></item>
<item><title>BBC story</title><link>https://bbc.test/2</link><description>d</description>
 <media:thumbnail width="240" height="135" url="https://ichef.bbci.co.uk/ace/standard/240/cpsprodpb/z.jpg"/></item>
<item><title>Media story</title><link>https://m.test/3</link>
 <media:content url="https://m.test/s.jpg" medium="image" width="200"/>
 <media:content url="https://m.test/b.jpg" medium="image" width="900"/></item>
<item><title>Enclosure story</title><link>https://m.test/4</link>
 <enclosure url="https://m.test/e.jpg" type="image/jpeg" length="1"/></item>
<item><title>No image</title><link>https://m.test/5</link><description>nothing</description></item>
</channel></rss>"""


def test_real_feedparser_entries():
    import feedparser
    imgs = [brief.entry_image(e, e.get("link", "")) for e in feedparser.parse(RSS).entries]
    assert imgs == ["https://npr.test/a.jpg",
                    "https://ichef.bbci.co.uk/ace/standard/800/cpsprodpb/z.jpg",
                    "https://m.test/b.jpg", "https://m.test/e.jpg", None]


def test_youtube_atom_feed():
    import feedparser
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<title>x</title><entry><title>V</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/><id>1</id>
<updated>2026-10-09T00:00:00Z</updated><media:group><media:thumbnail url="https://i3.ytimg.com/vi/abc/default.jpg"/>
<media:content url="https://www.youtube.com/v/abc" type="application/x-shockwave-flash"/></media:group></entry></feed>"""
    e = feedparser.parse(xml).entries[0]
    assert brief.entry_image(e, e.link) == "https://i3.ytimg.com/vi/abc/hqdefault.jpg"


def test_tracking_pixels_are_skipped():
    import brief
    npr = "https://media.npr.org/include/images/tracking/npr-rss-pixel.png?story=nx-s1-5996938"
    real = "https://npr.brightspotcdn.com/dims3/default/strip/false/crop/4000x2667/photo.jpg"
    assert brief.clean_image(npr) is None
    assert brief.clean_image(real) == real
    assert brief.clean_image("https://example.com/img/spacer.gif") is None
    entry = {"content": [{"value": f'<img src="{npr}"/><p>x</p><img src="{real}"/>'}]}
    assert brief.entry_image(entry, "https://www.npr.org/2026/10/10/story") == real


# ---------- call 2: the desk (topic tabs) ----------

CFG = yaml.safe_load((ROOT / "sources.yaml").read_text())


def desk_items():
    def it(n, topic, pub="2026-10-0%dT00:00:00+00:00", image=None, url=None):
        return {"id": f"d{n}", "topic": topic, "source": f"src{n}", "title": f"Story {n}",
                "url": url or f"https://x.test/d{n}", "published": pub % (n % 9 + 1) if "%d" in pub else pub,
                "summary": "s", "image": image}
    items = [it(1, "Politics"), it(2, "Politics"), it(3, "World"), it(4, "World"), it(5, "Atlanta"),
             it(6, "Architecture"), it(7, "Tech & AI"), it(8, "Science & Nature")]
    items += [it(n, "Sports", pub=f"2026-10-{n:02d}T00:00:00+00:00") for n in range(20, 31)]   # 11 sports items
    return items


def test_desk_tool_schema_and_prompt():
    t = brief.desk_tool(TOPICS)
    assert t["name"] == "publish_desk" and t["input_schema"]["required"] == ["sections"]
    sec = t["input_schema"]["properties"]["sections"]["items"]["properties"]
    assert sec["topic"]["enum"] == TOPICS
    assert sec["items"]["minItems"] == 4 and sec["items"]["maxItems"] == 10
    assert set(sec["items"]["items"]["required"]) == {"id", "note"}
    assert "6 to 10" in brief.DESK_PROMPT and "at most 35 words" in brief.DESK_PROMPT
    assert "Never invent facts" in brief.DESK_PROMPT and "Refer to items by id only" in brief.DESK_PROMPT
    assert "neutral" in brief.DESK_PROMPT.lower() and "Politics" in brief.DESK_PROMPT
    assert "Politics" in brief.SYSTEM_PROMPT
    assert "2 to 3 items per topic" in brief.SYSTEM_PROMPT


def test_build_tabs_order_shape_and_dropping():
    choice = {"sections": [
        {"topic": "World", "items": [{"id": "d4", "note": "four"}, {"id": "d3", "note": "three"},
                                     {"id": "nope", "note": "x"}, {"id": "d4", "note": "dup"}]},
        {"topic": "Politics", "items": [{"id": "d2", "note": "two"}, {"id": "d1", "note": "one " * 200}]},
        {"topic": "Atlanta", "items": [{"id": "d6", "note": "wrong topic on purpose"}]},    # d6 is Architecture
    ]}
    tabs = brief.build_tabs(choice, desk_items(), CFG)
    assert [t["id"] for t in tabs] == ["news", "tech", "sports", "atlanta", "science"]
    assert [t["name"] for t in tabs] == ["News", "Tech & AI", "Sports", "Atlanta", "Science"]
    news = tabs[0]["sections"]
    assert [s["topic"] for s in news] == ["Politics", "World"]                 # the tab's topic order, not the model's
    assert [i["title"] for i in news[1]["items"]] == ["Story 4", "Story 3"]    # the model's order inside a topic
    assert [i["note"] for i in news[1]["items"]] == ["four", "three"]
    assert len(news[0]["items"][1]["note"]) <= 300
    item = news[0]["items"][0]
    assert set(item) == {"title", "url", "source", "topic", "note", "image", "video"}
    assert item["url"] == "https://x.test/d2" and item["topic"] == "Politics" and item["video"] is None
    atl = {s["topic"]: s["items"] for s in tabs[3]["sections"]}
    assert [i["title"] for i in atl["Architecture"]] == ["Story 6"]            # an item sits under its own topic
    assert [i["title"] for i in atl["Atlanta"]] == ["Story 5"]                 # model skipped it: newest fill, no note
    assert atl["Atlanta"][0]["note"] == ""


def test_build_tabs_caps_topics_and_drops_empty_sections_and_tabs():
    sports_ids = [{"id": f"d{n}", "note": "n"} for n in range(20, 31)]
    tabs = brief.build_tabs({"sections": [{"topic": "Sports", "items": sports_ids}]}, desk_items(), CFG)
    sports = next(t for t in tabs if t["id"] == "sports")
    assert len(sports["sections"][0]["items"]) == brief.DESK_MAX_ITEMS == 10
    only_world = [i for i in desk_items() if i["topic"] == "World"]
    tabs = brief.build_tabs(None, only_world, CFG)
    assert [t["id"] for t in tabs] == ["news"]                                  # every other tab is empty: dropped
    assert [s["topic"] for s in tabs[0]["sections"]] == ["World"]              # Politics is empty: dropped
    assert brief.build_tabs(None, [], CFG) == []


def test_build_tabs_fallback_uses_newest_eight_without_notes():
    for bad in (None, {}, {"sections": "nope"}, {"sections": [{"topic": "Sports", "items": "x"}]},
                {"sections": [{"topic": "Sports", "items": [{"id": "zzz", "note": "n"}]}]}, "text", 7):
        tabs = brief.build_tabs(bad, desk_items(), CFG)
        sports = next(t for t in tabs if t["id"] == "sports")["sections"][0]["items"]
        assert [i["title"] for i in sports] == [f"Story {n}" for n in range(30, 22, -1)], bad   # newest 8
        assert all(i["note"] == "" for t in tabs for s in t["sections"] for i in s["items"])
        assert [t["id"] for t in tabs] == ["news", "tech", "sports", "atlanta", "science"]


def test_desk_has_picks():
    items = desk_items()
    assert brief.desk_has_picks({"sections": [{"topic": "World", "items": [{"id": "d3", "note": "n"}]}]}, items)
    for bad in (None, {}, {"sections": []}, {"sections": [{"items": [{"id": "zzz"}]}]}, "x",
                {"sections": [None, {"items": None}]}):
        assert not brief.desk_has_picks(bad, items)


def test_resolve_og_images_covers_tabs_front_first_and_shares_by_url():
    def story(url, image=None, source="S"):
        return {"url": url, "image": image, "source": source}
    out = {"lead": story("https://a/lead"), "front": [story("https://a/f1")],
           "sections": [{"topic": "T", "items": [story("https://a/s1")]}],
           "tabs": [{"id": "x", "name": "X", "sections": [
               {"topic": "T", "items": [story("https://a/f1"), story("https://a/s1")]
                + [story(f"https://a/t{n}") for n in range(40)] + [story("https://a/known", "https://a/k.jpg")]}]},
               {"id": "y", "name": "Y", "sections": [{"topic": "U", "items": [story("https://a/known")]}]}]}
    calls = []

    def fake(u):
        calls.append(u)
        return "https://img/" + u.rsplit("/", 1)[1] + ".jpg"

    found = brief.resolve_og_images(out, set(), session=object(), fetcher=fake)
    first_25 = ["https://a/lead", "https://a/f1", "https://a/s1"] + [f"https://a/t{n}" for n in range(22)]
    assert sorted(calls) == sorted(first_25)                                       # front page first, then tab items
    assert len(calls) == len(set(calls)) == brief.OG_MAX_FETCHES                  # one fetch per URL, global cap
    assert found == brief.OG_MAX_FETCHES
    tab_items = out["tabs"][0]["sections"][0]["items"]
    assert tab_items[0]["image"] == out["front"][0]["image"] == "https://img/f1.jpg"   # the copy shares the picture
    assert tab_items[1]["image"] == "https://img/s1.jpg"
    assert out["tabs"][1]["sections"][0]["items"][0]["image"] == "https://a/k.jpg"     # known picture, no fetch
    assert "https://a/known" not in calls
    assert tab_items[-2]["image"] is None                                         # past the cap: left alone


# ---------- scores ----------

class FakeESPN:
    def __init__(self, events):
        self.events, self.urls = events, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        events = self.events

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"events": events}
        return R()


def espn_event(when, mine=None, other=None, done=True, home=True, opp="NYM", link=None):
    def comp(team_id, abbr, score, is_home):
        c = {"team": {"id": team_id, "abbreviation": abbr}, "homeAway": "home" if is_home else "away"}
        if score is not None:
            c["score"] = {"value": score}
        return c
    ev = {"date": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
          "competitions": [{"competitors": [comp("15", "ATL", mine, home), comp("21", opp, other, not home)],
                            "status": {"type": {"completed": done}}}]}
    if link:
        ev["links"] = [{"href": link}]
    return ev


def test_team_score_recent_and_upcoming():
    now = datetime.now(brief.TZ)
    events = [
        espn_event(now - timedelta(days=4), 5, 3, home=True, opp="NYM", link="https://espn.test/old"),
        espn_event(now - timedelta(days=3), 2, 6, home=False, opp="PHI"),
        espn_event(now - timedelta(days=2), 4, 4, home=True, opp="MIA"),
        espn_event(now - timedelta(days=1), 9, 1, home=False, opp="WSH", link="https://espn.test/last"),
        espn_event(now - timedelta(days=9), 1, 0, home=True, opp="BOS"),               # 5th newest: not shown
        espn_event(now - timedelta(days=5), None, None, home=True, opp="CHC"),         # no score: skipped
        espn_event(now + timedelta(days=1), done=False, home=False, opp="LAD", link="https://espn.test/next"),
        espn_event(now + timedelta(days=2), done=False, home=True, opp="SD"),
        espn_event(now + timedelta(days=3), done=False, home=True, opp="SF"),
        espn_event(now + timedelta(days=4), done=False, home=True, opp="SEA"),         # 4th: not shown
        espn_event(now - timedelta(days=1), done=False, home=True, opp="POSTPONED"),   # past and unfinished: ignored
    ]
    out = brief.team_score(FakeESPN(events), {"team": "Atlanta Braves", "league_path": "baseball/mlb", "team_id": 15})
    last = now - timedelta(days=1)
    assert out["last"] == "W 9–1 at WSH" and out["last_date"] == f"{last:%b} {last.day}"
    assert out["url"] == "https://espn.test/last"
    assert len(out["recent"]) == 3
    assert out["recent"][0] == f"W 9–1 at WSH · {last:%b} {last.day}"                # newest first, each with a date
    assert out["recent"][1].startswith("T 4–4 vs MIA · ") and out["recent"][2].startswith("L 2–6 at PHI · ")
    ahead = now + timedelta(days=1)
    assert out["next"] == f"{ahead:%a %b} {ahead.day} at LAD" == out["upcoming"][0]
    assert [u.split()[-1] for u in out["upcoming"]] == ["LAD", "SD", "SF"]
    assert set(out) >= {"team", "last", "last_date", "next", "url", "recent", "upcoming"}


def test_team_score_with_nothing_to_show():
    out = brief.team_score(FakeESPN([]), {"team": "T", "league_path": "baseball/mlb", "team_id": 15})
    assert out == {"team": "T", "last": None, "last_date": None, "next": None, "url": None,
                   "recent": [], "upcoming": []}
    only_future = [espn_event(datetime.now(brief.TZ) + timedelta(days=2), done=False, link="https://espn.test/n")]
    out = brief.team_score(FakeESPN(only_future), {"team": "T", "league_path": "baseball/mlb", "team_id": 15})
    assert out["last"] is None and out["recent"] == [] and out["url"] == "https://espn.test/n"
    assert len(out["upcoming"]) == 1 and out["next"] == out["upcoming"][0]


# ---------- recalls ----------

def recalls_feed(*rows):
    from email.utils import format_datetime
    body = "".join(f"<item><title>{t}</title><link>{u}</link>" + (f"<pubDate>{format_datetime(d)}</pubDate>" if d else "")
                   + "</item>" for t, u, d in rows)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>r</title>{body}</channel></rss>'


NOW_UTC = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def test_parse_recalls_age_order_cap_and_cleaning():
    d = lambda days: NOW_UTC - timedelta(days=days)  # noqa: E731
    feed = recalls_feed(("Older one", "https://fda.test/2", d(3)), ("Newest &amp; <b>best</b>", "https://fda.test/1", d(0)),
                        ("Too old", "https://fda.test/old", d(5)), ("No date", "https://fda.test/nodate", None),
                        ("Bad link", "ftp://fda.test/x", d(1)), ("Dup", "https://fda.test/1", d(1)),
                        ("Middle", "https://fda.test/3", d(1)), ("Fourth", "https://fda.test/4", d(2)))
    cfg = {"max_items": 3, "max_age_days": 4}
    got = brief.parse_recalls(feed, cfg, NOW_UTC)
    assert [r["title"] for r in got] == ["Newest & best", "Middle", "Fourth"]          # newest first, capped at 3
    assert all(set(r) == {"title", "url", "published"} for r in got)
    assert got[0]["url"] == "https://fda.test/1" and got[0]["published"].startswith("2026-10-11T12:00:00")
    assert "Too old" not in json.dumps(got) and "No date" not in json.dumps(got)
    assert len(brief.parse_recalls(feed, {"max_items": 10, "max_age_days": 4}, NOW_UTC)) == 4   # 4 in range, the duplicate link counts once
    assert brief.parse_recalls(feed, {"max_items": 3, "max_age_days": 0.1}, NOW_UTC)[0]["title"].startswith("Newest")
    assert brief.parse_recalls(recalls_feed(("Old", "https://fda.test/o", d(9))), cfg, NOW_UTC) == []
    with pytest.raises(ValueError):
        brief.parse_recalls("not a feed", cfg, NOW_UTC)


def test_dry_run_recalls_fixture_drops_the_old_item():
    now = datetime.now(brief.TZ)
    got = brief.load_recalls_fixture(ROOT / "fixtures/sample_recalls.xml", CFG, now)
    assert len(got) == 3 and all("too old" not in r["title"] for r in got)
    assert got == sorted(got, key=lambda r: r["published"], reverse=True)


class FakeSession:
    def __init__(self, text="", status=200, boom=None):
        self.text, self.status, self.boom, self.urls = text, status, boom, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.boom:
            raise self.boom
        outer = self

        class R:
            content = outer.text.encode()
            text = outer.text
            encoding = None

            def raise_for_status(self):
                if outer.status >= 400:
                    raise brief.requests.HTTPError(f"{outer.status} Server Error")
        return R()


def test_fetch_recalls_uses_configured_url():
    s = FakeSession(recalls_feed(("Fresh", "https://fda.test/1", NOW_UTC)))
    assert [r["title"] for r in brief.fetch_recalls(CFG, NOW_UTC, session=s)] == ["Fresh"]
    assert s.urls == [CFG["recalls"]["url"]]
    with pytest.raises(Exception):
        brief.fetch_recalls(CFG, NOW_UTC, session=FakeSession(status=500))


def test_fetch_extras_fails_soft(monkeypatch, tmp_path):
    def boom(cfg, now=None, session=None):
        raise brief.requests.ConnectionError("offline")
    monkeypatch.setattr(brief, "fetch_recalls", boom)
    recalls, quote, errors = brief.fetch_extras(CFG, NOW_UTC)           # the repo's own quotes.yaml
    assert recalls == [] and quote["text"] and quote["who"] and quote["title"] == "Words to Live By"
    assert [e["source"] for e in errors] == ["Recalls"] and "ConnectionError" in errors[0]["error"]
    monkeypatch.setattr(brief, "ROOT", tmp_path)                        # no quotes.yaml here
    recalls, quote, errors = brief.fetch_extras(CFG, NOW_UTC)
    assert quote is None and [e["source"] for e in errors] == ["Recalls", "Quotes"]
    assert brief.fetch_extras({"topics": []}, NOW_UTC) == ([], None, [])           # nothing configured: nothing to do


def test_fetch_extras_loads_the_quote_from_a_local_file(monkeypatch, tmp_path):
    (tmp_path / "q.yaml").write_text(yaml.safe_dump([
        {"text": "A quote that is long enough to be kept.", "who": "Someone", "about": "A person"}]))
    monkeypatch.setattr(brief, "ROOT", tmp_path)
    cfg = {"quotes": {"file": "q.yaml", "title": "Words"}}
    assert brief.fetch_extras(cfg, NOW_UTC) == (
        [], {"text": "A quote that is long enough to be kept.", "who": "Someone", "about": "A person", "title": "Words"}, [])


# ---------- the daily quote (Words to Live By) ----------

def make_quotes(n, people):
    """n synthetic quotes by `people` names with uneven counts (person 0 has the most)."""
    return [{"text": f"Synthetic quote number {i:04d} with enough words to pass.",
             "who": f"Person {int(people * (i / n) ** 2):02d}", "about": "About them", "source": ""}
            for i in range(n)]


def test_load_quotes_keeps_valid_and_drops_bad(tmp_path):
    long_ok = "x" * 240
    rows = [
        {"text": "  A good quote that is long enough.  ", "who": " Ann ", "about": " Writer ", "source": " Book ",
         "ref": "https://x.test", "group": "stoic", "extra": 1},
        {"text": "No who on this quote but it is long enough."},
        {"text": "Blank who on this quote but it is long enough.", "who": "  "},
        {"text": "Too short", "who": "Bob"},
        {"text": "y" * 241, "who": "Bob"},
        {"text": "a good   QUOTE that is\nlong enough.", "who": "Cy"},                  # duplicate of the first
        {"text": long_ok, "who": "Dee"},
        {"text": 42, "who": "Eve"},
        "not a mapping",
        {"text": "Another fine quote with no extras at all.", "who": "Fay"},
    ]
    path = tmp_path / "q.yaml"
    path.write_text(yaml.safe_dump(rows))
    got = brief.load_quotes(path)
    assert got == [
        {"text": "A good quote that is long enough.", "who": "Ann", "about": "Writer", "source": "Book"},
        {"text": long_ok, "who": "Dee", "about": "", "source": ""},
        {"text": "Another fine quote with no extras at all.", "who": "Fay", "about": "", "source": ""}]
    assert all(set(q) == {"text", "who", "about", "source"} for q in got)


def test_load_quotes_raises_on_bad_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        brief.load_quotes(tmp_path / "missing.yaml")
    (tmp_path / "d.yaml").write_text("text: not a list\nwho: x\n")
    with pytest.raises(ValueError):
        brief.load_quotes(tmp_path / "d.yaml")
    (tmp_path / "e.yaml").write_text("")
    with pytest.raises(ValueError):
        brief.load_quotes(tmp_path / "e.yaml")


def test_quote_order_is_deterministic_and_ignores_file_order():
    import random
    quotes = make_quotes(120, 20)
    order = brief.quote_order(quotes)
    assert sorted(order) == list(range(120)) and order == brief.quote_order(quotes)
    sequence = [quotes[i]["text"] for i in order]
    shuffled = quotes[:]
    random.Random(7).shuffle(shuffled)
    assert [shuffled[i]["text"] for i in brief.quote_order(shuffled)] == sequence
    assert brief.quote_order([]) == [] and brief.quote_order(quotes[:1]) == [0]


def test_quote_order_separates_same_person_neighbors_including_the_wrap():
    quotes = make_quotes(400, 70)
    counts = Counter(q["who"] for q in quotes)
    assert len(counts) > 50 and max(counts.values()) > 3 * min(counts.values())      # uneven on purpose
    order = brief.quote_order(quotes)
    assert sorted(order) == list(range(400))
    whos = [quotes[i]["who"] for i in order]
    assert all(whos[i] != whos[(i + 1) % 400] for i in range(400))


def test_quote_order_leaves_an_impossible_clash_alone():
    quotes = make_quotes(6, 1)                                  # one person owns everything
    assert sorted(brief.quote_order(quotes)) == list(range(6))


def test_pick_quote_is_the_same_all_day_and_walks_the_whole_list():
    quotes = make_quotes(380, 60)
    cfg = {"title": "Words to Live By"}
    eastern = lambda h, m, d=11: datetime(2026, 10, d, h, m, tzinfo=brief.TZ)  # noqa: E731
    first = brief.pick_quote(quotes, cfg, eastern(0, 30))
    assert first == brief.pick_quote(quotes, cfg, eastern(23, 30))
    assert first == brief.pick_quote(quotes, cfg, eastern(12, 0).astimezone(timezone.utc))
    assert first == brief.pick_quote(quotes, cfg, datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc))   # 22:00 Eastern on the 11th
    assert set(first) == {"text", "who", "about", "title"} and first["title"] == "Words to Live By"
    start = datetime(2026, 3, 1, 12, 0, tzinfo=brief.TZ)
    days = [brief.pick_quote(quotes, cfg, start + timedelta(days=d))["text"] for d in range(366)]
    assert len(set(days)) == 366
    assert brief.pick_quote(quotes, cfg, start + timedelta(days=380))["text"] == days[0]    # the cycle restarts


def test_pick_quote_title_default_and_empty_list():
    quotes = make_quotes(5, 5)
    assert brief.pick_quote(quotes, {}, NOW_UTC)["title"] == "Words to Live By"
    assert brief.pick_quote(quotes, {"title": "Daily Words"}, NOW_UTC)["title"] == "Daily Words"
    assert brief.pick_quote([], {}, NOW_UTC) is None


def test_real_quotes_file():
    quotes = brief.load_quotes(ROOT / CFG["quotes"]["file"])
    assert len(quotes) >= 366
    assert all(q["who"] and q["about"] for q in quotes)
    assert max(Counter(q["who"] for q in quotes).values()) <= 15
    assert len({brief.norm_text(q["text"]) for q in quotes}) == len(quotes)


# ---------- main(): two calls, fail-soft desk, summed usage ----------

def run_main(monkeypatch, tmp_path, capsys, ask_desk, edit_choice=None, force_week=False, videos=None,
             ask_videos=None, spy=None, yesterday=None):
    """Runs brief.main() with every network call replaced. `videos` are the fetched YouTube items,
    `ask_videos` replaces call 3, `spy` (a dict) gets the items that calls 1 and 2 were given, and
    `yesterday` is the live brief.json of the day before (fetched once)."""
    items = json.loads((ROOT / "fixtures/sample_items.json").read_text())["items"]
    choice = json.loads((ROOT / "fixtures/sample_response.json").read_text())
    if edit_choice:
        edit_choice(choice)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("BRIEF_PASSPHRASE", raising=False)
    monkeypatch.setenv("BRIEF_DATA_DIR", str(tmp_path / "data"))      # a test that wants data creates it first
    if force_week:
        monkeypatch.setenv("BRIEF_FORCE_WEEK", "1")
    else:
        monkeypatch.delenv("BRIEF_FORCE_WEEK", raising=False)
    monkeypatch.setattr(sys, "argv", ["brief.py"])
    monkeypatch.setattr(brief, "SITE_DIR", tmp_path)
    monkeypatch.setattr(brief, "fetch_all", lambda cfg: (items, [{"source": "Bad feed", "error": "x"}]))
    monkeypatch.setattr(brief, "fetch_scores", lambda cfg: ([], []))
    monkeypatch.setattr(brief, "fetch_youtube", lambda cfg: (list(videos or []), []))
    monkeypatch.setattr(brief, "ask_videos", ask_videos or (lambda cfg, items, today: (_ for _ in ()).throw(
        AssertionError("call 3 was not expected"))))
    monkeypatch.setattr(brief, "fetch_extras", lambda cfg, now: (
        [{"title": "R", "url": "https://fda.test/r", "published": "2026-10-11T00:00:00+00:00"}], None,
        [{"source": "Quotes", "error": "FileNotFoundError: x"}]))
    monkeypatch.setattr(brief, "fetch_yesterday", lambda url: (
        spy.setdefault("yesterday_fetches", []).append(url) if spy is not None else None) or (yesterday or {}))
    monkeypatch.setattr(brief, "resolve_og_images", lambda out, no_image: 0)
    def front_call(cfg, its, today, recent=None):
        if spy is not None:
            spy["front"] = list(its)
            spy["recent"] = recent
        return choice, {"input_tokens": 100, "output_tokens": 10}

    def desk_call(cfg, its, today):
        if spy is not None:
            spy["desk"] = list(its)
        return ask_desk(cfg, its, today)

    monkeypatch.setattr(brief, "ask_claude", front_call)
    monkeypatch.setattr(brief, "ask_desk", desk_call)
    assert brief.main() == 0
    return json.loads((tmp_path / "brief.json").read_text()), capsys.readouterr().out


def test_main_two_calls_sum_usage_and_write_new_keys(monkeypatch, tmp_path, capsys):
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 300, "output_tokens": 70}))
    assert b["usage"] == {"input_tokens": 400, "output_tokens": 80}
    assert "desk: fallback" not in out
    assert [t["id"] for t in b["tabs"]] == ["news", "tech", "sports", "atlanta", "science"]
    assert b["recalls"][0]["title"] == "R" and b["quote"] is None
    assert [e["source"] for e in b["source_errors"]] == ["Bad feed", "Quotes"]
    assert "private tabs: skipped" in out


def test_main_desk_failure_falls_back_and_still_succeeds(monkeypatch, tmp_path, capsys):
    def boom(cfg, items, today):
        raise RuntimeError("model said something private-looking")
    b, out = run_main(monkeypatch, tmp_path, capsys, boom)
    assert "desk: fallback" in out and "private-looking" not in out
    assert b["usage"] == {"input_tokens": 100, "output_tokens": 10}                # only call 1 counted
    assert [t["id"] for t in b["tabs"]] == ["news", "tech", "sports", "atlanta", "science"]
    tab_items = [i for t in b["tabs"] for s in t["sections"] for i in s["items"]]
    assert tab_items and all(i["note"] == "" for i in tab_items)
    sports = next(t for t in b["tabs"] if t["id"] == "sports")["sections"][0]["items"]
    assert len(sports) <= brief.DESK_FALLBACK_ITEMS
    assert b["lead"] and b["sections"]                                             # the front page is untouched


def test_main_desk_with_no_usable_ids_also_falls_back(monkeypatch, tmp_path, capsys):
    b, out = run_main(monkeypatch, tmp_path, capsys,
                      lambda cfg, items, today: ({"sections": [{"topic": "World", "items": [{"id": "zzz", "note": "n"}]}]},
                                                 {"input_tokens": 5, "output_tokens": 5}))
    assert "desk: fallback" in out and b["usage"] == {"input_tokens": 105, "output_tokens": 15}
    assert b["tabs"]


# ---------- one paper per day ----------

NY = brief.TZ


def test_printed_today_same_day():
    now = datetime(2026, 10, 12, 14, 0, tzinfo=NY)
    assert brief.printed_today({"generated_at": "2026-10-12T02:17:05-04:00"}, now)


def test_printed_today_yesterday_is_false():
    now = datetime(2026, 10, 12, 2, 17, tzinfo=NY)
    assert not brief.printed_today({"generated_at": "2026-10-11T02:17:05-04:00"}, now)


def test_printed_today_uses_new_york_day_not_utc():
    # 11:30 PM ET on Oct 11 is already Oct 12 in UTC
    made = {"generated_at": "2026-10-11T23:30:00-04:00"}
    assert not brief.printed_today(made, datetime(2026, 10, 12, 6, 17, tzinfo=timezone.utc))   # 2:17 AM ET Oct 12
    assert brief.printed_today(made, datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))        # 9 PM ET Oct 11
    # a UTC stamp just after midnight UTC is still the evening before in New York
    assert brief.printed_today({"generated_at": "2026-10-12T01:00:00+00:00"}, datetime(2026, 10, 11, 20, 0, tzinfo=NY))


@pytest.mark.parametrize("bad", [None, {}, [], "x", {"generated_at": None}, {"generated_at": 5},
                                 {"generated_at": "not a date"}, {"generated_at": "2026-10-12T02:17:05"}])
def test_printed_today_bad_input_is_false(bad):
    assert not brief.printed_today(bad, datetime(2026, 10, 12, 12, 0, tzinfo=NY))


def run_check_today(monkeypatch, tmp_path, capsys, live):
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["brief.py", "--check-today"])
    monkeypatch.setattr(brief, "fetch_live_brief", lambda site_url: live)
    assert brief.main() == 0
    return capsys.readouterr().out, out_file.read_text()


def test_check_today_printed_sets_skip_true(monkeypatch, tmp_path, capsys):
    stamp = datetime.now(NY).isoformat(timespec="seconds")
    out, gh = run_check_today(monkeypatch, tmp_path, capsys, {"generated_at": stamp})
    assert "printed_today=true" in out and gh == "skip=true\n"


def test_check_today_old_or_missing_sets_skip_false(monkeypatch, tmp_path, capsys):
    old = (datetime.now(NY) - timedelta(days=2)).isoformat(timespec="seconds")
    for live in ({"generated_at": old}, None):
        out, gh = run_check_today(monkeypatch, tmp_path, capsys, live)
        assert "printed_today=false" in out and gh.endswith("skip=false\n")


def test_check_today_without_github_output_still_prints(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["brief.py", "--check-today"])
    monkeypatch.setattr(brief, "fetch_live_brief", lambda site_url: None)
    assert brief.main() == 0
    assert "printed_today=false" in capsys.readouterr().out


def test_fetch_live_brief_fails_soft(monkeypatch):
    def boom(*a, **k):
        raise brief.requests.ConnectionError("down")
    monkeypatch.setattr(brief.requests, "get", boom)
    assert brief.fetch_live_brief("https://x.test/") is None


def test_workflow_guard_wiring():
    wf = yaml.safe_load((ROOT / ".github/workflows/daily-brief.yml").read_text())
    assert set(wf[True]) == {"schedule", "workflow_dispatch"}                 # YAML reads "on" as True
    build, deploy = wf["jobs"]["build"], wf["jobs"]["deploy"]
    steps = {s.get("id") or s.get("name"): s for s in build["steps"]}
    assert steps["check"]["if"] == "github.event_name == 'schedule'"
    assert build["outputs"]["skip"] == "${{ steps.check.outputs.skip }}"
    assert steps["Generate brief"]["if"] == "steps.check.outputs.skip != 'true'"
    # the build uploads the finished site as a normal artifact; the assemble job publishes it to Pages
    assert not any(s.get("uses", "").startswith("actions/upload-pages-artifact") for s in build["steps"])
    assert any(s.get("uses", "").startswith("actions/upload-artifact") and s["with"]["name"] == "site" and
               s["if"] == "steps.check.outputs.skip != 'true'" for s in build["steps"])
    assert any(s.get("uses", "").startswith("actions/upload-pages-artifact") for s in wf["jobs"]["assemble"]["steps"])
    assert deploy["needs"] == "assemble" and deploy["if"] == "needs.assemble.result == 'success'"


def test_page_has_print_button_wiring():
    page = (ROOT / "site/index.html").read_text()
    assert 'var SHORTCUT_NAME = "Print Daily Brief";' in page
    assert "shortcuts://x-callback-url/run-shortcut?name=" in page
    assert "Print a New Edition" in page and "printed=" in page


# ---------- models per call ----------

class FakeAnthropic:
    """Stands in for the anthropic module; records what each call asked for."""
    calls: list = []
    inits: list = []

    def __init__(self, api_key=None, **kwargs):
        FakeAnthropic.inits.append(kwargs)
        self.messages = self

    def create(self, **kw):
        from types import SimpleNamespace as NS
        FakeAnthropic.calls.append(kw)
        block = NS(type="tool_use", input={"sections": []})
        return NS(content=[block], stop_reason="tool_use", usage=NS(input_tokens=3, output_tokens=2))


@pytest.fixture
def fake_anthropic(monkeypatch):
    from types import SimpleNamespace
    FakeAnthropic.calls, FakeAnthropic.inits = [], []
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("BRIEF_DATA_DIR", "/nonexistent-data-dir")
    return FakeAnthropic.calls


def test_model_per_call(fake_anthropic):
    cfg = {**CFG, "model": "m-main", "front_model": "m-front", "week_model": "m-week"}
    items = items_fixture()
    brief.ask_claude(cfg, items, "Monday, October 12, 2026")
    brief.ask_desk(cfg, items, "Monday, October 12, 2026")
    brief.ask_week(cfg, "x")
    assert [c["model"] for c in fake_anthropic] == ["m-front", "m-main", "m-week"]
    assert [c["max_tokens"] for c in fake_anthropic] == [6000, 8000, 4000]
    assert [c["tool_choice"]["name"] for c in fake_anthropic] == ["publish_brief", "publish_desk", "publish_week"]


def test_models_fall_back_to_the_main_model(fake_anthropic):
    cfg = {k: v for k, v in CFG.items() if k not in ("front_model", "week_model")}
    brief.ask_claude(cfg, items_fixture(), "Monday, October 12, 2026")
    brief.ask_week(cfg, "x")
    assert [c["model"] for c in fake_anthropic] == [cfg["model"], cfg["model"]]
    brief._forced_call(cfg, "s", brief.week_tool(), "u", 10)                 # no model argument: the main model
    brief._forced_call(cfg, "s", brief.week_tool(), "u", 10, model="other")
    assert [c["model"] for c in fake_anthropic][2:] == [cfg["model"], "other"]


def test_front_call_prompt_has_listen_rule_and_never_urls(fake_anthropic):
    brief.ask_claude(CFG, items_fixture(), "Monday, October 12, 2026")
    system = fake_anthropic[0]["system"]
    assert "300 to 450 words" in system and "Good morning. This is The Daily Brief for" in system
    assert "That is the brief. Have a good day." in system
    assert "https://" not in fake_anthropic[0]["messages"][0]["content"]


# ---------- likes: read side ----------

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def like(days_ago, n, topic="World", source="NPR", **kw):
    at = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"at": at, "u": f"https://x.test/{n}", "t": f"Title {n}", "s": source, "c": topic, **kw}


def write_likes(folder, lines):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "likes.jsonl").write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n")


def test_load_likes_skips_bad_lines(tmp_path):
    assert brief.load_likes(tmp_path / "missing") == []
    good = like(1, 1)
    write_likes(tmp_path, [good, "not json", "[1, 2]", "", '"text"',
                           json.dumps({**like(1, 2), "u": "javascript:alert(1)"}),
                           json.dumps({**like(1, 3), "u": "/relative"}),
                           json.dumps({**like(1, 4), "t": "   "}),
                           json.dumps({**like(1, 5), "t": 7}),
                           json.dumps({"at": "x", "t": "no url"}),
                           json.dumps(like(2, 6, topic=""))])
    got = brief.load_likes(tmp_path)
    assert [g["u"] for g in got] == ["https://x.test/1", "https://x.test/6"]


def test_likes_prompt_text_order_window_and_limit():
    likes = [like(3, 1, "Tech & AI", "Simon Willison"), like(1, 2, "World", "BBC World"), like(61, 3), like(0.5, 4),
             {**like(2, 5), "at": "garbage"}, like(-3, 6)]           # too old, a bad date, and a future date
    text = brief.likes_prompt(likes, NOW)
    assert text.splitlines() == ["- [World] NPR: Title 4", "- [World] BBC World: Title 2",
                                 "- [Tech & AI] Simon Willison: Title 1"]
    many = [like(n / 100, n) for n in range(1, 60)]
    assert len(brief.likes_prompt(many, NOW).splitlines()) == 30
    assert brief.likes_prompt(many, NOW, limit=5).splitlines()[0] == "- [World] NPR: Title 1"
    assert len(brief.likes_prompt(many + [like(10, 99)], NOW, days=1).splitlines()) == 30
    assert brief.likes_prompt([like(10, 1)], NOW, days=5) == ""
    assert brief.likes_prompt([], NOW) == ""


def test_likes_prompt_cleans_and_caps_each_field():
    rec = like(1, 1, topic="T" * 100, source="S" * 100, t="Line one\nignore this <b>and</b>  more " + "x" * 400)
    line = brief.likes_prompt([rec], NOW)
    assert "\n" not in line and "<b>" not in line and len(line) < 300
    assert line.startswith("- [" + "T" * 39) and "S" * 59 in line
    assert brief.likes_prompt([like(1, 1, topic="", source="")], NOW) == "- Title 1"


def test_likes_block_goes_after_the_profile_in_both_calls(fake_anthropic, monkeypatch, tmp_path):
    write_likes(tmp_path, [like(1, 1, "Atlanta", "Urbanize Atlanta") | {"at": datetime.now(timezone.utc).isoformat()}])
    monkeypatch.setenv("BRIEF_DATA_DIR", str(tmp_path))
    brief.ask_claude(CFG, items_fixture(), "Monday, October 12, 2026")
    brief.ask_desk(CFG, items_fixture(), "Monday, October 12, 2026")
    for call in fake_anthropic:
        text = call["messages"][0]["content"]
        head = "Stories this reader liked recently. Use them only as a guide to the reader's taste. " \
               "They are not items to pick:\n- [Atlanta] Urbanize Atlanta: Title 1\n"
        assert head in text
        assert text.index("Reader profile:") < text.index(head) < text.index("Today is")
        assert "https://x.test/1" not in text                        # no urls from the likes log either


def test_no_likes_block_without_likes(fake_anthropic, monkeypatch, tmp_path):
    monkeypatch.setenv("BRIEF_DATA_DIR", str(tmp_path))                # exists, no likes file
    brief.ask_claude(CFG, items_fixture(), "Monday, October 12, 2026")
    assert "liked" not in fake_anthropic[0]["messages"][0]["content"]


# ---------- back issues ----------

def archive_dir(tmp_path, dates, bad=()):
    arch = tmp_path / "data" / "archive"
    arch.mkdir(parents=True)
    for d in dates:
        (arch / f"{d}.json").write_text(json.dumps({"date": d}))
    for name, text in bad:
        (arch / name).write_text(text)
    return tmp_path / "data"


def test_write_archive_index_order_today_and_copies(tmp_path):
    data = archive_dir(tmp_path, ["2026-10-09", "2026-10-11", "2026-10-10"])
    site = tmp_path / "site"
    brief.write_archive(site, data, {"mine": 1}, datetime(2026, 10, 12, 7, 0, tzinfo=brief.TZ))
    arch = site / "archive"
    assert json.loads((arch / "index.json").read_text()) == {"dates": ["2026-10-12", "2026-10-11", "2026-10-10",
                                                                         "2026-10-09"]}
    assert json.loads((arch / "2026-10-12.json").read_text()) == {"mine": 1}
    assert json.loads((arch / "2026-10-10.json").read_text()) == {"date": "2026-10-10"}


def test_write_archive_uses_the_new_york_day(tmp_path):
    site = tmp_path / "site"
    brief.write_archive(site, tmp_path / "nodata", {"a": 1}, datetime(2026, 10, 12, 2, 30, tzinfo=timezone.utc))
    assert json.loads((site / "archive/index.json").read_text()) == {"dates": ["2026-10-11"]}   # still the 11th in NY


def test_write_archive_keep_limit_and_bad_names_skipped(tmp_path):
    dates = [f"2026-08-{d:02d}" for d in range(1, 31)] + [f"2026-09-{d:02d}" for d in range(1, 31)]   # 60 files
    data = archive_dir(tmp_path, dates, bad=[("notes.json", "{}"), ("2026-10-1.json", "{}"), ("index.json", "{}"),
                                             ("2026-10-05.json.bak", "{}"), ("2026-10-06.JSON", "{}"),
                                             ("2026-10-07.json", "not json"), ("2026-10-08.json", "[1]"),
                                             ("2026-10-12.json", '{"stale": "same day"}')])
    site = tmp_path / "site"
    brief.write_archive(site, data, {"mine": 1}, datetime(2026, 10, 12, 7, 0, tzinfo=brief.TZ), keep=10)
    got = json.loads((site / "archive/index.json").read_text())["dates"]
    assert got == ["2026-10-12"] + [f"2026-09-{d:02d}" for d in range(30, 20, -1)]
    assert json.loads((site / "archive/2026-10-12.json").read_text()) == {"mine": 1}      # not the stale copy
    assert sorted(p.name for p in (site / "archive").iterdir()) == sorted([f"{d}.json" for d in got] + ["index.json"])
    # a later run with fewer files removes leftovers
    brief.write_archive(site, data, {"mine": 2}, datetime(2026, 10, 12, 7, 0, tzinfo=brief.TZ), keep=2)
    assert len(list((site / "archive").iterdir())) == 4


def test_write_archive_never_fails_the_run(tmp_path, capsys):
    blocker = tmp_path / "site"
    blocker.write_text("a file, not a folder")
    brief.write_archive(blocker, tmp_path, {"a": 1}, NOW)
    assert "archive: skipped" in capsys.readouterr().out


# ---------- the spoken script ----------

def test_fallback_listen():
    lead = {"headline": "Haiku Arrives On The API", "title": "t", "note": "A short note about it"}
    front = [{"title": "Tower proposed on the Beltline"}, {"title": "Aid convoys delayed."}, {"title": ""}]
    text = brief.fallback_listen(lead, front, "Monday, October 12, 2026")
    assert text.startswith("Good morning. This is The Daily Brief for Monday, October 12.")
    assert "The top story. Haiku Arrives On The API. A short note about it." in text
    assert "Also on the front page. Tower proposed on the Beltline. Aid convoys delayed." in text
    assert text.endswith("That is the brief. Have a good day.") and "  " not in text
    bare = brief.fallback_listen(None, [], "Monday, October 12, 2026")
    assert bare == "Good morning. This is The Daily Brief for Monday, October 12. That is the brief. Have a good day."


def test_clean_listen():
    assert brief.clean_listen("  Good   morning.\n\nHello <b>there</b>.  ") == "Good morning. Hello there ."
    assert brief.clean_listen(None) == "" and brief.clean_listen(5) == "" and brief.clean_listen("  \n ") == ""
    long = ("This is a sentence. " * 400)
    got = brief.clean_listen(long)
    assert len(got) <= brief.LISTEN_CHARS and got.endswith("sentence.")


def test_main_writes_listen_models_and_archive(monkeypatch, tmp_path, capsys):
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (
        json.loads((ROOT / "fixtures/sample_desk_response.json").read_text()), {"input_tokens": 1, "output_tokens": 1}),
        edit_choice=lambda c: c.update(listen="  Good morning.\n\nOne two.  "))
    assert b["listen"] == "Good morning. One two."
    assert b["model"] == "claude-sonnet-5-5" and b["models"]["front"] == "claude-sonnet-5-5"
    assert b["models"]["desk"] == "claude-haiku-5-5"
    index = json.loads((tmp_path / "archive/index.json").read_text())
    assert index["dates"][0] == b["generated_at"][:10]
    assert json.loads((tmp_path / "archive" / f"{index['dates'][0]}.json").read_text())["listen"] == b["listen"]


def test_main_builds_a_fallback_listen_when_the_model_gives_none(monkeypatch, tmp_path, capsys):
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: ({"sections": []}, {
        "input_tokens": 1, "output_tokens": 1}), edit_choice=lambda c: c.pop("listen"))
    assert "listen: fallback" in out
    assert b["listen"].startswith("Good morning. This is The Daily Brief for ")
    assert b["lead"]["headline"] in b["listen"] and all(f["title"] in b["listen"] for f in b["front"])
    assert b["listen"].endswith("That is the brief. Have a good day.")


# ---------- Sunday edition: the week in review ----------

def paper(date, n, extra=0):
    """A small archived paper. Story urls are unique per paper."""
    def st(k, topic="World"):
        return {"title": f"Story {date} {k}", "url": f"https://{date}.test/{k}", "source": f"Src{k}", "topic": topic,
                "note": f"Note {k}", "image": f"http://img.test/{date}/{k}.jpg", "video": None}
    return {"generated_at": f"{date}T07:00:00-04:00", "lead": {**st(0, "Tech & AI"), "headline": "H"},
            "front": [st(1), st(2, "Sports")],
            "sections": [{"topic": "World", "items": [st(k) for k in range(3, 3 + n)]}] +
                        [{"topic": "Atlanta", "items": [st(100 + k, "Atlanta") for k in range(extra)]}]}


SUNDAY = datetime(2026, 10, 11, 7, 0, tzinfo=brief.TZ)          # a Sunday in New York


def week_data(tmp_path, dates):
    data = tmp_path / "data"
    arch = data / "archive"
    arch.mkdir(parents=True)
    for d in dates:
        (arch / f"{d}.json").write_text(json.dumps(paper(d, 2)))
    return data


def test_week_runs_on_sunday_or_when_forced(monkeypatch):
    monkeypatch.delenv("BRIEF_FORCE_WEEK", raising=False)
    assert brief.week_wanted(SUNDAY) and not brief.week_wanted(SUNDAY + timedelta(days=1))
    assert brief.week_wanted(datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc))          # still Sunday night in New York
    monkeypatch.setenv("BRIEF_FORCE_WEEK", "1")
    assert brief.week_wanted(SUNDAY + timedelta(days=1))
    monkeypatch.setenv("BRIEF_FORCE_WEEK", "0")
    assert not brief.week_wanted(SUNDAY + timedelta(days=1))


def test_week_skipped_with_fewer_than_three_papers(monkeypatch, tmp_path, capsys):
    data = week_data(tmp_path, ["2026-10-09"])                    # one archive paper plus today = 2
    monkeypatch.setenv("BRIEF_DATA_DIR", str(data))
    monkeypatch.setattr(brief, "ask_week", lambda cfg, user: pytest.fail("the model must not be called"))
    week, usage = brief.make_week(CFG, paper("2026-10-11", 2), SUNDAY)
    assert week is None and usage == {"input_tokens": 0, "output_tokens": 0}
    assert "week: skipped, only 2 papers" in capsys.readouterr().out


def test_week_not_made_on_a_weekday(monkeypatch, tmp_path):
    monkeypatch.delenv("BRIEF_FORCE_WEEK", raising=False)
    monkeypatch.setenv("BRIEF_DATA_DIR", str(week_data(tmp_path, ["2026-10-08", "2026-10-09", "2026-10-10"])))
    monkeypatch.setattr(brief, "ask_week", lambda cfg, user: pytest.fail("the model must not be called"))
    assert brief.make_week(CFG, paper("2026-10-12", 2), SUNDAY + timedelta(days=1))[0] is None


def test_load_archive_papers_uses_the_six_days_before_today(tmp_path):
    data = week_data(tmp_path, ["2026-10-04", "2026-10-05", "2026-10-06", "2026-10-10", "2026-10-11"])
    (data / "archive/2026-10-08.json").write_text("not json")
    got = brief.load_archive_papers(data, datetime(2026, 10, 11).date())
    assert [d for d, _ in got] == ["2026-10-05", "2026-10-06", "2026-10-10"]     # 04 too old, 08 bad, 11 is today


def test_week_stories_ids_dedupe_cap_and_no_urls_for_the_model():
    papers = [("2026-10-09", paper("2026-10-09", 2)), ("2026-10-10", paper("2026-10-10", 2)),
              ("2026-10-11", {**paper("2026-10-11", 2), "front": [paper("2026-10-10", 2)["lead"]]})]   # repeats a url
    stories = brief.week_stories(papers)
    assert [s["id"] for s in stories] == [f"w{n}" for n in range(1, len(stories) + 1)]
    assert len({s["url"] for s in stories}) == len(stories) and stories[0]["date"] == "2026-10-09"
    assert stories[0]["image"] == "https://img.test/2026-10-09/0.jpg"             # http is upgraded
    capped = brief.week_stories([("2026-10-0%d" % d, paper("2026-10-0%d" % d, 5, extra=6)) for d in range(1, 8)], cap=20)
    assert len(capped) == 20
    assert sum(1 for s in capped if s["note"] == "Note 0") == 7                    # every lead survives the cap
    assert [s["date"] for s in capped] == sorted(s["date"] for s in capped)
    msg = brief.week_user_message(CFG, stories, [], "2026-10-09", "2026-10-11", 3)
    assert "https://" not in msg and "http://" not in msg and "w1" in msg and "(none this week)" in msg
    assert "Houston Rockets" in msg and "SaportaReport" in msg                      # configured sources by topic


def test_week_built_from_archive_ids_map_back(monkeypatch, tmp_path, capsys):
    data = week_data(tmp_path, ["2026-10-07", "2026-10-09", "2026-10-10"])
    write_likes(data, [{"at": "2026-10-10T12:00:00Z", "u": "https://l.test/a", "t": "Liked A", "s": "BBC", "c": "World"},
                       {"at": "2026-10-09T12:00:00Z", "u": "https://l.test/b", "t": "Liked B", "s": "BBC", "c": "World"},
                       {"at": "2026-09-01T12:00:00Z", "u": "https://l.test/old", "t": "Too old", "s": "X", "c": "Y"}])
    monkeypatch.setenv("BRIEF_DATA_DIR", str(data))
    seen = {}

    def fake_ask(cfg, user):
        seen["user"] = user
        return ({"headline": "A Quiet Week", "summary": "First paragraph.\n\nSecond <i>paragraph</i>.",
                 "top": [{"id": "w2", "note": "Front story note."}, {"id": "w1", "note": "Lead."}, {"id": "w2", "note": "dup"},
                         {"id": "w999", "note": "unknown"}],
                 "suggestions": [{"change": "Add a source.", "reason": "Because."}, {"change": "", "reason": "no change"},
                                 "junk"]}, {"input_tokens": 50, "output_tokens": 20})

    monkeypatch.setattr(brief, "ask_week", fake_ask)
    week, usage = brief.make_week(CFG, paper("2026-10-11", 2), SUNDAY)
    assert usage == {"input_tokens": 50, "output_tokens": 20}
    assert (week["from"], week["to"]) == ("2026-10-07", "2026-10-11")
    assert (week["headline"], week["summary"]) == ("A Quiet Week", "First paragraph.\n\nSecond paragraph .")
    # w1 is the lead of the oldest paper, w2 its first front story
    assert [t["title"] for t in week["top"]] == ["Story 2026-10-07 1", "Story 2026-10-07 0"]
    assert week["top"][0] == {"title": "Story 2026-10-07 1", "url": "https://2026-10-07.test/1", "source": "Src1",
                              "topic": "World", "note": "Front story note.",
                              "image": "https://img.test/2026-10-07/1.jpg", "video": None}
    assert week["suggestions"] == [{"change": "Add a source.", "reason": "Because."}]
    assert [(l["title"], l["url"]) for l in week["liked"]] == [("Liked A", "https://l.test/a"), ("Liked B", "https://l.test/b")]
    assert set(week["liked"][0]) == {"title", "url", "source", "topic"}
    assert "Liked A" in seen["user"] and "Too old" not in seen["user"] and '"World":2' in seen["user"]
    assert '"BBC":2' in seen["user"] and "Story 2026-10-11 0" in seen["user"]          # today's paper is included
    assert "week: 4 papers" in capsys.readouterr().out


def test_week_never_stops_the_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BRIEF_DATA_DIR", str(week_data(tmp_path, ["2026-10-08", "2026-10-09", "2026-10-10"])))

    def boom(cfg, user):
        raise RuntimeError("private-looking text")

    monkeypatch.setattr(brief, "ask_week", boom)
    assert brief.make_week(CFG, paper("2026-10-11", 2), SUNDAY)[0] is None
    out = capsys.readouterr().out
    assert "week: skipped (RuntimeError)" in out and "private-looking" not in out
    monkeypatch.setattr(brief, "ask_week", lambda cfg, user: ({"headline": "", "summary": " "}, {"input_tokens": 1, "output_tokens": 1}))
    week, usage = brief.make_week(CFG, paper("2026-10-11", 2), SUNDAY)
    assert week is None and usage == {"input_tokens": 1, "output_tokens": 1}


def test_week_tool_and_prompt():
    t = brief.week_tool()
    props = t["input_schema"]["properties"]
    assert t["name"] == "publish_week" and set(t["input_schema"]["required"]) == {"headline", "summary", "top", "suggestions"}
    assert props["top"]["minItems"] == 3 and props["top"]["maxItems"] == 5
    assert props["suggestions"]["minItems"] == 3 and props["suggestions"]["maxItems"] == 5
    assert set(props["suggestions"]["items"]["required"]) == {"change", "reason"}
    for phrase in ("Never invent", "approves by hand", "Nothing changes automatically", "few likes", "neutral"):
        assert phrase in brief.WEEK_PROMPT


def test_main_sunday_edition_adds_week_and_listen(monkeypatch, tmp_path, capsys):
    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return SUNDAY.astimezone(tz) if tz else SUNDAY

    week_data(tmp_path, ["2026-10-08", "2026-10-09", "2026-10-10"])
    monkeypatch.setattr(brief, "datetime", Sunday)
    monkeypatch.setattr(brief, "ask_week", lambda cfg, user: (
        {"headline": "A Quiet Week", "summary": "First.\n\nSecond.", "top": [{"id": "w1", "note": "n"}],
         "suggestions": []}, {"input_tokens": 7, "output_tokens": 3}))
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (
        json.loads((ROOT / "fixtures/sample_desk_response.json").read_text()), {"input_tokens": 300, "output_tokens": 70}))
    assert b["week"]["headline"] == "A Quiet Week" and b["models"]["week"] == "claude-opus-5-5"
    assert b["listen"].endswith(" This is the Sunday edition. Here is the week in review. A Quiet Week. First. Second.")
    assert b["usage"] == {"input_tokens": 407, "output_tokens": 83}                       # 100+300+7, 10+70+3
    assert json.loads((tmp_path / "archive/index.json").read_text())["dates"] == [
        "2026-10-11", "2026-10-10", "2026-10-09", "2026-10-08"]


def test_dry_run_listen_and_week_preview(tmp_path):
    env = {**os.environ, "BRIEF_SITE_DIR": str(tmp_path), "BRIEF_DATA_DIR": str(tmp_path / "nodata")}
    env.pop("BRIEF_PASSPHRASE", None)
    env.pop("BRIEF_FORCE_WEEK", None)
    run = lambda e: subprocess.run([sys.executable, str(ROOT / "brief.py"), "--dry-run"],  # noqa: E731
                                   capture_output=True, text=True, env=e)
    r = run(env)
    assert r.returncode == 0, r.stderr
    b = json.loads((tmp_path / "brief.json").read_text())
    assert b["listen"].startswith("Good morning. This is The Daily Brief for ") and "@DATE@" not in b["listen"]
    assert 250 <= len(b["listen"].split()) <= 450 and b["listen"].endswith("That is the brief. Have a good day.")
    assert b["model"] == "claude-sonnet-5-5" and "week" not in b["models"]
    assert (tmp_path / "archive/index.json").exists()

    r = run({**env, "BRIEF_FORCE_WEEK": "1"})
    assert r.returncode == 0, r.stderr
    b = json.loads((tmp_path / "brief.json").read_text())
    week = b["week"]
    assert week["headline"] and week["summary"].count("\n\n") == 2 and len(week["suggestions"]) == 3
    assert b["models"]["week"] == "claude-opus-5-5"
    stories = [b["lead"]] + b["front"] + [i for s in b["sections"] for i in s["items"]]
    assert 3 <= len(week["top"]) <= 5 and {t["url"] for t in week["top"]} <= {st["url"] for st in stories}    # ids resolve
    assert week["from"] == week["to"] == b["generated_at"][:10]


# ---------- workflows ----------

def test_daily_workflow_saves_back_issues():
    wf = yaml.safe_load((ROOT / ".github/workflows/daily-brief.yml").read_text())
    build, save = wf["jobs"]["build"], wf["jobs"]["save"]
    steps = build["steps"]
    main_checkout = steps[0]
    data_checkout = steps[1]
    assert main_checkout["uses"] == "actions/checkout@v4" and "with" not in main_checkout
    assert data_checkout["uses"] == "actions/checkout@v4" and data_checkout["continue-on-error"] is True
    assert data_checkout["with"] == {"ref": "data", "path": "data"}
    paper_step = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@v4"))
    assert paper_step["if"] == "steps.check.outputs.skip != 'true'"
    assert paper_step["with"] == {"name": "paper", "path": "site/brief.json", "retention-days": 3}
    assert save["needs"] == "build" and save["if"] == "needs.build.outputs.skip != 'true'"
    assert save["runs-on"] == "ubuntu-latest" and save["timeout-minutes"] == 5
    assert save["permissions"] == {"contents": "write"}
    assert save["concurrency"] == {"group": "data-branch", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}                                   # the build stays read-only
    assert "permissions" not in build
    uses = [s.get("uses") for s in save["steps"]]
    assert uses[:2] == ["actions/checkout@v4", "actions/download-artifact@v4"]
    assert save["steps"][0]["with"] == {"ref": "data"} and save["steps"][1]["with"] == {"name": "paper", "path": "incoming"}
    runs = "\n".join(s.get("run", "") for s in save["steps"])
    assert "America/New_York" in runs and "archive/{date}.json" in runs and 'git commit -m "Back issue $ISSUE_DATE"' in runs
    assert runs.count("git pull --rebase origin data") == 1 and "for try in 1 2 3" in runs
    assert "pip" not in runs and "brief.py" not in runs                             # no repo code, no packages
    assert "${{" not in runs                                                            # no interpolation inside scripts


def test_likes_workflow_never_puts_the_input_inside_a_run_block():
    text = (ROOT / ".github/workflows/likes.yml").read_text()
    wf = yaml.safe_load(text)
    assert wf["name"] == "Save likes" and list(wf[True]) == ["workflow_dispatch"]
    spec = wf[True]["workflow_dispatch"]["inputs"]["likes"]
    assert spec["required"] is True and spec["type"] == "string"
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["save"]
    assert job["permissions"] == {"contents": "write"} and job["timeout-minutes"] == 5
    assert "concurrency" not in job      # a group would cancel waiting runs and lose likes
    for step in job["steps"]:
        assert "inputs.likes" not in step.get("run", ""), "interpolating the input into a script is a script injection"
        assert "${{" not in step.get("run", "")
    add = next(s for s in job["steps"] if "python3 ../likes.py" in s.get("run", ""))
    assert add["env"] == {"LIKES_JSON": "${{ inputs.likes }}", "BRIEF_DATA_DIR": "."}
    assert add["working-directory"] == "data" and "git reset -q --hard origin/data" in add["run"]
    assert text.count("inputs.likes") == 1                                         # only the env line
    assert job["steps"][1]["with"] == {"ref": "data", "path": "data"}


def test_gitignore_has_archive_folder():
    assert "site/archive/" in (ROOT / ".gitignore").read_text().split()



def test_main_front_model_failure_retries_with_main_model(monkeypatch, tmp_path, capsys):
    """If the stronger front-page model fails, the paper still prints with the main model."""
    choice = json.loads((ROOT / "fixtures/sample_response.json").read_text())
    seen = []
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    orig_setattr = monkeypatch.setattr

    def flaky(cfg, items, today, recent=None):
        seen.append(brief.front_model(cfg))
        if brief.front_model(cfg) != cfg["model"]:
            raise RuntimeError("overloaded")
        return choice, {"input_tokens": 5, "output_tokens": 1}

    def patched(target, name, value=None, *a, **k):
        if target is brief and name == "ask_claude":
            value = flaky
        return orig_setattr(target, name, value, *a, **k)

    monkeypatch.setattr = patched
    b, _ = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}))
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    assert seen == [cfg["front_model"], cfg["model"]]
    assert b["lead"]


def test_prompts_never_take_the_team_from_the_source_name():
    for prompt in (brief.SYSTEM_PROMPT, brief.DESK_PROMPT):
        assert "Never take the team from the source name." in prompt


# ---------- the YouTube tab ----------

YT = CFG["youtube"]
YT_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
CHAN = "UCabcdefghijklmnopqrstuv"                     # a good id: UC plus 22 characters
GOOD_IDS = [ch["id"] for g in YT["groups"] for ch in g["channels"]]


def vid_cfg(**over):
    """A small youtube config: group A with channels one and two, group B with channel three."""
    block = {"max_age_days": 2, "per_channel": 3, "pick_min": 2, "pick_max": 4, "groups": [
        {"name": "A", "channels": [{"name": "One", "id": "UC" + "1" * 22}, {"name": "Two", "id": "UC" + "2" * 22}]},
        {"name": "B", "channels": [{"name": "Three", "id": "UC" + "3" * 22}]}]}
    return {**CFG, "youtube": {**block, **over}}


def atom(*rows):
    """A YouTube-style Atom feed. rows are (video id, title, hours old, link or None, description[, views]).
    Without views the entry has no media:statistics element."""
    out = ['<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
           'xmlns:media="http://search.yahoo.com/mrss/"><title>c</title>']
    for vid, title, hours, link, desc, *views in rows:
        stats = (f'<media:community><media:statistics views="{views[0]}"/></media:community>'
                 if views else "")
        when = (YT_NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        out.append(f'<entry><id>yt:video:{vid}</id><title>{title}</title>'
                   f'<link rel="alternate" href="{link or "https://www.youtube.com/watch?v=" + vid}"/>'
                   f'<published>{when}</published><updated>{when}</updated><media:group>'
                   f'<media:thumbnail url="https://i1.ytimg.com/vi/{vid}/hqdefault.jpg"/>'
                   f'<media:description>{desc}</media:description>{stats}</media:group></entry>')
    return "".join(out) + "</feed>"


class FeedSession:
    """Stands in for requests.Session: one feed (or error) per feed url."""
    feeds: dict = {}
    asked: list = []

    def __init__(self):
        self.headers = {}

    def get(self, url, timeout=None):
        FeedSession.asked.append(url)
        found = FeedSession.feeds.get(url)
        if isinstance(found, list):             # a different answer for each try; the last one repeats
            found = found.pop(0) if len(found) > 1 else found[0]
        if isinstance(found, Exception):
            raise found
        status = found if isinstance(found, int) else (404 if found is None else None)
        text = found if isinstance(found, str) else ""

        class R:
            content = text.encode()

            def raise_for_status(self):
                if status is not None:
                    from types import SimpleNamespace
                    raise brief.requests.HTTPError(f"{status} Error", response=SimpleNamespace(status_code=status))
        return R()


def feed_url(n):
    return brief.YOUTUBE_FEED + "UC" + str(n) * 22


@pytest.fixture
def yt_feeds(monkeypatch):
    FeedSession.feeds, FeedSession.asked = {}, []
    monkeypatch.setattr(brief.requests, "Session", FeedSession)
    FeedSession.slept = []
    monkeypatch.setattr(brief.time, "sleep", lambda secs: FeedSession.slept.append(secs))   # no real waiting
    return FeedSession.feeds


def test_youtube_config_loads_groups_and_checks_ids():
    yt = brief.youtube_settings(CFG)
    assert (yt["max_age_days"], yt["per_channel"], yt["pick_min"], yt["pick_max"]) == (2, 3, 8, 12)
    assert [g["name"] for g in yt["groups"]] == ["News & Politics", "Sports", "Architecture & Building",
                                                 "Tech & Science", "Make & Cook"]
    assert [len(g["channels"]) for g in yt["groups"]] == [3, 8, 7, 9, 7]
    assert yt["bad"] == [] and len(set(GOOD_IDS)) == len(GOOD_IDS) == 34
    for g in yt["groups"]:
        for ch in g["channels"]:
            assert re.fullmatch(r"UC[\w-]{22}", ch["id"]) and ch["url"] == brief.YOUTUBE_FEED + ch["id"]
    assert ch["name"] == "Home RenoVision DIY"
    ids = {c["name"]: c["id"] for g in yt["groups"] for c in g["channels"]}
    assert ids["Breaking Points"] == "UCDRIjKy6eZOvKtOELtTdeUA" and ids["Einzelgänger"] == "UCybBViio_TH_uiFFDJuz5tg"


def test_youtube_config_skips_bad_ids_and_junk():
    bad = {"max_age_days": "x", "per_channel": 0, "pick_min": 99, "pick_max": 5, "groups": [
        {"name": "G", "channels": [
            {"name": "ok", "id": CHAN},
            {"name": "short", "id": "UC" + "a" * 21}, {"name": "long", "id": "UC" + "a" * 23},
            {"name": "no prefix", "id": "XX" + "a" * 22}, {"name": "symbol", "id": "UC" + "a" * 21 + "!"},
            {"name": "accent", "id": "UC" + "a" * 21 + "é"}, {"name": "newline", "id": CHAN[:-1] + "\n"},
            {"name": "repeat", "id": CHAN}, {"name": "", "id": "UC" + "b" * 22}, "junk", None]},
        {"name": "Empty", "channels": [{"name": "bad", "id": "nope"}]},
        {"name": "G", "channels": [{"name": "same group name", "id": "UC" + "c" * 22}]}, "junk", None]}
    yt = brief.youtube_settings({"youtube": bad})
    assert [(g["name"], [c["name"] for c in g["channels"]]) for g in yt["groups"]] == [("G", ["ok"])]
    assert "short" in yt["bad"] and "accent" in yt["bad"] and "repeat" in yt["bad"] and "bad" in yt["bad"]
    assert (yt["max_age_days"], yt["per_channel"], yt["pick_max"], yt["pick_min"]) == (2, 3, 5, 5)   # bad numbers: defaults
    for junk in (None, {}, {"youtube": None}, {"youtube": []}, {"youtube": {"groups": "x"}}, {"youtube": {"groups": []}}):
        assert brief.youtube_settings(junk) is None


def test_youtube_sources_are_no_longer_topic_sources():
    every = [s for t in CFG["topics"] for s in t["sources"]]
    assert not [s for s in every if "youtube.com" in s["url"]]
    gone = {"The Majority Report", "Breaking Points", "More Perfect Union", "Braves highlights",
            "Rockets highlights", "49ers highlights", "BIM Pure YouTube"}
    assert not gone & {s["name"] for s in every}
    assert {"BIM Pure", "Breaking Points"} <= {c["name"] for g in YT["groups"] for c in g["channels"]}


def test_is_short():
    assert brief.is_short("Clip", "https://www.youtube.com/shorts/abc123DEF45")
    assert brief.is_short("Funny moment #Shorts", "https://www.youtube.com/watch?v=abc123DEF45")
    assert brief.is_short("a #SHORTS b", "https://www.youtube.com/watch?v=abc123DEF45")
    assert not brief.is_short("Short film about shorts", "https://www.youtube.com/watch?v=abc123DEF45")
    assert brief.is_short("Clip", "https://www.youtube.com/watch?v=abc123DEF45", "Quick one #Shorts")
    assert not brief.is_short("", "")


def test_fetch_youtube_drops_shorts_old_videos_and_non_watch_links(yt_feeds):
    yt_feeds[feed_url(1)] = atom(
        ("aaaaaaaaaa1", "A real video", 5, None, "About the thing. See https://sponsor.test/x now"),
        ("aaaaaaaaaa2", "Quick clip", 6, "https://www.youtube.com/shorts/aaaaaaaaaa2", "d"),
        ("aaaaaaaaaa3", "Another quick clip #shorts", 7, None, "d"),
        ("aaaaaaaaaa4", "Too old", 49, None, "d"),
        ("aaaaaaaaaa7", "Clip with the tag in the description", 4, None, "Fun one #Shorts"),
        ("aaaaaaaaaa5", "Playlist link", 8, "https://www.youtube.com/playlist?list=aaaaaaaaaa5", "d"),
        ("aaaaaaaaaa6", "Just inside the window", 47, None, "d"))
    items, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
    one = [i for i in items if i["source"] == "One"]
    assert [i["title"] for i in one] == ["A real video", "Just inside the window"] and errors[0]["source"] == "Two"
    first = one[0]
    assert first["topic"] == "A" and first["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaa1"
    assert first["image"] == "https://i1.ytimg.com/vi/aaaaaaaaaa1/hqdefault.jpg"
    assert "sponsor.test" not in first["summary"] and first["summary"].startswith("About the thing.")
    assert set(first) == {"id", "topic", "source", "title", "url", "published", "summary", "image"}
    assert brief.youtube_id(first["url"]) == "aaaaaaaaaa1"


def test_fetch_youtube_keeps_the_newest_per_channel_and_uses_a_fallback_thumbnail(yt_feeds):
    rows = [(f"vvvvvvvvvv{n}", f"Video {n}", n, None, "d") for n in (9, 3, 7, 1, 5)]     # not in date order
    yt_feeds[feed_url(1)] = atom(*rows).replace('<media:thumbnail url="https://i1.ytimg.com/vi/vvvvvvvvvv1/hqdefault.jpg"/>', "")
    items, _ = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert [i["title"] for i in items] == ["Video 1", "Video 3", "Video 5"]                # newest first, 3 kept
    assert items[0]["image"] == "https://i.ytimg.com/vi/vvvvvvvvvv1/hqdefault.jpg"          # no thumbnail in the feed
    items, _ = brief.fetch_youtube(vid_cfg(per_channel=1), YT_NOW)
    assert [i["title"] for i in items] == ["Video 1"]


def test_fetch_youtube_errors_name_the_channel_and_never_raise(yt_feeds):
    yt_feeds[feed_url(1)] = atom(("bbbbbbbbbb1", "Good", 2, None, "d"))
    yt_feeds[feed_url(2)] = brief.requests.ConnectionError("offline")
    yt_feeds[feed_url(3)] = "<feed></feed>"                                              # parses, but has no entries
    items, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert [i["title"] for i in items] == ["Good"]
    assert [e["source"] for e in errors] == ["Two", "Three"]
    assert "ConnectionError" in errors[0]["error"] and "no entries" in errors[1]["error"]
    # a bad channel id in the config is reported by name, and its feed is never asked for
    cfg = vid_cfg()
    cfg["youtube"]["groups"][0]["channels"].append({"name": "Broken", "id": "nope"})
    FeedSession.asked.clear()
    _, errors = brief.fetch_youtube(cfg, YT_NOW)
    assert errors[0] == {"source": "Broken", "error": "ValueError: bad channel id"}
    assert len(FeedSession.asked) == 3 and all(u.startswith(brief.YOUTUBE_FEED + "UC") for u in FeedSession.asked)
    assert brief.fetch_youtube({"topics": []}, YT_NOW) == ([], [])                        # nothing configured


def test_fetch_youtube_skips_premieres_and_scheduled_streams_with_zero_views(yt_feeds):
    yt_feeds[feed_url(1)] = atom(
        ("dddddddddd1", "Premiere tonight", 1, None, "d", 0),
        ("dddddddddd2", "Has views", 2, None, "d", 1234),
        ("dddddddddd3", "No stats in the feed", 3, None, "d"),
        ("dddddddddd4", "Zero as text", 4, None, "d", "0"))
    items, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert [i["title"] for i in items if i["source"] == "One"] == ["Has views", "No stats in the feed"]
    assert [e["source"] for e in errors] == ["Two", "Three"]            # the empty feeds, nothing about the premiere


def test_fetch_youtube_retries_a_failed_feed_once_for_404_429_and_5xx(yt_feeds):
    good = atom(("eeeeeeeeee1", "Back again", 2, None, "d"))
    for status in (404, 429, 500, 503):
        FeedSession.asked.clear(); FeedSession.slept.clear()
        yt_feeds.clear()
        yt_feeds[feed_url(1)] = [status, good]
        yt_feeds[feed_url(2)] = good.replace("eeeeeeeeee1", "eeeeeeeeee2")
        yt_feeds[feed_url(3)] = good.replace("eeeeeeeeee1", "eeeeeeeeee3")
        items, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
        assert errors == [] and len(items) == 3, status
        assert FeedSession.asked.count(feed_url(1)) == 2 and FeedSession.slept == [brief.YOUTUBE_RETRY_WAIT]
    assert brief.YOUTUBE_RETRY_WAIT == 2.0


def test_fetch_youtube_gives_up_after_one_retry_and_does_not_retry_other_errors(yt_feeds):
    good = atom(("ffffffffff1", "Fine", 2, None, "d"))
    yt_feeds[feed_url(1)] = 503                                          # still down on the second try
    yt_feeds[feed_url(2)] = 403                                          # not retried
    yt_feeds[feed_url(3)] = good
    items, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert [i["title"] for i in items] == ["Fine"]
    assert [e["source"] for e in errors] == ["One", "Two"] and "503" in errors[0]["error"] and "403" in errors[1]["error"]
    assert FeedSession.asked.count(feed_url(1)) == 2 and FeedSession.asked.count(feed_url(2)) == 1
    assert FeedSession.slept == [brief.YOUTUBE_RETRY_WAIT]               # only the 503 waited
    FeedSession.asked.clear(); FeedSession.slept.clear()
    yt_feeds[feed_url(1)] = [404, brief.requests.ConnectionError("offline")]   # the retry itself fails: still an error
    _, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert "ConnectionError" in errors[0]["error"] and FeedSession.asked.count(feed_url(1)) == 2


def test_fetch_youtube_folds_many_channel_failures_into_one_error(yt_feeds, capsys):
    cfg = vid_cfg()
    cfg["youtube"]["groups"][1]["channels"] += [{"name": f"Extra {n}", "id": "UC" + str(n) * 22} for n in (4, 5)]
    for n in (1, 2, 3, 4, 5):
        yt_feeds[feed_url(n)] = brief.requests.ConnectionError("offline")
    items, errors = brief.fetch_youtube(cfg, YT_NOW)
    assert items == [] and errors == [{"source": "YouTube",
                                       "error": "5 of 5 channels failed: One, Two, Three, Extra 4, Extra 5"}]
    assert "youtube: 5 of 5 channels failed" in capsys.readouterr().out
    # four failures: still folded, and the names are cut to about 200 characters
    long_cfg = {**vid_cfg(), "youtube": {**vid_cfg()["youtube"], "groups": [{"name": "A", "channels": [
        {"name": f"Channel {'x' * 70} {n}", "id": "UC" + str(n) * 22} for n in (6, 7, 8, 9)]}]}}
    for n in (6, 7, 8, 9):
        yt_feeds[feed_url(n)] = brief.requests.ConnectionError("offline")
    _, errors = brief.fetch_youtube(long_cfg, YT_NOW)
    assert len(errors) == 1 and errors[0]["source"] == "YouTube" and errors[0]["error"].startswith("4 of 4 channels failed: ")
    assert len(errors[0]["error"]) <= len("4 of 4 channels failed: ") + 200


def test_fetch_youtube_keeps_one_to_three_failures_per_channel(yt_feeds, capsys):
    good = atom(("gggggggggg1", "Fine", 2, None, "d"))
    yt_feeds[feed_url(1)] = brief.requests.ConnectionError("offline")
    yt_feeds[feed_url(2)] = brief.requests.ConnectionError("offline")
    yt_feeds[feed_url(3)] = good
    _, errors = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert [e["source"] for e in errors] == ["One", "Two"]
    assert "youtube: 2 of 3 channels failed" in capsys.readouterr().out
    cfg = vid_cfg()
    cfg["youtube"]["groups"][1]["channels"].append({"name": "Four", "id": "UC" + "4" * 22})
    yt_feeds[feed_url(3)] = brief.requests.ConnectionError("offline")
    yt_feeds[feed_url(4)] = good
    _, errors = brief.fetch_youtube(cfg, YT_NOW)
    assert [e["source"] for e in errors] == ["One", "Two", "Three"]        # exactly three: not folded


def test_fetch_youtube_same_video_on_two_channels_is_kept_once(yt_feeds):
    row = ("cccccccccc1", "Shared", 3, None, "d")
    yt_feeds[feed_url(1)] = atom(row)
    yt_feeds[feed_url(2)] = atom(row)
    items, _ = brief.fetch_youtube(vid_cfg(), YT_NOW)
    assert [i["source"] for i in items] == ["One"]




def video_items_fixture(groups=("A", "B"), per_group=3):
    """Fetched videos: `per_group` per group, ids v1, v2, ... (newer videos have lower numbers)."""
    out = []
    for g, name in enumerate(groups):
        for k in range(per_group):
            n = g * per_group + k + 1
            vid = f"vid{n:08d}"
            out.append({"id": f"v{n}", "topic": name, "source": f"Chan {n}", "title": f"Video {n}",
                        "url": f"https://www.youtube.com/watch?v={vid}", "summary": "s",
                        "published": (YT_NOW - timedelta(hours=n)).isoformat(),
                        "image": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"})
    return out


def picks(*groups):
    """A model answer: picks(("A", "v1", "v2"), ("B", "v4")) -> {"groups": [...]}, with a note per id."""
    return {"groups": [{"group": g, "items": [{"id": i, "note": f"Note {i}."} for i in ids]}
                       for g, *ids in groups]}


def test_build_videos_shape_order_and_unknown_ids():
    yt = brief.youtube_settings(vid_cfg())
    items = video_items_fixture()
    choice = picks(("B", "v6", "nope", "v4"), ("A", "v2", "v6", "v1"), ("A", "v1"))   # B first; repeats and unknown ids
    out = brief.build_videos(choice, items, yt)
    assert [g["group"] for g in out] == ["A", "B"]                                    # configured group order
    assert [i["title"] for i in out[0]["items"]] == ["Video 2", "Video 1"]             # model order kept in a group
    assert [i["title"] for i in out[1]["items"]] == ["Video 6", "Video 4"]             # v6 stays with its first pick
    story = out[0]["items"][0]
    assert story == {"title": "Video 2", "url": "https://www.youtube.com/watch?v=vid00000002", "source": "Chan 2",
                     "topic": "A", "note": "Note v2.", "image": "https://i.ytimg.com/vi/vid00000002/hqdefault.jpg",
                     "video": "vid00000002"}


def test_build_videos_puts_a_video_under_its_own_group_and_drops_empty_groups():
    yt = brief.youtube_settings(vid_cfg())
    out = brief.build_videos(picks(("B", "v1", "v2")), video_items_fixture(), yt)       # the model named the wrong group
    assert [(g["group"], len(g["items"])) for g in out] == [("A", 2)]
    for bad in (None, {}, [], {"groups": "x"}, {"groups": [None, "x", {"group": "A"}, {"group": "A", "items": "x"},
                                                           {"group": "A", "items": [None, {"id": 5}, {"note": "n"}]}]}):
        assert brief.build_videos(bad, video_items_fixture(), yt) == []


def test_build_videos_trims_to_pick_max_in_group_order():
    yt = brief.youtube_settings(vid_cfg(pick_max=4))
    items = video_items_fixture(per_group=4)                                           # A: v1-v4, B: v5-v8
    out = brief.build_videos(picks(("B", "v5", "v6"), ("A", "v3", "v1", "v2", "v4")), items, yt)
    assert [(g["group"], [i["title"] for i in g["items"]]) for g in out] == [
        ("A", ["Video 3", "Video 1", "Video 2", "Video 4"])]                           # group A fills all 4 places
    out = brief.build_videos(picks(("A", "v1", "v2", "v3"), ("B", "v5", "v6")), items, yt)
    assert [(g["group"], len(g["items"])) for g in out] == [("A", 3), ("B", 1)]
    assert sum(len(g["items"]) for g in out) == 4


def test_fallback_videos_newest_two_per_group_capped_without_notes():
    yt = brief.youtube_settings(vid_cfg(pick_max=3))
    items = video_items_fixture()
    items[0]["published"], items[2]["published"] = items[2]["published"], items[0]["published"]   # v3 is now newest of A
    out = brief.fallback_videos(items, yt)
    assert [(g["group"], [i["title"] for i in g["items"]]) for g in out] == [
        ("A", ["Video 3", "Video 2"]), ("B", ["Video 4"])]                              # capped at 3 in all
    assert all(i["note"] == "" and i["video"] for g in out for i in g["items"])
    assert brief.fallback_videos([], yt) == []


def test_ask_videos_call_shape(fake_anthropic):
    cfg = {**vid_cfg(), "model": "m-main"}
    brief.ask_videos(cfg, video_items_fixture(), "Monday, October 12, 2026")
    call = fake_anthropic[0]
    assert call["model"] == "m-main" and call["tool_choice"] == {"type": "tool", "name": "publish_videos"}
    assert call["max_tokens"] == brief.VIDEO_MAX_TOKENS
    tool = call["tools"][0]
    group = tool["input_schema"]["properties"]["groups"]["items"]["properties"]
    assert group["group"]["enum"] == ["A", "B"] and tool["input_schema"]["required"] == ["groups"]
    assert group["items"]["maxItems"] == 4
    assert "2 to 4 videos in total" in call["system"] and "{lo}" not in call["system"]
    for word in ("sponsor", "trailer", "3 hours", "near-duplicates", "25 words", "neutral", "id"):
        assert word in call["system"]
    user = call["messages"][0]["content"]
    assert user.startswith("Reader profile:") and "Monday, October 12, 2026" in user
    assert "Video 1" in user and "https://" not in user and "ytimg" not in user         # titles yes, links no


def test_ask_videos_includes_recent_likes(fake_anthropic, monkeypatch, tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "likes.jsonl").write_text(json.dumps(
        {"u": "https://x.test/1", "t": "A liked story", "s": "NPR", "c": "World",
         "at": datetime.now(timezone.utc).isoformat()}) + "\n")
    monkeypatch.setenv("BRIEF_DATA_DIR", str(folder))
    brief.ask_videos(vid_cfg(), video_items_fixture(), "Monday, October 12, 2026")
    assert "A liked story" in fake_anthropic[0]["messages"][0]["content"]


def test_make_videos_picks_trims_and_counts_usage(monkeypatch, capsys):
    cfg = vid_cfg(pick_max=3)
    items = video_items_fixture()
    monkeypatch.setattr(brief, "ask_videos", lambda c, its, today: (
        picks(("A", "v1", "v2"), ("B", "v4", "v5")), {"input_tokens": 50, "output_tokens": 8}))
    videos, usage, from_model = brief.make_videos(cfg, items, "today")
    assert from_model is True
    assert [(g["group"], len(g["items"])) for g in videos] == [("A", 2), ("B", 1)] and usage["input_tokens"] == 50
    assert capsys.readouterr().out == "videos: 3 in 2 groups, 6 fetched\n"              # counts only


def test_make_videos_falls_back_when_the_call_fails_or_gives_nothing(monkeypatch, capsys):
    cfg = vid_cfg()
    items = video_items_fixture()

    def boom(c, its, today):
        raise RuntimeError("a private-looking message")
    monkeypatch.setattr(brief, "ask_videos", boom)
    videos, usage, from_model = brief.make_videos(cfg, items, "today")
    out = capsys.readouterr().out
    assert from_model is False
    assert "videos: call failed (RuntimeError)" in out and "videos: fallback" in out and "private-looking" not in out
    assert [(g["group"], [i["title"] for i in g["items"]]) for g in videos] == [
        ("A", ["Video 1", "Video 2"]), ("B", ["Video 4", "Video 5"])]
    assert all(i["note"] == "" for g in videos for i in g["items"]) and usage == {"input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(brief, "ask_videos", lambda c, its, today: (
        picks(("A", "unknown-1", "unknown-2")), {"input_tokens": 9, "output_tokens": 1}))
    videos, usage, from_model = brief.make_videos(cfg, items, "today")
    assert "videos: fallback" in capsys.readouterr().out and len(videos) == 2 and usage["input_tokens"] == 9
    assert from_model is False
    assert brief.make_videos(cfg, [], "today")[0] == []                                  # nothing fetched: nothing to do
    assert brief.make_videos({"topics": []}, items, "today")[0] == []                      # nothing configured


def test_make_videos_never_raises(monkeypatch, capsys):
    monkeypatch.setattr(brief, "fallback_videos", lambda its, yt: 1 / 0)
    monkeypatch.setattr(brief, "ask_videos", lambda c, its, today: ({"groups": []}, {"input_tokens": 1, "output_tokens": 1}))
    videos, usage, from_model = brief.make_videos(vid_cfg(), video_items_fixture(), "today")
    assert videos == [] and from_model is False and "videos: skipped (ZeroDivisionError)" in capsys.readouterr().out


def test_main_keeps_videos_out_of_calls_one_and_two_and_writes_the_videos_key(monkeypatch, tmp_path, capsys):
    videos = video_items_fixture(("News & Politics", "Sports"))
    spy, calls = {}, []
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())

    def ask_videos(cfg, items, today):
        calls.append(items)
        return picks(("News & Politics", "v1"), ("Sports", "v5", "v4")), {"input_tokens": 40, "output_tokens": 9}
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 300, "output_tokens": 70}),
                      videos=videos, ask_videos=ask_videos, spy=spy)
    assert spy["front"] and spy["desk"]
    for key in ("front", "desk"):
        assert not [i for i in spy[key] if i["id"] in {v["id"] for v in videos} or "youtube.com" in i["url"]]
    assert [c["id"] for c in calls[0]] == [v["id"] for v in videos]                       # call 3 sees the videos
    assert b["usage"] == {"input_tokens": 440, "output_tokens": 89}                      # 100+300+40, 10+70+9
    assert [g["group"] for g in b["videos"]] == ["News & Politics", "Sports"]
    assert [i["title"] for i in b["videos"][1]["items"]] == ["Video 5", "Video 4"]
    assert all(set(i) == {"title", "url", "source", "topic", "note", "image", "video"}
               for g in b["videos"] for i in g["items"])
    assert b["models"]["videos"] == brief.yaml.safe_load((ROOT / "sources.yaml").read_text())["model"]
    assert "videos: 3 in 2 groups, 6 fetched" in out
    assert not any(i.get("video") for t in b["tabs"] for s in t["sections"] for i in s["items"])


def test_video_prompt_has_the_team_rule():
    assert ("Name a team only when the title or description names it." in brief.VIDEO_PROMPT
            and "Never take the team from the channel name." in brief.VIDEO_PROMPT)


def test_calls_one_and_two_prompts_no_longer_mention_videos():
    for prompt in (brief.SYSTEM_PROMPT, brief.DESK_PROMPT):
        assert "video" not in prompt.lower()


def test_forced_call_timeout_and_retries(fake_anthropic):
    cfg = {**vid_cfg(), "model": "m-main"}
    brief.ask_claude(cfg, items_fixture(), "Monday, October 12, 2026")
    brief.ask_videos(cfg, video_items_fixture(), "Monday, October 12, 2026")
    front, videos = FakeAnthropic.inits
    assert front["timeout"] == 180.0 and front["max_retries"] == 2                       # defaults unchanged
    assert videos["timeout"] == 60.0 and videos["max_retries"] == 1                      # the YouTube call is short
    brief._forced_call(cfg, "s", {"name": "t"}, "u", 10, timeout=5.0, max_retries=0)
    assert FakeAnthropic.inits[-1] == {"timeout": 5.0, "max_retries": 0}


def test_reader_profile_and_politics_sources():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    profile = cfg["reader_profile"]
    assert "Majority Report" not in profile and "Breaking Points" not in profile
    assert "The Lever" in profile and "Drop Site News" in profile
    assert ("On YouTube he also watches cooking, woodworking and home-building channels, science and "
            "engineering channels, and architecture and Revit/BIM tutorials.") in " ".join(profile.split())
    politics = next(t for t in cfg["topics"] if t["name"] == "Politics")
    npr = next(s for s in politics["sources"] if s["name"] == "NPR Politics")
    assert npr == {"name": "NPR Politics", "type": "rss", "url": "https://feeds.npr.org/1014/rss.xml"}


def test_fetch_yesterday_never_raises(monkeypatch):
    def boom(*a, **k):
        raise brief.requests.ConnectionError("offline")
    monkeypatch.setattr(brief.requests, "get", boom)
    assert brief.fetch_yesterday("https://x.test/") == {}

    class Bad:
        def raise_for_status(self): pass
        def json(self): return ["not", "a", "dict"]
    monkeypatch.setattr(brief.requests, "get", lambda *a, **k: Bad())
    assert brief.fetch_yesterday("https://x.test/") == {}

    class Ok(Bad):
        def json(self): return {"lead": None, "videos": []}
    seen = {}
    monkeypatch.setattr(brief.requests, "get", lambda url, **k: seen.setdefault("url", url) and Ok())
    assert brief.fetch_yesterday("https://x.test/site/") == {"lead": None, "videos": []}
    assert seen["url"] == "https://x.test/site/brief.json"


def test_drop_seen_yesterday_uses_the_live_brief_it_is_given(monkeypatch):
    monkeypatch.setattr(brief, "fetch_yesterday", lambda url: pytest.fail("fetched again"))
    items = [{"url": "https://a.test/1/"}, {"url": "https://a.test/2"}, {"url": "https://a.test/3"}]
    old = {"lead": {"url": "https://a.test/1"}, "front": [], "sections": [{"items": [{"url": "https://a.test/2/"}]}]}
    assert brief.drop_seen_yesterday(items, "https://x.test", old) == [{"url": "https://a.test/3"}]
    monkeypatch.setattr(brief, "fetch_yesterday", lambda url: old)                        # no brief given: fetch one
    assert brief.drop_seen_yesterday(items, "https://x.test") == [{"url": "https://a.test/3"}]
    assert brief.drop_seen_yesterday(items, "https://x.test", {}) == items                # an empty brief drops nothing


def test_drop_seen_videos_compares_by_youtube_id():
    items = video_items_fixture()
    old = {"videos": [{"group": "A", "items": [
        {"url": "https://www.youtube.com/watch?v=vid00000001"},                              # same id as v1
        {"url": "https://youtu.be/vid00000003?t=5"},                                         # another link form of v3
        {"url": "https://example.test/other/"}]}]}
    assert [v["id"] for v in brief.drop_seen_videos(items, old)] == ["v2", "v4", "v5", "v6"]
    other = [{"url": "https://example.test/other"}, {"url": "https://example.test/new"}]       # no id: compared by url
    assert brief.drop_seen_videos(other, old) == [{"url": "https://example.test/new"}]
    for junk in (None, {}, {"videos": None}, {"videos": [None, "x", {"items": "x"}]}, {"videos": [{"items": [{}]}]}):
        assert brief.drop_seen_videos(items, junk) == items                                  # never raises, drops nothing


def test_main_drops_videos_shown_yesterday_with_one_live_fetch(monkeypatch, tmp_path, capsys):
    videos = video_items_fixture(("News & Politics", "Sports"))
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    yesterday = {"videos": [{"group": "News & Politics", "items": [{"url": videos[0]["url"]}, {"url": videos[3]["url"]}]}]}
    spy, calls = {}, []

    def ask_videos(cfg, items, today):
        calls.append([i["id"] for i in items])
        return picks(("News & Politics", "v2")), {"input_tokens": 1, "output_tokens": 1}
    b, _ = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}),
                    videos=videos, ask_videos=ask_videos, spy=spy, yesterday=yesterday)
    assert calls == [["v2", "v3", "v5", "v6"]]                                               # v1 and v4 were shown yesterday
    assert len(spy["yesterday_fetches"]) == 1
    assert [i["title"] for g in b["videos"] for i in g["items"]] == ["Video 2"]


def test_main_still_runs_when_yesterdays_brief_cannot_be_fetched(monkeypatch, tmp_path, capsys):
    videos = video_items_fixture(("News & Politics", "Sports"))
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    calls = []

    def ask_videos(cfg, items, today):
        calls.append(len(items))
        return picks(("News & Politics", "v1")), {"input_tokens": 1, "output_tokens": 1}
    b, _ = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}),
                    videos=videos, ask_videos=ask_videos, yesterday={})
    assert calls == [6] and b["videos"]


def test_main_models_videos_only_when_the_model_made_the_picks(monkeypatch, tmp_path, capsys):
    videos = video_items_fixture(("News & Politics", "Sports"))
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    ok = lambda cfg, items, today: (picks(("News & Politics", "v1")), {"input_tokens": 1, "output_tokens": 1})   # noqa: E731
    b, _ = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}),
                    videos=videos, ask_videos=ok)
    assert "videos" in b["models"] and b["videos"]

    def boom(cfg, items, today):
        raise RuntimeError("overloaded")
    b, _ = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}),
                    videos=videos, ask_videos=boom)
    assert b["videos"] and "videos" not in b["models"]                                       # the fallback made them


def test_main_omits_the_videos_key_when_there_are_no_videos(monkeypatch, tmp_path, capsys):
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}))
    assert "videos" not in b and "videos" not in b["models"] and "videos:" not in out
    assert b["usage"] == {"input_tokens": 100, "output_tokens": 10}


def test_main_video_call_failure_falls_back_and_still_succeeds(monkeypatch, tmp_path, capsys):
    videos = video_items_fixture(("News & Politics", "Sports"))
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())

    def boom(cfg, items, today):
        raise RuntimeError("overloaded")
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 300, "output_tokens": 70}),
                      videos=videos, ask_videos=boom)
    assert "videos: fallback" in out and "overloaded" not in out
    assert [(g["group"], len(g["items"])) for g in b["videos"]] == [("News & Politics", 2), ("Sports", 2)]
    assert all(i["note"] == "" for g in b["videos"] for i in g["items"])
    assert b["usage"] == {"input_tokens": 400, "output_tokens": 80}                       # calls 1 and 2 only
    assert b["lead"] and b["tabs"]


def test_main_a_crash_in_the_video_fetch_never_stops_the_run(monkeypatch, tmp_path, capsys):
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    orig = monkeypatch.setattr

    def patched(target, name, value=None, *a, **k):
        if target is brief and name == "fetch_youtube":
            value = lambda cfg: 1 / 0   # noqa: E731
        return orig(target, name, value, *a, **k)
    monkeypatch.setattr = patched
    b, _ = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 0, "output_tokens": 0}))
    assert "videos" not in b and any(e["source"] == "YouTube" and "ZeroDivisionError" in e["error"]
                                     for e in b["source_errors"])


def test_fetch_only_writes_the_videos(monkeypatch, tmp_path, capsys):
    videos = video_items_fixture(("News & Politics", "Sports"))
    monkeypatch.setattr(sys, "argv", ["brief.py", "--fetch-only"])
    monkeypatch.setattr(brief, "ROOT", tmp_path)                                         # out/items.json goes to a scratch folder
    (tmp_path / "sources.yaml").write_text((ROOT / "sources.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(brief, "fetch_all", lambda cfg: ([], []))
    monkeypatch.setattr(brief, "fetch_youtube", lambda cfg: (videos, [{"source": "Bad channel", "error": "x"}]))
    monkeypatch.setattr(brief, "fetch_scores", lambda cfg: ([], []))
    monkeypatch.setattr(brief, "fetch_extras", lambda cfg, now: ([], None, []))
    assert brief.main() == 0
    saved = json.loads((tmp_path / "out/items.json").read_text())
    assert [v["id"] for v in saved["videos"]] == [v["id"] for v in videos] and saved["items"] == []
    assert saved["source_errors"] == [{"source": "Bad channel", "error": "x"}]
    assert "6 videos" in capsys.readouterr().out


def test_dry_run_produces_videos(tmp_path):
    env = {**os.environ, "BRIEF_SITE_DIR": str(tmp_path), "BRIEF_DATA_DIR": str(tmp_path / "nodata")}
    env.pop("BRIEF_PASSPHRASE", None)
    r = subprocess.run([sys.executable, str(ROOT / "brief.py"), "--dry-run"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    b = json.loads((tmp_path / "brief.json").read_text())
    names = [g["name"] for g in YT["groups"]]
    groups = [g["group"] for g in b["videos"]]
    assert groups == [n for n in names if n in groups] and len(groups) >= 3             # config order, at least 3 groups
    total = sum(len(g["items"]) for g in b["videos"])
    assert YT["pick_min"] <= total <= YT["pick_max"]
    ids = set()
    for g in b["videos"]:
        assert g["items"] and all(i["topic"] == g["group"] and i["note"] for i in g["items"])
        for i in g["items"]:
            assert re.fullmatch(r"[A-Za-z0-9_-]{11}", i["video"]) and i["url"].endswith(i["video"])
            assert i["image"].startswith("https://i.ytimg.com/vi/") and i["video"] not in ids
            ids.add(i["video"])
    assert "not-a-real-video-id" not in json.dumps(b)
    assert "videos: " in r.stdout and b["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_video_fixture_items_are_well_formed():
    items = json.loads((ROOT / "fixtures/sample_videos.json").read_text())["items"]
    assert {i["topic"] for i in items} <= {g["name"] for g in YT["groups"]} and len(items) >= 12
    assert len({i["topic"] for i in items}) >= 3 and len({i["id"] for i in items}) == len(items)
    for i in items:
        assert brief.youtube_id(i["url"]) and i["id"] == brief.item_id(i["url"]) and i["image"].startswith(
            "https://i.ytimg.com/vi/")
        assert not brief.is_short(i["title"], i["url"])


# ---------- the same story on two front pages ----------

POPE_BBC = "Pope Leo has lung surgery after cancer diagnosis"
POPE_NPR = "Pope Leo has lung cancer, the Vatican says"


def test_same_story_matches_other_outlets_and_rejects_other_news():
    assert brief.same_story(POPE_BBC, POPE_NPR)
    assert brief.same_story(POPE_NPR, "Pope diagnosed with lung cancer but had successful surgery")
    assert not brief.same_story("Braves beat Mets 5-3", "Braves lose to Mets in extra innings")
    assert not brief.same_story("Six Ukrainian energy sites hit by Russia since Trump announced ceasefire, Zelensky says",
                                "Saudi minister vows serious response to Houthi attacks on airports")
    assert not brief.same_story("", "") and not brief.same_story(POPE_BBC, "") and not brief.same_story(None, POPE_BBC)


def test_headline_words_drop_stop_words_short_words_and_plural_s():
    assert brief.headline_words("The Pope’s lungs say: it is 5 new cats, glass") == {"pope", "lung", "cats", "glass"}


def recent_data(tmp_path, dates):
    arch = tmp_path / "data" / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    for d in dates:
        (arch / f"{d}.json").write_text(json.dumps(paper(d, 2)), encoding="utf-8")
    return tmp_path / "data"


TODAY = datetime(2026, 10, 14).date()


def test_recent_front_headlines_reads_three_days_plus_today_and_old(tmp_path):
    data = recent_data(tmp_path, ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14"])
    old = {"lead": {"title": "Live lead", "headline": "Live head"}, "front": [{"title": "Live front"}, {"title": ""}]}
    got = brief.recent_front_headlines(data, TODAY, old)
    assert "Story 2026-10-10 0" not in got                                    # four days back is too old
    assert got[:3] == ["Story 2026-10-11 0", "H", "Story 2026-10-11 1"]       # oldest first; repeated "H" kept once
    assert "Story 2026-10-14 0" in got and "Story 2026-10-13 2" in got        # today's reprint and yesterday's front
    assert "Story 2026-10-13 3" not in got                                    # sections are not the front page
    assert got[-3:] == ["Live lead", "Live head", "Live front"]
    assert len(got) == len(set(got)) and "" not in got
    assert brief.recent_front_headlines(data, TODAY, {}) == got[:-3]
    assert brief.recent_front_headlines(data, TODAY, None) == got[:-3]


def test_recent_front_headlines_never_raises(tmp_path):
    data = recent_data(tmp_path, ["2026-10-13"])
    (data / "archive" / "2026-10-12.json").write_text("not json", encoding="utf-8")
    (data / "archive" / "2026-10-11.json").write_text(json.dumps({"lead": 5, "front": "x"}), encoding="utf-8")
    assert brief.recent_front_headlines(data, TODAY, "bad old")[0] == "Story 2026-10-13 0"
    assert brief.recent_front_headlines(tmp_path / "missing", TODAY, {}) == []
    assert brief.recent_front_headlines(data, "not a date", {}) == []


def test_mark_recent_repeats_flags_only_matches_and_copies():
    items = [{"id": "a", "title": POPE_NPR}, {"id": "b", "title": "Braves beat Mets 5-3"}]
    out = brief.mark_recent_repeats(items, ["Unrelated headline here", POPE_BBC])
    assert [i.get("seen_recently") for i in out] == [True, None]
    assert "seen_recently" not in items[0]                                    # the input is not changed
    assert brief.mark_recent_repeats(items, []) == items
    bad = [{"id": "c"}]
    assert brief.mark_recent_repeats(bad, [POPE_BBC]) is bad                  # no title: returned unchanged


def test_slim_items_adds_seen_recently_only_when_set():
    items = items_fixture()
    items[1]["seen_recently"] = True
    slim = brief.slim_items(items)
    assert slim[1]["seen_recently"] is True
    assert all("seen_recently" not in r for n, r in enumerate(slim) if n != 1)
    assert list(slim[0]) == ["id", "topic", "source", "title", "summary", "published", "has_image"]


def capture_user(monkeypatch):
    seen = {}

    def fake(cfg, system, tool, user, max_tokens, model=None, **kw):
        seen.update(system=system, tool=tool, user=user)
        return {}, {"input_tokens": 0, "output_tokens": 0}
    monkeypatch.setattr(brief, "_forced_call", fake)
    return seen


def test_ask_claude_lists_recent_front_stories_before_the_items(monkeypatch):
    monkeypatch.setenv("BRIEF_DATA_DIR", "/nonexistent-data-dir")
    seen = capture_user(monkeypatch)
    brief.ask_claude(CFG, items_fixture(), "Tuesday, October 14, 2026", [POPE_BBC, "<b>Bold</b>  story", "x" * 300])
    user = seen["user"]
    head = "Front-page stories from the last 3 days (do not repeat them unless there is a major new development):\n"
    assert head + f"- {POPE_BBC}\n- Bold story\n- " + "x" * 139 + "…\n\n" in user
    assert user.index(head) < user.index("Items (JSON):")
    brief.ask_claude(CFG, items_fixture(), "Tuesday, October 14, 2026", [f"Story {n}" for n in range(30)])
    assert seen["user"].count("\n- Story ") == brief.RECENT_LINES
    for none in (None, [], [""]):
        brief.ask_claude(CFG, items_fixture(), "Tuesday, October 14, 2026", none)
        assert "Front-page stories" not in seen["user"]


def test_prompt_and_schema_explain_seen_recently_and_whats_new():
    for phrase in ("seen_recently=true", "major new development", "whats_new", "at most 15 words", "\"sections\""):
        assert phrase in brief.SYSTEM_PROMPT
    props = brief.publish_tool(ORDER)["input_schema"]["properties"]
    assert props["lead"]["properties"]["whats_new"]["type"] == "string"
    assert props["front"]["items"]["properties"]["whats_new"]["type"] == "string"
    assert "whats_new" not in brief._NOTE_ITEM["properties"]
    assert "whats_new" not in props["sections"]["items"]["properties"]["items"]["items"]["properties"]
    assert "whats_new" not in props["lead"]["required"] and "whats_new" not in props["front"]["items"]["required"]


def repeat_items(*flagged):
    items = items_fixture()
    for i in items:
        if i["id"] in flagged:
            i["seen_recently"] = True
    return items


def test_repeat_lead_without_whats_new_is_dropped_and_first_front_is_promoted():
    choice = {"lead": {"id": "id2", "headline": "Old news", "note": "n", "decks": ["a", "b"]},
              "front": [{"id": "id4", "note": "f1"}, {"id": "id5", "note": "f2"}],
              "sections": []}
    for blank in (None, "", "   ", "<b> </b>"):
        entry_ = {**choice["lead"], **({} if blank is None else {"whats_new": blank})}
        out = brief.build_output({**choice, "lead": entry_}, repeat_items("id2"), ORDER)
        assert out["lead"]["title"] == "Title 4" and out["lead"]["headline"] == "Title 4"
        assert out["lead"]["decks"] == [] and out["lead"]["note"] == "f1"
        assert [f["title"] for f in out["front"]] == ["Title 5"]
        assert "follow_up" not in out["lead"]
        tech = next(s for s in out["sections"] if s["topic"] == "Tech & AI")
        assert "Title 2" in [i["title"] for i in tech["items"]]            # the fallback may still place it in its section


def test_repeat_lead_dropped_with_no_front_leaves_no_lead():
    out = brief.build_output({"lead": {"id": "id2", "headline": "H", "note": "n"}, "front": [], "sections": []},
                             repeat_items("id2"), ORDER)
    assert out["lead"] is None and out["front"] == []


def test_repeat_front_without_whats_new_leaves_the_front_but_can_sit_in_its_section():
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n"},
              "front": [{"id": "id5", "note": "repeat"}, {"id": "id4", "note": "ok"}],
              "sections": [{"topic": "World", "items": [{"id": "id5", "note": "in section"}, {"id": "id6", "note": "c"}]}]}
    out = brief.build_output(choice, repeat_items("id5"), ORDER)
    assert [f["title"] for f in out["front"]] == ["Title 4"]
    world = next(s for s in out["sections"] if s["topic"] == "World")
    assert [i["title"] for i in world["items"]] == ["Title 5", "Title 6"]
    assert world["items"][0]["note"] == "in section" and "follow_up" not in world["items"][0]
    assert out["lead"]["title"] == "Title 2"                                # an unflagged lead is not replaced


def test_repeat_with_whats_new_is_kept_and_carries_follow_up():
    long = "A new fact " + "w" * 200
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n", "whats_new": "  Surgery was <b>done</b> today.  "},
              "front": [{"id": "id4", "note": "f", "whats_new": long}, {"id": "id5", "note": "plain"}],
              "sections": [{"topic": "World", "items": [{"id": "id6", "note": "c", "whats_new": "ignored"}]}]}
    out = brief.build_output(choice, repeat_items("id2", "id4", "id6"), ORDER)
    assert out["lead"]["title"] == "Title 2" and out["lead"]["follow_up"] == "Surgery was done today."
    assert len(out["front"][0]["follow_up"]) <= 140 and out["front"][0]["follow_up"].startswith("A new fact")
    assert "follow_up" not in out["front"][1]
    world = next(s for s in out["sections"] if s["topic"] == "World")
    assert "follow_up" not in world["items"][0]                            # sections never get the key


def test_unflagged_items_ignore_whats_new_and_get_no_follow_up():
    choice = {"lead": {"id": "id2", "headline": "H", "note": "n", "whats_new": "stray"},
              "front": [{"id": "id4", "note": "f", "whats_new": "stray"}], "sections": []}
    out = brief.build_output(choice, items_fixture(), ORDER)
    assert "follow_up" not in out["lead"] and "follow_up" not in out["front"][0]
    assert [f["title"] for f in out["front"]] == ["Title 4"]


def test_main_marks_repeats_sends_recent_headlines_and_drops_a_repeat_lead(monkeypatch, tmp_path, capsys):
    recent_data(tmp_path, ["2026-10-13"])
    items = json.loads((ROOT / "fixtures/sample_items.json").read_text())["items"]
    lead_title = next(i["title"] for i in items if i["id"] == "f4dce0c7ac")
    yesterday = {"lead": {"title": lead_title + " today", "url": "https://other-outlet.test/haiku"}, "front": []}
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    spy = {}
    monkeypatch.setattr(brief, "datetime", type("D", (datetime,), {"now": classmethod(
        lambda cls, tz=None: datetime(2026, 10, 14, 7, 0, tzinfo=brief.TZ))}))
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, its, today: (desk, {"input_tokens": 0, "output_tokens": 0}),
                      spy=spy, yesterday=yesterday)
    assert "repeats: 1 items seen in the last 3 days" in out
    assert [i["id"] for i in spy["front"] if i.get("seen_recently")] == ["f4dce0c7ac"]
    assert spy["recent"][0] == "Story 2026-10-13 0" and spy["recent"][-1] == lead_title + " today"
    assert b["lead"]["title"] != lead_title and "follow_up" not in b["lead"]   # the repeat lead had no whats_new
    assert b["lead"]["title"] == "Mixed-use tower proposed beside the Beltline's Eastside Trail"
