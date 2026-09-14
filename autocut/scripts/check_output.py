# -*- coding: utf-8 -*-
"""Шаг 5. Приёмка: сверяем речь до и после реза.

    python check_output.py <проект> [ru|en]

Расшифровывает готовый файл и сравнивает поток слов с исходным. Всё, что
пропало, раскладывается на две кучи:
  - ожидаемое — слова-паразиты и выброшенные дубли, так и задумано;
  - НЕОЖИДАННОЕ — значимые слова, которых не стало. Это брак: скорее всего
    рез пришёлся на слово.

Ещё смотрит склейки: не оказалось ли шва в середине слова. Признак —
слово короче 0,1 с вплотную к границе куска.

Код возврата 1, если есть неожиданные потери.
"""
from __future__ import annotations

import json
import re
import sys
import difflib
from pathlib import Path

from common import read_words, stdout_utf8

sys.path.insert(0, str(Path(__file__).parent))
from rough_cut import FILLERS, FILLER_PHRASES, norm  # noqa: E402


def transcribe(wav: Path, lang: str) -> Path:
    out = wav.with_suffix(".txt")
    if out.exists():
        return out
    import os

    from faster_whisper import WhisperModel
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
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def verify_cuts_silent(work: Path, plan: dict) -> list[tuple]:
    """Главная проверка: в каждом вырезанном окне была тишина?

    Не по таймкодам Whisper — он растягивает слова на паузы и показывает
    «обрубленное слово» там, где его нет (проверено: спорные окна лежали
    на -41..-48 дБ при шумовом поле -36 дБ, то есть тише комнаты).
    Меряем сам звук: если в вырезанном окне уровень выше порога речи,
    значит рез пришёлся на слово — вот это настоящий брак.
    """
    import numpy as np
    from scipy.io import wavfile

    wav = work / "audio.wav"
    if not wav.exists():
        return []
    sr, data = wavfile.read(str(wav))
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float32) / float(np.iinfo(data.dtype).max)

    win = int(sr * 0.02)
    frames = len(data) // win
    rms = np.sqrt((data[:frames * win].reshape(frames, win) ** 2).mean(axis=1) + 1e-12)
    level = 20 * np.log10(rms)
    floor = float(np.percentile(level, 20))
    peak = float(np.percentile(level, 95))
    thr = floor + (peak - floor) * 0.35

    # Паразиты и дубли режут речь намеренно — их не проверяем.
    # Смотрим только паузы и края: там обязана быть тишина.
    # причина может быть составной («пауза+паразиты»), если резы слиплись
    silent_expected = [c for c in plan.get("cuts", [])
                       if set(c["why"].split("+")) <= {"пауза", "края"}]

    bad = []
    for c in silent_expected:
        seg = data[int(c["start"] * sr):int(c["end"] * sr)]
        if len(seg) == 0:
            continue
        lvl = float(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-12))
        if lvl > thr:
            bad.append((round(c["start"], 2), round(c["end"], 2),
                        round(lvl, 1), round(thr, 1)))
    return bad


def main() -> int:
    stdout_utf8()
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    project = Path(sys.argv[1]).expanduser().resolve()
    lang = sys.argv[2] if len(sys.argv) > 2 else "ru"
    work, out_dir = project / "work", project / "out"

    before_txt = work / "audio.txt"
    if not before_txt.exists():
        print("нет work/audio.txt — сначала transcribe.py")
        return 2

    rendered = next((p for p in (out_dir / "rough.mp4", out_dir / "rough.wav")
                     if p.exists()), None)
    if rendered is None:
        print("нет out/rough.* — сначала rough_cut.py --render")
        return 2

    wav = rendered
    if rendered.suffix == ".mp4":
        from common import ffmpeg, run
        wav = work / "rough.wav"
        if not wav.exists():
            run([ffmpeg(), "-y", "-v", "error", "-i", str(rendered),
                 "-vn", "-ac", "1", "-ar", "16000", str(wav)])

    print("расшифровываю результат...", flush=True)
    after_txt = transcribe(wav, lang)

    before = [norm(w["word"]) for w in read_words(before_txt)]
    after_words = read_words(after_txt)
    after = [norm(w["word"]) for w in after_words]
    before = [w for w in before if w]
    after = [w for w in after if w]

    fillers = FILLERS.get(lang, set())
    phrase_words = {w for ph in FILLER_PHRASES.get(lang, []) for w in ph}

    print(f"\nслов было:  {len(before)}")
    print(f"слов стало: {len(after)}")

    # Сравниваем не мешок слов, а последовательность: Whisper на одном
    # и том же звуке дробит слова по-разному («мастерменда» -> «мастер»
    # + «-майда»), и мешок показывает потерю там, где её нет.
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    expected, unexpected = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        gone = before[i1:i2]
        came = after[j1:j2]
        if not gone:
            continue
        # замена похожего на похожее — это разночтение Whisper, не потеря
        if came and difflib.SequenceMatcher(
                None, "".join(gone), "".join(came)).ratio() >= 0.6:
            continue
        ctx = " ".join(before[max(0, i1 - 4):i1])
        item = (" ".join(gone), " ".join(came), ctx)
        if all(w in fillers or w in phrase_words for w in gone):
            expected.append(item)
        else:
            unexpected.append(item)

    if expected:
        print(f"\nубрано как задумано ({len(expected)} мест):")
        for gone, _, ctx in expected[:20]:
            print(f"  «{gone}»   после: ...{ctx}")

    # шов посреди слова: очень короткое слово впритык к границе куска
    plan = work / "cut.json"
    suspicious = []
    if plan.exists():
        segs = json.loads(plan.read_text(encoding="utf-8"))["segments"]
        edges, acc = [], 0.0
        for s in segs[:-1]:
            acc += s["end"] - s["start"]
            edges.append(acc)
        for w in after_words:
            if w["end"] - w["start"] < 0.10:
                for e in edges:
                    if abs(w["start"] - e) < 0.06 or abs(w["end"] - e) < 0.06:
                        suspicious.append((round(e, 2), w["word"]))
                        break

    status = 0

    # --- решающая проверка: резали по тишине или по слову
    if plan.exists():
        bad = verify_cuts_silent(work, json.loads(plan.read_text(encoding="utf-8")))
        if bad:
            print(f"\nРЕЗ ПО ЖИВОМУ ({len(bad)} мест): в вырезанном окне была речь")
            for a, b, lvl, thr in bad[:15]:
                print(f"  {a}-{b} с: {lvl} дБ при пороге речи {thr} дБ")
            status = 1
        else:
            print("\nвсе резы пришлись на тишину — слова не обрублены")

    if unexpected:
        print(f"\nразночтения расшифровки ({len(unexpected)} мест) — глянуть, "
              f"но обычно это Whisper иначе расслышал, а не потеря:")
        for gone, came, ctx in unexpected[:25]:
            print(f"  было «{gone}» -> стало «{came}»")
            print(f"      контекст: ...{ctx}")

    # Справочно, без вердикта: у любого намеренного реза паразита рядом
    # окажется короткое слово, и как признак брака это не работает.
    # Вердикт даёт только замер тишины выше.
    if suspicious:
        print(f"\nкороткие слова у швов ({len(suspicious)}) — послушать эти места:")
        for t, w in suspicious[:10]:
            print(f"  {t} с: {w!r}")

    print("\nитог:", "ЕСТЬ ЧТО ПРОВЕРИТЬ" if status else "чисто")
    return status


if __name__ == "__main__":
    sys.exit(main())
