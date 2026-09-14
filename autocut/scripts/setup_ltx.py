# -*- coding: utf-8 -*-
"""Проверка машины перед установкой локальной генерации видео.

    python setup_ltx.py [--yes]

Ничего не качает и не ставит. Смотрит, потянет ли машина локальную
генерацию видео, и печатает честный вывод: ставить или идти в облако.
Весов там около 37 ГБ, поэтому решение принимает человек, а не скрипт.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

NEED_VRAM_GB = 16
NEED_DISK_GB = 60      # веса плюс запас на файл подкачки


def gpu() -> tuple[str, float] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        res = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    line = (res.stdout or "").strip().splitlines()
    if not line:
        return None
    parts = line[0].split(",")
    name = parts[0].strip()
    mb = "".join(c for c in parts[-1] if c.isdigit())
    return name, (int(mb) / 1024 if mb else 0.0)


def free_disk_gb(path: str = ".") -> float:
    return shutil.disk_usage(path).free / 1024 ** 3


def main() -> int:
    system = platform.system()
    print("Локальная генерация видео — что нужно машине\n")

    card = gpu()
    disk = free_disk_gb(Path.home().anchor or ".")

    print(f"система:        {system} {platform.machine()}")
    if card:
        print(f"видеокарта:     {card[0]}, {card[1]:.0f} ГБ видеопамяти")
    elif system == "Darwin":
        print("видеокарта:     Apple Silicon / нет NVIDIA")
    else:
        print("видеокарта:     NVIDIA не найдена")
    print(f"свободно на диске: {disk:.0f} ГБ")

    print()
    verdict_local = True
    if system == "Darwin":
        print("На маке локальной генерации НЕ БУДЕТ. Модели этого класса")
        print("считаются на CUDA, а её на маке не существует. Это не наша")
        print("недоработка и не чинится настройками.")
        verdict_local = False
    elif not card:
        print("Видеокарта NVIDIA не найдена. Локальная генерация не пойдёт.")
        verdict_local = False
    # Округляем: карта, которую продают как 16 ГБ, рапортует 15,93,
    # и строгое сравнение отсекало заведомо рабочее железо.
    elif round(card[1]) < NEED_VRAM_GB:
        print(f"Видеопамяти {card[1]:.1f} ГБ, нужно от {NEED_VRAM_GB}.")
        print("Модель не поместится.")
        verdict_local = False

    if verdict_local and disk < NEED_DISK_GB:
        print(f"Места на диске {disk:.0f} ГБ, нужно от {NEED_DISK_GB}:")
        print("около 37 ГБ занимают веса, остальное — рабочий запас.")
        verdict_local = False

    print()
    if verdict_local:
        print("МАШИНА ПОТЯНЕТ. Что предстоит, чтобы решение было осознанным:")
        print("  1. Поставить ComfyUI — это отдельная программа, не пакет.")
        print("  2. Скачать веса, около 37 ГБ. На обычном канале это часы.")
        print("  3. Разложить файлы по папкам моделей и взять воркфлоу.")
        print("  4. Держать сервер запущенным во время генерации.")
        print()
        print("Чего ждать по скорости: 4 секунды видео считаются примерно")
        print("две минуты на карте уровня 5060 Ti. На слабее — кратно дольше.")
        print()
        print("Подводный камень Windows: пока сервер держит модель, файл")
        print("подкачки раздувается до ~45 ГБ на системном диске. Проверяйте")
        print("свободное место и останавливайте сервер после работы.")
        print()
        print("Скрипт сознательно ничего не качает сам: 37 ГБ и установка")
        print("отдельной программы — это решение человека.")
    else:
        print("ЛОКАЛЬНО НЕ ПОЙДЁТ. Это не тупик, есть два пути:")
        print("  - облачная генерация по своему ключу: работает на любой")
        print("    машине, включая мак, платится за клип;")
        print("  - обойтись без видео-перебивок: карточки-вставки рисуются")
        print("    локально, бесплатно и быстрее реального времени.")

    print()
    print("Лицензия весов: свободно для компаний с выручкой до 10 млн")
    print("долларов в год, выше — нужен отдельный договор с правообладателем.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
