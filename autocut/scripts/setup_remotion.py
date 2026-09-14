# -*- coding: utf-8 -*-
"""Проверка и заготовка для моушен-графики на Remotion.

    python setup_remotion.py <папка-проекта> [--scaffold]

Без --scaffold только проверяет машину и печатает условия лицензии.
С --scaffold разворачивает в проекте заготовку: package.json, конфиг
и одну композицию-вставку, которую дальше правите под себя.

Сам Remotion скрипт НЕ ставит. Устанавливать его — решение человека,
потому что у него платная лицензия для части компаний.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PKG = {
    "name": "autocut-inserts",
    "private": True,
    "scripts": {
        "studio": "remotion studio",
        "render": "remotion render Insert out/insert.mp4",
    },
    "dependencies": {
        "@remotion/cli": "^4.0.0",
        "remotion": "^4.0.0",
        "react": "^18.3.1",
        "react-dom": "^18.3.1",
    },
}

ROOT_TSX = '''import {Composition} from "remotion";
import {Insert} from "./Insert";

// Размер под полосу кадра: 1080x576 — это полоса 30% у вертикали 1080x1920.
export const RemotionRoot = () => (
  <Composition
    id="Insert"
    component={Insert}
    durationInFrames={120}
    fps={30}
    width={1080}
    height={576}
    defaultProps={{title: "Заголовок", items: ["первый пункт", "второй"]}}
  />
);
'''

INSERT_TSX = '''import {interpolate, useCurrentFrame, useVideoConfig} from "remotion";

// Цвета нейтральные намеренно. Подставьте свои — те же, что задали
// на интейке скилла, иначе вставки будут не в стиле канала.
const BG = "#101010";
const FG = "#F2F2F2";
const ACCENT = "#4C8BF5";

export const Insert: React.FC<{title: string; items: string[]}> = ({
  title,
  items,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  // Появление по частям: сначала заголовок, потом пункты по очереди.
  const reveal = (i: number) => {
    const start = i * 0.12 * fps;
    return {
      opacity: interpolate(frame, [start, start + 0.35 * fps], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      }),
      transform: `translateY(${interpolate(
        frame,
        [start, start + 0.35 * fps],
        [24, 0],
        {extrapolateLeft: "clamp", extrapolateRight: "clamp"}
      )}px)`,
    };
  };

  return (
    <div
      style={{
        flex: 1,
        background: BG,
        color: FG,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: 64,
        fontFamily: "Arial, Helvetica, sans-serif",
        borderLeft: `6px solid ${ACCENT}`,
      }}
    >
      <div style={{fontSize: 84, fontWeight: 700, ...reveal(0)}}>{title}</div>
      {items.map((text, i) => (
        <div key={text} style={{fontSize: 46, marginTop: 18, ...reveal(i + 1)}}>
          • {text}
        </div>
      ))}
    </div>
  );
};
'''

CONFIG = '''import {Config} from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
'''

INDEX_TS = '''import {registerRoot} from "remotion";
import {RemotionRoot} from "./Root";

registerRoot(RemotionRoot);
'''

LICENSE_NOTE = """# Лицензия Remotion — прочитайте до установки

Remotion распространяется НЕ на свободной лицензии.

Бесплатно можно:
  - физическому лицу;
  - коммерческой организации, где не больше 3 сотрудников;
  - некоммерческой организации;
  - для пробы, пока не используете в коммерческой работе.

Всем остальным нужна платная Company License у правообладателя.

Это условие относится только к этой части. Всё остальное в скилле —
рез, звук, субтитры, раскладка, карточки — свободно и таких требований
не имеет. Карточки рисуются локально, быстрее реального времени
и без Node.

Берите Remotion тогда, когда нужна анимация, которую не сделать
карточками. Для обычных вставок он избыточен.
"""


def have(cmd: str) -> str | None:
    return shutil.which(cmd)


def version(exe: str) -> str:
    try:
        res = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=30)
        return (res.stdout or res.stderr or "").strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "не отвечает"


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    project = Path(args[0]).expanduser().resolve()

    print("Моушен-графика на Remotion\n")
    node = have("node")
    # На Windows это npm.cmd; голый «npm» находится, но не запускается.
    npm = have("npm.cmd") or have("npm")
    print(f"node: {version(node) if node else 'НЕ НАЙДЕН'}")
    print(f"npm:  {version(npm) if npm else 'НЕ НАЙДЕН'}")

    print()
    print(LICENSE_NOTE)

    if not node or not npm:
        print("Node не найден. Без него Remotion не работает — ставится")
        print("с nodejs.org или через пакетный менеджер системы.")
        print("Заготовку развернуть можно и сейчас, установить позже.")

    if "--scaffold" not in args:
        print("Развернуть заготовку: запустите ещё раз с ключом --scaffold")
        return 0

    root = project / "remotion"
    (root / "src").mkdir(parents=True, exist_ok=True)
    files = {
        root / "package.json": json.dumps(PKG, ensure_ascii=False, indent=2),
        root / "remotion.config.ts": CONFIG,
        root / "src" / "index.ts": INDEX_TS,
        root / "src" / "Root.tsx": ROOT_TSX,
        root / "src" / "Insert.tsx": INSERT_TSX,
        root / "remotion-license.md": LICENSE_NOTE,
    }
    made = []
    for path, text in files.items():
        if path.exists():
            continue
        path.write_text(text, encoding="utf-8")
        made.append(path.relative_to(project).as_posix())

    print(f"\nзаготовка: {root}")
    for name in made:
        print(f"  создан {name}")
    if not made:
        print("  всё уже было на месте, ничего не трогал")

    print("\nдальше, если решили ставить:")
    print(f"  cd \"{root}\"")
    print("  npm install          — поставит Remotion, это сотни мегабайт")
    print("  npm run studio       — предпросмотр в браузере")
    print("  npm run render       — соберёт out/insert.mp4")
    print("\nГотовый файл кладите в inserts/ проекта и указывайте")
    print("в plan.json как обычную вставку.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
