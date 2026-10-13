#!/usr/bin/env python3
"""Daily audio for the paper.

Reads brief.json, speaks its "listen" text with Kokoro (a small voice model that runs on the CPU) and writes
listen.mp3. The MP3 is written with soundfile (no ffmpeg needed). ffmpeg is used only if soundfile cannot do it.

Two ways to run:
    python audio.py --brief in/brief.json --out out
        The workflow way. Reads the given brief.json and writes out/listen.mp3 and out/audio.json
        ({"url", "seconds", "voice"}). It never writes into site/ and never edits brief.json.
    python audio.py
        The local way. Reads site/brief.json, writes site/listen.mp3, and adds an "audio" key to site/brief.json.

This runs in its own job, after the paper is built. Whatever happens here, the paper is already built, so every
problem prints one short reason and exits 0, and no half-made file or old "audio" key is left behind.

Needs: pip install -r requirements-audio.txt and the two model files (see MODEL_FILES).
The model folder is KOKORO_DIR, else `audio.model_dir` in sources.yaml. BRIEF_SITE_DIR moves site/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent
SITE_DIR = Path(os.environ.get("BRIEF_SITE_DIR") or ROOT / "site")   # tests point this at a scratch folder
MP3_NAME = "listen.mp3"
VOICE_LABEL = "Kokoro Michael"
FFMPEG_TIMEOUT = 120   # seconds
AUDIO_JSON = "audio.json"

# The model files never change (release tag model-files-v1.0 of thewh1teagle/kokoro-onnx). A file is used only
# if its sha256 matches. The workflow checks the same values after it downloads them.
MODEL_FILES = {
    "kokoro-v1.0.fp16.onnx": "c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
MODEL_FILE, VOICES_FILE = MODEL_FILES

DEFAULTS = {"enabled": True, "voice": "am_michael", "speed": 1.0, "lang": "en-us",
            "model_dir": ".kokoro", "bitrate": "64k", "pronounce": {}}

# Built-in spellings for the voice. The visible "listen" text is never changed.
BUILTIN_PRONOUNCE = {"Jr.": "Junior", "Sr.": "Senior", "U.S.": "U S", "&": "and"}


class Skip(Exception):
    """A normal reason to make no audio today. The message is printed, the exit code stays 0."""


# ---------- pronunciation ----------

def pronounce(text: str, extra: Any = None) -> str:
    """A copy of text with the spoken spellings applied.

    Keys match whole words only (no letter or digit directly before or after) and are case-sensitive.
    Entries from sources.yaml (`extra`) win over the built-in ones. Bad entries are ignored.
    One pass: a replacement is never replaced again.
    """
    rules = dict(BUILTIN_PRONOUNCE)
    if isinstance(extra, dict):
        rules.update({str(k): str(v) for k, v in extra.items()
                      if isinstance(k, str) and k.strip() and isinstance(v, (str, int, float))
                      and not isinstance(v, bool)})   # YAML turns a bare No or Yes into False or True
    if not rules:
        return text
    keys = sorted(rules, key=len, reverse=True)   # longest first, so "U.S.A." wins over "U.S."
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")

    def swap(m: re.Match) -> str:
        key, new = m.group(0), rules[m.group(0)]
        # A dotted word at the very end of a line also ends the sentence: keep that full stop.
        if key.endswith(".") and not new.endswith(".") and text[m.end():m.end() + 1] in ("", "\n"):
            new += "."
        return new

    return pattern.sub(swap, text)


# ---------- config and model files ----------

def load_config(path: Path | None = None) -> dict:
    """The audio block of sources.yaml with defaults. A missing or odd block gives the defaults."""
    cfg: dict = {}
    try:
        data = yaml.safe_load((path or ROOT / "sources.yaml").read_text(encoding="utf-8")) or {}
        block = data.get("audio") if isinstance(data, dict) else None
        cfg = block if isinstance(block, dict) else {}
    except Exception as exc:
        print(f"audio: sources.yaml not read ({type(exc).__name__}); using defaults")
    out = {**DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
    if not isinstance(out["pronounce"], dict):
        out["pronounce"] = {}
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def find_models(folder: Path) -> tuple[Path, Path]:
    """The two model paths, after both files are found and their sha256 values match. Else Skip."""
    paths = []
    for name, want in MODEL_FILES.items():
        p = folder / name
        if not p.is_file():
            raise Skip(f"model file missing: {p}")
        if sha256_of(p) != want:
            raise Skip(f"model file has the wrong sha256: {p.name}")
        paths.append(p)
    return paths[0], paths[1]


def model_folder(cfg: dict) -> Path:
    env = os.environ.get("KOKORO_DIR")
    folder = Path(env) if env else Path(str(cfg["model_dir"]))
    return folder if folder.is_absolute() else ROOT / folder


# ---------- speech ----------

def synthesize(text: str, model: Path, voices: Path, cfg: dict) -> tuple[Any, int]:
    """Speak the text. Returns (samples, sample rate).

    kokoro-onnx 0.6.1 cuts long text into batches itself (at punctuation, then at word breaks, under the
    model's phoneme limit), so the whole script goes in one call.
    """
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(model), str(voices))
    return kokoro.create(text, voice=cfg["voice"], speed=float(cfg["speed"]), lang=cfg["lang"])


# libsndfile sets the MP3 bitrate from a "compression level" (0 = biggest file, 1 = smallest). With
# bitrate_mode CONSTANT each level gives one fixed bitrate for 24 kHz mono. These levels were measured.
MP3_LEVELS = {32: 0.85, 48: 0.75, 56: 0.7, 64: 0.62, 80: 0.5, 96: 0.4, 112: 0.3, 128: 0.2}


def mp3_level(bitrate: Any) -> float:
    """The soundfile compression level for a bitrate such as "64k". An odd value gives 64k."""
    digits = re.sub(r"\D", "", str(bitrate))
    kbps = int(digits) if digits else 64
    return MP3_LEVELS[min(MP3_LEVELS, key=lambda k: abs(k - kbps))]


def looks_like_mp3(head: bytes) -> bool:
    """True for an ID3 tag or an MPEG audio frame sync at the start."""
    return head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)


def write_mp3(samples: Any, rate: int, path: Path, bitrate: str) -> None:
    """Mono MP3 straight from soundfile (its wheel bundles libsndfile 1.2 with MP3). No ffmpeg."""
    import soundfile

    if "MP3" not in soundfile.available_formats():
        raise RuntimeError("this soundfile has no MP3 support")
    soundfile.write(str(path), samples, rate, format="MP3", subtype="MPEG_LAYER_III",
                    compression_level=mp3_level(bitrate), bitrate_mode="CONSTANT")


def write_mp3_ffmpeg(samples: Any, rate: int, path: Path, bitrate: str) -> None:
    """The fallback: a WAV file, then ffmpeg (mono, 24 kHz)."""
    import soundfile

    with tempfile.TemporaryDirectory(prefix="kokoro-") as tmp:
        wav = Path(tmp) / "listen.wav"
        soundfile.write(str(wav), samples, rate)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-ar", "24000",
               "-codec:a", "libmp3lame", "-b:a", str(bitrate), "-f", "mp3", str(path)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=FFMPEG_TIMEOUT)


def encode_mp3(samples: Any, rate: int, path: Path, bitrate: str) -> None:
    """Soundfile first. ffmpeg only if soundfile fails and ffmpeg is on PATH."""
    try:
        write_mp3(samples, rate, path, bitrate)
    except Exception as first:
        if not shutil.which("ffmpeg"):
            raise
        print(f"audio: soundfile MP3 failed ({type(first).__name__}); using ffmpeg")
        write_mp3_ffmpeg(samples, rate, path, bitrate)


def make_mp3(text: str, model: Path, voices: Path, cfg: dict, out: Path) -> float:
    """Speak the text into the MP3 file `out`. Returns the length in seconds. Leaves nothing half-written."""
    part = out.with_name(out.name + ".part")
    try:
        samples, rate = synthesize(text, model, voices, cfg)
        seconds = len(samples) / rate
        if seconds <= 0:
            raise RuntimeError("the voice made no sound")
        out.parent.mkdir(parents=True, exist_ok=True)
        encode_mp3(samples, rate, part, str(cfg["bitrate"]))   # the file gets its real name only when whole
        if not part.is_file() or part.stat().st_size == 0:
            raise RuntimeError("no MP3 file was made")
        with open(part, "rb") as f:
            if not looks_like_mp3(f.read(4)):
                raise RuntimeError("the file is not an MP3")
        os.replace(part, out)
        return seconds
    finally:
        part.unlink(missing_ok=True)


# ---------- brief.json ----------

def audio_url(generated_at: Any) -> str:
    """listen.mp3?v=<unix seconds of generated_at>. The number changes with each edition, so no old copy is used."""
    try:
        stamp = int(datetime.fromisoformat(str(generated_at)).timestamp())
    except ValueError:
        stamp = int(time.time())
    return f"{MP3_NAME}?v={stamp}"


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def clear_outputs(brief_path: Path, out_dir: Path | None) -> None:
    """Remove every audio result: the MP3, a half-made MP3, audio.json, and (local mode) an old "audio" key."""
    folder = out_dir or brief_path.parent
    for name in (MP3_NAME, MP3_NAME + ".part") + ((AUDIO_JSON,) if out_dir else ()):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            pass
    if out_dir is None:
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            if isinstance(brief, dict) and "audio" in brief:
                del brief["audio"]
                write_json_atomic(brief_path, brief)
        except Exception:
            pass


def run(brief_path: Path, out_dir: Path | None) -> None:
    """Do the work. Raises Skip for a normal reason, or any other error for a surprise."""
    cfg = load_config()
    if not cfg["enabled"]:
        raise Skip("switched off in sources.yaml")
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Skip(f"{brief_path} not found") from None
    listen = brief.get("listen") if isinstance(brief, dict) else None
    if not isinstance(listen, str) or not listen.strip():
        raise Skip("no listen text")
    model, voices = find_models(model_folder(cfg))

    spoken = pronounce(listen.strip(), cfg["pronounce"])
    out = (out_dir or brief_path.parent) / MP3_NAME
    started = time.time()
    seconds = make_mp3(spoken, model, voices, cfg, out)
    took = time.time() - started

    info = {"url": audio_url(brief.get("generated_at")), "seconds": round(seconds, 1), "voice": VOICE_LABEL}
    if out_dir is not None:
        write_json_atomic(out_dir / AUDIO_JSON, info)
    else:
        brief["audio"] = info
        write_json_atomic(brief_path, brief)
    print(f"audio: {seconds:.1f} s of speech, {out.stat().st_size / 1e6:.2f} MB, "
          f"made in {took:.1f} s -> {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make the daily audio.")
    ap.add_argument("--brief", help="brief.json to read (default: site/brief.json)")
    ap.add_argument("--out", help="folder for listen.mp3 and audio.json; brief.json is then not changed")
    try:
        args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        print("audio: skipped, bad arguments")
        return 0
    brief_path = Path(args.brief) if args.brief else SITE_DIR / "brief.json"
    out_dir = Path(args.out) if args.out else None
    done = False
    status = "made"
    try:
        clear_outputs(brief_path, out_dir)   # an old result must never survive a run that makes none
        run(brief_path, out_dir)
        done = True
    except Skip as why:
        status = f"skipped, {why}"
        print(f"audio: {status}")
    except Exception as exc:
        status = f"skipped, {type(exc).__name__}: {str(exc)[:140]}"
        print(f"audio: {status}")   # never stop the paper
    if not done:
        clear_outputs(brief_path, out_dir)
    write_status(out_dir, status)
    return 0


def write_status(out_dir: Path | None, status: str) -> None:
    """In out-mode, leave a one-line status for the assemble job (it shows in brief.json as audio_status)."""
    if out_dir is None:
        return
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        line = "".join(c for c in status if c.isprintable())[:200]
        (out_dir / "status.txt").write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
