"""Unit checks for brief.py. Run with: python -m pytest -q"""
import json
import subprocess
import sys
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

ORDER = ["Tech & AI", "Sports", "Architecture", "World"]


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
    assert sec["items"]["properties"]["items"]["minItems"] == 3
    assert sec["items"]["properties"]["items"]["maxItems"] == 5
    assert "image" not in json.dumps(t).replace("has_image", "")


def test_sources_config():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    assert [t["name"] for t in cfg["topics"]] == ORDER
    assert cfg["max_total_items"] <= 110
    assert (cfg["masthead"], cfg["tagline"], cfg["edition"]) == ("CMRLM", "The Daily Brief", "Atlanta Edition")
    world = next(t for t in cfg["topics"] if t["name"] == "World")
    assert [s["name"] for s in world["sources"]] == ["NPR World", "NPR News", "BBC World"]


# ---------- dry run ----------

def test_dry_run_writes_new_schema(tmp_path):
    target = ROOT / "site" / "brief.json"
    backup = target.read_bytes() if target.exists() else None
    try:
        r = subprocess.run([sys.executable, str(ROOT / "brief.py"), "--dry-run"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        b = json.loads(target.read_text())
    finally:
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(backup)
    assert list(b)[:6] == ["masthead", "tagline", "edition", "date_label", "generated_at", "model"]
    assert "top" not in b
    assert b["lead"]["headline"] and len(b["front"]) == 3
    assert 2 <= len(b["lead"]["decks"]) <= 3
    assert [s["topic"] for s in b["sections"]] == ORDER
    every = [b["lead"]] + b["front"] + [i for s in b["sections"] for i in s["items"]]
    assert all(set(i) >= {"title", "url", "source", "topic", "note", "image", "video"} for i in every)
    assert "does-not-exist-123" not in json.dumps(b)
    arch = next(s for s in b["sections"] if s["topic"] == "Architecture")
    first_img = next(i for i in arch["items"] if i["image"])
    assert first_img["source"] == "BIM Pure YouTube" and first_img["video"] == "abc123DEF45"
    assert sum(1 for i in every if i["video"]) == 1


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
