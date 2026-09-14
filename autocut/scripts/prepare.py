# -*- coding: utf-8 -*-
"""Шаг 1. Готовим сырьё к разбору.

Из исходного файла достаём моно-дорожку 16 кГц — на ней работают
и расшифровка, и карта речи. Сам исходник не трогаем.

    python prepare.py <файл> [папка-проекта]

Кладёт <проект>/work/audio.wav и печатает, что за материал пришёл.
"""
from __future__ import annotations

import sys
from pathlib import Path

from common import (duration, ffmpeg, fmt_time, has_video, run, stdout_utf8,
                    video_fps)


def main() -> int:
    stdout_utf8()
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"нет файла: {src}")
        return 2

    project = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) > 2 \
        else src.parent / (src.stem + "-autocut")
    work = project / "work"
    work.mkdir(parents=True, exist_ok=True)

    dur = duration(src)
    video = has_video(src)
    print(f"исходник: {src.name}")
    print(f"длина:    {fmt_time(dur)} ({dur:.1f} с)")
    if video:
        print(f"видео:    есть, {video_fps(src):.3g} кадр/с")
    else:
        print("видео:    нет, только звук")

    wav = work / "audio.wav"
    run([ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    print(f"\nдорожка для разбора: {wav}")
    print(f"проект: {project}")
    print("\nдальше: transcribe.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
