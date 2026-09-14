# -*- coding: utf-8 -*-
"""Общее для всех шагов: поиск ffmpeg, пути, чтение расшифровки.

Работает одинаково на Windows, macOS и Linux: никаких абсолютных путей
и букв дисков, ffmpeg ищется в PATH.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def stdout_utf8() -> None:
    """Windows-консоль по умолчанию не в utf-8 — кириллица ломается."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit(
            "ffmpeg не найден в PATH.\n"
            "  macOS:   brew install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )
    return exe


def ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        sys.exit("ffprobe не найден в PATH (ставится вместе с ffmpeg).")
    return exe


def run(cmd: list[str], quiet: bool = True,
        cwd: Path | None = None) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=quiet, text=True,
                         encoding="utf-8", errors="replace",
                         cwd=str(cwd) if cwd else None)
    if res.returncode != 0:
        err = res.stderr or ""
        # Хвоста вывода ffmpeg мало: настоящая причина обычно в первой
        # жалобе, а в хвосте лежат её последствия (ловили — реальную
        # ошибку геометрии скрыло сообщение про звук). Поэтому сначала
        # показываем строки, похожие на корень проблемы.
        keys = ("Invalid", "error", "Error", "failed", "does not", "cannot",
                "No such", "out of range", "not divisible")
        first = [ln for ln in err.splitlines() if any(k in ln for k in keys)]
        head = "\n".join(first[:6])
        sys.exit(f"команда упала: {' '.join(cmd[:3])} ...\n"
                 + (head + "\n---\n" if head else "") + err[-900:])
    return res


def probe(path: Path, entries: str, stream: str = "v:0") -> list[str]:
    """Значения из ffprobe, по одному на строку, уже очищенные.

    Формат csv использовать нельзя: на записи с телефона ffprobe отдал
    «30/1,» — с висячей запятой и возвратом каретки, и разбор упал прямо
    на первом шаге. Формат default с nk=1 печатает только значения,
    по одному в строке, без разделителей.
    """
    res = run([ffprobe(), "-v", "error", "-select_streams", stream,
               "-show_entries", f"stream={entries}",
               "-of", "default=nw=1:nk=1", str(path)])
    return [ln.strip().rstrip(",") for ln in res.stdout.splitlines() if ln.strip()]


def duration(path: Path) -> float:
    res = run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(res.stdout.strip().rstrip(","))


def has_video(path: Path) -> bool:
    return "video" in probe(path, "codec_type")


def video_start_time(path: Path) -> float:
    """Смещение первого кадра. У многих файлов поток начинается не с нуля
    (ловили 0,021 с), и сетка кадров, посчитанная от нуля, промахивается
    мимо реальных меток — куски выходят на кадр короче звука."""
    vals = probe(path, "start_time")
    try:
        return float(vals[0])
    except (IndexError, ValueError):
        return 0.0


def video_fps(path: Path) -> float:
    vals = probe(path, "r_frame_rate")
    raw = vals[0] if vals else ""
    try:
        if "/" in raw:
            num, den = raw.split("/")[:2]
            return float(num) / float(den) if float(den) else 30.0
        return float(raw)
    except ValueError:
        return 30.0


def video_size(path: Path) -> tuple[int, int]:
    vals = probe(path, "width,height")
    try:
        return int(vals[0]), int(vals[1])
    except (IndexError, ValueError):
        return 1080, 1920


# ------------------------------------------------------------- расшифровка

WORD_RE = re.compile(r"^\s+([\d.]+)\s+([\d.]+)\s+(.*)$")
SEG_RE = re.compile(r"^\[\s*([\d.]+)\s*-\s*([\d.]+)\]\s*(.*)$")


def read_words(txt: Path) -> list[dict]:
    """Пословные таймкоды из файла, который пишет transcribe.py."""
    out = []
    for line in txt.read_text(encoding="utf-8").splitlines():
        m = WORD_RE.match(line)
        if m:
            out.append({"start": float(m.group(1)), "end": float(m.group(2)),
                        "word": m.group(3).strip()})
    return out


def read_segments(txt: Path) -> list[dict]:
    out = []
    for line in txt.read_text(encoding="utf-8").splitlines():
        m = SEG_RE.match(line)
        if m:
            out.append({"start": float(m.group(1)), "end": float(m.group(2)),
                        "text": m.group(3).strip()})
    return out


def load_style(project: Path) -> dict:
    """Стиль пользователя. Пусто — значит интейк ещё не проходили."""
    f = project / "style.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def fmt_time(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m):d}:{s:05.2f}"
