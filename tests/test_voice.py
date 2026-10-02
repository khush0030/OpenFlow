"""voice.py: how-you-speak analytics. Crafted rows, fixed clock, UTC."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import voice
from history import Entry

UTC = timezone.utc
NOW = datetime(2026, 10, 1, 23, tzinfo=UTC)


def _ts(m, d, h=12, mi=0):
    return datetime(2026, m, d, h, mi, tzinfo=UTC).timestamp()


def E(ts, raw, final=None, duration=6.0, i=[0]):
    i[0] += 1
    return Entry(i[0], ts, raw, raw if final is None else final, "verbatim", "en", duration)


# ── pace ───────────────────────────────────────────────────────────────────

def test_pace_ignores_taps_and_tiny_takes():
    rows = [E(_ts(9, 1), "one two three four five six", duration=3.0),   # 120 wpm
            E(_ts(9, 1), "a b c d e f g h i j", duration=1.0),          # < 1.5 s: noise
            E(_ts(9, 1), "ok fine", duration=5.0)]                      # < 3 words: noise
    assert [voice.is_pace_row(r) for r in rows] == [True, False, False]
    assert voice.pace(rows) == pytest.approx(120.0)
    assert voice.pace([]) == 0.0


def test_pace_uses_raw_words_not_final():
    r = E(_ts(9, 1), "um so one two three four", "One, two, three, four.", duration=3.0)
    assert voice.pace([r]) == pytest.approx(120.0)        # 6 raw words / 3 s


def test_weekly_pace_groups_by_monday_and_needs_two_rows():
    rows = [E(_ts(9, 14), "a b c d e f", duration=3.0),    # Mon Sep 14: 120
            E(_ts(9, 20), "a b c d e f", duration=6.0),    # Sun Sep 20, same week: 60
            E(_ts(9, 21), "a b c d e f", duration=3.0),    # lone row: not a point
            E(_ts(9, 28), "a b c", duration=3.0),
            E(_ts(9, 29), "a b c", duration=3.0),
            E(_ts(10, 5), "a b c", duration=3.0),          # after NOW: ignored
            E(_ts(10, 5), "a b c", duration=3.0)]
    pts = voice.weekly_pace(rows, UTC, NOW)
    assert [p.week for p in pts] == [date(2026, 9, 14), date(2026, 9, 28)]
    assert pts[0].value == pytest.approx(12 / (9 / 60))    # 80 wpm pooled
    assert pts[0].n == 2 and pts[1].value == pytest.approx(60.0)


@pytest.mark.parametrize("hour,label", [(5, "Morning"), (11, "Morning"), (12, "Afternoon"),
                                        (16, "Afternoon"), (17, "Evening"), (21, "Evening"),
                                        (22, "Night"), (0, "Night"), (4, "Night")])
def test_daypart(hour, label):
    assert voice.daypart(hour) == label


def test_pace_by_daypart_needs_three_rows():
    rows = [E(_ts(9, 1, 9, i), "a b c d e f", duration=3.0) for i in range(3)]
    rows += [E(_ts(9, 1, 20, i), "a b c d e f", duration=6.0) for i in range(2)]
    parts = {d.label: d for d in voice.pace_by_daypart(rows, UTC)}
    assert parts["Morning"].wpm == pytest.approx(120.0) and parts["Morning"].n == 3
    assert parts["Evening"].wpm == 0.0 and parts["Evening"].n == 2      # too few to say
    assert parts["Night"].n == 0


def test_hour_counts_and_peak():
    rows = [E(_ts(9, 1, 9), "a"), E(_ts(9, 2, 9), "b"), E(_ts(9, 2, 23), "c")]
    v = voice.analyze(rows, NOW, UTC)
    assert v.hours[9] == 2 and v.hours[23] == 1 and sum(v.hours) == 3
    assert v.peak_hour == 9
    assert voice.VoiceStats().peak_hour is None


def test_length_buckets():
    rows = [E(0, "a b", duration=d) for d in (0.5, 4.99, 5.0, 14.9, 15.0, 44.0, 45.0, 300.0)]
    rows.append(E(0, "", duration=10.0))          # no speech: left out
    rows.append(E(0, "words", duration=0.0))
    assert voice.length_buckets(rows) == [("Under 5 s", 2), ("5–15 s", 2),
                                          ("15–45 s", 2), ("45 s and up", 2)]


# ── fillers ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("um I think uh we should go", {"um": 1, "uh": 1}),
    ("Umm, hmm, okay", {"um": 1, "hmm": 1}),
    ("I would like a coffee", {}),                         # verb
    ("it looks like rain", {}),                            # comparison
    ("anything like that", {}),
    ("it was, like, huge", {"like": 1}),                   # commas
    ("and I was like no way", {"like": 1}),                # quotative
    ("Like it is taking a lot of time.", {"like": 1}),     # sentence-initial
    ("in terms of like experience", {"like": 1}),
    ("our app is like that", {}),
    ("So we need to ship. So yeah.", {"so": 2}),
    ("it is so good", {}),                                 # intensifier
    ("I was tired, so I left", {}),                        # conjunction
    ("That works, right? Okay so next", {"right": 1, "okay so": 1}),
    ("turn right at the light", {}),
    ("I am right", {}),
    ("You know what, I think so", {"you know": 1}),        # tic at sentence start
    ("Do you know what time it is", {}),                   # question
    ("we should, you know, test it", {"you know": 1}),
    ("I know what you mean", {}),
    ("what kind of car is that", {}),
    ("it is kind of slow", {"kind of": 1}),
    ("this sort of thing", {}),
    ("I mean, it works", {"I mean": 1}),
    ("that is what I mean", {}),
    ("It is basically done, literally, actually", {"basically": 1, "literally": 1, "actually": 1}),
    ("matlab toh yaar accha haan", {"matlab": 1, "toh": 1, "yaar": 1, "accha": 1, "haan": 1}),
    ("I want to go too", {}),                              # "to" is not "toh"
    ("Theme is summer", {}),                               # word boundaries
    ("aaj chalein na?", {"na": 1}),
    ("na kiya", {}),
])
def test_count_fillers(text, expected):
    assert dict(voice.count_fillers(text)) == expected


def test_okay_so_is_one_filler_not_two():
    assert dict(voice.count_fillers("Okay so we start")) == {"okay so": 1}


def test_filler_list_lives_in_module():
    for f in ("um", "like", "you know", "matlab", "yaar", "accha"):
        assert f in voice.FILLERS


def test_filler_stats_rate_removed_and_weekly():
    rows = [
        # 10 raw words, 2 fillers; cleanup removed both
        E(_ts(9, 14), "um so the plan is to ship it, like, today",
          "The plan is to ship it today."),
        # 10 raw words, 1 filler kept by cleanup
        E(_ts(9, 15), "basically we are done with the work for this week",
          "Basically we are done with the work for this week."),
    ]
    f = voice.filler_stats(rows, UTC, NOW)
    assert f.raw_words == 20 and f.total == 3
    assert f.per_100 == pytest.approx(15.0)
    assert f.removed == 2
    assert ("um", 1, 1) in f.top and ("basically", 1, 0) in f.top
    assert f.weekly == []                       # 20 words < MIN_WEEK_WORDS


def test_filler_weekly_points():
    filler_row = "um " + "word " * 39       # 40 words, 1 filler
    rows = [E(_ts(9, d), filler_row) for d in (14, 15, 21, 22)]
    f = voice.filler_stats(rows, UTC, NOW)
    assert [p.week for p in f.weekly] == [date(2026, 9, 14), date(2026, 9, 21)]
    assert f.weekly[0].value == pytest.approx(2.5)


def test_no_fillers():
    f = voice.filler_stats([E(0, "clean words only here")], UTC, NOW)
    assert f.total == 0 and f.per_100 == 0.0 and f.top == []
    assert voice.filler_stats([], UTC, NOW).per_100 == 0.0


# ── phrases, vocabulary, sentences ─────────────────────────────────────────

def test_top_phrases_min_count_stopwords_and_subsumption():
    rows = [E(0, f"in terms of {w} we should make sure it works")
            for w in ("design", "speed", "money")]
    rows += [E(0, "you know it is what it is")] * 5          # stopwords only
    rows += [E(0, "make sure")] * 2
    phrases = dict(voice.top_phrases(rows, n=50))
    assert phrases["in terms of"] == 3                        # "in terms" folded in
    assert "in terms" not in phrases
    assert phrases["make sure"] == 5                          # 3 + 2 on their own
    assert phrases["we should make sure"] == 3
    assert not any(p in phrases for p in ("you know", "it is", "what it is"))
    assert all(c >= 3 for c in phrases.values())


def test_phrases_do_not_cross_sentences_or_commas():
    rows = [E(0, "We ship. Today we rest, design team")] * 3
    phrases = dict(voice.top_phrases(rows, n=50))
    assert "ship today" not in phrases and "rest design" not in phrases
    assert phrases.get("design team") == 3


def test_vocabulary_window():
    v = voice.vocabulary([E(0, "a b c a")])                  # < window: scaled
    assert (v.total, v.distinct) == (4, 3) and v.per_window == pytest.approx(75.0)
    rows = [E(0, " ".join(f"w{i}" for i in range(150)))]    # all distinct
    assert voice.vocabulary(rows).per_window == pytest.approx(100.0)
    rows = [E(0, " ".join(["same"] * 150))]
    assert voice.vocabulary(rows).per_window == pytest.approx(1.0)
    assert voice.vocabulary([]).per_window == 0.0


def test_sentence_length_and_openers_from_final():
    rows = [E(0, "raw ignored", "So we ship today. I think it works! Okay."),
            E(0, "x", "So the team agrees? I'm sure.")]
    assert voice.avg_sentence_length(rows) == pytest.approx((4 + 4 + 1 + 4 + 2) / 5)
    assert voice.openers(rows, 3) == [("so", 2), ("I", 1), ("I'm", 1)]
    assert ("so the", 1) in voice.openers(rows, 5, size=2)


# ── analyze ────────────────────────────────────────────────────────────────

def test_analyze_thresholds_and_future_rows():
    rows = [E(_ts(9, 1, 10, i), "um we should ship the update today") for i in range(9)]
    v = voice.analyze(rows, NOW, UTC)
    assert v.dictations == 9 and not v.enough
    rows.append(E(_ts(9, 2), "one more dictation to reach ten"))
    rows.append(E(_ts(10, 9), "from the future"))
    v = voice.analyze(rows, NOW, UTC)
    assert v.dictations == 10 and v.enough
    assert v.pace_dictations == 10
    assert v.fillers.total == 9


def test_analyze_empty():
    v = voice.analyze([], NOW, UTC)
    assert v.dictations == 0 and v.wpm == 0.0 and v.phrases == [] and v.sentence_len == 0.0
