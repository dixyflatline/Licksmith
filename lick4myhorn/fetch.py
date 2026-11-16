"""Download a recording (YouTube or any yt-dlp site) and keep a local WAV for analysis."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def fetch(url: str, work_dir: Path, on_progress=None) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError("yt-dlp is not installed (run setup_mac.sh).") from e

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(work_dir / "download.%(ext)s"),
        "quiet": False,
        "noplaylist": True,
    }
    if on_progress:
        def hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                on_progress(d.get("downloaded_bytes", 0) / total if total else 0.0, total)
        opts["progress_hooks"] = [hook]
        opts["quiet"] = True
        opts["no_warnings"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = Path(ydl.prepare_filename(info))
    wav = work_dir / "audio.wav"
    if on_progress:
        on_progress(1.0, -1)  # downloaded; converting next
    to_wav(downloaded, wav)
    (work_dir / "source.json").write_text(json.dumps(
        {"url": url, "title": info.get("title"), "duration": info.get("duration"),
         "uploader": info.get("uploader"), "file": downloaded.name}, indent=1))
    return wav


def to_wav(src: Path, wav: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed (brew install ffmpeg).")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "44100", str(wav)], check=True)
    return wav
