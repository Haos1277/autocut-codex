# -*- coding: utf-8 -*-
"""Генерация картинок-вставок.

    python gen_image.py --prompt "..." --out cards/a.png [--size 1080x576]
    python gen_image.py --prompt-file p.txt --out a.png --ref фото.png
    python gen_image.py --prompt "..." --out a.png --dry

Работает через ваш собственный ключ. Ключ ищется так:
  1. переменная окружения GEMINI_API_KEY;
  2. файл .env рядом с проектом или в текущей папке, строка GEMINI_API_KEY=...

Ключ нигде не печатается и никуда не записывается. Файл .env закрыт
от git — если вы положите его в репозиторий, он туда не попадёт.

ВАЖНО, скажите это вслух заказчику: генерация — обращение к чужому
серверу. Ваш текст запроса и приложенные картинки уходят наружу,
к владельцу модели. Всё остальное в этом наборе работает на вашей
машине, а этот шаг — нет.

Ключи:
    --size 1080x576   довести до точного размера полосы кадра
    --ref файл        картинка-образец, можно несколько
    --model ...       по умолчанию самая качественная
    --ar 16:9         соотношение сторон запроса (обычно считается из --size)
    --tries 5         сколько раз повторить при перегрузке сервиса
    --dry             ничего не отправлять, только показать, что ушло бы
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# Поток ошибок тоже: sys.exit печатает именно в него, и без этого
# русские сообщения об ошибках выходят кракозябрами в консоли Windows.
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3-pro-image"
# Соотношения, которые принимает сервис. Точный размер полосы почти
# никогда им не совпадает, поэтому берём ближайшее, а до пикселя
# доводим сами.
SUPPORTED_AR = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9,
                "9:16": 9 / 16, "3:2": 1.5, "2:3": 2 / 3}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp"}


def api_key(project: Path | None) -> str:
    """Ключ из окружения или из .env. Никогда не печатается."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    for folder in filter(None, (project, Path.cwd())):
        env = folder / ".env"
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                got = line.split("=", 1)[1].strip().strip('"').strip("'")
                if got:
                    return got
    sys.exit(
        "Ключа нет. Заведите свой и положите его одним из двух способов:\n"
        "  переменная окружения GEMINI_API_KEY\n"
        "  файл .env рядом с проектом, строка GEMINI_API_KEY=ваш_ключ\n"
        "Свой ключ создаётся в Google AI Studio. Чужие ключи не подходят:\n"
        "генерация платная, и платит владелец ключа.\n"
        "Генерация не обязательна — без неё соберутся карточки-вставки."
    )


def nearest_ar(width: int, height: int) -> str:
    target = width / height
    return min(SUPPORTED_AR, key=lambda k: abs(SUPPORTED_AR[k] - target))


def fit_exact(path: Path, size: tuple[int, int]) -> None:
    """Довести картинку до точного размера полосы: заполнить и обрезать."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow не установлен — оставляю картинку как пришла")
        return
    w, h = size
    img = Image.open(path)
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(int(img.width * scale), w),
                      max(int(img.height * scale), h)), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    img.crop((left, top, left + w, top + h)).save(path)
    print(f"приведено к {w}x{h}")


def build_body(prompt: str, refs: list[str], ar: str) -> dict:
    parts: list[dict] = [{"text": prompt}]
    for ref in refs:
        data = Path(ref).read_bytes()
        parts.append({"inline_data": {
            "mime_type": MIME.get(Path(ref).suffix.lower(), "image/png"),
            "data": base64.b64encode(data).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]}}
    if ar:
        body["generationConfig"]["imageConfig"] = {"aspectRatio": ar}
    return body


def request(body: dict, model: str, key: str, tries: int) -> dict:
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(
            API.format(model=model), data=payload,
            headers={"x-goog-api-key": key,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:400]
            # 429 и 5xx — сервис перегружен, это лечится ожиданием
            if e.code in (429, 500, 502, 503) and attempt < tries:
                wait = 15 * attempt
                print(f"сервис ответил {e.code}, попытка {attempt} "
                      f"из {tries}, жду {wait} с...", flush=True)
                time.sleep(wait)
                continue
            if e.code in (401, 403):
                sys.exit(f"сервис отказал ({e.code}). Проверьте свой ключ "
                         f"и что генерация картинок для него включена.")
            sys.exit(f"ошибка {e.code}: {text}")
        except urllib.error.URLError as e:
            if attempt < tries:
                print(f"сеть недоступна ({e.reason}), повтор...", flush=True)
                time.sleep(5 * attempt)
                continue
            sys.exit(f"сеть недоступна: {e.reason}")
    sys.exit(f"не получилось за {tries} попыток")


def save_images(data: dict, out: Path) -> list[Path]:
    cands = data.get("candidates") or []
    if not cands:
        sys.exit("пустой ответ сервиса: " + json.dumps(data)[:600])
    saved: list[Path] = []
    for i, part in enumerate(cands[0].get("content", {}).get("parts", [])):
        inline = part.get("inlineData") or part.get("inline_data")
        if inline:
            # расширение берём из ответа: модель отдаёт то jpeg, то png
            mime = inline.get("mimeType") or inline.get("mime_type")
            ext = {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")
            base = out.with_suffix("")
            path = Path(str(base) + ("" if not saved else f"_{i}") + ext)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(inline["data"]))
            saved.append(path)
        elif part.get("text"):
            print("модель ответила текстом:", part["text"][:400])
    if not saved:
        sys.exit(f"картинки в ответе нет "
                 f"(причина завершения: {cands[0].get('finishReason')})")
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ref", action="append", default=[])
    ap.add_argument("--size", help="точный размер полосы, например 1080x576")
    ap.add_argument("--ar")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--tries", type=int, default=5)
    ap.add_argument("--project")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    prompt = a.prompt
    if a.prompt_file:
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    if not prompt or not prompt.strip():
        sys.exit("нужен --prompt или --prompt-file")

    for ref in a.ref:
        if not Path(ref).exists():
            sys.exit(f"нет файла образца: {ref}")

    size = None
    if a.size:
        try:
            w, h = (int(x) for x in a.size.lower().split("x"))
            size = (w, h)
        except ValueError:
            sys.exit("--size пишется как 1080x576")

    ar = a.ar or (nearest_ar(*size) if size else "1:1")
    out = Path(a.out).expanduser().resolve()
    body = build_body(prompt, a.ref, ar)

    print(f"модель: {a.model}")
    print(f"соотношение: {ar}" + (f"  -> потом до {size[0]}x{size[1]}"
                                  if size else ""))
    print(f"образцов приложено: {len(a.ref)}")
    print(f"запрос: {prompt.strip()[:200]}")

    if a.dry:
        print(f"\n--dry: наружу ничего не отправлено.")
        print(f"ушло бы {len(json.dumps(body))} байт на "
              f"{API.format(model=a.model)}")
        return 0

    project = Path(a.project).expanduser().resolve() if a.project else None
    key = api_key(project)
    print("\nотправляю запрос наружу...", flush=True)
    data = request(body, a.model, key, a.tries)

    saved = save_images(data, out)
    for path in saved:
        print(f"сохранено: {path}  {path.stat().st_size} байт")
        if size:
            fit_exact(path, size)

    usage = data.get("usageMetadata") or {}
    if usage:
        print("израсходовано:", json.dumps(usage, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())