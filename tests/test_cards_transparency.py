import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "autocut"
SCRIPTS = SKILL / "scripts"
PYTHON = sys.executable

sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("cards", SCRIPTS / "cards.py")
cards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cards)


def style(**overrides):
    value = dict(cards.DEFAULTS)
    value.update(overrides)
    value["_font"] = cards.find_font(None)
    value["_mono"] = cards.find_font(None, mono=True)
    return value


class CardsTransparencyTests(unittest.TestCase):
    def test_default_card_is_transparent_rgba(self):
        image = cards.draw_card(
            {"type": "text", "title": "Точный текст", "size": [320, 180]},
            style(),
        )

        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((319, 179))[3], 0)
        self.assertGreater(image.getbbox()[3], 0)

    def test_explicit_background_remains_opaque(self):
        image = cards.draw_card(
            {"type": "text", "title": "Точный текст", "size": [320, 180]},
            style(bg="#123456"),
        )

        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((319, 179)), (18, 52, 86, 255))

    def test_animated_card_uses_alpha_capable_mov(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "cards.json"
            output = root / "out"
            source.write_text(
                json.dumps({
                    "name": "title",
                    "type": "text",
                    "title": "Точный текст",
                    "size": [320, 180],
                    "dur": 0.1,
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            subprocess.run(
                [str(PYTHON), str(SCRIPTS / "cards.py"), str(source),
                 str(output), "--anim"],
                check=True,
                capture_output=True,
                text=True,
            )
            animation = output / "title.mov"
            self.assertTrue(animation.exists(), "transparent animation must be .mov")
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,pix_fmt", "-of", "json",
                 str(animation)],
                check=True,
                capture_output=True,
                text=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["codec_name"], "prores")
            self.assertTrue(stream["pix_fmt"].startswith("yuva"), stream)


if __name__ == "__main__":
    unittest.main()
