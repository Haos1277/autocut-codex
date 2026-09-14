# -*- coding: utf-8 -*-
"""Шаг 7. Субтитры своим стилем.

    python subtitles.py <видео> <проект> [--burn] [ключи]

Берёт пословные таймкоды из расшифровки и собирает субтитры: разбивает
речь на реплики по знакам препинания и паузам, а не по числу символов
наугад. Пишет .ass (со стилем) и .srt (для загрузки куда угодно).
С --burn впечатывает в картинку.

Ключи:
    --font Arial        шрифт (должен стоять в системе)
    --size 64           высота букв в пикселях при 1080 по ширине
    --color #FFFFFF     цвет текста
    --outline #000000   цвет обводки
    --outline-w 4       толщина обводки
    --shadow 2          мягкая тень (0 отключить)
    --pos bottom        bottom | top | center
    --margin 220        отступ от края кадра, пикселей
    --caps              КАПСОМ
    --karaoke           подсвечивать произносимое слово
    --hl #FFCC00        цвет подсветки для --karaoke
    --max-chars 0       символов в строке (0 = посчитать под кадр и шрифт)
    --plan план.json    поднимать субтитры над вставкой там, где она есть
    --burn              впечатать в видео

Стиль берётся из style.json проекта, ключи командной строки его
перекрывают. Свой стиль задавайте сами — подставлять чужой по умолчанию
значит делать все каналы похожими друг на друга.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from common import (ffmpeg, has_video, load_style, read_words, run,
                    stdout_utf8, video_size)

END_PUNCT = ".!?…"
SOFT_PUNCT = ",;:—–-"
GAP_BREAK = 0.35       # пауза между словами, на которой рвём реплику
MIN_DUR = 0.80
MAX_DUR = 3.50
TAIL = 0.12            # сколько подержать реплику после последнего слова


def ass_colour(hex_colour: str, alpha: str = "00") -> str:
    """ASS хранит цвет как &HAABBGGRR — байты наоборот, и альфа первая."""
    h = hex_colour.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def group_words(words: list[dict], max_chars: int) -> list[list[dict]]:
    """Слова в реплики. Рвём по смыслу: точка, длинная пауза, предел длины."""
    cues: list[list[dict]] = []
    cur: list[dict] = []

    def flush():
        if cur:
            cues.append(cur.copy())
            cur.clear()

    for i, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["word"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99.0
        dur = w["end"] - cur[0]["start"]

        tail = w["word"].rstrip()[-1:]
        hard = tail in END_PUNCT
        # Рвать ровно по лимиту символов плохо: реплика обрывается посреди
        # словосочетания («раскладываешь ее на»). Если мы уже набрали
        # больше двух третей строки и стоим на запятой — рвём здесь.
        comma = tail in SOFT_PUNCT and len(text) >= max_chars * 0.66
        if hard or comma or gap > GAP_BREAK or len(text) >= max_chars \
                or dur > MAX_DUR:
            flush()
    flush()

    # Реплика из одного короткого слова читается как мусор — приклеиваем
    # к соседней, если та не переполнится.
    out: list[list[dict]] = []
    for cue in cues:
        text = " ".join(w["word"] for w in cue)
        if out and len(text) <= 6:
            prev_text = " ".join(w["word"] for w in out[-1])
            if len(prev_text) + len(text) + 1 <= max_chars * 2:
                out[-1].extend(cue)
                continue
        out.append(cue)
    return out


def wrap(text: str, max_chars: int) -> str:
    """Не больше двух строк, ломаем по словам, короткий хвост не оставляем."""
    if len(text) <= max_chars:
        return text
    words = text.split()
    best, best_score = None, -10 ** 9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_chars or len(b) > max_chars:
            continue
        # Ровные половинки — хорошо, но перенос после знака препинания
        # важнее: строка, оборванная посреди словосочетания, читается
        # рывком.
        score = -abs(len(a) - len(b))
        tail = a.rstrip()[-1:]
        if tail in END_PUNCT:
            score += 40
        elif tail in SOFT_PUNCT:
            score += 20
        if score > best_score:
            best, best_score = (a, b), score
    if best:
        return best[0] + r"\N" + best[1]
    return text


def merge_unreadable(cues: list[list[dict]], max_chars: int) -> list[list[dict]]:
    """Склеиваем реплики, которые физически не прочитать.

    Реплика живёт от первого своего слова до первого слова следующей —
    растянуть её нельзя, иначе полезут друг на друга. Поэтому короткую
    фразу нельзя «подержать подольше», её можно только объединить
    с соседней. Замер до этой правки: реплика 18 знаков на 0,57 с,
    то есть 31 знак в секунду при комфортных 20.
    """
    limit = max_chars * 2
    changed = True
    while changed:
        changed = False
        for i in range(len(cues) - 1):
            a, b = cues[i], cues[i + 1]
            a_text = " ".join(w["word"] for w in a)
            a_dur = a[-1]["end"] - a[0]["start"]
            span = b[0]["start"] - a[0]["start"]
            cps = len(a_text) / span if span > 0 else 99
            if a_dur >= MIN_DUR and cps <= 20:
                continue
            # Через точку не склеиваем: две фразы в одной реплике читаются
            # хуже, чем одна чуть быстрая, и перенос строки почти всегда
            # падает посреди словосочетания. Исключение — совсем мелькающие
            # обрывки короче полусекунды.
            if a_text.rstrip()[-1:] in END_PUNCT and a_dur >= 0.5:
                continue
            merged = len(a_text) + 1 + len(" ".join(w["word"] for w in b))
            total = b[-1]["end"] - a[0]["start"]
            if merged <= limit and total <= MAX_DUR:
                cues[i] = a + b
                del cues[i + 1]
                changed = True
                break
    return cues


def timings(cues: list[list[dict]]) -> list[tuple[float, float]]:
    spans = []
    for i, cue in enumerate(cues):
        start = cue[0]["start"]
        end = cue[-1]["end"] + TAIL
        if end - start < MIN_DUR:
            end = start + MIN_DUR
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0]["start"] - 0.01)
        if end <= start:
            end = start + 0.2
        spans.append((start, end))
    return spans


def ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def srt_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def margins_from_plan(plan_path: Path, spans, h: int, base: int) -> list[int]:
    """Свой отступ снизу для каждой реплики.

    Отступ один на весь ролик не годится: там, где снизу стоит вставка,
    субтитры ложатся прямо на неё и обе надписи становятся нечитаемыми
    (поймано глазами на готовом ролике). Поэтому под каждую реплику
    смотрим, какая раскладка играет в этот момент, и поднимаем текст
    над полосой вставки.
    """
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segs = []
    for seg in plan.get("segments", []):
        segs.append((float(seg["start"]), float(seg["end"]),
                     seg.get("layout", "none")))
    bands = {"70/30": 0.30, "50/50": 0.50}
    gap = int(h * 0.02)
    out = []
    for start, _ in spans:
        layout = "none"
        for a, b, lay in segs:
            if a <= start < b:
                layout = lay
                break
        share = bands.get(layout)
        out.append(int(h * share) + gap if share else base)
    return out


def build_ass(cues, spans, size: tuple[int, int], opt: dict) -> str:
    w, h = size
    align = {"bottom": 2, "center": 5, "top": 8}[opt["pos"]]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: main,{opt['font']},{opt['size']},{ass_colour(opt['color'])},{ass_colour(opt['hl'])},{ass_colour(opt['outline'])},&H90000000,{-1 if opt['bold'] else 0},0,0,0,100,100,0,0,1,{opt['outline_w']},{opt['shadow']},{align},60,60,{opt['margin']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [head]
    for idx, (cue, (start, end)) in enumerate(zip(cues, spans)):
        raw = " ".join(x["word"] for x in cue)
        raw = re.sub(r"\s+", " ", raw).strip()
        if opt["caps"]:
            raw = raw.upper()

        mv = opt["_margins"][idx] if opt.get("_margins") else 0

        if not opt["karaoke"]:
            text = wrap(raw, opt["max_chars"])
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},main,"
                         f",0,0,{mv},,{text}")
            continue

        # Подсветку делаем отдельным событием на каждое слово: тег \k
        # поддерживается не всеми проигрывателями одинаково, а это
        # работает везде.
        parts = [x["word"].upper() if opt["caps"] else x["word"] for x in cue]
        for idx, word in enumerate(cue):
            ws = max(word["start"], start)
            we = min(word["end"] + 0.02, end)
            if idx + 1 < len(cue):
                we = min(we, cue[idx + 1]["start"])
            if we <= ws:
                continue
            shown = []
            for j, p in enumerate(parts):
                if j == idx:
                    shown.append(r"{\c" + ass_colour(opt["hl"]) + "}" + p
                                 + r"{\c" + ass_colour(opt["color"]) + "}")
                else:
                    shown.append(p)
            text = wrap(" ".join(shown), opt["max_chars"] * 3)
            lines.append(f"Dialogue: 0,{ass_time(ws)},{ass_time(we)},main,"
                         f",0,0,{mv},,{text}")
    return "\n".join(lines) + "\n"


def build_srt(cues, spans, caps: bool, max_chars: int) -> str:
    out = []
    for i, (cue, (start, end)) in enumerate(zip(cues, spans), 1):
        raw = re.sub(r"\s+", " ", " ".join(x["word"] for x in cue)).strip()
        if caps:
            raw = raw.upper()
        out.append(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n"
                   f"{wrap(raw, max_chars).replace(chr(92) + 'N', chr(10))}\n")
    return "\n".join(out)


def main() -> int:
    stdout_utf8()
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2

    src = Path(args[0]).expanduser().resolve()
    project = Path(args[1]).expanduser().resolve()
    txt = project / "work" / "audio.txt"
    if not txt.exists():
        print("нет work/audio.txt — сначала prepare.py и transcribe.py")
        return 2

    style = load_style(project).get("subtitles", {})

    def val(name, default):
        if name in args:
            return args[args.index(name) + 1]
        return default

    size = video_size(src) if has_video(src) else (1080, 1920)
    # Размер шрифта задаём в пикселях кадра: 64 при ширине 1080 — примерно
    # то, что читается на телефоне.
    font_size = int(val("--size", style.get("size", round(size[0] / 17))))
    opt = {
        "font": val("--font", style.get("font", "Arial")),
        "size": font_size,
        "color": val("--color", style.get("color", "#FFFFFF")),
        "outline": val("--outline", style.get("outline", "#000000")),
        "outline_w": int(val("--outline-w", style.get("outline_w", 4))),
        # Тень нужна не для красоты: белый текст на светлом фоне (частый
        # случай — человек в белой футболке) держится на одной обводке
        # и на пределе читаемости. Тень добавляет вторую линию защиты.
        "shadow": int(val("--shadow", style.get("shadow", 2))),
        "pos": val("--pos", style.get("pos", "bottom")),
        "margin": int(val("--margin", style.get("margin", round(size[1] * 0.11)))),
        "caps": "--caps" in args or style.get("caps", False),
        "bold": style.get("bold", True),
        "karaoke": "--karaoke" in args or style.get("karaoke", False),
        "hl": val("--hl", style.get("highlight", "#FFCC00")),
    }

    # Сколько символов влезает в строку — считаем под кадр и кегль, а не
    # берём наугад. Обрезанный краем кадра текст ловится именно здесь.
    fit = max(int(size[0] * 0.88 / (font_size * 0.55)), 12)
    asked = int(val("--max-chars", style.get("max_chars", 0)))
    if asked and asked > fit:
        print(f"внимание: {asked} символов в строке не влезут в кадр "
              f"шириной {size[0]} при кегле {font_size}, беру {fit}")
    opt["max_chars"] = min(asked, fit) if asked else fit

    words = [w for w in read_words(txt) if w["word"].strip()]
    if not words:
        print("в расшифровке нет слов с таймкодами")
        return 2

    cues = merge_unreadable(group_words(words, opt["max_chars"]),
                            opt["max_chars"])
    spans = timings(cues)

    opt["_margins"] = None
    plan_arg = val("--plan", None)
    if plan_arg:
        pp = Path(plan_arg).expanduser().resolve()
        if not pp.exists():
            print(f"нет файла плана: {pp}")
            return 2
        opt["_margins"] = margins_from_plan(pp, spans, size[1], opt["margin"])
        поднято = sum(1 for m in opt["_margins"] if m != opt["margin"])
        print(f"по плану раскладок поднято над вставкой реплик: {поднято} "
              f"из {len(spans)}")

    out_dir = project / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    ass = out_dir / "subs.ass"
    srt = out_dir / "subs.srt"
    ass.write_text(build_ass(cues, spans, size, opt), encoding="utf-8")
    srt.write_text(build_srt(cues, spans, opt["caps"], opt["max_chars"]),
                   encoding="utf-8")

    lens = [len(" ".join(x["word"] for x in c)) for c in cues]
    durs = [b - a for a, b in spans]
    print(f"кадр: {size[0]}x{size[1]}, шрифт {opt['font']} {opt['size']}, "
          f"в строке до {opt['max_chars']} символов")
    print(f"реплик: {len(cues)}")
    print(f"длина реплики: {min(durs):.1f}-{max(durs):.1f} с "
          f"(в среднем {sum(durs) / len(durs):.1f})")
    print(f"символов в реплике: {min(lens)}-{max(lens)}")
    # Скорость чтения — главный показатель качества субтитров.
    # Комфортно до 20 знаков в секунду, 25 это уже впритык.
    cps = [n / d for n, d in zip(lens, durs) if d > 0]
    worst = max(cps)
    fast = sum(1 for c in cps if c > 25)
    print(f"скорость чтения: в среднем {sum(cps) / len(cps):.0f}, "
          f"максимум {worst:.0f} знаков в секунду", end="")
    print("  — норма" if worst <= 25 else f"  — {fast} реплик слишком быстрые")
    print(f"\n{ass}\n{srt}")

    print("\nпервые пять:")
    for cue, (a, b) in list(zip(cues, spans))[:5]:
        t = " ".join(x["word"] for x in cue)
        print(f"  {a:6.2f}-{b:6.2f}  {t[:70]}")

    if "--burn" in args:
        if not has_video(src):
            print("\nв исходнике нет видео, впечатывать некуда")
            return 1
        dst = out_dir / "subtitled.mp4"
        print("\nвпечатываю...", flush=True)
        # путь к .ass в фильтр отдаём относительным из его папки:
        # у фильтра subtitles двоеточие в C:\ разбирается как разделитель
        run([ffmpeg(), "-y", "-v", "error", "-i", str(src),
             "-vf", f"ass={ass.name}", "-c:a", "copy",
             "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(dst)], cwd=out_dir)
        print(f"готово: {dst}")
    else:
        print("\nв видео не впечатывал. Нужно — запусти с --burn")
    return 0


if __name__ == "__main__":
    sys.exit(main())