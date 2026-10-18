import re

from clipper import captions
from clipper.models import Word


def words(*items):
    return [Word(text=t, start=s, end=e) for t, s, e in items]


def test_group_words_breaks_on_limits_pauses_and_punctuation():
    ws = words(
        ("Hola", 0.0, 0.3), ("a", 0.35, 0.4), ("todos.", 0.45, 0.8),
        ("Hoy", 0.9, 1.1), ("vamos", 1.15, 1.4), ("a", 1.45, 1.5), ("ver", 1.55, 1.8),
        ("algo", 3.0, 3.3),
    )
    groups = captions.group_words(ws, max_words=3, max_chars=40)
    assert [[w.text for w in g] for g in groups] == [
        ["Hola", "a", "todos."], ["Hoy", "vamos", "a"], ["ver"], ["algo"],
    ]


def test_shift_words_keeps_only_clip_range():
    ws = words(("antes", 0, 1), ("dentro", 5, 6), ("borde", 9.5, 10.5), ("después", 12, 13))
    shifted = captions.shift_words(ws, 4.0, 10.0)
    assert [(w.text, w.start, w.end) for w in shifted] == [("dentro", 1.0, 2.0), ("borde", 5.5, 6.0)]


def test_time_formats():
    assert captions.ass_time(3725.456) == "1:02:05.46"
    assert captions.srt_time(3725.456) == "01:02:05,456"


def test_viral_style_highlights_each_word_in_turn():
    ws = words(("¿Sabes", 0.0, 0.4), ("qué", 0.5, 0.8), ("pasó?", 0.9, 1.3))
    ass = captions.build_ass(ws, "viral", "Inter")
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 3
    for i, line in enumerate(dialogues):
        highlighted = re.findall(r"\\fscy112\}([^{]+)\{\\r\}", line)
        assert highlighted == [["¿SABES", "QUÉ", "PASÓ?"][i]]
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_ass_escapes_override_characters():
    ass = captions.build_ass(words(("{\\b1}hack", 0, 1)), "clasico", "Inter")
    line = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "{" not in line.split(",,", 1)[1]


def test_karaoke_durations_cover_each_word():
    ass = captions.build_ass(words(("uno", 0.0, 0.5), ("dos", 0.6, 1.0)), "karaoke", "Inter")
    line = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "{\\kf60}UNO" in line and "{\\kf40}DOS" in line


def test_srt_blocks_are_numbered_and_ordered():
    srt = captions.build_srt(words(("Hola.", 0, 0.5), ("Adiós.", 2, 2.5)))
    blocks = srt.strip().split("\n\n")
    assert blocks[0].startswith("1\n00:00:00,000 --> ")
    assert blocks[1].startswith("2\n00:00:02,000 --> ")
