# -*- coding: utf-8 -*-
"""Шаг 4. Черновая сборка: убрать паузы, слова-паразиты и дубли.

    python rough_cut.py <исходник> <проект> [--render] [ключи]

Без --render только считает и печатает отчёт, файл не рендерит: сначала
посмотреть, что скрипт собрался вырезать, и только потом тратить время
на кодирование. С --render собирает <проект>/out/rough.mp4 (или .wav,
если видео в исходнике нет).

Ключи:
    --pause 0.35        сколько паузы оставлять на стыке фраз, с
    --edge 0.25         сколько тишины оставлять в начале и в конце, с
    --keep-fillers      не трогать слова-паразиты
    --keep-repeats      не выбрасывать повторные дубли
    --repeat 0.90       порог похожести дублей, 0..1

Что считаем дублем: две подряд идущие фразы, совпадающие по тексту выше
порога. Оставляем последнюю — обычно человек переговаривает, потому что
предыдущий заход не понравился. Каждое такое решение печатается отдельной
строкой, чтобы его можно было отменить руками.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

from common import (duration, ffmpeg, fmt_time, has_video, load_style,
                    read_words, run, stdout_utf8, video_fps, video_start_time)

FILLERS = {
    "ru": {"ну", "вот", "типа", "короче", "блин", "эм", "эмм", "ээ", "эээ",
           "аа", "ааа", "мм", "ммм", "как-бы", "прям", "значит", "слушай"},
    "en": {"um", "uh", "erm", "hmm", "like", "basically", "actually",
           "literally", "so", "right", "okay"},
}
FILLER_PHRASES = {
    "ru": [("как", "бы"), ("то", "есть"), ("это", "самое")],
    "en": [("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of")],
}

PAD = 0.03      # запас вокруг вырезаемого слова, с
MIN_KEEP = 0.08  # огрызки короче — не оставляем


def norm(word: str) -> str:
    return re.sub(r"[^\w\-]", "", word.lower(), flags=re.UNICODE)


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def merge(cuts: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Слипшиеся резы объединяем, НО причины копим все.

    Раньше оставлялась причина первого — и рез слова-паразита, слипшийся
    с соседней паузой, выглядел как «пауза». Приёмка потом честно ругалась
    «рез по живому», хотя речь там вырезана намеренно.
    """
    if not cuts:
        return []
    cuts = sorted(cuts)
    out = [[cuts[0][0], cuts[0][1], {cuts[0][2]}]]
    for a, b, why in cuts[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].add(why)
        else:
            out.append([a, b, {why}])
    return [(a, b, "+".join(sorted(w))) for a, b, w in out]


def invert(cuts, total: float) -> list[tuple[float, float]]:
    keep, pos = [], 0.0
    for a, b, _ in cuts:
        if a - pos > MIN_KEEP:
            keep.append((pos, a))
        pos = max(pos, b)
    if total - pos > MIN_KEEP:
        keep.append((pos, total))
    return keep


def find_restarts(words, opt) -> list[tuple]:
    """Переснятые заходы ВНУТРИ куска речи.

    Поиск по кускам их не находит: между попытками человек делает паузу
    короче порога, и огибающая слепляет все заходы в один кусок. На живом
    сырье так и вышло — четыре подхода к одной фразе оказались внутри
    одного куска длиной 52 секунды.

    Поэтому ищем в потоке слов повтор ЗАЧИНА: если несколько слов подряд
    почти дословно повторяются чуть дальше, значит человек оборвался
    и начал ту же фразу заново. Выбрасываем всё от первого захода
    до последнего, последний оставляем — он и есть удачный.
    """
    N = 6          # сколько слов сравниваем в зачине
    LOOK = 110     # как далеко вперёд ищем повтор
    MAX_GAP = 30.0  # и не дальше этого по времени
    norms = [norm(w["word"]) for w in words]
    out = []
    i = 0
    while i + N * 2 <= len(norms):
        a = " ".join(norms[i:i + N])
        if len(a) < 12:
            i += 1
            continue
        best = None
        for j in range(i + N, min(i + LOOK, len(norms) - N + 1)):
            if words[j]["start"] - words[i]["start"] > MAX_GAP:
                break
            ratio = difflib.SequenceMatcher(
                None, a, " ".join(norms[j:j + N])).ratio()
            if ratio >= opt["repeat_thr"]:
                best = (j, ratio)     # запоминаем САМЫЙ ПОЗДНИЙ повтор
        if best:
            j, ratio = best
            out.append((words[i]["start"], words[j]["start"], ratio, a))
            i = j
        else:
            i += 1
    return out


def build_cuts(regions, words, total, opt) -> tuple[list, dict]:
    cuts: list[tuple[float, float, str]] = []
    stats = {"пауза": 0.0, "паразиты": 0.0, "дубли": 0.0, "края": 0.0}

    if not regions:
        return [], stats

    # --- края
    head = regions[0]["start"] - opt["edge"]
    if head > 0:
        cuts.append((0.0, head, "края"))
        stats["края"] += head
    tail = regions[-1]["end"] + opt["edge"]
    if total - tail > 0:
        cuts.append((tail, total, "края"))
        stats["края"] += total - tail

    # --- длинные паузы между фразами
    for prev, nxt in zip(regions, regions[1:]):
        gap = nxt["start"] - prev["end"]
        if gap > opt["pause"]:
            extra = gap - opt["pause"]
            mid = prev["end"] + gap / 2
            a, b = mid - extra / 2, mid + extra / 2
            cuts.append((a, b, "пауза"))
            stats["пауза"] += extra

    # --- слова-паразиты
    if opt["fillers"]:
        lang = opt["lang"]
        single = FILLERS.get(lang, set()) | set(opt["extra_fillers"])
        phrases = FILLER_PHRASES.get(lang, [])
        i = 0
        while i < len(words):
            w = words[i]
            hit, span = None, None

            for ph in phrases:
                if i + len(ph) <= len(words) and all(
                        norm(words[i + k]["word"]) == ph[k] for k in range(len(ph))):
                    hit = " ".join(ph)
                    span = (w["start"], words[i + len(ph) - 1]["end"])
                    break
            if hit is None and norm(w["word"]) in single:
                hit, span = norm(w["word"]), (w["start"], w["end"])

            if hit:
                before = span[0] - (words[i - 1]["end"] if i > 0 else 0.0)
                after_idx = i + (len(hit.split()) if " " in hit else 1)
                after = ((words[after_idx]["start"] if after_idx < len(words)
                          else total) - span[1])
                # режем только если слово стоит обособленно: иначе шов
                # придётся на середину слитной фразы и будет слышно
                if before >= 0.08 or after >= 0.08:
                    a, b = max(span[0] - PAD, 0.0), min(span[1] + PAD, total)
                    cuts.append((a, b, "паразиты"))
                    stats["паразиты"] += b - a
                    i = after_idx
                    continue
            i += 1

    # --- повторные дубли
    dropped = []
    if opt["repeats"]:
        for prev, nxt in zip(regions, regions[1:]):
            a, b = norm_text(prev["text"]), norm_text(nxt["text"])
            if len(a) < 12:
                continue
            why = None

            # Человек сказал вслух, что переснимает. Смотрим только конец
            # куска: «давайте ещё раз посмотрим» в середине фразы —
            # обычная речь, а «...и получаешь. давай ещё раз» в конце —
            # это фальстарт.
            tail = " ".join(a.split()[-5:])
            for mark in opt["retake_marks"]:
                if mark in tail:
                    why = f"сказал «{mark}»"
                    break

            if why is None and len(b) >= 12:
                ratio = difflib.SequenceMatcher(None, a, b).ratio()
                # Целиком совпадают — обычный повтор.
                if ratio >= opt["repeat_thr"]:
                    why = f"повтор, совпадение {ratio:.0%}"
                else:
                    # Фальстарт: человек оборвался и начал ту же фразу
                    # заново, поэтому вторая версия длиннее. По всей длине
                    # совпадение не дотягивает, а начало совпадает почти
                    # дословно. Ловилось на живом сырье: четыре захода
                    # на одну фразу, находился только один.
                    head_ratio = difflib.SequenceMatcher(None, a, b[:len(a)]).ratio()
                    if head_ratio >= opt["repeat_thr"]:
                        why = f"фальстарт, начало совпало на {head_ratio:.0%}"

            if why:
                cuts.append((prev["start"], prev["end"], "дубли"))
                stats["дубли"] += prev["end"] - prev["start"]
                dropped.append((prev, why))

    # --- переснятые заходы внутри куска
    restarts = []
    if opt["repeats"] and words:
        for a, b, ratio, text in find_restarts(words, opt):
            if b - a < 0.3:
                continue
            cuts.append((a, b, "дубли"))
            stats["дубли"] += b - a
            restarts.append((a, b, ratio, text))

    opt["_dropped"] = dropped
    opt["_restarts"] = restarts
    return merge(cuts), stats


def render(src: Path, keep, dst: Path) -> None:
    """Склейка кусков одним проходом ffmpeg, звук и картинка кусок в кусок.

    Синхрон здесь уезжает по трём независимым причинам, и лечится каждая
    отдельно. Замер на 11 склейках: было расхождение 122 мс, стало 0.

    1. atrim режет звук посемплово и даёт ровно (конец - начало) секунд,
       а trim у картинки отбирает кадры по попаданию метки времени
       в интервал. Граница ровно на метке — кадр то попадает, то нет.
       Лечится сдвигом границы на четверть кадра назад: попадание
       становится однозначным. Длину куска считаем в кадрах и той же
       длиной режем звук.
    2. Кадры лежат не на k/fps, а на t0 + k/fps: у многих файлов поток
       начинается не с нуля. Сетку строим от t0.
    3. В исходнике бывают дыры — и в звуке, и в кадрах. Арифметика их
       не чинит, данных там нет. Достраиваем: aresample для звука,
       fps для картинки.
    """
    video = has_video(src)
    fps = video_fps(src) if video else 0.0
    step = 1.0 / fps if video else 0.0
    # кадры лежат не на k*step, а на t0 + k*step
    t0 = video_start_time(src) if video else 0.0

    # Вторая тонкость: у файлов, которые сами собраны монтажом, звуковая
    # дорожка бывает рваной — с микроскопическими дырами между пакетами.
    # atrim на таком куске отдаёт меньше звука, чем просили (замер: три
    # куска из одиннадцати недобрали 45-113 мс), и синхрон уезжает.
    # aresample выравнивает дорожку в непрерывную до всякой резки.
    parts = ["[0:a]aresample=async=1:first_pts=0[src_a]"]
    streams = ""
    for i, (a, b) in enumerate(keep):
        if video:
            # первый кадр куска — ближайшая метка не раньше a
            k = max(int(-(-(a - t0) / step // 1)), 0)
            frames = max(int(round((b - a) / step)), 1)
            start = max(t0 + k * step - step * 0.25, 0.0)
            end = start + frames * step
            # fps= в конце — обязателен. В исходниках попадаются пропуски
            # кадров (замер: два куска из одиннадцати недосчитались кадра,
            # ровно там, где у звука были дыры). Никакая арифметика границ
            # это не чинит — кадров там просто нет. Приведение к постоянной
            # частоте достраивает недостающее, и кусок выходит ровно той
            # длины, что и его звук. Для картинки это то же, что aresample
            # для звука.
            parts.append(f"[0:v]trim=start={start:.5f}:end={end:.5f},"
                         f"setpts=PTS-STARTPTS,fps={fps:.6f}[v{i}]")
            streams += f"[v{i}][a{i}]"
        else:
            start, end = a, b
            streams += f"[a{i}]"
        parts.append(f"[src_a]atrim=start={start:.5f}:end={end:.5f},"
                     f"asetpts=PTS-STARTPTS[a{i}]")

    n = len(keep)
    if video:
        parts.append(f"{streams}concat=n={n}:v=1:a=1[outv][outa]")
        maps = ["-map", "[outv]", "-map", "[outa]"]
        codec = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                 "-preset", "medium", "-movflags", "+faststart"]
    else:
        parts.append(f"{streams}concat=n={n}:v=0:a=1[outa]")
        maps = ["-map", "[outa]"]
        codec = []

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(src),
           "-filter_complex", ";".join(parts), *maps, *codec,
           "-c:a", "aac" if video else "pcm_s16le", str(dst)]
    run(cmd)


def main() -> int:
    stdout_utf8()
    args = [a for a in sys.argv[1:]]
    if len(args) < 2:
        print(__doc__)
        return 2

    src = Path(args[0]).expanduser().resolve()
    project = Path(args[1]).expanduser().resolve()
    work = project / "work"
    if not (work / "speech.json").exists():
        print("нет work/speech.json — сначала prepare.py, transcribe.py, speech_map.py")
        return 2

    style = load_style(project)

    def flag(name: str) -> bool:
        return name in args

    def val(name: str, default: float) -> float:
        return float(args[args.index(name) + 1]) if name in args else default

    opt = {
        "pause": val("--pause", style.get("max_pause", 0.35)),
        "edge": val("--edge", 0.25),
        "fillers": not flag("--keep-fillers"),
        "repeats": not flag("--keep-repeats"),
        "repeat_thr": val("--repeat", 0.90),
        "lang": style.get("lang", "ru"),
        "extra_fillers": [w.lower() for w in style.get("extra_fillers", [])],
        "retake_marks": [m.lower() for m in style.get(
            "retake_marks", ["давай еще раз", "давайте еще раз", "еще дубль",
                             "переснимаем", "заново", "стоп снято"])],
    }

    regions = json.loads((work / "speech.json").read_text(encoding="utf-8"))
    txt = work / "audio.txt"
    words = read_words(txt) if txt.exists() else []
    total = duration(src)

    cuts, stats = build_cuts(regions, words, total, opt)
    keep = invert(cuts, total)

    # У видео звук режется посемплово, а картинка прыгает по кадрам:
    # на каждой склейке остаётся ошибка меньше кадра, но на сотне склеек
    # они складываются в заметный рассинхрон. Сажаем границы на сетку
    # кадров — тогда звук и картинка режутся в одной точке.
    # Полкадра (16 мс при 30 fps) на слух не слышно, а паузы вокруг реза
    # всё равно длиннее.
    if has_video(src):
        step = 1.0 / video_fps(src)
        keep = [(round(a / step) * step, round(b / step) * step) for a, b in keep]
        keep = [(a, min(b, total)) for a, b in keep if b - a > MIN_KEEP]

    left = sum(b - a for a, b in keep)

    print(f"исходник:  {fmt_time(total)}")
    print(f"после реза {fmt_time(left)}   убрали {fmt_time(total - left)} "
          f"({(total - left) / total * 100:.0f}%)\n")
    for name, sec in stats.items():
        if sec > 0.01:
            print(f"  {name:10s} {sec:6.1f} с")
    print(f"\nкусков в сборке: {len(keep)}")

    # Если паразитов не нашлось совсем — сказать почему, иначе выглядит
    # как будто функция не работает. Замер на сырой записи: 343 слова
    # и один паразит в расшифровке.
    if opt["fillers"] and stats["паразиты"] < 0.01 and words:
        print("\nслов-паразитов в расшифровке не нашлось. Это не поломка:\n"
              "  Whisper не записывает запинки — тянущиеся «эээ», «ммм»\n"
              "  и часть «ну» он выбрасывает как шум. По громкости они речь,\n"
              "  поэтому и срезание паузы их не берёт. Такие места придётся\n"
              "  убирать руками, автоматически они не ловятся.")

    for a, b, ratio, text in opt.get("_restarts", []):
        print(f"  выброшен заход {fmt_time(a)}-{fmt_time(b)} "
              f"(зачин повторён на {ratio:.0%}): {text[:56]}")
    for reg, why in opt["_dropped"]:
        print(f"  выброшен дубль {fmt_time(reg['start'])} ({why}): "
              f"{reg['text'][:64]}")

    plan = {"source": src.name, "total": round(total, 3),
            "kept": round(left, 3), "options": {k: v for k, v in opt.items()
                                                if not k.startswith("_")},
            "segments": [{"start": round(a, 3), "end": round(b, 3)} for a, b in keep],
            # причина каждого реза: приёмка проверяет на тишину только паузы
            # и края, а «паразиты» и «дубли» режут речь намеренно
            "cuts": [{"start": round(a, 3), "end": round(b, 3), "why": why}
                     for a, b, why in cuts]}
    (work / "cut.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(f"\nплан реза: {work / 'cut.json'}")

    if flag("--render"):
        ext = ".mp4" if has_video(src) else ".wav"
        dst = project / "out" / f"rough{ext}"
        print("рендерю...", flush=True)
        render(src, keep, dst)
        print(f"готово: {dst}")
    else:
        print("файл не рендерил. Устраивает план — запусти ещё раз с --render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
