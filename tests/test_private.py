"""Checks for the private tabs (Plans and Mail). Run with: python -m pytest -q

Everything private in the fixtures is fake. The main rule under test: private text never reaches the
logs, brief.json, or the repo; it exists only inside the encrypted site/private.enc.
"""
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import private  # noqa: E402

TZ = private.TZ
FIXTURE = ROOT / "fixtures/sample_private.json"
ICS_URL = "https://calendar.google.com/calendar/ical/fixture%40example.invalid/private-FAKETOKEN0123456789/basic.ics"
SECRETS = {"BRIEF_PASSPHRASE": "correct horse battery fixture", "GCAL_ICS_URL": ICS_URL,
           "GMAIL_CLIENT_ID": "fake-client-id-123", "GMAIL_CLIENT_SECRET": "fake-client-secret-456",
           "GMAIL_REFRESH_TOKEN": "fake-refresh-token-789"}
CFG = yaml.safe_load((ROOT / "sources.yaml").read_text())
NOW = datetime(2026, 10, 14, 5, 45, tzinfo=TZ)   # a Wednesday


def ics(*events):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\n" + "".join(
        "BEGIN:VEVENT\r\n" + "\r\n".join(e) + "\r\nEND:VEVENT\r\n" for e in events) + "END:VCALENDAR\r\n")


def leaves(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from leaves(v)
    elif isinstance(obj, str):
        yield obj


def private_strings(payload):
    """Every free-text string in a payload that must never leak (short generic labels are skipped)."""
    skip = set(private.CATEGORIES) | {"2026-10-14", "Wednesday, October 14"}
    day = re.compile(r"^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day, [A-Z][a-z]+ \d+$")      # the plain date label
    return {s for s in leaves(payload) if len(s) >= 8 and s not in skip and not day.match(s)
            and not s.startswith("https://mail.google.com/") and not s[:4].isdigit()}


# ---------- encryption ----------

def test_round_trip():
    payload = {"generated_at": "x", "plans": {"events": [{"title": "Fixture é ✓"}]}, "mail": None, "errors": []}
    blob = private.encrypt_payload(payload, "demo passphrase")
    assert set(blob) == {"v", "kdf", "iter", "salt", "iv", "ct"}
    assert (blob["v"], blob["kdf"], blob["iter"]) == (1, "PBKDF2-SHA256", 600000)
    assert private.decrypt_payload(blob, "demo passphrase") == payload
    assert private.decrypt_payload(blob, "  demo passphrase\n") == payload   # a pasted newline cannot lock you out


def test_wrong_passphrase_fails():
    blob = private.encrypt_payload({"a": 1}, "right one")
    with pytest.raises(ValueError):
        private.decrypt_payload(blob, "wrong one")


def test_fresh_salt_and_iv_and_padding():
    a, b = private.encrypt_payload({"a": 1}, "p"), private.encrypt_payload({"a": 1}, "p")
    assert a["salt"] != b["salt"] and a["iv"] != b["iv"] and a["ct"] != b["ct"]
    import base64
    assert (len(base64.b64decode(a["ct"])) - 16) % private.PAD_TO == 0     # padded; the tag adds 16 bytes
    assert len(base64.b64decode(a["salt"])) == 16 and len(base64.b64decode(a["iv"])) == 12


def test_ciphertext_hides_plaintext():
    blob = json.dumps(private.encrypt_payload({"t": "Fixture Secret Meeting Title"}, "p"))
    assert "Fixture Secret" not in blob


# ---------- calendar ----------

CAL = ics(
    ["UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;VALUE=DATE:20261014", "DTEND;VALUE=DATE:20261015", "SUMMARY:All-day thing"],
    ["UID:b", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=America/New_York:20260930T093000",
     "DTEND;TZID=America/New_York:20260930T100000", "RRULE:FREQ=WEEKLY;BYDAY=WE", "SUMMARY:Weekly sync",
     "LOCATION:Room 4\\, Test Hall"],
    ["UID:c", "DTSTAMP:20260101T000000Z", "DTSTART:20261014T230000Z", "DTEND:20261015T010000Z", "SUMMARY:UTC evening"],
    ["UID:d", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=America/New_York:20261015T081500",
     "DTEND;TZID=America/New_York:20261015T090000", "SUMMARY:Tomorrow first"],
    ["UID:e", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=America/New_York:20261015T140000",
     "DTEND;TZID=America/New_York:20261015T150000", "SUMMARY:Tomorrow later"],
    ["UID:f", "DTSTAMP:20260101T000000Z", "DTSTART;VALUE=DATE:20261013", "DTEND;VALUE=DATE:20261014", "SUMMARY:Yesterday all-day"],
    ["UID:g", "DTSTAMP:20260101T000000Z", "STATUS:CANCELLED", "DTSTART;TZID=America/New_York:20261014T110000",
     "DTEND;TZID=America/New_York:20261014T120000", "SUMMARY:Cancelled"],
    ["UID:h", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=America/New_York:20261014T120000",
     "DTEND;TZID=America/New_York:20261014T130000", "SUMMARY:Lunch"],
)


def test_ics_today_recurring_allday_and_tomorrow():
    p = private.parse_calendar(CAL, NOW)
    assert p["date"] == "2026-10-14" and p["date_label"] == "Wednesday, October 14"
    titles = [e["title"] for e in p["events"]]
    assert titles == ["All-day thing", "Weekly sync", "Lunch", "UTC evening"]       # all-day first, then by start
    allday, weekly, lunch, utc = p["events"]
    assert allday["all_day"] is True and allday["start"] == "2026-10-14" and "location" not in allday
    assert weekly["all_day"] is False and weekly["start"] == "2026-10-14T09:30:00-04:00"
    assert weekly["end"] == "2026-10-14T10:00:00-04:00" and weekly["location"] == "Room 4, Test Hall"
    assert utc["start"] == "2026-10-14T19:00:00-04:00"                              # converted to New York time
    assert "Yesterday all-day" not in titles and "Cancelled" not in titles
    assert p["tomorrow_first"]["title"] == "Tomorrow first"
    assert p["tomorrow_first"]["start"] == "2026-10-15T08:15:00-04:00"


def test_ics_recurring_does_not_hit_other_days():
    p = private.parse_calendar(CAL, datetime(2026, 10, 15, 6, 0, tzinfo=TZ))        # Thursday: no weekly sync
    assert "Weekly sync" not in [e["title"] for e in p["events"]]


def test_ics_cap_and_no_events():
    many = ics(*[["UID:%d" % i, "DTSTAMP:20260101T000000Z", "DTSTART;TZID=America/New_York:20261014T%02d0000" % (i % 20),
                  "DTEND;TZID=America/New_York:20261014T%02d3000" % (i % 20), "SUMMARY:E%d" % i] for i in range(55)])
    assert len(private.parse_calendar(many, NOW)["events"]) == private.MAX_EVENTS
    empty = private.parse_calendar(ics(), NOW)
    assert empty["events"] == [] and empty["tomorrow_first"] is None


def test_ics_dst_day_uses_new_york_offsets():
    cal = ics(["UID:z", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=America/New_York:20261101T090000",
               "DTEND;TZID=America/New_York:20261101T100000", "SUMMARY:After the clocks change"])
    p = private.parse_calendar(cal, datetime(2026, 11, 1, 6, 0, tzinfo=TZ))
    assert p["events"][0]["start"] == "2026-11-01T09:00:00-05:00"


# ---------- Gmail ----------

def raw_msg(**kw):
    base = {"id": "1a2b3c", "threadId": "abc123", "labelIds": ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS", "Label_9"],
            "snippet": "Tom &amp; Jerry &#39;fixture&#39; " + "x" * 400, "internalDate": "1791000000000",
            "payload": {"headers": [{"name": "From", "value": '"Testy, Fixture" <testy@example.invalid>'},
                                    {"name": "Subject", "value": "=?UTF-8?B?Rml4dHVyZSDinJM=?="},
                                    {"name": "Date", "value": "Wed, 14 Oct 2026 05:10:00 -0400"}]}}
    base.update(kw)
    return base


def test_gmail_metadata_parsing():
    m = private.parse_message(raw_msg())
    assert m["from_name"] == "Testy, Fixture" and m["from_addr"] == "testy@example.invalid"
    assert m["subject"] == "Fixture ✓"                                        # RFC 2047 decoded
    assert m["snippet"].startswith("Tom & Jerry 'fixture'") and len(m["snippet"]) <= private.SNIPPET_CHARS
    assert m["labelIds"] == ["UNREAD", "CATEGORY_PROMOTIONS"]                  # only labels we use
    assert m["date"] == "2026-10-14T05:10:00-04:00"
    bare = private.parse_message(raw_msg(payload={"headers": [{"name": "From", "value": "solo@example.invalid"}]}))
    assert bare["from_name"] == "solo" and bare["subject"] == "(no subject)" and bare["date"] is not None
    assert private.parse_message({"nope": 1}) is None


def _msgs(n):
    return [private.parse_message(raw_msg(id=f"{i:02x}", threadId=f"{i:02x}ab", labelIds=["UNREAD"] if i == 1 else [],
                                          payload={"headers": [{"name": "From", "value": f"Sender {i} <s{i}@example.invalid>"},
                                                               {"name": "Subject", "value": f"Subject {i}"},
                                                               {"name": "Date", "value": f"Wed, 14 Oct 2026 0{i}:00:00 -0400"}]}))
            for i in range(1, n + 1)]


def test_sort_mail_mapping():
    msgs = _msgs(4)
    msgs[3]["threadId"] = "../evil?x=1"                                       # not hex: no link
    choice = {"items": [
        {"id": "m1", "category": "Action needed", "line": "Please reply " + "word " * 30 + "https://evil.example.invalid/x www.evil.example.invalid"},
        {"id": "m2", "category": "Shipping", "line": "Package coming"},
        {"id": "m99", "category": "Personal", "line": "unknown id"},           # dropped
        {"id": "m3", "category": "Not a category", "line": "bad category"},    # falls to Other
        {"id": "m2", "category": "Personal", "line": "duplicate id ignored"}],
        "overview": [{"category": "Shipping", "summary": "One parcel. https://evil.example.invalid"},
                     {"category": "Bogus", "summary": "dropped"}]}
    out = private.build_mail(msgs, choice)
    assert out["total"] == 4
    assert [c["name"] for c in out["categories"]] == ["Action needed", "Shipping", "Other"]   # fixed order, empties hidden
    act, ship, other = out["categories"]
    line = act["items"][0]["line"]
    assert "http" not in line and "www" not in line and len(line.split()) <= private.LINE_WORDS + 1
    assert act["items"][0]["unread"] is True and act["items"][0]["link"] == "https://mail.google.com/mail/u/0/#inbox/01ab"
    assert ship["items"][0]["line"] == "Package coming" and ship["overview"] == "One parcel."
    assert ship["count"] == 1
    subjects = {i["subject"]: i for i in other["items"]}
    assert set(subjects) == {"Subject 3", "Subject 4"} and other["count"] == 2   # m3 bad category, m4 skipped
    assert subjects["Subject 4"]["line"] == "Subject 4"                      # skipped -> subject as the line
    assert subjects["Subject 4"]["link"] is None                              # invalid threadId: no link
    assert subjects["Subject 3"]["link"] == "https://mail.google.com/mail/u/0/#inbox/03ab"
    assert "unknown id" not in json.dumps(out) and "duplicate" not in json.dumps(out)


def test_sort_mail_total_failure_and_empty():
    out = private.build_mail(_msgs(2), None)
    assert [c["name"] for c in out["categories"]] == ["Other"] and out["categories"][0]["count"] == 2
    assert private.build_mail([], {"items": []}) == {"total": 0, "categories": []}


def test_thread_link_validation():
    ok = "https://mail.google.com/mail/u/0/#inbox/18f3a1b2c3d4e5f6"
    assert private.thread_link("18f3a1b2c3d4e5f6") == ok
    for bad in ("", "XYZ", "18F3", "18f3/../x", "18f3 a", None, 12, "18f3a\n"):
        assert private.thread_link(bad) is None, bad


def test_sort_mail_tool_and_prompt():
    t = private.sort_mail_tool()
    items = t["input_schema"]["properties"]["items"]["items"]["properties"]
    assert items["category"]["enum"] == private.CATEGORIES and private.CATEGORIES[0] == "Action needed"
    assert "untrusted" in private.SORT_PROMPT and "never output URLs" in private.SORT_PROMPT
    slim = private.mail_for_model(_msgs(2))
    assert [m["id"] for m in slim] == ["m1", "m2"] and "threadId" not in json.dumps(slim) and "body" not in json.dumps(slim)


# ---------- the whole pipeline ----------

class FakeResp:
    def __init__(self, body=None, content=b"", status=200):
        self._body, self.content, self.status_code = body, content, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} Client Error for url: {ICS_URL}", response=self)


def mock_network(monkeypatch, fx, fail_ics=False):
    ids = [m["id"] for m in fx["gmail_messages"]]
    by_id = {m["id"]: m for m in fx["gmail_messages"]}

    def get(url, **kw):
        if url == ICS_URL:
            if fail_ics:
                import requests
                raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {ICS_URL}")
            return FakeResp(content=fx["calendar_ics"].encode())
        assert kw["headers"]["Authorization"] == "Bearer fake-access-token"
        if url.endswith("/messages"):
            assert kw["params"]["q"] == "in:inbox newer_than:1d" and kw["params"]["maxResults"] == 60
            return FakeResp({"messages": [{"id": i, "threadId": "x"} for i in ids]})
        assert kw["params"]["format"] == "metadata" and set(kw["params"]["metadataHeaders"]) == {"From", "Subject", "Date"}
        return FakeResp(by_id[url.rsplit("/", 1)[1]])

    def post(url, **kw):
        assert url == "https://oauth2.googleapis.com/token" and kw["data"]["grant_type"] == "refresh_token"
        return FakeResp({"access_token": "fake-access-token"})

    monkeypatch.setattr(private.requests, "get", get)
    monkeypatch.setattr(private.requests, "post", post)
    monkeypatch.setattr(private, "call_sort_mail",
                        lambda cfg, msgs: (fx["sort_mail_response"], {"input_tokens": 1200, "output_tokens": 340}))


def run_captured(tmp_path, env, dry_run=False):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        usage = private.run_private(CFG, NOW, tmp_path, ROOT, dry_run=dry_run, env=env)
    return usage, out.getvalue() + err.getvalue()


def fixture_for(now=NOW):
    return private.load_fixture(FIXTURE, now)


def test_full_pipeline_writes_only_ciphertext_and_logs_only_counts(tmp_path, monkeypatch):
    fx = fixture_for()
    mock_network(monkeypatch, fx)
    usage, logs = run_captured(tmp_path, SECRETS)
    assert usage == {"input_tokens": 1200, "output_tokens": 340}
    blob = json.loads((tmp_path / "private.enc").read_text())
    payload = private.decrypt_payload(blob, SECRETS["BRIEF_PASSPHRASE"])
    assert payload["plans"]["events"] and payload["mail"]["total"] == 10 and payload["errors"] == []
    secret_text = private_strings(payload)
    assert len(secret_text) > 25
    raw = (tmp_path / "private.enc").read_text()
    for s in list(secret_text) + [ICS_URL, "FAKETOKEN"]:
        assert s not in logs, f"leaked to logs: {s!r}"
        assert s not in raw, f"plaintext in private.enc: {s!r}"
    for v in SECRETS.values():
        assert v not in logs and v not in raw
    assert "plans: 4 events" in logs and "mail: 10 messages in 8 categories" in logs
    assert "fake-access-token" not in logs


def test_failures_log_only_exception_types(tmp_path, monkeypatch):
    fx = fixture_for()
    mock_network(monkeypatch, fx, fail_ics=True)
    usage, logs = run_captured(tmp_path, SECRETS)
    assert "ConnectionError" in logs
    for s in (ICS_URL, "FAKETOKEN", "Max retries", "private-"):
        assert s not in logs
    payload = private.decrypt_payload(json.loads((tmp_path / "private.enc").read_text()), SECRETS["BRIEF_PASSPHRASE"])
    assert payload["plans"] is None and payload["mail"]["total"] == 10
    assert payload["errors"] == ["Calendar could not be read: ConnectionError"]
    assert ICS_URL not in json.dumps(payload)


def test_http_error_hides_url(tmp_path, monkeypatch):
    fx = fixture_for()
    mock_network(monkeypatch, fx)
    real_get = private.requests.get
    monkeypatch.setattr(private.requests, "get", lambda url, **kw: FakeResp(status=404) if url == ICS_URL else real_get(url, **kw))
    usage, logs = run_captured(tmp_path, SECRETS)
    assert "HTTP 404" in logs and ICS_URL not in logs and "FAKETOKEN" not in logs


def test_sort_failure_still_builds_mail(tmp_path, monkeypatch):
    fx = fixture_for()
    mock_network(monkeypatch, fx)

    def boom(cfg, msgs):
        raise RuntimeError("model said: Fixture Secret subject")
    monkeypatch.setattr(private, "call_sort_mail", boom)
    usage, logs = run_captured(tmp_path, SECRETS)
    assert "Fixture Secret" not in logs and "RuntimeError" in logs
    payload = private.decrypt_payload(json.loads((tmp_path / "private.enc").read_text()), SECRETS["BRIEF_PASSPHRASE"])
    assert [c["name"] for c in payload["mail"]["categories"]] == ["Other"] and payload["mail"]["total"] == 10


def test_unconnected_sources_are_null(tmp_path, monkeypatch):
    fx = fixture_for()
    mock_network(monkeypatch, fx)
    usage, logs = run_captured(tmp_path, {"BRIEF_PASSPHRASE": "pw only"})
    payload = private.decrypt_payload(json.loads((tmp_path / "private.enc").read_text()), "pw only")
    assert payload["plans"] is None and payload["mail"] is None and payload["errors"] == []
    assert "plans: not connected" in logs and "mail: not connected" in logs


def test_no_passphrase_means_no_fetch_and_no_file(tmp_path, monkeypatch):
    def nope(*a, **k):
        raise AssertionError("network must not be touched without a passphrase")
    monkeypatch.setattr(private.requests, "get", nope)
    monkeypatch.setattr(private.requests, "post", nope)
    monkeypatch.setattr(private, "call_sort_mail", nope)
    env = {k: v for k, v in SECRETS.items() if k != "BRIEF_PASSPHRASE"}
    usage, logs = run_captured(tmp_path, env)
    assert logs.strip() == "private tabs: skipped (no passphrase)"
    assert list(tmp_path.iterdir()) == []
    usage, logs = run_captured(tmp_path, {**env, "BRIEF_PASSPHRASE": "   \n"})
    assert logs.strip() == "private tabs: skipped (no passphrase)" and list(tmp_path.iterdir()) == []


def test_dry_run_defaults_to_demo_passphrase(tmp_path):
    usage, logs = run_captured(tmp_path, {}, dry_run=True)
    payload = private.decrypt_payload(json.loads((tmp_path / "private.enc").read_text()), "demo passphrase")
    assert payload["plans"]["events"] and payload["mail"]["total"] == 10
    titles = [e["title"] for e in payload["plans"]["events"]]
    assert "Fake Weekly Standup" in titles and "Fixture Observance Day (fake)" in titles   # recurring + all-day
    assert not any("Cancelled" in t or "Yesterday" in t for t in titles)
    cats = {c["name"]: c for c in payload["mail"]["categories"]}
    assert "Other" in cats and "must be dropped" not in json.dumps(payload)
    assert "Fixture" not in logs.replace("private tabs", "") and "Testy" not in logs


# ---------- brief.json and the repo stay clean ----------

def test_dry_run_brief_json_has_no_private_text(tmp_path):
    env = {**os.environ, "BRIEF_SITE_DIR": str(tmp_path)}
    env.pop("BRIEF_PASSPHRASE", None)
    r = subprocess.run([sys.executable, str(ROOT / "brief.py"), "--dry-run"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "private.enc").exists()
    payload = private.decrypt_payload(json.loads((tmp_path / "private.enc").read_text()), "demo passphrase")
    brief_text = (tmp_path / "brief.json").read_text()
    for s in private_strings(payload):
        assert s not in brief_text and s not in r.stdout + r.stderr, s
    b = json.loads(brief_text)
    assert set(b["usage"]) == {"input_tokens", "output_tokens"}
    assert "plans" not in b and "mail" not in b


def test_repo_ignores_private_files_and_workflow_passes_secrets():
    assert "site/private.enc" in (ROOT / ".gitignore").read_text().split()
    wf = (ROOT / ".github/workflows/daily-brief.yml").read_text()
    for name in ("BRIEF_PASSPHRASE", "GCAL_ICS_URL", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in wf
    fx = json.loads(FIXTURE.read_text())
    assert "example.invalid" in json.dumps(fx)
