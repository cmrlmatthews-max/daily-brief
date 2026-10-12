"""Checks for audio.py (the daily Kokoro voice). Run with: python -m pytest -q"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import audio  # noqa: E402

WORKFLOW = ROOT / ".github/workflows/daily-brief.yml"


# ---------- helpers ----------

STAMP = 1_791_795_600           # 2026-10-12T05:00:00-04:00 in unix seconds


def brief_file(tmp_path, folder="site", **kw):
    site = tmp_path / folder
    site.mkdir(exist_ok=True)
    data = {"generated_at": "2026-10-12T05:00:00-04:00", "listen": "Good morning. Mitte is here.", "lead": {"title": "T"}}
    data.update(kw)
    for k in [k for k, v in data.items() if v is None]:
        del data[k]
    (site / "brief.json").write_text(json.dumps(data))
    return site


def fake_models(tmp_path, monkeypatch):
    """Two small stand-in model files whose sha256 values are the ones audio.py expects."""
    folder = tmp_path / "models"
    folder.mkdir()
    sums = {}
    for name in audio.MODEL_FILES:
        (folder / name).write_bytes(name.encode())
        sums[name] = hashlib.sha256(name.encode()).hexdigest()
    monkeypatch.setattr(audio, "MODEL_FILES", sums)
    monkeypatch.setenv("KOKORO_DIR", str(folder))
    return folder


def setup_run(tmp_path, monkeypatch, cfg=None, **brief_kw):
    site = brief_file(tmp_path, **brief_kw)
    monkeypatch.setattr(audio, "SITE_DIR", site)
    monkeypatch.setattr(audio, "load_config", lambda path=None: {**audio.DEFAULTS, **(cfg or {})})
    return site


FAKE_MP3 = b"ID3" + b"\x00" * 20


def mock_voice(monkeypatch, seconds=12.34, calls=None):
    """No model and no encoder: the voice makes `seconds` of silence and the encoder writes a small file."""
    def synthesize(text, model, voices, cfg):
        if calls is not None:
            calls.append(text)
        return [0.0] * int(seconds * 1000), 1000

    def encode(samples, rate, path, bitrate):
        Path(path).write_bytes(FAKE_MP3 + bitrate.encode())
    monkeypatch.setattr(audio, "synthesize", synthesize)
    monkeypatch.setattr(audio, "encode_mp3", encode)


def saved(site):
    return json.loads((site / "brief.json").read_text())


# ---------- pronunciation ----------

def test_builtin_pronunciations():
    assert audio.pronounce("Martin Luther King Jr. and Hank Williams Sr. spoke") == \
        "Martin Luther King Junior and Hank Williams Senior spoke"
    assert audio.pronounce("The U.S. Senate met") == "The U S Senate met"
    assert audio.pronounce("Ben & Jerry") == "Ben and Jerry"


def test_pronounce_entries_from_yaml_win_and_add():
    extra = {"Mitte": "Mitty", "&": "ampersand", "Nguyen": "Win"}
    assert audio.pronounce("Mitte & Nguyen", extra) == "Mitty ampersand Win"


def test_pronounce_whole_word_and_case_sensitive_only():
    extra = {"Mitte": "Mitty"}
    assert audio.pronounce("Mitten Mitte Mitter mitte Smitte Mitte.", extra) == "Mitten Mitty Mitter mitte Smitte Mitty."
    assert audio.pronounce("AT&T and R&D", extra) == "AT&T and R&D"            # the & is inside a word
    assert audio.pronounce("Jr.x and aJr. and Jr.", {}) == "Jr.x and aJr. and Junior."


def test_pronounce_is_one_pass_and_keeps_a_final_full_stop():
    assert audio.pronounce("A B", {"A": "B", "B": "C"}) == "B C"             # a replacement is not replaced again
    assert audio.pronounce("He lives in the U.S.") == "He lives in the U S."
    assert audio.pronounce("King Jr.\nNext") == "King Junior.\nNext"
    assert audio.pronounce("U.S.A. and U.S.", {"U.S.A.": "U S A"}) == "U S A and U S."


def test_pronounce_ignores_bad_entries():
    for bad in (None, [], "x", {"": "y", " ": "z", 5: "q", "Ok": None}):
        assert audio.pronounce("Ok & Ok", bad) == "Ok and Ok"


def test_pronounce_ignores_yaml_booleans():
    """YAML reads a bare No or Yes as False or True. They must never be spoken."""
    cfg = yaml.safe_load("pronounce:\n  Mitte: Mitty\n  Nope: No\n  Yep: Yes\n  No: Nein\n")
    assert cfg["pronounce"] == {"Mitte": "Mitty", "Nope": False, "Yep": True, False: "Nein"}
    assert audio.pronounce("Mitte Nope Yep No False", cfg["pronounce"]) == "Mitty Nope Yep No False"


def test_pronounce_changes_a_copy_not_the_visible_text(tmp_path, monkeypatch):
    original = "King Jr. met Mitte & friends."
    site = setup_run(tmp_path, monkeypatch, {"pronounce": {"Mitte": "Mitty"}}, listen=original)
    fake_models(tmp_path, monkeypatch)
    spoken = []
    mock_voice(monkeypatch, calls=spoken)
    assert audio.main([]) == 0
    assert spoken == ["King Junior met Mitty and friends."]
    assert json.loads((site / "brief.json").read_text())["listen"] == original


def test_sources_yaml_has_the_audio_block():
    cfg = audio.load_config()
    assert cfg["enabled"] is True and cfg["voice"] == "am_michael" and cfg["speed"] == 1.0
    assert cfg["lang"] == "en-us" and cfg["model_dir"] == ".kokoro" and cfg["bitrate"] == "64k"
    assert cfg["pronounce"] == {"Mitte": "Mitty"}


def test_load_config_falls_back_to_defaults(tmp_path):
    for text in ("", "audio: 5\n", "audio:\n  voice: am_puck\n  pronounce: [a]\n", "{{{ not yaml"):
        p = tmp_path / "s.yaml"
        p.write_text(text)
        cfg = audio.load_config(p)
        assert cfg["pronounce"] == {} and cfg["bitrate"] == "64k"
    assert audio.load_config(tmp_path / "missing.yaml")["voice"] == "am_michael"
    p.write_text("audio:\n  voice: am_puck\n")
    assert audio.load_config(p)["voice"] == "am_puck"


# ---------- skipping ----------

def test_missing_or_empty_listen_changes_nothing(tmp_path, monkeypatch, capsys):
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch)
    for kw in ({"listen": None}, {"listen": ""}, {"listen": "  \n "}, {"listen": 5}):
        site = setup_run(tmp_path, monkeypatch, **kw)
        before = (site / "brief.json").read_text()
        assert audio.main([]) == 0
        assert (site / "brief.json").read_text() == before and not (site / "listen.mp3").exists()
        assert "no listen text" in capsys.readouterr().out


def test_missing_brief_json_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audio, "SITE_DIR", tmp_path / "nowhere")
    monkeypatch.setattr(audio, "load_config", lambda path=None: dict(audio.DEFAULTS))
    assert audio.main([]) == 0 and "not found" in capsys.readouterr().out


def test_switched_off_in_config(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch, {"enabled": False})
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch)
    assert audio.main([]) == 0 and "switched off" in capsys.readouterr().out
    assert "audio" not in json.loads((site / "brief.json").read_text())


def test_missing_model_files_exit_zero_without_audio(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch)
    monkeypatch.setenv("KOKORO_DIR", str(tmp_path / "empty"))
    mock_voice(monkeypatch)
    assert audio.main([]) == 0
    assert "model file missing" in capsys.readouterr().out
    assert "audio" not in json.loads((site / "brief.json").read_text()) and not (site / "listen.mp3").exists()


def test_wrong_sha256_exits_zero_without_audio(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch)
    folder = fake_models(tmp_path, monkeypatch)
    (folder / audio.VOICES_FILE).write_bytes(b"changed")
    mock_voice(monkeypatch)
    assert audio.main([]) == 0
    assert "wrong sha256" in capsys.readouterr().out
    assert "audio" not in json.loads((site / "brief.json").read_text()) and not (site / "listen.mp3").exists()


def test_model_folder_env_wins_over_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KOKORO_DIR", str(tmp_path / "a"))
    assert audio.model_folder({"model_dir": "b"}) == tmp_path / "a"
    monkeypatch.delenv("KOKORO_DIR")
    assert audio.model_folder({"model_dir": "b"}) == ROOT / "b"
    assert audio.model_folder({"model_dir": str(tmp_path)}) == tmp_path


def test_real_pinned_hashes_look_right():
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in audio.MODEL_FILES.values())
    assert list(audio.MODEL_FILES) == ["kokoro-v1.0.fp16.onnx", "voices-v1.0.bin"]


# ---------- the good run ----------

def test_local_mode_adds_the_audio_key(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch, {"bitrate": "48k"})
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch, seconds=12.34)
    assert audio.main([]) == 0
    brief = saved(site)
    assert brief["audio"] == {"url": f"listen.mp3?v={STAMP}", "seconds": 12.3, "voice": "Kokoro Michael"}
    assert brief["lead"] == {"title": "T"} and brief["listen"] == "Good morning. Mitte is here."   # the rest is kept
    assert (site / "listen.mp3").read_bytes() == FAKE_MP3 + b"48k"
    assert not list(site.glob("*.part")) and not list(site.glob("*.tmp"))
    assert "12.3 s of speech" in capsys.readouterr().out


def test_out_mode_writes_only_to_the_out_folder(tmp_path, monkeypatch, capsys):
    """The workflow way: read in/brief.json, write out/listen.mp3 and out/audio.json. Nothing else changes."""
    inn = brief_file(tmp_path, "in")
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setattr(audio, "SITE_DIR", site)
    monkeypatch.setattr(audio, "load_config", lambda path=None: dict(audio.DEFAULTS))
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch, seconds=30.04)
    before = (inn / "brief.json").read_text()
    out = tmp_path / "out"
    assert audio.main(["--brief", str(inn / "brief.json"), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["audio.json", "listen.mp3"]
    assert json.loads((out / "audio.json").read_text()) == \
        {"url": f"listen.mp3?v={STAMP}", "seconds": 30.0, "voice": "Kokoro Michael"}
    assert (out / "listen.mp3").read_bytes().startswith(b"ID3")
    assert (inn / "brief.json").read_text() == before            # brief.json is not rewritten
    assert list(site.iterdir()) == [] and sorted(p.name for p in inn.iterdir()) == ["brief.json"]


def test_out_mode_skip_leaves_nothing_and_clears_old_files(tmp_path, monkeypatch, capsys):
    inn = brief_file(tmp_path, "in", listen="")
    out = tmp_path / "out"
    out.mkdir()
    for name in ("listen.mp3", "listen.mp3.part", "audio.json"):
        (out / name).write_bytes(b"old")
    monkeypatch.setattr(audio, "load_config", lambda path=None: dict(audio.DEFAULTS))
    assert audio.main(["--brief", str(inn / "brief.json"), "--out", str(out)]) == 0
    assert list(out.iterdir()) == [] and "no listen text" in capsys.readouterr().out


def test_out_mode_failure_leaves_nothing(tmp_path, monkeypatch, capsys):
    inn = brief_file(tmp_path, "in")
    out = tmp_path / "out"
    fake_models(tmp_path, monkeypatch)
    monkeypatch.setattr(audio, "load_config", lambda path=None: dict(audio.DEFAULTS))
    mock_voice(monkeypatch)
    real = audio.write_json_atomic
    monkeypatch.setattr(audio, "write_json_atomic", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert audio.main(["--brief", str(inn / "brief.json"), "--out", str(out)]) == 0
    assert list(out.iterdir()) == [] and "disk full" in capsys.readouterr().out    # the MP3 made before is removed
    monkeypatch.setattr(audio, "write_json_atomic", real)


def test_bad_arguments_still_exit_zero(capsys):
    assert audio.main(["--nope"]) == 0 and "bad arguments" in capsys.readouterr().out


def test_old_audio_key_and_mp3_never_survive_a_run_without_audio(tmp_path, monkeypatch):
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch)
    old = {"url": "listen.mp3?v=1", "seconds": 5, "voice": "Kokoro Michael"}
    cases = [({"enabled": False}, {}), ({}, {"listen": ""}), ({}, {"listen": None})]
    for cfg, kw in cases:
        site = setup_run(tmp_path, monkeypatch, cfg, audio=old, **kw)
        (site / "listen.mp3").write_bytes(b"old")
        (site / "listen.mp3.part").write_bytes(b"old")
        assert audio.main([]) == 0
        assert "audio" not in saved(site) and not (site / "listen.mp3").exists() and not (site / "listen.mp3.part").exists()
    # a failed run, too
    site = setup_run(tmp_path, monkeypatch, audio=old)
    monkeypatch.setattr(audio, "synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    (site / "listen.mp3").write_bytes(b"old")
    assert audio.main([]) == 0
    assert "audio" not in saved(site) and not (site / "listen.mp3").exists()


def test_audio_url_uses_generated_at():
    assert audio.audio_url("2026-10-12T05:00:00-04:00") == audio.audio_url("2026-10-12T09:00:00+00:00")
    assert audio.audio_url("2026-10-12T05:00:00-04:00") != audio.audio_url("2026-10-13T05:00:00-04:00")
    assert re.fullmatch(r"listen\.mp3\?v=\d+", audio.audio_url(None))
    assert re.fullmatch(r"listen\.mp3\?v=\d+", audio.audio_url("garbage"))


def test_json_is_written_atomically(tmp_path, monkeypatch):
    target = tmp_path / "brief.json"
    target.write_text('{"keep": 1}')
    real = audio.os.replace

    def boom(a, b):
        raise OSError("disk full")
    monkeypatch.setattr(audio.os, "replace", boom)
    try:
        audio.write_json_atomic(target, {"new": 2})
    except OSError:
        pass
    monkeypatch.setattr(audio.os, "replace", real)
    assert json.loads(target.read_text()) == {"keep": 1} and not list(tmp_path.glob("*.tmp"))
    audio.write_json_atomic(target, {"new": "é"})
    assert json.loads(target.read_text()) == {"new": "é"} and not list(tmp_path.glob("*.tmp"))


# ---------- nothing escapes ----------

def test_exceptions_never_escape_main(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch)
    fake_models(tmp_path, monkeypatch)
    (site / "listen.mp3").write_bytes(b"old")

    def boom(*a, **k):
        raise RuntimeError("voice exploded")
    for name in ("synthesize", "encode_mp3"):
        mock_voice(monkeypatch)
        monkeypatch.setattr(audio, name, boom)
        assert audio.main([]) == 0
        assert "voice exploded" in capsys.readouterr().out
        assert not (site / "listen.mp3").exists() and not list(site.glob("*.part"))
        assert "audio" not in saved(site)


def test_import_error_and_bad_json_never_escape(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch)
    fake_models(tmp_path, monkeypatch)
    monkeypatch.setattr(audio, "synthesize", lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("kokoro_onnx")))
    assert audio.main([]) == 0 and "ModuleNotFoundError" in capsys.readouterr().out
    (site / "brief.json").write_text("{ not json")
    mock_voice(monkeypatch)
    assert audio.main([]) == 0 and "JSONDecodeError" in capsys.readouterr().out
    assert not (site / "listen.mp3").exists()


def test_silent_voice_empty_file_or_non_mp3_gives_no_audio(tmp_path, monkeypatch, capsys):
    site = setup_run(tmp_path, monkeypatch)
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch, seconds=0)
    assert audio.main([]) == 0 and "no sound" in capsys.readouterr().out
    for content, reason in ((b"", "no MP3 file"), (b"RIFFxxxx", "not an MP3")):
        mock_voice(monkeypatch)
        monkeypatch.setattr(audio, "encode_mp3", lambda s, r, path, b, c=content: Path(path).write_bytes(c))
        assert audio.main([]) == 0 and reason in capsys.readouterr().out
        assert not (site / "listen.mp3").exists() and not list(site.glob("*.part")) and "audio" not in saved(site)


# ---------- the MP3 writer ----------

def test_mp3_is_written_by_soundfile_mono_with_the_bitrate_level(tmp_path, monkeypatch):
    calls = []

    class FakeSoundfile:
        @staticmethod
        def available_formats():
            return {"WAV": "x", "MP3": "x"}

        @staticmethod
        def write(path, samples, rate, **kw):
            calls.append((path, rate, kw))
            Path(path).write_bytes(FAKE_MP3)
    monkeypatch.setitem(sys.modules, "soundfile", FakeSoundfile)
    audio.encode_mp3([0.0] * 10, 24000, tmp_path / "a.mp3", "96k")
    assert calls == [(str(tmp_path / "a.mp3"), 24000,
                      {"format": "MP3", "subtype": "MPEG_LAYER_III", "compression_level": 0.4,
                       "bitrate_mode": "CONSTANT"})]


def test_mp3_levels_and_header_check():
    assert audio.mp3_level("64k") == 0.62 and audio.mp3_level("128k") == 0.2 and audio.mp3_level("32k") == 0.85
    assert audio.mp3_level("70k") == 0.62 and audio.mp3_level("junk") == 0.62 and audio.mp3_level(None) == 0.62
    assert audio.looks_like_mp3(b"ID3\x04") and audio.looks_like_mp3(b"\xff\xf3\x84\xc4")
    assert not audio.looks_like_mp3(b"RIFF") and not audio.looks_like_mp3(b"\xff\x00") and not audio.looks_like_mp3(b"")


def test_ffmpeg_is_only_the_fallback(tmp_path, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(audio, "write_mp3", lambda *a: (_ for _ in ()).throw(RuntimeError("no MP3")))
    monkeypatch.setattr(audio, "write_mp3_ffmpeg", lambda *a: ran.append(a))
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    try:
        audio.encode_mp3([0.0], 24000, tmp_path / "a.mp3", "64k")
        assert False, "should raise when ffmpeg is not there"
    except RuntimeError as exc:
        assert "no MP3" in str(exc)
    assert ran == []
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    audio.encode_mp3([0.0], 24000, tmp_path / "a.mp3", "64k")
    assert len(ran) == 1 and "using ffmpeg" in capsys.readouterr().out
    # a working soundfile never calls ffmpeg
    ran.clear()
    monkeypatch.setattr(audio, "write_mp3", lambda *a: None)
    audio.encode_mp3([0.0], 24000, tmp_path / "a.mp3", "64k")
    assert ran == []


def test_real_soundfile_writes_a_valid_mp3(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    if "MP3" not in sf.available_formats():
        pytest.skip("this libsndfile has no MP3")
    t = np.arange(24000 * 5) / 24000
    samples = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    path = tmp_path / "a.mp3"
    audio.encode_mp3(samples, 24000, path, "64k")
    data = path.read_bytes()
    assert audio.looks_like_mp3(data[:4])
    info = sf.info(str(path))
    assert info.channels == 1 and info.samplerate == 24000 and abs(info.duration - 5.0) < 0.2
    kbps = len(data) * 8 / 5.0 / 1000
    assert 55 < kbps < 75, kbps                                  # about 64 kbps


# ---------- workflow ----------

def jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def steps_of(job):
    return {s.get("name") or s.get("uses") or s.get("run"): s for s in job["steps"]}


def test_job_graph():
    j = jobs()
    assert set(j) == {"build", "audio", "assemble", "deploy", "save"}
    assert j["build"]["timeout-minutes"] == 15 and "needs" not in j["build"]
    assert j["audio"]["needs"] == "build" and j["audio"]["if"] == "needs.build.outputs.skip != 'true'"
    assert j["assemble"]["needs"] == ["build", "audio"]
    assert j["assemble"]["if"] == "always() && needs.build.result == 'success' && needs.build.outputs.skip != 'true'"
    assert j["deploy"]["needs"] == "assemble" and j["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert j["save"]["needs"] == "build"
    # the audio steps are not in the build job
    build_text = yaml.safe_dump(j["build"])
    assert "kokoro" not in build_text.lower() and "audio.py" not in build_text and "upload-pages-artifact" not in build_text


def test_build_uploads_the_paper_and_the_site_artifacts():
    uploads = [s for s in jobs()["build"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact@v4")]
    assert [u["with"] for u in uploads] == [
        {"name": "paper", "path": "site/brief.json", "retention-days": 3},
        {"name": "site", "path": "site", "retention-days": 1}]
    assert all(u["if"] == "steps.check.outputs.skip != 'true'" for u in uploads)


def test_audio_job_is_isolated():
    job = jobs()["audio"]
    assert job["continue-on-error"] is True and job["timeout-minutes"] == 12
    assert job["permissions"] == {"contents": "read"}
    text = yaml.safe_dump(job)
    assert "secrets." not in text and "ANTHROPIC" not in text and "GITHUB_TOKEN" not in text
    assert "upload-pages-artifact" not in text and "deploy-pages" not in text
    env = {k for s in job["steps"] for k in s.get("env", {})}
    assert env == {"KOKORO_DIR"}
    # nothing in this job can write to site/: it reads in/ and writes out/
    assert "site" not in " ".join(str(s.get("run", "")) for s in job["steps"])


def test_audio_job_steps_and_cache_saved_only_after_the_sha_check():
    steps = jobs()["audio"]["steps"]
    names = [s.get("name") or s.get("uses") or s.get("run") for s in steps]
    by = steps_of(jobs()["audio"])
    assert names[:2] == ["actions/checkout@v4", "actions/download-artifact@v4"]
    assert steps[1]["with"] == {"name": "paper", "path": "in"}
    restore, get, save = by["Restore the voice model"], by["Get the voice model"], by["Save the voice model"]
    assert restore["uses"] == "actions/cache/restore@v4" and restore["with"] == {"path": ".kokoro", "key": "kokoro-v1.0-fp16"}
    assert save["uses"] == "actions/cache/save@v4" and save["with"] == restore["with"]
    assert get["if"] == save["if"] == f"steps.{restore['id']}.outputs.cache-hit != 'true'"
    assert names.index("Restore the voice model") < names.index("Get the voice model") < names.index("Save the voice model") \
        < names.index("pip install -r requirements-audio.txt") < names.index("Make the audio")
    run = get["run"]
    assert "set -euo pipefail" in run and run.count("curl -fsSL --max-time 180") == 2
    # download to a temp folder, check, and only then move into .kokoro
    assert run.index("sha256sum -c") < run.index("mv ") and "$RUNNER_TEMP" in run
    assert run.index('-o "$dl/') < run.index("sha256sum -c")
    assert ".kokoro" not in run[:run.index("sha256sum -c")].replace("mkdir -p .kokoro", "")
    assert steps[names.index("actions/setup-python@v5")]["with"]["python-version"] == "3.12"
    make = by["Make the audio"]
    assert make["timeout-minutes"] == 6 and make["continue-on-error"] is True
    assert make["run"] == "python audio.py --brief in/brief.json --out out" and make["env"] == {"KOKORO_DIR": ".kokoro"}
    up = steps[-1]
    assert up["uses"] == "actions/upload-artifact@v4" and up["with"] == {"name": "audio", "path": "out", "retention-days": 1}
    assert "hashFiles('out/listen.mp3')" in up["if"] and "hashFiles('out/audio.json')" in up["if"]


def test_workflow_sha256_values_match_audio_py():
    run = steps_of(jobs()["audio"])["Get the voice model"]["run"]
    for name, want in audio.MODEL_FILES.items():
        assert re.search(rf"{want}\s+{re.escape(name)}", run), name
        assert f'"$base/{name}"' in run
    assert "base=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0\n" in run
    assert len(set(re.findall(r"\b[0-9a-f]{64}\b", run))) == 2


def test_model_folder_is_outside_site_and_ignored():
    ignored = (ROOT / ".gitignore").read_text().split()
    assert ".kokoro/" in ignored and "site/listen.mp3" in ignored
    assert not audio.load_config()["model_dir"].startswith("site")
    pages = [s for s in jobs()["assemble"]["steps"] if s.get("uses", "").startswith("actions/upload-pages-artifact")]
    assert len(pages) == 1 and pages[0]["with"]["path"] == "site"


def test_requirements_are_separate_and_pinned():
    main_reqs = (ROOT / "requirements.txt").read_text()
    assert "kokoro" not in main_reqs and "soundfile" not in main_reqs and "onnxruntime" not in main_reqs
    reqs = (ROOT / "requirements-audio.txt").read_text().split()
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+==\d+(\.\d+)*", r) for r in reqs), reqs
    assert {r.split("==")[0] for r in reqs} >= {"kokoro-onnx", "soundfile", "onnxruntime", "numpy"}
    assert "kokoro-onnx==0.6.1" in reqs
    src = (ROOT / "audio.py").read_text()
    top = src.split("def pronounce")[0]
    assert "import kokoro_onnx" not in top and "import soundfile" not in top and "from kokoro_onnx" not in top


def test_save_job_archives_the_paper_without_audio():
    paper = next(s for s in jobs()["build"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    assert paper["with"]["path"] == "site/brief.json"        # taken from the build, before any audio key exists


# ---------- the assemble job ----------

def assemble_script():
    job = jobs()["assemble"]
    run = steps_of(job)["Add the audio if it is good"]["run"]
    m = re.fullmatch(r"python3 - <<'PY'\n(.*)\nPY\n?", run, re.S)
    assert m, "the step must be one inline python3 script"
    return m.group(1)


def test_assemble_job_runs_no_packages_and_no_repo_code():
    job = jobs()["assemble"]
    assert job["permissions"] == {"contents": "read"}
    text = yaml.safe_dump(job)
    for bad in ("actions/checkout", "pip", "setup-python", "audio.py", "brief.py", "secrets.", "npm"):
        assert bad not in text, bad
    uses = [s.get("uses") for s in job["steps"] if "uses" in s]
    assert uses == ["actions/download-artifact@v4", "actions/download-artifact@v4", "actions/upload-pages-artifact@v3"]
    dl_site, dl_audio = job["steps"][0], job["steps"][1]
    assert dl_site["with"] == {"name": "site", "path": "site"} and "continue-on-error" not in dl_site
    assert dl_audio["with"] == {"name": "audio", "path": "audio_in"} and dl_audio["continue-on-error"] is True
    script = assemble_script()
    imports = set(re.findall(r"^\s*import ([\w, ]+)$", script, re.M))
    assert {m.strip() for line in imports for m in line.split(",")} <= {"json", "math", "os", "re", "shutil"}
    assert "${{" not in script and "subprocess" not in script


def run_assemble(tmp_path, mp3=FAKE_MP3 + b"\x00" * 20_000, meta=None, brief=None, as_symlink=False):
    """Run the inline script of the assemble job in a scratch folder. Returns (stdout, site folder)."""
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "brief.json").write_text(json.dumps(brief or {"generated_at": "x", "listen": "hi"}))
    (tmp_path / "audio_in").mkdir()
    if mp3 is not None:
        (tmp_path / "audio_in" / "listen.mp3").write_bytes(mp3)
    if meta is not None:
        (tmp_path / "audio_in" / "audio.json").write_text(meta if isinstance(meta, str) else json.dumps(meta))
    if as_symlink:
        (tmp_path / "audio_in" / "listen.mp3").unlink()
        (tmp_path / "audio_in" / "listen.mp3").symlink_to("/etc/hostname")
    r = subprocess.run([sys.executable, "-I", "-c", assemble_script()], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout, tmp_path / "site"


GOOD = {"url": "listen.mp3?v=1791795600", "seconds": 159.54, "voice": "Kokoro Michael"}


def test_assemble_adds_good_audio(tmp_path):
    out, site = run_assemble(tmp_path, meta={**GOOD, "extra": "dropped"})
    brief = saved(site)
    assert brief["audio"] == {"url": GOOD["url"], "seconds": 159.5, "voice": "Kokoro Michael"}   # only the 3 known keys
    assert brief["listen"] == "hi" and (site / "listen.mp3").read_bytes() == (tmp_path / "audio_in/listen.mp3").read_bytes()
    assert not list(site.glob("*.tmp")) and "audio: added" in out


def test_assemble_accepts_an_mpeg_frame_header(tmp_path):
    _, site = run_assemble(tmp_path, mp3=b"\xff\xf3\x84\xc4" + b"\x00" * 20_000, meta=GOOD)
    assert "audio" in saved(site)


def test_assemble_rejects_bad_audio_and_leaves_the_paper_alone(tmp_path_factory):
    big = b"ID3" + b"\x00" * 15_000_001
    cases = {
        "no files": dict(mp3=None, meta=None),
        "no meta": dict(meta=None),
        "too small": dict(mp3=b"ID3" + b"\x00" * 100, meta=GOOD),
        "too big": dict(mp3=big, meta=GOOD),
        "wav header": dict(mp3=b"RIFF" + b"\x00" * 20_000, meta=GOOD),
        "meta not json": dict(meta="{nope"),
        "meta is a list": dict(meta=[GOOD]),
        "url other file": dict(meta={**GOOD, "url": "evil.js?v=1"}),
        "url absolute": dict(meta={**GOOD, "url": "https://evil.example/listen.mp3?v=1"}),
        "url no number": dict(meta={**GOOD, "url": "listen.mp3?v=abc"}),
        "url bare": dict(meta={**GOOD, "url": "listen.mp3"}),
        "url trailing text": dict(meta={**GOOD, "url": "listen.mp3?v=1&x=<script>"}),
        "seconds zero": dict(meta={**GOOD, "seconds": 0}),
        "seconds huge": dict(meta={**GOOD, "seconds": 3601}),
        "seconds text": dict(meta={**GOOD, "seconds": "12"}),
        "seconds bool": dict(meta={**GOOD, "seconds": True}),
        "seconds nan": dict(meta='{"url": "listen.mp3?v=1", "seconds": NaN, "voice": "V"}'),
        "voice long": dict(meta={**GOOD, "voice": "x" * 41}),
        "voice empty": dict(meta={**GOOD, "voice": ""}),
        "voice number": dict(meta={**GOOD, "voice": 5}),
        "meta big": dict(meta={**GOOD, "pad": "x" * 5000}),
        "symlink": dict(meta=GOOD, as_symlink=True),
    }
    for label, kw in cases.items():
        folder = tmp_path_factory.mktemp("a")
        out, site = run_assemble(folder, **kw)
        assert "audio: not added" in out, label
        assert saved(site) == {"generated_at": "x", "listen": "hi"} and not (site / "listen.mp3").exists(), label


def test_assemble_survives_a_broken_brief_json(tmp_path):
    (tmp_path / "audio_in").mkdir()
    (tmp_path / "audio_in" / "listen.mp3").write_bytes(FAKE_MP3 + b"\x00" * 20_000)
    (tmp_path / "audio_in" / "audio.json").write_text(json.dumps(GOOD))
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "brief.json").write_text("{ broken")
    r = subprocess.run([sys.executable, "-I", "-c", assemble_script()], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "not added" in r.stdout
    assert not (tmp_path / "site" / "listen.mp3").exists() and (tmp_path / "site" / "brief.json").read_text() == "{ broken"


def test_assemble_works_with_no_audio_folder_at_all(tmp_path):
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "brief.json").write_text("{}")
    r = subprocess.run([sys.executable, "-I", "-c", assemble_script()], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "no audio files" in r.stdout


def test_audio_py_output_passes_the_assemble_check(tmp_path, monkeypatch):
    """audio.py in out mode and the assemble script agree on the format."""
    inn = brief_file(tmp_path, "in")
    out = tmp_path / "out"
    monkeypatch.setattr(audio, "load_config", lambda path=None: dict(audio.DEFAULTS))
    fake_models(tmp_path, monkeypatch)
    mock_voice(monkeypatch, seconds=20)
    monkeypatch.setattr(audio, "encode_mp3", lambda s, r, path, b: Path(path).write_bytes(FAKE_MP3 + b"\x00" * 20_000))
    assert audio.main(["--brief", str(inn / "brief.json"), "--out", str(out)]) == 0
    work = tmp_path / "work"
    work.mkdir()
    shutil.copytree(out, work / "audio_in")
    (work / "site").mkdir()
    (work / "site" / "brief.json").write_text((inn / "brief.json").read_text())
    r = subprocess.run([sys.executable, "-I", "-c", assemble_script()], cwd=work, capture_output=True, text=True)
    assert "audio: added" in r.stdout, r.stdout + r.stderr
    assert saved(work / "site")["audio"]["url"] == f"listen.mp3?v={STAMP}"
