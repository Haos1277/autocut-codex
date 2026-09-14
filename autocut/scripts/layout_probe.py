# -*- coding: utf-8 -*-
"""Шаг 8а. Где в кадре говорящий и можно ли делить кадр.

    python layout_probe.py <видео> [--frames 40] [--json]

Прежде чем делить кадр на «ведущий сверху, вставка снизу», надо знать,
влезет ли ведущий в отведённую полосу. Если человек занимает кадр целиком
и снизу торчит микрофон, обрезка отрежет ему рот — на этом мы уже
наступали.

Ищем не человека, а ЦЕНУ РЕЗА. Границу «где заканчивается человек» по
движению надёжно не найти: руки и микрофон шевелятся сильнее лица, и любая
такая граница расползается почти на весь кадр (проверено — на четырёх
разных роликах выходило 5-95% высоты, то есть бесполезно).

Работающая мера другая: как движение распределено по высоте кадра, где
его самое живое место и сколько активности уедет за кадр при резе.
Если самое живое место ниже линии реза — резать нельзя, срежет главное.

Ни распознавания лиц, ни нейросетей не нужно, и работает на любом кадре,
включая те, где лица не видно вовсе (со спины, в маске, за экраном).
Кадры читаем сырыми через ffmpeg, без библиотек для картинок.

Печатает вердикт для каждой раскладки: делить можно, или только вписывать
целиком, или только накладывать вставку поверх.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from common import duration, ffmpeg, has_video, stdout_utf8

PROBE_W, PROBE_H = 108, 192   # мелко и быстро, пропорции 9:16 сохранены
SAFE = 0.04                   # запас: 4% высоты кадра


def sample_gray(src: Path, count: int) -> np.ndarray:
    """count кадров в серости, как массив (count, H, W)."""
    dur = max(duration(src), 0.1)
    fps = max(count / dur, 0.1)
    cmd = [ffmpeg(), "-v", "error", "-i", str(src),
           "-vf", f"fps={fps:.4f},scale={PROBE_W}:{PROBE_H}",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    res = subprocess.run(cmd, capture_output=True)
    frame_bytes = PROBE_W * PROBE_H
    n = len(res.stdout) // frame_bytes
    if n == 0:
        sys.exit("не удалось прочитать кадры")
    data = np.frombuffer(res.stdout[:n * frame_bytes], dtype=np.uint8)
    return data.reshape(n, PROBE_H, PROBE_W).astype(np.float32)


def motion_profile(frames: np.ndarray) -> tuple[np.ndarray, int]:
    """Сколько движения приходится на каждую строку кадра.

    Пары кадров со склейкой выбрасываем: на монтажной склейке меняется
    весь кадр целиком, и профиль движения становится ровным — из него
    уже не понять, где человек.
    """
    if len(frames) < 2:
        return np.zeros(PROBE_H), 0
    diffs = np.abs(np.diff(frames, axis=0))               # (n-1, H, W)
    per_pair = diffs.mean(axis=(1, 2))
    med = float(np.median(per_pair)) or 1.0
    keep = per_pair < med * 3.0
    dropped = int((~keep).sum())
    if keep.sum() == 0:
        keep = np.ones_like(keep)
        dropped = 0
    prof = diffs[keep].mean(axis=0).mean(axis=1)
    # шум сжатия ровным слоем лежит по всему кадру — вычитаем его,
    # иначе он размывает картину и «движение» оказывается везде
    prof = np.clip(prof - np.percentile(prof, 15), 0, None)
    k = 9
    pad = np.pad(prof, k // 2, mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid"), dropped


def mass_below(prof: np.ndarray, share: float) -> float:
    """Какая доля всего движения окажется ниже линии реза."""
    total = prof.sum()
    if total <= 0:
        return 0.0
    cut = int(len(prof) * share)
    return float(prof[cut:].sum() / total)


def peak_row(prof: np.ndarray) -> float:
    return float(np.argmax(prof) / len(prof))


def verdict(prof: np.ndarray, share: float) -> tuple[str, str, float]:
    """Можно ли отдать ведущему верхние share кадра, обрезав остальное.

    Решает не «где заканчивается человек» — эту границу по движению
    надёжно не найти, руки и микрофон шевелятся сильнее лица. Решает
    цена реза: сколько живого содержания уедет за кадр.
    """
    lost = mass_below(prof, share)
    if peak_row(prof) > share - SAFE:
        return "поверх", (f"самое живое место кадра ниже линии реза "
                          f"— срежет главное"), lost
    if lost <= 0.15:
        return "обрезать", f"за кадром останется {lost:.0%} активности", lost
    if lost <= 0.30:
        return "вписать", (f"резать жалко ({lost:.0%} активности), "
                           f"вписать кадр целиком"), lost
    return "поверх", f"рез унесёт {lost:.0%} активности, делить нельзя", lost


def main() -> int:
    stdout_utf8()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    src = Path(args[0]).expanduser().resolve()
    if not src.exists():
        print(f"нет файла: {src}")
        return 2
    if not has_video(src):
        print("в файле нет видео")
        return 2

    count = int(args[args.index("--frames") + 1]) if "--frames" in args else 40
    frames = sample_gray(src, count)
    prof, dropped = motion_profile(frames)
    peak = peak_row(prof)

    report = {
        "file": src.name,
        "кадров_просмотрено": int(len(frames)),
        "склеек_отброшено": dropped,
        "самое_живое_место": round(peak, 3),
        "раскладки": {},
    }

    print(f"файл: {src.name}")
    print(f"просмотрено кадров: {len(frames)}"
          + (f", отброшено склеек: {dropped}" if dropped else ""))
    print(f"самое живое место кадра: {peak * 100:.0f}% высоты\n")

    # столбик движения по высоте — видно глазами, где человек
    top_val = prof.max() or 1.0
    step = max(len(prof) // 24, 1)
    for i in range(0, len(prof), step):
        y = i / len(prof)
        bar = "#" * int(prof[i] / top_val * 34)
        mark = " <-- самое живое" if abs(y - peak) < 0.03 else ""
        print(f"  {y * 100:3.0f}% |{bar}{mark}")

    print()
    for name, share in (("50/50", 0.50), ("70/30", 0.70)):
        how, why, lost = verdict(prof, share)
        report["раскладки"][name] = {"как": how, "потеряем": round(lost, 3)}
        print(f"{name:6s} ведущий сверху {share * 100:.0f}%: {how:9s} — {why}")
    report["раскладки"]["фуллскрин"] = {"как": "обрезать", "потеряем": 0.0}
    print(f"{'фуллскрин':6s} вставка на весь кадр: годится всегда")

    if report["раскладки"]["70/30"]["как"] == "поверх":
        print("\nВНИМАНИЕ: делить кадр у этого исходника нельзя ни в какой "
              "пропорции.\nВставки класть ПОВЕРХ картинки, не сжимая ведущего.")

    if "--json" in args:
        print("\n" + json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())