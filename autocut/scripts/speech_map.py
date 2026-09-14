# -*- coding: utf-8 -*-
"""Шаг 3. Карта речи: где человек говорит, где молчит.

    python speech_map.py <проект>/work/audio.wav [пауза-с]

Почему по громкости, а не по расшифровке: Whisper на границах пауз врёт
на сотни миллисекунд и режет слова. Огибающая громкости показывает край
слова точно, поэтому границы кусков берём от неё, а текст из расшифровки
просто раскладываем по готовым кускам.

Порог не абсолютный, а от самой записи: между её собственной тишиной
и её пиками. Поэтому одинаково работает и на тихой запиcи с телефона,
и на громкой студийной.

Пишет work/speech.json — его читает rough_cut.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from common import fmt_time, read_words, stdout_utf8

WIN = 0.02          # шаг анализа, с
MIN_SPEECH = 0.30   # короче — это щелчок или вдох, не речь
THR_FRAC = 0.35     # доля пути от тишины к пикам


def envelope(wav: Path):
    sr, data = wavfile.read(str(wav))
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float32) / float(np.iinfo(data.dtype).max)
    else:
        data = data.astype(np.float32)
    n = max(int(sr * WIN), 1)
    frames = len(data) // n
    if frames == 0:
        return np.array([]), sr
    rms = np.sqrt((data[:frames * n].reshape(frames, n) ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms), sr


def speech_regions(wav: Path, gap: float = 0.45) -> list[tuple[float, float]]:
    db, _ = envelope(wav)
    if db.size == 0:
        return []
    floor = float(np.percentile(db, 20))
    peak = float(np.percentile(db, 95))
    thr = floor + (peak - floor) * THR_FRAC
    voiced = db > thr

    regions = []
    i, frames = 0, len(db)
    while i < frames:
        if voiced[i]:
            j, silence = i, 0
            while j < frames:
                if voiced[j]:
                    silence = 0
                else:
                    silence += 1
                    if silence * WIN > gap:
                        break
                j += 1
            regions.append((i * WIN, (j - silence) * WIN))
            i = j
        i += 1
    return [(a, b) for a, b in regions if b - a >= MIN_SPEECH]


def assign_words(regions, words) -> dict[int, list[str]]:
    """Каждое слово — в тот кусок, с которым оно больше всего пересекается.

    Ни по попаданию целиком, ни по середине нельзя: Whisper растягивает
    слово на соседнюю паузу (ловили слово длиной 1,76 с на границе), и кусок
    оставался с пустым текстом. Если пересечения нет вообще — отдаём
    ближайшему куску, чтобы слово не потерялось.
    """
    buckets: dict[int, list[str]] = {i: [] for i in range(len(regions))}
    for w in words:
        best, best_ov = None, 0.0
        for i, (a, b) in enumerate(regions):
            ov = min(w["end"], b) - max(w["start"], a)
            if ov > best_ov:
                best, best_ov = i, ov
        if best is None:
            mid = (w["start"] + w["end"]) / 2
            best = min(range(len(regions)),
                       key=lambda i: min(abs(mid - regions[i][0]),
                                         abs(mid - regions[i][1])))
        buckets[best].append(w["word"])
    return buckets


def main() -> int:
    stdout_utf8()
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    wav = Path(sys.argv[1]).expanduser().resolve()
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 0.45
    if not wav.exists():
        print(f"нет файла: {wav}")
        return 2

    regions = speech_regions(wav, gap)
    txt = wav.with_suffix(".txt")
    words = read_words(txt) if txt.exists() else []

    total = sum(b - a for a, b in regions)
    print(f"кусков речи: {len(regions)}   пауза-шов от {gap} с")
    print(f"речи всего:  {fmt_time(total)}\n")

    buckets = assign_words(regions, words)

    out = []
    for idx, (a, b) in enumerate(regions):
        text = " ".join(buckets[idx])
        out.append({"start": round(a, 3), "end": round(b, 3), "text": text})
        print(f"{fmt_time(a)}-{fmt_time(b)} ({b - a:5.1f}с)  {text[:90]}")

    dst = wav.parent / "speech.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nсохранено: {dst}")
    print("дальше: rough_cut.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
