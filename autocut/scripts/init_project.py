# -*- coding: utf-8 -*-
"""Шаг 0. Завести проект: папки и памятка, что куда класть.

    python init_project.py <папка-проекта> [--source путь-к-записи]

Создаёт разложенную структуру и кладёт внутрь памятку. Если указать
--source, исходная запись сразу копируется в input/.

Папки называются латиницей намеренно: кириллица в путях ломается
на чужих системах и в некоторых инструментах. Памятка внутри — на русском.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from common import duration, has_video, stdout_utf8

FOLDERS = {
    "input": "сюда кладём исходные записи, как сняли",
    "inserts": "карточки и картинки для вставок",
    "work": "служебное: расшифровки, карты речи, планы. Можно удалять",
    "out": "результат: черновой рез, ролик со вставками, субтитры",
}

GUIDE = """# Проект автомонтажа

## Что куда класть

| Папка | Что в ней |
|---|---|
| `input/` | **исходные записи**, как сняли. Сюда кладёте свой файл |
| `inserts/` | карточки и картинки, которые лягут в кадр |
| `work/` | служебное: расшифровки, карты речи, планы. Можно удалять |
| `out/` | **результат**: черновой рез, готовый ролик, файлы субтитров |

Файлы в корне проекта:

- `style.json` — ваши настройки: язык, плотность реза, стиль субтитров.
  Появляется после интейка, править можно руками.
- `cards.json` — описание карточек-вставок.
- `plan.json` — раскладка ролика по кускам: где что показываем.

## Порядок работы

1. Положить запись в `input/`.
2. Разобрать: `prepare.py`, `transcribe.py`, `speech_map.py`.
3. Черновой рез: `rough_cut.py` — сначала без ключей (покажет план),
   потом с `--render`.
4. Проверить: `check_output.py`.
5. Звук: `clean_audio.py`.
6. Вставки: описать в `cards.json`, нарисовать `cards.py`.
7. Раскладка: описать в `plan.json`, собрать `compose.py`.
8. Субтитры: `subtitles.py --plan plan.json` — ключ `--plan` обязателен,
   иначе субтитры лягут поверх вставок.

Ничего не рендерится без явной команды: сначала каждый шаг показывает,
что собирается сделать.

## Чего ждать не надо

- Скилл **не выбирает, что оставить в ролике**. Он убирает мусор:
  паузы, переснятые дубли, слова-паразиты. Решение «эта мысль лишняя»
  остаётся за вами.
- Запинки («эээ», «ммм») автоматически не убираются: распознавание
  их не записывает, а по громкости они речь.
"""

CARDS_EXAMPLE = {
    "style": {"bg": "#101010", "fg": "#F2F2F2", "accent": "#4C8BF5"},
    "cards": [
        {"name": "a", "size": [1080, 576], "type": "list", "dur": 4.0,
         "title": "Заголовок карточки",
         "items": ["первый пункт", "второй пункт"]},
        {"name": "b", "size": [1080, 1920], "type": "stat", "dur": 3.0,
         "value": "24%", "caption": "подпись под числом"},
    ],
}

PLAN_EXAMPLE = {
    "source": "out/clean.mp4",
    "out": "out/final.mp4",
    "canvas": [1080, 1920], "fps": 30, "bg": "#101010", "fill": "blur",
    "segments": [
        {"start": 0.0, "end": 4.0, "layout": "none", "mode": "fit"},
        {"start": 4.0, "end": 8.0, "layout": "70/30",
         "insert": "inserts/a.mp4", "mode": "fit"},
        {"start": 8.0, "end": 11.0, "layout": "full",
         "insert": "inserts/b.mp4"},
    ],
}


def main() -> int:
    stdout_utf8()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    project = Path(args[0]).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)

    made, existed = [], []
    for name in FOLDERS:
        folder = project / name
        (existed if folder.exists() else made).append(name)
        folder.mkdir(exist_ok=True)

    guide = project / "README.md"
    if not guide.exists():
        guide.write_text(GUIDE, encoding="utf-8")

    for name, data in (("cards.json", CARDS_EXAMPLE),
                       ("plan.json", PLAN_EXAMPLE)):
        f = project / name
        if not f.exists():
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    print(f"проект: {project}\n")
    for name, what in FOLDERS.items():
        mark = "создана" if name in made else "была"
        print(f"  {name + '/':10s} {mark:8s} — {what}")
    print("\n  README.md   памятка, что куда класть")
    print("  cards.json  образец описания вставок")
    print("  plan.json   образец раскладки ролика")

    # исходник, если дали
    src_arg = args[args.index("--source") + 1] if "--source" in args else None
    if src_arg:
        src = Path(src_arg).expanduser().resolve()
        if not src.exists():
            print(f"\nнет файла: {src}")
            return 2
        dst = project / "input" / src.name
        if dst.exists():
            print(f"\nуже лежит: {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"\nисходник скопирован: {dst}")
        print(f"  длина {duration(dst):.1f} с, "
              f"{'видео' if has_video(dst) else 'только звук'}")
        print(f"\nдальше: python prepare.py \"{dst}\" \"{project}\"")
    else:
        print(f"\nПоложите запись в {project / 'input'} и запустите prepare.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
