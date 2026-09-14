# -*- coding: utf-8 -*-
"""Шаг 8б. Сборка кадра: ведущий и вставки.

    python compose.py <план.json> [--render]

Без --render только проверяет план и печатает разбор: ритм, чередование
раскладок, длину фуллскринов, наличие файлов. Рендерит только по команде.

План — обычный json, его пишет Claude, прочитав расшифровку:

{
 "source": "out/clean.mp4",
 "bg": "#000000",
 "segments": [
  {"start": 0.0,  "end": 4.2,  "layout": "none"},
  {"start": 4.2,  "end": 8.0,  "layout": "70/30", "insert": "cards/a.png"},
  {"start": 8.0,  "end": 11.0, "layout": "full",  "insert": "cards/b.mp4"},
  {"start": 11.0, "end": 15.0, "layout": "50/50", "insert": "cards/c.png"},
  {"start": 15.0, "end": 19.0, "layout": "over",  "insert": "cards/d.png"}
 ]
}

Раскладки:
    none    ведущий на весь кадр, вставки нет
    70/30   ведущий сверху 70% высоты, вставка снизу 30%
    50/50   пополам
    full    вставка на весь кадр, ведущего не видно
    over    ведущий на весь кадр, вставка НАКЛАДЫВАЕТСЯ поверх

Как подать ведущего в свою полосу, задаётся ключом "mode":
    crop    обрезать до полосы — крупно, но низ кадра уедет
    fit     вписать кадр целиком — лицо целое, но мелкое (по умолчанию)

Что выбрать, говорит layout_probe.py. Если он сказал «поверх» — брать
раскладку over, а не делить кадр: у селфи-кадра деление отрезает рот
и микрофон.

Вставка вписывается в свою полосу с заполнением: лишнее по краям
обрезается, пустых полей не остаётся. Картинка держится всю длину куска,
видео при нехватке длины повторяется.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import (duration, ffmpeg, fmt_time, has_video, run, stdout_utf8,
                    video_fps, video_size)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
FULL_MIN, FULL_MAX = 2.0, 4.0     # сколько держать фуллскрин
RHYTHM_MIN, RHYTHM_MAX = 2.5, 5.0  # комфортная длина куска
OVER_W = 0.86        # ширина накладываемой вставки, доля кадра
OVER_BOTTOM = 0.72   # где её низ, доля высоты: выше зоны субтитров
SHARES = {"70/30": 0.70, "50/50": 0.50}


def even(n: float) -> int:
    """Кодеку нужны чётные размеры."""
    return int(round(n / 2) * 2)


def hex_to_ff(colour: str) -> str:
    c = colour.lstrip("#")
    return "0x" + (c if len(c) == 6 else "000000")


def band_geometry(layout: str, w: int, h: int) -> tuple[tuple, tuple] | None:
    """Полосы (ширина, высота, y) для ведущего и для вставки."""
    if layout in SHARES:
        top = even(h * SHARES[layout])
        return (w, top, 0), (w, h - top, top)
    return None


def presenter_chain(src_label: str, out_label: str, layout: str, mode: str,
                    w: int, h: int, src_w: int, src_h: int,
                    fill: str, bg: str) -> list[str]:
    """Как привести кадр ведущего к его полосе. Возвращает звенья фильтра."""
    geo = band_geometry(layout, w, h)
    if geo is None:
        return [f"[{src_label}]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1[{out_label}]"]
    (bw, bh, _), _ = geo

    if mode == "crop":
        # Масштабируем по той стороне, которой не хватает, и обрезаем
        # излишек по другой. Кадр 9:16 (0,56) в полосу 70% высоты (0,80)
        # тянем по ШИРИНЕ и режем высоту; перепутать оси нельзя — обрезка
        # окажется шире исходника, и ffmpeg упадёт.
        # Режем сверху: голова говорящего почти всегда в верхней части.
        if src_w / src_h <= bw / bh:
            f = f"scale={bw}:-2,crop={bw}:{bh}:0:0,setsar=1"
        else:
            f = f"scale=-2:{bh},crop={bw}:{bh}:(iw-{bw})/2:0,setsar=1"
        return [f"[{src_label}]{f}[{out_label}]"]

    fit = (f"scale={bw}:{bh}:force_original_aspect_ratio=decrease,setsar=1")
    if fill != "blur":
        return [f"[{src_label}]{fit},"
                f"pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color={bg}[{out_label}]"]

    # Вписанный вертикальный кадр оставляет по бокам пустые полосы,
    # и однотонные столбы выглядят недоделанно. Заполняем размытой
    # копией самого кадра. Размытие делаем дёшево — уменьшить, размыть,
    # вернуть размер: полноценный gblur с большим радиусом на каждом
    # кадре считается заметно дольше, а на глаз разницы нет.
    small_w, small_h = max(even(bw / 12), 2), max(even(bh / 12), 2)
    return [
        f"[{src_label}]split=2[pf][pb]",
        f"[pb]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
        f"crop={small_w}:{small_h},gblur=sigma=3,scale={bw}:{bh},setsar=1[pbg]",
        f"[pf]{fit}[pfg]",
        f"[pbg][pfg]overlay=(W-w)/2:(H-h)/2[{out_label}]",
    ]


def insert_filter(w: int, h: int) -> str:
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1")


def build_segment(seg: dict, plan: dict, base: Path, out: Path,
                  size: tuple[int, int], fps: float) -> None:
    w, h = size
    src = (base / plan["source"]).resolve()
    start = float(seg["start"])
    dur = float(seg["end"]) - start
    layout = seg.get("layout", "none")
    mode = seg.get("mode", "fit")
    bg = hex_to_ff(plan.get("bg", "#000000"))

    src_w, src_h = plan["_src_size"]

    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    ins = seg.get("insert")
    ins_path = (base / ins).resolve() if ins else None
    if ins_path is not None:
        if ins_path.suffix.lower() in IMAGE_EXT:
            inputs = ["-loop", "1", "-t", f"{dur:.3f}",
                      "-i", str(ins_path)] + inputs
        else:
            inputs = ["-stream_loop", "-1", "-t", f"{dur:.3f}",
                      "-i", str(ins_path)] + inputs
        src_idx, ins_idx = 1, 0
    else:
        src_idx, ins_idx = 0, None

    parts = [f"[{src_idx}:a]aresample=async=1:first_pts=0,"
             f"asetpts=PTS-STARTPTS[oa]"]

    fill = seg.get("fill", plan.get("fill", "blur"))

    if layout == "full" and ins_idx is not None:
        parts.append(f"[{ins_idx}:v]{insert_filter(w, h)},fps={fps:.6f}[ov]")
    elif layout == "over" and ins_idx is not None:
        ow = even(w * OVER_W)
        oh = even(ow * 9 / 16)
        parts += presenter_chain(f"{src_idx}:v", "pv0", "none", mode,
                                 w, h, src_w, src_h, fill, bg)
        parts.append(f"[pv0]fps={fps:.6f}[pv]")
        parts.append(f"[{ins_idx}:v]{insert_filter(ow, oh)}[iv]")
        y = even(h * OVER_BOTTOM) - oh
        parts.append(f"[pv][iv]overlay=(W-w)/2:{y}[ov]")
    elif layout in SHARES and ins_idx is not None:
        (bw, bh, _), (iw2, ih2, iy) = band_geometry(layout, w, h)
        parts.append(f"color=c={bg}:s={w}x{h}:d={dur:.3f}:r={fps:.6f}[bgv]")
        parts += presenter_chain(f"{src_idx}:v", "pv0", layout, mode,
                                 w, h, src_w, src_h, fill, bg)
        parts.append(f"[pv0]fps={fps:.6f}[pv]")
        parts.append(f"[{ins_idx}:v]{insert_filter(iw2, ih2)}[iv]")
        parts.append(f"[bgv][pv]overlay=0:0:shortest=1[st1]")
        parts.append(f"[st1][iv]overlay=0:{iy}[ov]")
    else:
        parts += presenter_chain(f"{src_idx}:v", "pv0", "none", mode,
                                 w, h, src_w, src_h, fill, bg)
        parts.append(f"[pv0]fps={fps:.6f}[ov]")

    cmd = [ffmpeg(), "-y", "-v", "error", *inputs,
           "-filter_complex", ";".join(parts),
           "-map", "[ov]", "-map", "[oa]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", f"{fps:.6f}",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-t", f"{dur:.3f}", str(out)]
    run(cmd)


def check(plan: dict, base: Path) -> list[str]:
    """Разбор плана. Возвращает замечания."""
    notes = []
    segs = plan["segments"]

    prev_layout = None
    for i, s in enumerate(segs):
        dur = float(s["end"]) - float(s["start"])
        layout = s.get("layout", "none")

        if dur <= 0:
            notes.append(f"кусок {i}: длина {dur:.2f} с — конец не позже начала")
        if layout not in ("none", "full", "over", *SHARES):
            notes.append(f"кусок {i}: неизвестная раскладка «{layout}»")
        if layout != "none" and not s.get("insert"):
            notes.append(f"кусок {i}: раскладка «{layout}» без вставки")
        if s.get("insert"):
            p = (base / s["insert"]).resolve()
            if not p.exists():
                notes.append(f"кусок {i}: нет файла вставки {s['insert']}")

        if layout == "full":
            if dur > FULL_MAX:
                notes.append(f"кусок {i}: фуллскрин {dur:.1f} с — дольше "
                             f"{FULL_MAX:.0f} с, ведущий пропадает")
            elif dur < FULL_MIN:
                notes.append(f"кусок {i}: фуллскрин {dur:.1f} с — мелькнёт")

        if layout == prev_layout and layout != "none":
            notes.append(f"кусок {i}: раскладка «{layout}» второй раз подряд "
                         f"— чередование теряется")
        prev_layout = layout

        if i > 0 and abs(float(s["start"]) - float(segs[i - 1]["end"])) > 0.01:
            notes.append(f"кусок {i}: начало не сходится с концом предыдущего")

        if dur > RHYTHM_MAX:
            notes.append(f"кусок {i}: {dur:.1f} с без смены картинки "
                         f"— дольше {RHYTHM_MAX:.0f} с зритель отваливается")
    return notes


def main() -> int:
    stdout_utf8()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    plan_path = Path(args[0]).expanduser().resolve()
    if not plan_path.exists():
        print(f"нет файла плана: {plan_path}")
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    base = plan_path.parent

    src = (base / plan["source"]).resolve()
    if not src.exists():
        print(f"нет исходника: {src}")
        return 2
    if not has_video(src):
        print("в исходнике нет видео")
        return 2

    sw, sh = video_size(src)
    plan["_src_size"] = (sw, sh)
    size = tuple(plan.get("canvas", [sw, sh]))
    fps = float(plan.get("fps", video_fps(src)))

    segs = plan["segments"]
    total = sum(float(s["end"]) - float(s["start"]) for s in segs)
    counts: dict[str, int] = {}
    for s in segs:
        counts[s.get("layout", "none")] = counts.get(s.get("layout", "none"), 0) + 1

    print(f"исходник: {src.name}  {sw}x{sh}")
    print(f"кадр:     {size[0]}x{size[1]}, {fps:.3g} кадр/с")
    print(f"кусков:   {len(segs)}, всего {fmt_time(total)}")
    print(f"смена картинки: раз в {total / len(segs):.1f} с в среднем")
    print("раскладки: " + ", ".join(f"{k} x{v}" for k, v in counts.items()))
    print()
    for i, s in enumerate(segs):
        dur = float(s["end"]) - float(s["start"])
        ins = s.get("insert", "")
        print(f"  {i:2d} {fmt_time(float(s['start']))}-{fmt_time(float(s['end']))}"
              f" ({dur:4.1f}с)  {s.get('layout', 'none'):6s}"
              f" {s.get('mode', 'fit'):5s}  {ins}")

    notes = check(plan, base)
    if notes:
        print(f"\nзамечания ({len(notes)}):")
        for n in notes:
            print(f"  - {n}")
    else:
        print("\nплан без замечаний")

    if "--render" not in args:
        print("\nне рендерил. Устраивает — запусти с --render")
        return 1 if notes else 0

    tmp = Path(tempfile.mkdtemp(prefix="compose-"))
    try:
        print("\nсобираю куски...", flush=True)
        files = []
        for i, s in enumerate(segs):
            part = tmp / f"p{i:03d}.mp4"
            build_segment(s, plan, base, part, size, fps)
            files.append(part)
            print(f"  {i + 1}/{len(segs)}", end="\r", flush=True)

        listing = tmp / "list.txt"
        listing.write_text("".join(
            f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
        dst = base / plan.get("out", "out/composed.mp4")
        dst.parent.mkdir(parents=True, exist_ok=True)
        run([ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
             str(dst)])
        print(f"\nготово: {dst}")

        got = duration(dst)
        print(f"длина: {fmt_time(got)} (по плану {fmt_time(total)}, "
              f"разница {(got - total) * 1000:+.0f} мс)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())