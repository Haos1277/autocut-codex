# -*- coding: utf-8 -*-
"""Шаг 6. Звук: диагностика и починка.

    python clean_audio.py <файл> <проект> [--render] [ключи]

Сначала меряет запись и печатает диагноз: шум, клиппинг, громкость,
завалы по частотам. Чинит только то, что действительно сломано — глушить
шум там, где его нет, значит съесть воздух в голосе.

Ключи:
    --like <файл>   подогнать тембр под эталон: берём звук, который вам
                    нравится, и подтягиваем свой под его форму спектра
    --lufs -14      целевая громкость, LUFS (-14 для сети, -16 тише,
                    -23 телевещание)
    --no-denoise    не трогать шум
    --render        применить и записать файл; без него только диагноз

Если в исходнике есть видео, картинка копируется без перекодирования —
лишний проход через кодек только съест качество.

Всё делается средствами ffmpeg. Нейросетевого подавления эха здесь нет
намеренно: рабочие модели идут под GPL-3.0 и тянут torch, а это и лицензия
несовместимая с MIT, и тяжёлая установка.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from common import ffmpeg, has_video, run, stdout_utf8

BANDS = [(60, 120), (120, 250), (250, 500), (500, 1000), (1000, 2000),
         (2000, 4000), (4000, 6000), (6000, 8000), (8000, 12000),
         (12000, 16000)]
MAX_EQ = 6.0        # больше не двигаем: это уже не подгонка, а искажение
CLIP_LEVEL = 0.985


def to_wav(src: Path, dst: Path, rate: int = 48000) -> None:
    run([ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", str(rate), str(dst)])


def load(path: Path) -> tuple[np.ndarray, int]:
    sr, data = wavfile.read(str(path))
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float64) / float(np.iinfo(data.dtype).max)
    return data.astype(np.float64), sr


def band_levels(data: np.ndarray, sr: int) -> dict[tuple[int, int], float]:
    """Форма спектра по полосам, в дБ относительно общего уровня.

    Нормируем на общий RMS, а не приводим громкость фильтрами: loudnorm
    и компрессор подтягивают тихие места, а они богаче верхом — и замер
    формы спектра после них врёт.
    """
    from scipy.signal import welch
    freqs, power = welch(data, sr, nperseg=min(8192, len(data)))
    total = np.sqrt(np.mean(data ** 2)) + 1e-12
    out = {}
    for lo, hi in BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        if not sel.any():
            continue
        energy = np.trapezoid(power[sel], freqs[sel]) if hasattr(np, "trapezoid") \
            else np.trapz(power[sel], freqs[sel])
        out[(lo, hi)] = 10 * np.log10(energy / total ** 2 + 1e-20)
    return out


def measure(path: Path) -> dict:
    data, sr = load(path)
    win = int(sr * 0.02)
    frames = len(data) // win
    rms = np.sqrt((data[:frames * win].reshape(frames, win) ** 2).mean(axis=1) + 1e-12)
    level = 20 * np.log10(rms)
    return {
        "sr": sr,
        "floor": float(np.percentile(level, 10)),
        "speech": float(np.percentile(level, 90)),
        "peak": float(20 * np.log10(np.abs(data).max() + 1e-12)),
        "clipped": float((np.abs(data) > CLIP_LEVEL).mean()),
        "bands": band_levels(data, sr),
        "data": data,
    }


def loudness(path: Path) -> dict:
    """Замер громкости по стандарту EBU R128 первым проходом loudnorm."""
    res = subprocess.run(
        [ffmpeg(), "-hide_banner", "-i", str(path), "-af",
         "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = res.stderr or ""
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def main() -> int:
    stdout_utf8()
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2

    src = Path(args[0]).expanduser().resolve()
    project = Path(args[1]).expanduser().resolve()
    if not src.exists():
        print(f"нет файла: {src}")
        return 2

    def val(name, default):
        return args[args.index(name) + 1] if name in args else default

    target_lufs = float(val("--lufs", -14))
    like = val("--like", None)

    tmp = Path(tempfile.mkdtemp(prefix="autocut-"))
    try:
        probe = tmp / "probe.wav"
        to_wav(src, probe)
        m = measure(probe)
        snr = m["speech"] - m["floor"]
        # LUFS меряем на самом файле, а не на моно-копии: сведение стерео
        # в моно занижает замер примерно на 3 дБ, и отчёт начинает врать
        # (показывал -17 там, где на деле было -14).
        lg = loudness(src)

        print(f"файл: {src.name}\n")
        print(f"речь        {m['speech']:6.1f} дБ")
        print(f"шум         {m['floor']:6.1f} дБ")
        print(f"запас       {snr:6.1f} дБ", end="  ")
        print("— тихий шум" if snr > 40 else
              "— шум слышно" if snr > 25 else "— ШУМА МНОГО")
        print(f"пик         {m['peak']:6.1f} дБ")
        if m["clipped"] > 0:
            print(f"клиппинг    {m['clipped'] * 100:6.3f}% отсчётов срезано")
        if lg:
            print(f"громкость   {float(lg.get('input_i', 0)):6.1f} LUFS "
                  f"(цель {target_lufs})")

        # --- что чиним
        chain, notes = [], []

        chain.append("highpass=f=70")
        notes.append("срез гула ниже 70 Гц")

        if m["clipped"] > 0.0001:
            chain.append("adeclip")
            notes.append("восстановление срезанных пиков")

        if "--no-denoise" not in args:
            if snr < 45:
                nf = int(round(min(max(m["floor"], -80), -20)))
                nr = 12 if snr > 30 else 20
                chain.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
                notes.append(f"подавление шума на {nr} дБ (порог {nf} дБ)")
            else:
                notes.append("шум не трогаем — его и так почти нет")

        # --- подгонка тембра под эталон
        if like:
            ref_path = Path(like).expanduser().resolve()
            if not ref_path.exists():
                print(f"\nнет эталона: {ref_path}")
                return 2
            ref_wav = tmp / "ref.wav"
            to_wav(ref_path, ref_wav)
            ref = measure(ref_wav)
            print(f"\nподгоняю тембр под {ref_path.name}:")
            biggest = 0.0
            for band in BANDS:
                if band not in m["bands"] or band not in ref["bands"]:
                    continue
                diff = ref["bands"][band] - m["bands"][band]
                biggest = max(biggest, abs(diff))
                if abs(diff) < 1.0:
                    continue
                gain = max(-MAX_EQ, min(MAX_EQ, diff))
                lo, hi = band
                centre = int((lo * hi) ** 0.5)
                width = hi - lo
                chain.append(f"equalizer=f={centre}:t=h:w={width}:g={gain:.1f}")
                print(f"  {lo}-{hi} Гц: {gain:+.1f} дБ")
            notes.append("подгонка тембра под эталон")
            if biggest > 4:
                print(f"\n  Правка крупная (до {biggest:.1f} дБ) — записи сильно "
                      f"разные.\n  Подъём верха вытягивает и остатки шума: "
                      f"эквалайзер не различает,\n  где голос, а где шипение. "
                      f"Послушайте результат, и если зашумело —\n  возьмите "
                      f"эталон, записанный похожим микрофоном в похожей "
                      f"комнате.")

        notes.append(f"громкость к {target_lufs} LUFS")

        print("\nчто делаем:")
        for n in notes:
            print(f"  - {n}")

        if "--render" not in args:
            print("\nфайл не трогал. Устраивает — запусти ещё раз с --render")
            return 0

        out_dir = project / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / ("clean" + (".mp4" if has_video(src) else ".wav"))

        # Громкость выставляем ДВУМЯ проходами, и это принципиально.
        # loudnorm за один проход работает динамически — как компрессор:
        # подтягивает тихие места, а тихие места это и есть шум. Замер
        # на голосовухе: запас упал с 29,7 до 26,9 дБ, то есть чистка
        # шума ушла впустую. Во втором проходе передаём замеренные
        # значения и linear=true, тогда это простое статическое усиление
        # и шум остаётся там, где был.
        print("\nчиню звук...", flush=True)
        repaired = tmp / "repaired.wav"
        run([ffmpeg(), "-y", "-v", "error", "-i", str(src), "-vn",
             "-af", ",".join(chain), "-ar", "48000", "-c:a", "pcm_s24le",
             str(repaired)])

        mr = measure(repaired)
        lr = loudness(repaired)
        print("меряю громкость и выравниваю...", flush=True)

        # loudnorm не годится даже во втором проходе. Он умеет линейный
        # режим, но только когда запаса по пикам хватает; у горячего
        # материала (пик 0 дБ — обычное дело для телефонных записей)
        # он молча откатывается в динамический и снова душит тишину.
        # Замер: запас падал с 30,1 до 27,0 дБ, а до цели всё равно
        # не дотягивало.
        # Поэтому вручную: статическое усиление на нужную величину плюс
        # лимитер по пикам. Лимитер трогает только верхушки и не поднимает
        # тихие места, поэтому шум остаётся внизу.
        # Сколько усиления нужно — подбираем итеративно: лимитер сам
        # съедает часть прибавки, и «цель минус замер» промахивается
        # (с первого раза недотянуло 1,5 LUFS). Гоняем на временном
        # файле, это доли секунды, и только потом рендерим начисто.
        gain = target_lufs - float(lr["input_i"]) if lr else 0.0
        if gain > 6:
            print(f"  внимание: не хватает {gain:.1f} дБ, лимитеру придётся "
                  f"много срезать — звук может стать плоским")
        probe_gain = tmp / "gain.wav"
        for _ in range(3):
            run([ffmpeg(), "-y", "-v", "error", "-i", str(repaired), "-af",
                 f"volume={gain:.2f}dB,alimiter=limit=-1.5dB:level=false",
                 "-c:a", "pcm_s24le", str(probe_gain)])
            got = loudness(probe_gain)
            if not got:
                break
            err = target_lufs - float(got["input_i"])
            if abs(err) < 0.3:
                break
            gain += err
        norm = f"volume={gain:.2f}dB,alimiter=limit=-1.5dB:level=false"

        cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(src),
               "-i", str(repaired), "-map", "1:a:0", "-af", norm]
        if has_video(src):
            # картинку копируем: лишний прогон через кодек съест качество
            cmd += ["-map", "0:v:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-c:a", "pcm_s16le"]
        cmd.append(str(dst))
        run(cmd)

        after = tmp / "after.wav"
        to_wav(dst, after)
        ma = measure(after)
        la = loudness(dst)
        print(f"готово: {dst}\n")
        print("было -> стало:")
        print(f"  шум       {m['floor']:6.1f} -> {ma['floor']:6.1f} дБ")
        print(f"  запас     {snr:6.1f} -> {ma['speech'] - ma['floor']:6.1f} дБ"
              f"   (после чистки, до громкости: {mr['speech'] - mr['floor']:.1f})")
        if lg and la:
            print(f"  громкость {float(lg.get('input_i', 0)):6.1f} -> "
                  f"{float(la.get('input_i', 0)):6.1f} LUFS")

        # Сошлась ли подгонка тембра: считаем остаточное расхождение
        # с эталоном. Иначе неясно, сработал эквалайзер или нет.
        if like:
            before_gap = max(abs(ref["bands"][b] - m["bands"][b])
                             for b in BANDS if b in m["bands"] and b in ref["bands"])
            after_gap = max(abs(ref["bands"][b] - ma["bands"][b])
                            for b in BANDS if b in ma["bands"] and b in ref["bands"])
            print(f"  тембр     расхождение с эталоном "
                  f"{before_gap:.1f} -> {after_gap:.1f} дБ")

        cost = (mr["speech"] - mr["floor"]) - (ma["speech"] - ma["floor"])
        if cost > 2:
            print(f"\n  Запас съеден на {cost:.1f} дБ при подтягивании "
                  f"громкости.\n  Материал горячий, и чтобы дотянуть "
                  f"до {target_lufs} LUFS, лимитеру пришлось поработать.\n"
                  f"  Если на слух стало плоско — возьмите цель тише: "
                  f"--lufs {target_lufs - 2:.0f}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
