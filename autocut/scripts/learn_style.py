# -*- coding: utf-8 -*-
"""Снять настройки реза с готовых роликов автора.

    python learn_style.py <папка-с-роликами> <проект> [ru|en]

Вместо того чтобы спрашивать «насколько плотно резать», можно посмотреть,
как человек уже режет сам. Скрипт разбирает 3-5 его выпущенных роликов
и меряет:

  - какие паузы он оставляет между фразами;
  - темп речи;
  - как часто у него в готовом ролике остаются слова-паразиты;
  - как часто он меняет план (по смене картинки).

Из этого получаются настройки, которые дадут результат в его манере,
а не в чужой. Пишет <проект>/style.json.

Ролики нужны ГОТОВЫЕ, уже смонтированные, а не сырьё: мы снимаем
привычки монтажа, а не привычки речи.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from common import (duration, ffmpeg, has_video, run, stdout_utf8)
from rough_cut import FILLERS, norm
from speech_map import speech_regions

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"}


def scene_cuts(src: Path) -> int:
    """Сколько раз в ролике меняется картинка. Считает ffmpeg."""
    if not has_video(src):
        return 0
    res = run([ffmpeg(), "-i", str(src), "-filter:v",
               "select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
              quiet=True)
    return (res.stderr or "").count("pts_time")


def transcribe_file(wav: Path, lang: str) -> None:
    """Расшифровка примера. Если faster-whisper не стоит — молча пропускаем,
    остальные замеры (паузы, темп, смена плана) от неё не зависят."""
    try:
        import os

        from faster_whisper import WhisperModel
    except ImportError:
        return
    model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 4)
    kwargs = dict(word_timestamps=True, vad_filter=False, beam_size=5)
    if lang != "auto":
        kwargs["language"] = lang
    segments, _ = model.transcribe(str(wav), **kwargs)
    lines = []
    for s in segments:
        lines.append(f"[{s.start:7.2f} - {s.end:7.2f}] {s.text.strip()}")
        for w in s.words or []:
            lines.append(f"    {w.start:7.2f} {w.end:7.2f} {w.word.strip()}")
    wav.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyse(src: Path, work: Path, lang: str) -> dict | None:
    wav = work / (src.stem + ".wav")
    if not wav.exists():
        run([ffmpeg(), "-y", "-v", "error", "-i", str(src),
             "-vn", "-ac", "1", "-ar", "16000", str(wav)])

    total = duration(src)
    regions = speech_regions(wav, gap=0.25)
    if len(regions) < 2:
        print(f"  {src.name}: речь идёт сплошняком, пауз для замера нет")
        return None

    pauses = [b_start - a_end
              for (_, a_end), (b_start, _) in zip(regions, regions[1:])]
    speech = sum(b - a for a, b in regions)

    # Паразиты считаем только по расшифровке. Без неё счётчик даст ноль
    # и скрипт соврёт «автор вычищает паразитов» на пустом месте.
    txt = wav.with_suffix(".txt")
    if not txt.exists():
        transcribe_file(wav, lang)
    words, fillers = [], None
    if txt.exists():
        from common import read_words
        words = [w for w in (norm(x["word"]) for x in read_words(txt)) if w]
        flt = FILLERS.get(lang, set())
        fillers = sum(1 for w in words if w in flt)

    return {
        "файл": src.name,
        "длина": round(total, 1),
        "пауз": len(pauses),
        "пауза_медиана": round(statistics.median(pauses), 3),
        "пауза_90": round(sorted(pauses)[int(len(pauses) * 0.9)], 3),
        "доля_речи": round(speech / total, 3),
        "слов": len(words),
        "темп": round(len(words) / (speech / 60), 1) if speech else 0,
        "паразитов_на_минуту": (round(fillers / (total / 60), 1)
                                if fillers is not None and total else None),
        "смен_плана": scene_cuts(src),
    }


def main() -> int:
    stdout_utf8()
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    folder = Path(sys.argv[1]).expanduser().resolve()
    project = Path(sys.argv[2]).expanduser().resolve()
    lang = sys.argv[3] if len(sys.argv) > 3 else "ru"

    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in VIDEO_EXT | AUDIO_EXT)
    if not files:
        print(f"в {folder} нет видео или аудио")
        return 2

    work = project / "work" / "style"
    work.mkdir(parents=True, exist_ok=True)
    print(f"роликов на разбор: {len(files)}\n")

    rows = []
    for f in files:
        print(f"разбираю {f.name}...", flush=True)
        r = analyse(f, work, lang)
        if r:
            rows.append(r)
            print(f"  пауза медиана {r['пауза_медиана']} с, темп {r['темп']} "
                  f"слов/мин, речи {r['доля_речи']:.0%}, "
                  f"смен плана {r['смен_плана']}")

    if not rows:
        print("\nничего измерить не удалось")
        return 1

    med = statistics.median([r["пауза_медиана"] for r in rows])
    p90 = statistics.median([r["пауза_90"] for r in rows])
    tempo = statistics.median([r["темп"] for r in rows])
    junk_vals = [r["паразитов_на_минуту"] for r in rows
                 if r["паразитов_на_минуту"] is not None]
    junk = statistics.median(junk_vals) if junk_vals else None
    shots = [r["смен_плана"] / r["длина"] * 60 for r in rows if r["смен_плана"]]

    print("\n" + "=" * 58)
    print(f"типичная пауза между фразами: {med:.2f} с")
    print(f"длинные паузы (9 из 10 короче): {p90:.2f} с")
    print(f"темп речи: {tempo:.0f} слов в минуту")
    if junk is None:
        print("слов-паразитов: не измерено (нет расшифровки)")
    else:
        print(f"слов-паразитов остаётся: {junk:.1f} на минуту")
    if shots:
        print(f"смена плана: {statistics.median(shots):.1f} раз в минуту")

    # Порог реза ставим по его же типичной паузе, чуть плотнее.
    # Резать плотнее, чем человек привык — самый частый способ
    # испортить материал чужой манерой.
    max_pause = round(max(med * 0.9, 0.15), 2)
    cut_fillers = None if junk is None else junk < 3.0

    style = {
        "lang": lang,
        "max_pause": max_pause,
        "cut_fillers": cut_fillers,
        "extra_fillers": [],
        "измерено_по": [r["файл"] for r in rows],
        "замеры": rows,
    }
    project.mkdir(parents=True, exist_ok=True)
    (project / "style.json").write_text(
        json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\nпредлагаемые настройки:")
    print(f"  --pause {max_pause}")
    if cut_fillers is None:
        print("  паразиты резать: не определено, спросить у автора")
    elif cut_fillers:
        print("  паразиты резать: да (в его роликах их почти нет — "
              "значит он их вычищает)")
    else:
        print("  паразиты резать: НЕТ (он их оставляет, это часть манеры)")
    print(f"\nсохранено: {project / 'style.json'}")
    print("Покажи эти цифры автору и спроси, согласен ли он.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
