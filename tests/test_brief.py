"""Unit checks for brief.py. Run with: python -m pytest -q"""
import json
import os
import subprocess
import sys
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
    assert set(t["input_schema"]["required"]) == {"lead", "front", "sections"}
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
    assert cfg["max_total_items"] == 160
    assert cfg["max_output_tokens"] == 4000 and cfg["desk_max_output_tokens"] == 8000
    assert (cfg["masthead"], cfg["tagline"], cfg["edition"]) == ("CMRLM", "The Daily Brief", "Atlanta Edition")
    world = next(t for t in cfg["topics"] if t["name"] == "World")
    assert [s["name"] for s in world["sources"]] == ["NPR World", "NPR News", "BBC World"]
    names = {t["name"]: [s["name"] for s in t["sources"]] for t in cfg["topics"]}
    assert names["Politics"] == ["The Lever", "Drop Site News", "The Majority Report", "Breaking Points",
                                 "More Perfect Union"]
    assert names["Atlanta"] == ["Rough Draft Atlanta", "Urbanize Atlanta"]
    assert "Urbanize Atlanta" not in names["Architecture"]
    assert names["Science & Nature"] == ["Science News", "NASA", "Mongabay"]
    assert {"UFC (ESPN)", "Braves highlights", "Rockets highlights",
            "49ers highlights"} <= set(names["Sports"])
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
    assert cfg["stoic"]["url"] == "https://www.gutenberg.org/cache/epub/2680/pg2680.txt"
    assert "George Long" in cfg["stoic"]["credit"]
    for word in ("Majority Report", "UFC", "space", "Atlanta local news"):
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
    arch = next(s for s in b["sections"] if s["topic"] == "Architecture")
    first_img = next(i for i in arch["items"] if i["image"])
    assert first_img["source"] == "BIM Pure YouTube" and first_img["video"] == "abc123DEF45"
    assert sum(1 for i in every if i["video"]) == 2          # BIM Pure and The Majority Report
    # tabs, recalls, stoic and the longer score lines
    assert [t["id"] for t in b["tabs"]] == ["news", "tech", "sports", "atlanta", "science"]
    for t in b["tabs"]:
        assert t["name"] and t["sections"]
        for sec in t["sections"]:
            assert 4 <= len(sec["items"]) <= 10 and sec["topic"] in TOPICS
            assert all(set(i) >= {"title", "url", "source", "topic", "note", "image", "video"} for i in sec["items"])
    assert [s["topic"] for s in b["tabs"][3]["sections"]] == ["Atlanta", "Architecture"]
    assert "not-a-real-id" not in json.dumps(b)
    assert len(b["recalls"]) == 3 and all(set(r) == {"title", "url", "published"} for r in b["recalls"])
    assert b["stoic"]["text"] and b["stoic"]["credit"].startswith("Marcus Aurelius")
    for sc in b["scores"]:
        assert len(sc["recent"]) <= 3 and len(sc["upcoming"]) <= 3 and "last" in sc and "next" in sc
    assert b["usage"] == {"input_tokens": 0, "output_tokens": 0}


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
    assert "video segment" in brief.SYSTEM_PROMPT and "Politics" in brief.SYSTEM_PROMPT
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


def test_fetch_extras_fails_soft(monkeypatch):
    def boom(cfg, now=None, session=None):
        raise brief.requests.ConnectionError("offline")
    monkeypatch.setattr(brief, "fetch_recalls", boom)
    monkeypatch.setattr(brief, "fetch_stoic", lambda cfg, now, session=None: {"text": "Q", "credit": "C"})
    recalls, stoic, errors = brief.fetch_extras(CFG, NOW_UTC)
    assert recalls == [] and stoic == {"text": "Q", "credit": "C"}
    assert [e["source"] for e in errors] == ["Recalls"] and "ConnectionError" in errors[0]["error"]
    monkeypatch.setattr(brief, "fetch_stoic", boom)
    recalls, stoic, errors = brief.fetch_extras(CFG, NOW_UTC)
    assert stoic is None and [e["source"] for e in errors] == ["Recalls", "Stoic"]
    assert brief.fetch_extras({"topics": []}, NOW_UTC) == ([], None, [])           # nothing configured: nothing to do


# ---------- the daily Stoic quote ----------

BOOK = """The Project Gutenberg eBook of Meditations

CONTENTS
THE FIRST BOOK
THE SECOND BOOK

*** START OF THE PROJECT GUTENBERG EBOOK ***

THE FIRST BOOK

I. This sentence is in the book but it starts with a banned word so it is skipped.
Of my grandfather I have learned good morals and the government of my temper.

II. Short one. Another keeper that is long enough to pass every one of the rules here.
And this one starts with a linking word although it is long enough to pass otherwise.
The sentence with 3 digits in it is long enough but must be skipped by the digit rule.
The footnote mark makes this one long enough but it is dropped [1] because of the bracket.
the lowercase start is long enough to pass the length rule but fails the capital rule here.
It starts with a pronoun and so is skipped even though it is long enough to count here.
Was it right to ask a question that is long enough to pass every single one of the rules?
Not a sentence at all because it never ends and is long enough to pass the length rule

Words that are _emphasised_ stay readable once the underscores are gone from the line itself.
Xx. """ + "A very long sentence " * 12 + """ends here.

*** END OF THE PROJECT GUTENBERG EBOOK ***

After the end marker comes licence text that is long enough to pass the rules but must not be used.
"""


def test_stoic_sentences_filters():
    got = brief.stoic_sentences(BOOK)
    assert got == [
        "Of my grandfather I have learned good morals and the government of my temper.",
        "Another keeper that is long enough to pass every one of the rules here.",
        "Was it right to ask a question that is long enough to pass every single one of the rules?",
        "Words that are emphasised stay readable once the underscores are gone from the line itself."]
    assert all(brief.STOIC_MIN <= len(s) <= brief.STOIC_MAX for s in got)
    assert not any(c.isdigit() or c == "[" for s in got for c in s)
    assert brief.stoic_sentences("no book here at all") == []
    assert brief.stoic_sentences(BOOK.replace("\n", "\r\n")) == got                # Windows line ends


def test_stoic_uses_the_last_first_book_heading_and_stops_at_the_end_marker():
    text = "THE FIRST BOOK\nA contents line that is long enough to pass the length rule but is not in the body.\n" \
           "THE FIRST BOOK\n\nThe body sentence is long enough to pass the length rule and every other check here.\n" \
           "*** END OF THE PROJECT\nThe licence sentence is long enough to pass the length rule but comes after.\n"
    assert brief.stoic_sentences(text) == ["The body sentence is long enough to pass the length rule and every other check here."]


def test_pick_stoic_is_stable_per_day_and_varies_across_days():
    cfg = {"credit": "Marcus Aurelius, Meditations (trans. George Long)"}
    text = "THE FIRST BOOK\n\n" + "\n\n".join(
        f"The quote number {chr(65 + n) * 3} is a sentence that is long enough to pass every one of the rules."
        for n in range(12))
    day = lambda d: datetime(2026, 10, d, 7, 0, tzinfo=brief.TZ)  # noqa: E731
    first = brief.pick_stoic(text, cfg, day(11))
    assert first == brief.pick_stoic(text, cfg, day(11).replace(hour=23))         # the same all day
    assert first == brief.pick_stoic(text, cfg, day(11).astimezone(timezone.utc))
    assert set(first) == {"text", "credit"} and first["credit"] == cfg["credit"]
    assert first["text"] in brief.stoic_sentences(text)                            # only words from the book
    assert len({brief.pick_stoic(text, cfg, day(d))["text"] for d in range(1, 29)}) > 3
    assert brief.pick_stoic("THE FIRST BOOK\n\nToo short.", cfg, day(11)) is None
    assert brief.pick_stoic("", cfg, day(11)) is None


def test_fetch_stoic_downloads_and_picks():
    s = FakeSession(BOOK)
    got = brief.fetch_stoic(CFG, NOW_UTC, session=s)
    assert s.urls == [CFG["stoic"]["url"]]
    assert got["text"] in brief.stoic_sentences(BOOK) and got["credit"] == CFG["stoic"]["credit"]
    with pytest.raises(Exception):
        brief.fetch_stoic(CFG, NOW_UTC, session=FakeSession(boom=brief.requests.ConnectionError("x")))
    assert brief.fetch_stoic(CFG, NOW_UTC, session=FakeSession("<html>not the book</html>")) is None


def test_fixture_stoic_text_yields_a_quote():
    text = (ROOT / "fixtures/sample_stoic.txt").read_text()
    got = brief.stoic_sentences(text)
    assert got and all("licence" not in g.lower() and "[" not in g and "after the end marker" not in g for g in got)


# ---------- main(): two calls, fail-soft desk, summed usage ----------

def run_main(monkeypatch, tmp_path, capsys, ask_desk):
    items = json.loads((ROOT / "fixtures/sample_items.json").read_text())["items"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.delenv("BRIEF_PASSPHRASE", raising=False)
    monkeypatch.setattr(sys, "argv", ["brief.py"])
    monkeypatch.setattr(brief, "SITE_DIR", tmp_path)
    monkeypatch.setattr(brief, "fetch_all", lambda cfg: (items, [{"source": "Bad feed", "error": "x"}]))
    monkeypatch.setattr(brief, "fetch_scores", lambda cfg: ([], []))
    monkeypatch.setattr(brief, "fetch_extras", lambda cfg, now: (
        [{"title": "R", "url": "https://fda.test/r", "published": "2026-10-11T00:00:00+00:00"}], None,
        [{"source": "Stoic", "error": "ConnectionError: x"}]))
    monkeypatch.setattr(brief, "drop_seen_yesterday", lambda items, url: items)
    monkeypatch.setattr(brief, "resolve_og_images", lambda out, no_image: 0)
    monkeypatch.setattr(brief, "ask_claude", lambda cfg, items, today: (
        json.loads((ROOT / "fixtures/sample_response.json").read_text()), {"input_tokens": 100, "output_tokens": 10}))
    monkeypatch.setattr(brief, "ask_desk", ask_desk)
    assert brief.main() == 0
    return json.loads((tmp_path / "brief.json").read_text()), capsys.readouterr().out


def test_main_two_calls_sum_usage_and_write_new_keys(monkeypatch, tmp_path, capsys):
    desk = json.loads((ROOT / "fixtures/sample_desk_response.json").read_text())
    b, out = run_main(monkeypatch, tmp_path, capsys, lambda cfg, items, today: (desk, {"input_tokens": 300, "output_tokens": 70}))
    assert b["usage"] == {"input_tokens": 400, "output_tokens": 80}
    assert "desk: fallback" not in out
    assert [t["id"] for t in b["tabs"]] == ["news", "tech", "sports", "atlanta", "science"]
    assert b["recalls"][0]["title"] == "R" and b["stoic"] is None
    assert [e["source"] for e in b["source_errors"]] == ["Bad feed", "Stoic"]
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
