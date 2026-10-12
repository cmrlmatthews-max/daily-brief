"""Checks for likes.py (the likes writer). Run with: python -m pytest -q"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import likes  # noqa: E402


def run(tmp_path, payload, folder=None):
    env = {**os.environ, "BRIEF_DATA_DIR": str(folder or tmp_path / "data")}
    if payload is None:
        env.pop("LIKES_JSON", None)
    else:
        env["LIKES_JSON"] = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(ROOT / "likes.py")], capture_output=True, text=True, env=env)


def lines(tmp_path):
    path = tmp_path / "data" / "likes.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def entry(n, **kw):
    return {"u": f"https://x.test/{n}", "t": f"Title {n}", "s": "NPR", "c": "World", **kw}


def test_adds_valid_entries_and_creates_folder_and_file(tmp_path):
    r = run(tmp_path, [entry(1), entry(2, s="x" * 500, c="y" * 500), {"u": "https://x.test/3", "t": "Only required"}])
    assert r.returncode == 0 and r.stdout.strip() == "likes: received 3, added 3, skipped 0"
    got = lines(tmp_path)
    assert [g["u"] for g in got] == ["https://x.test/1", "https://x.test/2", "https://x.test/3"]
    assert set(got[0]) == {"at", "u", "t", "s", "c"} and got[0]["s"] == "NPR" and got[0]["c"] == "World"
    assert len(got[1]["s"]) == 100 and len(got[1]["c"]) == 60
    assert got[2]["s"] == "" and got[2]["c"] == ""
    when = datetime.fromisoformat(got[0]["at"])
    assert when.tzinfo is not None and abs((datetime.now(timezone.utc) - when).total_seconds()) < 60
    assert got[0]["at"].endswith("+00:00") and "." not in got[0]["at"]           # UTC, whole seconds


def test_drops_bad_urls_and_titles(tmp_path):
    bad = [entry(1, u="javascript:alert(1)"), entry(2, u="ftp://x.test/a"), entry(3, u="/relative"),
           entry(4, u="https://x.test/has space"), entry(5, u="https://x.test/new\nline"), entry(6, u="https://"),
           entry(7, u="https://x.test/" + "a" * 1000), entry(8, u=None), entry(9, t=""), entry(10, t="  \n\t "),
           entry(11, t=5), "string", 7, None, ["list"], {"t": "no url"}, {"u": "https://x.test/12"}]
    r = run(tmp_path, bad + [entry(20)])
    assert r.returncode == 0 and r.stdout.strip() == f"likes: received {len(bad) + 1}, added 1, skipped {len(bad)}"
    assert [g["u"] for g in lines(tmp_path)] == ["https://x.test/20"]


def test_url_exactly_1000_characters_is_allowed(tmp_path):
    url = "https://x.test/" + "a" * (1000 - len("https://x.test/"))
    assert len(url) == 1000
    run(tmp_path, [entry(1, u=url), entry(2, u=url + "b")])
    assert [g["u"] for g in lines(tmp_path)] == [url]


def test_cleans_control_characters(tmp_path):
    run(tmp_path, [entry(1, t="Line one\nline\ttwo\x00\x07 ‮end", s="So\x1burce", c="A\r\nB")])
    got = lines(tmp_path)[0]
    assert got["t"] == "Line one line two end" and got["s"] == "Source" and got["c"] == "A B"


def test_at_most_50_entries(tmp_path):
    r = run(tmp_path, [entry(n) for n in range(80)])
    assert r.returncode == 0 and r.stdout.strip() == "likes: received 80, added 50, skipped 30"
    assert [g["u"] for g in lines(tmp_path)] == [f"https://x.test/{n}" for n in range(50)]


def test_skips_urls_already_in_the_file_and_repeats_in_the_batch(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "likes.jsonl").write_text(json.dumps({"at": "2026-10-01T00:00:00+00:00", "u": "https://x.test/1",
                                                    "t": "Old", "s": "", "c": ""}) + "\nnot json")      # no final newline
    r = run(tmp_path, [entry(1), entry(2), entry(2), entry(3, u="https://x.test/1/")])
    assert r.stdout.strip() == "likes: received 4, added 1, skipped 3"
    raw = (folder / "likes.jsonl").read_text().splitlines()
    assert raw[1] == "not json" and json.loads(raw[2])["u"] == "https://x.test/2" and len(raw) == 3
    r = run(tmp_path, [entry(2)])
    assert r.stdout.strip() == "likes: received 1, added 0, skipped 1" and r.returncode == 0


def test_invalid_json_and_non_list_exit_1(tmp_path):
    for payload in ("not json", "", "{", '{"u": "https://x.test/1", "t": "a"}', '"text"', "5", "null"):
        r = run(tmp_path, payload)
        assert r.returncode == 1, payload
        assert not (tmp_path / "data" / "likes.jsonl").exists()
    assert run(tmp_path, None).returncode == 1                 # no LIKES_JSON at all
    assert run(tmp_path, "[" * 100000).returncode == 1         # deeply nested: still a clean exit 1


def test_empty_list_is_fine_and_creates_nothing(tmp_path):
    r = run(tmp_path, [])
    assert r.returncode == 0 and r.stdout.strip() == "likes: received 0, added 0, skipped 0"
    assert not (tmp_path / "data").exists()


def test_prints_counts_only(tmp_path):
    r = run(tmp_path, [entry(1, t="SECRET-TITLE-TEXT", u="https://secret.test/path")])
    assert "SECRET" not in r.stdout + r.stderr and "secret.test" not in r.stdout + r.stderr


def test_default_folder_is_data_next_to_the_working_directory(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "BRIEF_DATA_DIR"}
    env["LIKES_JSON"] = json.dumps([entry(1)])
    r = subprocess.run([sys.executable, str(ROOT / "likes.py")], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode == 0 and (tmp_path / "data" / "likes.jsonl").exists()


def test_written_lines_are_readable_by_the_brief_reader(tmp_path):
    import brief
    run(tmp_path, [entry(1), entry(2)])
    assert [l["u"] for l in brief.load_likes(tmp_path / "data")] == ["https://x.test/1", "https://x.test/2"]
