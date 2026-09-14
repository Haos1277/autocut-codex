# -*- coding: utf-8 -*-
"""Шаг 2. Расшифровка с пословными таймкодами.

    python transcribe.py <проект>/work/audio.wav [ru|en|auto] [модель]

Рядом появится audio.txt: строки сегментов, под каждой — слова с временем.
Их читают speech_map.py и rough_cut.py.

Модель по умолчанию large-v3-turbo. Если машина слабая — small или base:
качество ниже, зато в несколько раз быстрее.

Считает на процессоре. Это медленнее видеокарты, но работает одинаково
на любой машине, включая мак: ускорители у Whisper через CUDA, а её
на маке нет.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from common import stdout_utf8


def main() -> int:
    stdout_utf8()
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    wav = Path(sys.argv[1]).expanduser().resolve()
    lang = sys.argv[2] if len(sys.argv) > 2 else "auto"
    model_name = sys.argv[3] if len(sys.argv) > 3 else "large-v3-turbo"
    if not wav.exists():
        print(f"нет файла: {wav}")
        return 2

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("нет faster-whisper. Поставь: pip install -r requirements.txt")
        return 2

    print(f"модель {model_name}, первый запуск качает веса (~1,5 ГБ)...",
          flush=True)
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 4)

    kwargs = dict(word_timestamps=True, vad_filter=False, beam_size=5)
    if lang != "auto":
        kwargs["language"] = lang

    t0 = time.time()
    segments, info = model.transcribe(str(wav), **kwargs)
    print(f"язык: {info.language} (уверенность {info.language_probability:.2f})\n",
          flush=True)

    lines = []
    for s in segments:
        lines.append(f"[{s.start:7.2f} - {s.end:7.2f}] {s.text.strip()}")
        print(lines[-1], flush=True)
        for w in s.words or []:
            lines.append(f"    {w.start:7.2f} {w.end:7.2f} {w.word.strip()}")

    out = wav.with_suffix(".txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nсохранено: {out}  (за {time.time() - t0:.0f} с)")
    print("дальше: speech_map.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
