# -*- coding: utf-8 -*-
"""Шаг 8в. Карточки-вставки.

    python cards.py <карточки.json> <папка-выхода> [--anim]

Рисует вставки, которые compose.py кладёт в кадр: заголовок, список,
крупное число, блок кода. Всё в стиле, заданном на интейке — своём,
не чужом.

Файл описания — объект или список объектов:

[
 {"name": "a", "size": [1080, 576], "type": "list",
  "title": "Что он делает", "items": ["режет паузы", "ставит субтитры"]},
 {"name": "b", "size": [1080, 1920], "type": "stat",
  "value": "24%", "caption": "тишины в записи"},
 {"name": "c", "type": "text", "title": "Главное", "subtitle": "в одну строку"},
 {"name": "d", "type": "code", "lines": ["python rough_cut.py", "  --render"]}
]

Типы: text, list, stat, code.
Стиль (можно в каждой карточке, можно один на файл в ключе "style"):
    bg        фон; по умолчанию прозрачный, цвет задаёт непрозрачный фон
    fg        текст, #F2F2F2
    accent    акцент — маркеры и линии, #4C8BF5
    font      путь к шрифту; по умолчанию ищется системный
    mono      путь к моноширинному для типа code
    align     left | center
    padding   отступ от края, доля ширины (0.06)

С --anim рядом с png кладётся короткое .mov-видео с альфа-каналом:
элементы всплывают со сдвигом и прозрачностью за 0,45 с, дальше кадр
стоит. Длину задаёт ключ "dur" (по умолчанию 3 с).

Кегль подбирается автоматически под размер карточки: текст, вылезающий
за края, — самая частая поломка вставок, и здесь она исключена
по построению.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import ffmpeg, run, stdout_utf8

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("нет Pillow. Поставь: pip install -r requirements.txt")

DEFAULTS = {
    "bg": None,
    "fg": "#F2F2F2",
    "accent": "#4C8BF5",
    "align": "left",
    "padding": 0.06,
    "size": [1080, 576],
    "dur": 3.0,
}

FONT_CANDIDATES = {
    "Windows": [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
                r"C:\Windows\Fonts\segoeuib.ttf"],
    "Darwin": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
               "/Library/Fonts/Arial Bold.ttf",
               "/System/Library/Fonts/Helvetica.ttc"],
    "Linux": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
              "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"],
}
MONO_CANDIDATES = {
    "Windows": [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf"],
    "Darwin": ["/System/Library/Fonts/Menlo.ttc",
               "/System/Library/Fonts/Courier.ttc"],
    "Linux": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "/usr/share/fonts/TTF/DejaVuSansMono.ttf"],
}


def find_font(explicit: str | None, mono: bool = False) -> str:
    if explicit:
        if not Path(explicit).exists():
            sys.exit(f"нет шрифта: {explicit}")
        return explicit
    table = MONO_CANDIDATES if mono else FONT_CANDIDATES
    for path in table.get(platform.system(), []):
        if Path(path).exists():
            return path
    sys.exit("не нашёл системный шрифт — укажите свой ключом \"font\" "
             "(и \"mono\" для блоков кода)")


def has_cyrillic(font_path: str) -> bool:
    """Есть ли в шрифте кириллица.

    PIL про отсутствующие буквы не сообщает — молча рисует пустые
    прямоугольники. Поэтому сравниваем очертания русской буквы и заведомо
    отсутствующего символа из области для частного использования: совпали
    размеры — значит обе нарисовались одной и той же заглушкой.
    """
    try:
        f = ImageFont.truetype(font_path, 64)
        return f.getbbox("Ж") != f.getbbox("\ue000")
    except OSError:
        return True


def fit_font(path: str, text_lines: list[str], box: tuple[int, int],
             start: int, line_gap: float = 1.25, floor: int = 14) -> tuple:
    """Наибольший кегль, при котором строки влезают в box."""
    w, h = box
    size = start
    while size > floor:
        font = ImageFont.truetype(path, size)
        wrapped = []
        for line in text_lines:
            wrapped += wrap_text(line, font, w)
        height = len(wrapped) * size * line_gap
        widest = max((font.getbbox(x)[2] for x in wrapped), default=0)
        if height <= h and widest <= w:
            return font, wrapped
        size = int(size * 0.94)
    font = ImageFont.truetype(path, floor)
    wrapped = []
    for line in text_lines:
        wrapped += wrap_text(line, font, w)
    return font, wrapped


def wrap_text(text: str, font, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for word in words[1:]:
        probe = cur + " " + word
        if font.getbbox(probe)[2] <= max_w:
            cur = probe
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def draw_card(spec: dict, style: dict, reveal: float = 1.0) -> Image.Image:
    """reveal 0..1 — сколько содержимого уже появилось (для анимации)."""
    w, h = spec.get("size", style["size"])
    pad = int(w * style["padding"])
    box = (w - pad * 2, h - pad * 2)
    bg = style.get("bg")
    if bg in (None, "transparent"):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    else:
        img = Image.new("RGBA", (w, h), bg)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    kind = spec.get("type", "text")
    font_path = style["_font"]
    centre = style["align"] == "center"

    def blocks() -> list[tuple]:
        """Список (рисовалка, высота). Каждый блок появляется по очереди."""
        out = []
        if kind == "stat":
            vfont, vlines = fit_font(font_path, [spec.get("value", "")],
                                     (box[0], int(box[1] * 0.6)), int(h * 0.42))
            cfont, clines = fit_font(font_path, [spec.get("caption", "")],
                                     (box[0], int(box[1] * 0.3)), int(h * 0.09))
            out.append((vfont, vlines, style["fg"], int(vfont.size * 1.1)))
            if spec.get("caption"):
                out.append((cfont, clines, style["accent"],
                            int(cfont.size * 1.35)))
            return out

        if kind == "code":
            mono = style["_mono"]
            lines = spec.get("lines", [])
            font, wrapped = fit_font(mono, lines, box, int(h * 0.11),
                                     line_gap=1.5)
            out.append((font, wrapped, style["fg"], int(font.size * 1.5)))
            return out

        title = spec.get("title", "")
        title_size = int(h * 0.17)
        if title:
            share = 0.45 if (spec.get("items") or spec.get("subtitle")) else 0.9
            tfont, tlines = fit_font(font_path, [title],
                                     (box[0], int(box[1] * share)),
                                     title_size)
            title_size = tfont.size
            out.append((tfont, tlines, style["fg"], int(tfont.size * 1.3)))

        # Размер второго уровня считаем ОТ ЗАГОЛОВКА, а не от высоты
        # карточки. Иначе на высокой карточке длинный заголовок сжимается,
        # подпись остаётся крупной, и они сходятся в размере — иерархия
        # рушится. Ловилось глазами на карточке 1080x1920.
        if kind == "list":
            items = ["•  " + str(x) for x in spec.get("items", [])]
            if items:
                ifont, ilines = fit_font(font_path, items,
                                         (box[0], int(box[1] * 0.5)),
                                         max(int(title_size * 0.55), 14),
                                         line_gap=1.6)
                out.append((ifont, ilines, style["fg"], int(ifont.size * 1.6)))
        elif spec.get("subtitle"):
            sfont, slines = fit_font(font_path, [spec["subtitle"]],
                                     (box[0], int(box[1] * 0.4)),
                                     max(int(title_size * 0.5), 14))
            out.append((sfont, slines, style["accent"], int(sfont.size * 1.3)))
        return out

    parts = blocks()
    # Отбивка между блоками. Без неё подпись прижимается к заголовку
    # вплотную и читается как продолжение той же фразы — видно глазами
    # на карточке-заголовке.
    gaps = [0] + [int(font.size * 0.6) for font, _, _, _ in parts[1:]]
    # Высоту считаем без межстрочного интервала ПОСЛЕ последней строки:
    # иначе снизу остаётся фантомный отступ и весь блок съезжает вверх.
    total = sum(len(lines) * step for _, lines, _, step in parts) + sum(gaps)
    if parts:
        last_font, _, _, last_step = parts[-1]
        total -= max(last_step - last_font.size, 0)
    # Центруем по фактическому пятну текста, а не по метрике шрифта:
    # у цифр верх ink лежит заметно ниже верха строки, и карточка
    # с крупным числом уезжала вниз на 6% высоты.
    ink_top = 0
    if parts:
        first_font, first_lines, _, _ = parts[0]
        if first_lines:
            ink_top = first_font.getbbox(first_lines[0])[1]
    y = pad + max((box[1] - (total - ink_top)) // 2, 0) - ink_top

    # тонкая акцентная линия слева — единственное украшение
    if kind in ("text", "list") and spec.get("title"):
        lw = max(int(w * 0.006), 3)
        # Высота линии растёт вместе с появлением содержимого: иначе
        # на первых кадрах она висит в пустоте под одним заголовком.
        lh = int(min(total, box[1]) * min(max(reveal, 0.0), 1.0))
        if lh > 0:
            d.rectangle([pad - lw * 3, y, pad - lw * 2, y + lh],
                        fill=style["accent"])

    n = len(parts)
    for idx, (font, lines, colour, step) in enumerate(parts):
        y += gaps[idx]
        # блоки появляются по очереди: у каждого своя доля общего времени
        share_start = idx / max(n, 1)
        local = min(max((reveal - share_start) * max(n, 1), 0.0), 1.0)
        if local <= 0:
            y += len(lines) * step
            continue
        alpha = int(255 * local)
        shift = int((1 - local) * h * 0.05)
        for line in lines:
            x = pad if not centre else (w - font.getbbox(line)[2]) // 2
            d.text((x, y + shift), line, font=font,
                   fill=colour + f"{alpha:02x}" if colour.startswith("#")
                   else colour)
            y += step

    return Image.alpha_composite(img, layer)


def render_anim(spec: dict, style: dict, out: Path, fps: int = 30) -> None:
    dur = float(spec.get("dur", style["dur"]))
    grow = 0.45          # столько длится появление
    frames = max(int(dur * fps), 1)
    tmp = Path(tempfile.mkdtemp(prefix="cards-"))
    try:
        for i in range(frames):
            t = i / fps
            reveal = min(t / grow, 1.0) if grow > 0 else 1.0
            # плавное замедление в конце появления
            eased = 1 - (1 - reveal) ** 3
            draw_card(spec, style, eased).save(tmp / f"f{i:05d}.png")
        run([ffmpeg(), "-y", "-v", "error", "-framerate", str(fps),
             "-i", str(tmp / "f%05d.png"), "-c:v", "prores_ks",
             "-profile:v", "4", "-pix_fmt", "yuva444p10le",
             "-alpha_bits", "16", str(out)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    stdout_utf8()
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2

    spec_path = Path(args[0]).expanduser().resolve()
    out_dir = Path(args[1]).expanduser().resolve()
    if not spec_path.exists():
        print(f"нет файла: {spec_path}")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "cards" in raw:
        common_style, cards = raw.get("style", {}), raw["cards"]
    elif isinstance(raw, dict):
        common_style, cards = raw.get("style", {}), [raw]
    else:
        common_style, cards = {}, raw

    for i, spec in enumerate(cards):
        style = dict(DEFAULTS)
        style.update(common_style)
        style.update({k: v for k, v in spec.items() if k in DEFAULTS})
        style["_font"] = find_font(style.get("font"))
        style["_mono"] = find_font(style.get("mono"), mono=True)

        if not has_cyrillic(style["_font"]):
            print(f"внимание: в шрифте {Path(style['_font']).name} нет "
                  f"кириллицы — русский текст выйдет пустыми квадратами")

        name = spec.get("name", f"card{i + 1}")
        img = draw_card(spec, style)
        png = out_dir / f"{name}.png"
        img.save(png)
        line = f"{png.name}  {img.width}x{img.height}  {spec.get('type', 'text')}"

        if "--anim" in args:
            mov = out_dir / f"{name}.mov"
            render_anim(spec, style, mov)
            line += f"  + {mov.name}"
        print(line)

    print(f"\nготово: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
