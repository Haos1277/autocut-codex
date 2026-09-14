# autocut-skill

**[Русская версия](README.md)**

A Claude Code skill that builds a video out of a talking-head recording.
It cuts pauses and repeated takes, repairs the audio, adds subtitles, lays
out the frame and draws the inserts. No video editor required.

Runs on Windows, macOS and Linux.

## What it does

- **Cuts.** Finds from the sound where the speaker talks and where they
  don't. Shortens pauses, removes filler words, spots re-recorded phrases
  and keeps the last take.
- **Checks its own work.** Measures the level inside every window it
  removed and tells you if a cut landed on a word.
- **Repairs the audio.** Rumble, clipped peaks, noise, loudness. Can pull
  the tone toward a recording you like.
- **Adds subtitles** in your own style: .ass with styling plus a separate
  .srt, burn-in, and word-by-word highlighting.
- **Lays out the frame.** First checks whether it can be split at all,
  then composes: presenter and insert at 70/30 or half and half, insert
  full-frame, insert overlaid.
- **Draws the inserts:** headline, list, big number, code block — with the
  content appearing in stages.
- **Measures your settings** from your own published videos instead of
  guessing them from a questionnaire.

## What it does not do

- **It does not choose what stays in the video.** That is editorial work.
  The skill removes junk; it does not decide which thought is redundant.
  Measured on real material: from 3 minutes of raw footage the skill made
  2:15, a human by hand made 0:52.
- It does not remove hesitations (why — below).
- No color grading, no music.
- No voice synthesis.
- No generated images or footage — that is a separate add-on, and it is
  not free.

Won't suit you if: several people talk over each other; music is louder
than speech; pauses carry meaning (stand-up, drama).

### About hesitations — the most common expectation

The skill cannot remove them, and here is why.
Drawn-out "uhh" and "umm" never reach the transcript — speech recognition
discards them as noise — yet by loudness they are speech. Neither the word
search nor the pause trimming finds them. On a real three-minute recording
the transcript held 343 words and a single filler. The pause trimming is
where the value is: 40 seconds out of 42 in that same test.

## Requirements

- **Claude Code** — [installation guide](https://docs.claude.com/en/docs/claude-code)
- **Python 3.10+**
- **ffmpeg** on PATH
- **No GPU needed** — everything runs on the CPU

CPU transcription runs at roughly 1.5–2× real time: a minute of audio
takes a minute and a half to two minutes. On a slow machine, use a smaller
model (see below).

## Install

**1. ffmpeg**

```bash
# macOS
brew install ffmpeg

# Windows
winget install Gyan.FFmpeg

# Linux
sudo apt install ffmpeg
```

Verify with `ffmpeg -version`.

**2. Unpack the skill**

Unpack `autocut-skill.zip` somewhere convenient and open that folder.

**3. Python packages**

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

**4. Hook it into Claude Code**

Copy the `autocut` folder into your `.claude/skills/`:

```bash
# macOS / Linux
mkdir -p ~/.claude/skills && cp -r autocut ~/.claude/skills/

# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.claude\skills"
Copy-Item -Recurse autocut "$HOME\.claude\skills\"
```

Restart Claude Code, say "rough-cut this recording" and hand it a file.

## First run

The skill asks a few questions first: what kind of material this is, what
language, how aggressively to cut, which words to never touch. Your answers
go into `style.json` next to the project and won't be asked again.

This is deliberate. Cutting preferences differ wildly between channels, and
applying someone else's defaults is a reliable way to ruin footage.

If you already have published videos, the settings can be measured from
them instead of guessed:

```bash
python autocut/scripts/learn_style.py folder-with-videos project en
```

It measures your typical pause, speaking rate, how many filler words you
leave in yourself, and how often you change shots — then proposes settings
in your own manner. Use finished, edited episodes, not raw footage: what
we're reading is your editing habits, not your speech habits.

## Running it by hand, without Claude Code

```bash
python autocut/scripts/prepare.py    recording.mp4 project
python autocut/scripts/transcribe.py project/work/audio.wav en
python autocut/scripts/speech_map.py project/work/audio.wav
python autocut/scripts/rough_cut.py  recording.mp4 project
```

That last command **renders nothing**. It prints the plan: what it intends
to cut and how much. If you like it, run again with `--render`:

```bash
python autocut/scripts/rough_cut.py recording.mp4 project --render
python autocut/scripts/check_output.py project en
```

Result: `project/out/rough.mp4`.

### Audio

Last step — audio repair:

```bash
python autocut/scripts/clean_audio.py project/out/rough.mp4 project
```

Prints a diagnosis: how much noise, whether it clips, how loud it is.
Touches nothing until you add `--render`.

It fixes only what is actually broken: cuts rumble, restores clipped peaks,
suppresses noise — but leaves noise alone when there is barely any, since
over-denoising takes the air out of a voice. Then it brings loudness to
target.

```
--lufs -14      target loudness (-14 for the web, -16 quieter, -23 broadcast)
--like file     match the tone of a recording you like
--no-denoise    leave noise alone
```

`--like` compares the spectral shape of your recording to a reference,
band by band, and pulls yours toward it. It works when the two recordings
are comparable: an equalizer can't tell voice from hiss, so lifting the top
end lifts both. If the correction exceeds 4 dB, the script says so.

If the source has video, the picture is copied without re-encoding.

### Subtitles

```bash
python autocut/scripts/subtitles.py project/out/clean.mp4 project
```

Builds subtitles from the word-level timings and writes two files:
`subs.ass` with styling and `subs.srt` for uploading anywhere. Add `--burn`
to burn them into the picture.

Speech is split into cues at punctuation and pauses, not at an arbitrary
character count. Line breaks land after commas and full stops so a line
never snaps mid-phrase.

```
--font Arial        font (must be installed on the system)
--size 64           cap height in pixels
--color #FFFFFF     text colour
--outline #000000   outline colour, --outline-w for width
--shadow 2          soft shadow, 0 to disable
--pos bottom        bottom | top | center
--margin 220        distance from the frame edge
--caps              UPPERCASE
--karaoke           highlight the word being spoken, --hl sets the colour
--burn              burn into the video
```

The default margin is 11% of frame height: on vertical video the platform
UI covers the bottom strip, so text must stay out of it.

The script reports **reading speed** — the metric that actually decides
subtitle quality. Up to 20 characters per second is comfortable, 25 is the
limit. If too many cues exceed it, reduce the font size: more characters
fit per line, so cues last longer.

### Insert cards

Describe the cards in a json file and render them:

```bash
python autocut/scripts/cards.py cards.json cards --anim
```

```json
{
 "style": {"bg": "#101010", "fg": "#F2F2F2", "accent": "#4C8BF5"},
 "cards": [
  {"name": "a", "size": [1080, 576], "type": "list",
   "title": "What it does", "items": ["cuts pauses", "adds subtitles"]},
  {"name": "b", "size": [1080, 1920], "type": "stat",
   "value": "24%", "caption": "of the recording was silence"}
 ]
}
```

Card types: `text` (headline and subtitle), `list` (headline and bullets),
`stat` (a big number with a caption), `code` (a monospaced block).

The styling is yours: background, text colour, accent, font, alignment,
padding. The defaults are deliberately neutral — change them for your
own channel.

**The font size is chosen automatically** to fit the card. Text spilling
past the edges is the most common way inserts break, and it cannot happen
here: the script shrinks the type until the content fits.

With `--anim` a short video appears next to the png: the content rises into
place over half a second, then holds. Length comes from `"dur"`.

Size each card for the band it will occupy: 1080x576 for `70/30`,
1080x960 for `50/50`, the full frame for `full`.

Fonts are located on the system (Arial, Helvetica, DejaVu); pass your own
with `"font"`. The script checks the font for Cyrillic and warns you —
Pillow says nothing about missing glyphs and silently draws empty boxes.

### Frame layout and inserts

One layout for a whole video looks monotonous, so they alternate:

| Layout | What's on screen |
|---|---|
| `70/30` | presenter on top 70% of the height, insert in a strip below |
| `50/50` | split in half — good for before/after comparisons |
| `full` | insert fills the frame, 2-4 seconds |
| `over` | insert is overlaid; the frame is not split |
| `none` | presenter only |

First check whether the frame can be split at all:

```bash
python autocut/scripts/layout_probe.py recording.mp4
```

The script finds where the liveliest part of the frame is and, for each
layout, says: crop, fit the whole frame, or overlay only. If the person
fills the frame — face in the middle, mic and hands below — splitting cuts
their mouth off, and that is the only honest answer.

Then describe the video in a `plan.json`:

```json
{
 "source": "out/clean.mp4",
 "segments": [
  {"start": 0.0, "end": 4.0,  "layout": "none"},
  {"start": 4.0, "end": 7.5,  "layout": "70/30", "insert": "cards/a.png"},
  {"start": 7.5, "end": 10.5, "layout": "full",  "insert": "cards/b.png"}
 ]
}
```

```bash
python autocut/scripts/compose.py plan.json
python autocut/scripts/compose.py plan.json --render
```

Without `--render` the script reviews the plan and reports problems: the
same layout twice in a row, a fullscreen insert held too long, a gap
between segments, a missing insert file, a segment running over five
seconds without a change of picture.

`"mode"` decides how the presenter fills their band: `crop` (large, but the
bottom of the frame is lost) or `fit` (face intact, but small). When
fitting, the empty side margins are filled with a blurred copy of the frame
— flat bars look unfinished. For flat colour instead: `"fill": "color"`
with `"bg": "#000000"`.

### Cutting options

```
--pause 0.35      pause length to leave between phrases, seconds
--edge 0.25       silence to leave at the very start and end
--keep-fillers    leave filler words alone
--keep-repeats    keep repeated takes
--repeat 0.90     similarity threshold for repeats, 0..1
```

### Recognition model

Defaults to `large-v3-turbo`, which downloads ~1.5 GB on first run.
On a slow machine:

```bash
python autocut/scripts/transcribe.py project/work/audio.wav en small
```

`small` or `base` run several times faster but misread terms and names
more often.

## Why cuts follow loudness, not the transcript

The main trap in auto-editing: Whisper is off by hundreds of milliseconds
at pause boundaries and stretches words over adjacent silence. We caught
a word timed at 1.76 s that actually lasted half that — the rest was pause.
Cut on those timings and you start clipping words.

So segment boundaries come from the loudness envelope of the audio itself,
and the transcript is only used to know what was said inside each segment.

Verification works the same way — on sound, not text. The script measures
the level inside every window it removed. If there was speech in there,
the cut landed on a word. Diffing transcripts before and after is useless:
Whisper hears the same audio differently on two runs, so a "missing word"
in that diff is almost always an illusion.

## Privacy

Everything runs locally. Your recordings are never uploaded. The only
network access is downloading the recognition model weights on first run.

That holds for everything described above. A separate add-on that adds
image and footage generation works differently: it sends your prompt and
any attached images to the model owner's server. If you install it, factor
that in. Without it, the whole pipeline stays on your machine.

## Licensing

The skill itself is **MIT** — see [LICENSE](LICENSE). Use it however you
like, commercial work included.

Dependencies are permissive too:

| Package | License |
|---|---|
| faster-whisper | MIT |
| Whisper weights | MIT |
| numpy | BSD-3-Clause |
| scipy | BSD-3-Clause |
| ffmpeg | LGPL/GPL depending on build |

You install ffmpeg yourself; the skill only invokes it as a program.

## Feedback

Found where it breaks — open an issue. Especially useful: cases where the
check reported "clean" but you can hear a clipped edge. That means the
silence threshold is wrong for your kind of recording.
