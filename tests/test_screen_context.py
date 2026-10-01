"""Names on screen: term extraction, the correction guard, the AX walk
(over a fake tree) and the key-down capture. No AX, no network."""
from __future__ import annotations

import threading

import pytest

import screen_context as sc

# A stand-in for the system word list: ordinary words, lower case.
ENGLISH = frozenset("""
mark hall green ashen austin slack notion figma bill rose grace hope will
general random project design sync notes weekly meeting product hub git
open flow unfortunately apple orange actually cant sounds great
""".split())


def terms(*texts, **kw):
    kw.setdefault("english", ENGLISH)
    return sc.extract_terms(list(texts), **kw)


# -- Extraction: what counts as a name -----------------------------------

def test_multiword_name_in_a_sentence():
    assert "Ashton Hall" in terms("Can you send the deck to Ashton Hall before Friday?")


def test_greeting_and_sign_off_words_are_trimmed_from_a_name():
    out = terms("Hey Priya Raman", "Thanks Priya Raman")
    assert "Priya Raman" in out
    assert not any(t.startswith(("Hey", "Thanks")) for t in out)


def test_single_name_mid_sentence():
    assert "Ashton" in terms("I spoke with Ashton about it.")


def test_handles_become_names():
    out = terms("ping @ashton.hall and @priya and @OpenFlowHQ")
    assert "Ashton Hall" in out and "Priya" in out and "OpenFlowHQ" in out


def test_email_local_names():
    out = terms("From: kiran.mehta@acme.com", "cc: ravi_k@example.org")
    assert "Kiran Mehta" in out
    assert "Ravi" in out


def test_role_mailboxes_are_not_names():
    out = terms("noreply@github.com support@acme.com info@x.io hello@y.com team@z.co")
    assert out == []


def test_camelcase_and_product_tokens():
    out = terms("we moved OpenFlow to GitHub and tested on iPhone and macOS with GPT4o")
    for t in ("OpenFlow", "GitHub", "iPhone", "macOS", "GPT4o"):
        assert t in out, (t, out)


def test_possessive_is_stripped():
    assert "Ashton" in terms("that was Ashton's idea")
    assert "Ashton's" not in terms("that was Ashton's idea")


# -- Extraction: negatives -----------------------------------------------

@pytest.mark.parametrize("text", [
    "Inbox Sent Drafts Archive Trash",
    "Reply Forward Delete",
    "Monday Tuesday January March",
    "Today Yesterday Tomorrow",
    "Direct Messages Threads Mentions",
    "Hi there. Thanks. Best regards.",
    "OK", "PM", "AM",
    "the quick brown fox jumps over the lazy dog",
    "https://www.Example.com/SomePath/OpenThing",
    "Search Settings Help Window",
    "General Random",
])
def test_ui_chrome_and_common_words_are_not_terms(text):
    assert terms(text) == [], text


def test_sentence_initial_english_word_is_not_a_name():
    # "Unfortunately" / "Actually" open sentences; they are English words.
    assert terms("Unfortunately it broke. Actually never mind.") == []


def test_sentence_initial_unknown_word_is_kept():
    assert "Ashton" in terms("Ashton can you look?")


def test_long_title_case_heading_is_not_a_name():
    out = terms("Weekly Product Design Sync Meeting Notes")
    assert out == []


def test_all_caps_and_numbers_are_not_terms():
    assert terms("URGENT 2024 Q3 ASAP 10:30") == []


def test_non_latin_text_is_ignored():
    assert terms("नमस्ते आप कैसे हैं") == []


def test_own_name_is_dropped():
    out = terms("Khush Mutha", "Ashton Hall replied to Khush", own_name="Khush Mutha")
    assert "Ashton Hall" in out
    assert not any("Khush" in t or "Mutha" in t for t in out)


def test_own_name_inside_a_multiword_term_leaves_the_other_word():
    out = terms("Ashton Mutha", own_name="Khush Mutha")
    assert "Ashton" in out and not any("Mutha" in t for t in out)


# -- Extraction: ranking and limits ----------------------------------------

def test_terms_near_the_focused_field_rank_first():
    far = ["Zorawar Singh"] * 3
    texts = ["Ashton Hall"] + far
    out = terms(*texts, near=1)
    assert out[0] == "Ashton Hall"


def test_focus_index_ranks_by_distance_without_a_near_region():
    texts = ["Zorawar"] + ["filler text"] * 20 + ["Ashton"]
    out = terms(*texts, focus_index=21)
    assert out[0] == "Ashton"


def test_frequency_counts():
    out = terms("Ashton", "Ashton", "Ashton", "Zorawar")
    assert out[0] == "Ashton"


def test_limit_and_dedupe():
    many = [f"I met Person{i}x today" for i in range(50)]
    out = terms(*many, limit=30)
    assert len(out) == 30 and len(set(out)) == 30
    assert terms("Ashton", "ashton", "Ashton") == ["Ashton"]


def test_most_common_spelling_wins():
    assert terms("Ashton", "Ashton", "ASHTON") == ["Ashton"]


def test_keyterms_shape():
    long = "A" + "b" * 70
    out = sc.keyterms([long] + [f"Term{i}" for i in range(60)])
    assert long not in out and len(out) == 50


def test_glossary_line():
    assert sc.glossary_line([]) is None
    line = sc.glossary_line(["Ashton Hall", "OpenFlow"])
    assert "Ashton Hall, OpenFlow" in line and "never add" in line


# -- Correction guard -------------------------------------------------------

def fix(text, ts, threshold=85):
    return sc.correct(text, ts, threshold, english=ENGLISH)


def test_one_letter_slip_takes_the_screen_spelling():
    assert fix("thanks Ashtan, see you", ["Ashton"]) == "thanks Ashton, see you"


def test_multiword_name_parts_are_corrected():
    assert fix("ask Ashtan Hall", ["Ashton Hall"]) == "ask Ashton Hall"


def test_case_only_fix_for_a_name():
    assert fix("tell priyanka", ["Priyanka"]) == "tell Priyanka"


def test_two_words_join_to_a_camelcase_term():
    assert fix("push it to git hub", ["GitHub"]) == "push it to GitHub"
    assert fix("open flow is live", ["OpenFlow"]) == "OpenFlow is live"


def test_split_words_need_an_exact_join():
    assert fix("open flaw is live", ["OpenFlow"]) == "open flaw is live"


@pytest.mark.parametrize("text,ts", [
    ("mark it done", ["Mark"]),              # English word, even if a name
    ("the hall is booked", ["Hall"]),
    ("we went to austin", ["Ashton"]),       # a real word, not a slip
    ("ashen faced", ["Ashton"]),
    ("will do", ["Will"]),
    ("say hi to Ash", ["Ashton"]),           # short word: never touched
    ("Asha said so", ["Ashton"]),            # too short / too far
    ("Ahmed is here", ["Ashton"]),
    ("Bhaskar is here", ["Ashton"]),         # different first letter
    ("send it now", ["Sendgrid"]),
])
def test_ordinary_or_distant_words_are_never_replaced(text, ts):
    assert fix(text, ts) == text


def test_ambiguous_match_is_left_alone():
    # "Karan" is one edit from both: neither wins.
    assert fix("call Karan", ["Kiran", "Karen"]) == "call Karan"


def test_no_word_list_means_no_correction():
    old = sc._ENGLISH
    sc._ENGLISH = None
    try:
        assert sc.correct("thanks Ashtan", ["Ashton"]) == "thanks Ashtan"
    finally:
        sc._ENGLISH = old


def test_no_terms_is_a_no_op():
    assert fix("thanks Ashtan", []) == "thanks Ashtan"


def test_punctuation_and_spacing_survive():
    assert fix("Ashtan, Ashtan! (Ashtan)", ["Ashton"]) == "Ashton, Ashton! (Ashton)"


# -- AX walk over a fake tree ------------------------------------------------

class Node:
    def __init__(self, role="AXGroup", value=None, title=None, desc=None,
                 children=(), focused=False, subrole=None, delay=0.0):
        self.a = {"AXRole": role}
        if value is not None:
            self.a["AXValue"] = value
        if title is not None:
            self.a["AXTitle"] = title
        if desc is not None:
            self.a["AXDescription"] = desc
        if subrole:
            self.a["AXSubrole"] = subrole
        if focused:
            self.a["AXFocused"] = True
        self.children = list(children)
        self.parent = None
        self.delay = delay
        for c in self.children:
            c.parent = self


class FakeSource:
    def __init__(self, win, clock=None):
        self.win = win
        self.calls = 0
        self.clock = clock

    def window(self, pid):
        return self.win

    def parent(self, el):
        return el.parent

    def attrs(self, el):
        self.calls += 1
        if self.clock is not None:
            self.clock.t += el.delay
        return {**el.a, "AXChildren": el.children}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def chat_window(password=False, focus_password=False):
    box = Node("AXTextArea", value="draft to Ashton", focused=not focus_password)
    pw = Node("AXTextField", subrole="AXSecureTextField", value="hunter2",
              focused=focus_password)
    thread = Node(children=[
        Node("AXStaticText", value="Ashton Hall: can Priya review OpenFlow?"),
        Node("AXStaticText", value="Priya: on it"),
        Node(children=[box] + ([pw] if password or focus_password else [])),
    ])
    sidebar = Node(children=[Node("AXStaticText", value="Zorawar Singh"),
                             Node("AXButton", title="Inbox", desc="Inbox")])
    root = Node("AXWindow", title="Slack — Ashton Hall", children=[sidebar, thread])
    return root, box


def test_walk_reads_text_in_reading_order():
    win, _ = chat_window()
    wt = sc.read_window_text(1, source=FakeSource(win))
    assert wt.texts[0] == "Slack — Ashton Hall"
    assert "Zorawar Singh" in wt.texts and "Priya: on it" in wt.texts
    assert wt.texts.count("Inbox") == 1      # title == description: once
    assert wt.focus_index == wt.texts.index("draft to Ashton")
    assert not wt.truncated and not wt.secure


def test_walk_never_reads_password_fields():
    win, box = chat_window(password=True)
    wt = sc.read_window_text(1, box, source=FakeSource(win))
    assert "hunter2" not in wt.texts
    assert wt.texts and not wt.secure


def test_focused_password_field_reads_nothing():
    win, _ = chat_window(focus_password=True)
    wt = sc.read_window_text(1, source=FakeSource(win))
    assert wt.secure and wt.texts == []
    pw = win.children[1].children[2].children[1]
    wt = sc.read_window_text(1, pw, source=FakeSource(win))
    assert wt.secure and wt.texts == []


def test_region_around_the_focused_field_is_read_first():
    win, box = chat_window()
    wt = sc.read_window_text(1, box, source=FakeSource(win))
    assert wt.near > 0
    near = wt.texts[:wt.near]
    assert "Priya: on it" in near and "Zorawar Singh" not in near
    out = sc.extract_terms(wt.texts, wt.focus_index, near=wt.near, english=ENGLISH)
    assert out.index("Ashton Hall") < out.index("Zorawar Singh")


def test_walk_stops_at_the_time_budget():
    clock = Clock()
    leaves = [Node("AXStaticText", value=f"Name{i}x", delay=0.01) for i in range(100)]
    win = Node("AXWindow", children=leaves)
    src = FakeSource(win, clock)
    wt = sc.read_window_text(1, source=src, budget_s=0.15, clock=clock)
    assert wt.truncated and src.calls < 30


def test_walk_stops_at_the_character_cap():
    leaves = [Node("AXStaticText", value="x" * 1000) for _ in range(50)]
    wt = sc.read_window_text(1, source=FakeSource(Node("AXWindow", children=leaves)),
                             max_chars=5000)
    assert wt.truncated and wt.chars >= 5000 and len(wt.texts) <= 6


def test_one_huge_value_is_capped():
    leaf = Node("AXTextArea", value="y" * 100_000)
    wt = sc.read_window_text(1, source=FakeSource(Node("AXWindow", children=[leaf])))
    assert max(map(len, wt.texts)) == sc.MAX_VALUE_CHARS


def test_depth_cap():
    node = Node("AXStaticText", value="Deepname")
    for _ in range(10):
        node = Node(children=[node])
    wt = sc.read_window_text(1, source=FakeSource(Node("AXWindow", children=[node])),
                             max_depth=5)
    assert "Deepname" not in wt.texts and wt.truncated


def test_no_window_no_text():
    assert sc.read_window_text(1, source=FakeSource(None)).texts == []
    assert sc.read_window_text(0, source=FakeSource(Node())).texts == []


def test_cancel_stops_the_walk():
    win, _ = chat_window()
    wt = sc.read_window_text(1, source=FakeSource(win), cancelled=lambda: True)
    assert wt.texts == [] and wt.truncated


def test_ax_errors_skip_the_node():
    class Broken(FakeSource):
        def attrs(self, el):
            if el.a.get("AXValue") == "Priya: on it":
                raise RuntimeError("app went away")
            return super().attrs(el)
    win, _ = chat_window()
    wt = sc.read_window_text(1, source=Broken(win))
    assert "Priya: on it" not in wt.texts and "Zorawar Singh" in wt.texts


# -- Capture at key-down ------------------------------------------------------

def fake_reader(texts, near=0, secure=False, gate=None):
    def read(pid, focused, cancelled=lambda: False):
        if gate is not None:
            gate.wait(2)
        return sc.WindowText(texts=list(texts), near=near, secure=secure)
    return read


@pytest.fixture
def words_loaded(monkeypatch):
    monkeypatch.setattr(sc, "_ENGLISH", ENGLISH)


def test_capture_returns_terms_when_done(words_loaded):
    cap = sc.Capture(1, reader=fake_reader(["Ashton Hall said hi"]), own_name="",
                     secure_check=lambda: False).run_now()
    assert cap.terms() == ["Ashton Hall"]
    assert cap.elapsed_s is not None


def test_capture_not_ready_at_key_up_gives_nothing_and_never_waits(words_loaded):
    gate = threading.Event()
    cap = sc.Capture(1, reader=fake_reader(["Ashton Hall"], gate=gate), own_name="",
                     secure_check=lambda: False).start()
    assert cap.terms() == []
    assert cap.skipped == "not ready at key-up"
    gate.set()
    cap._done.wait(2)
    assert cap._terms == []      # a late finish is dropped


def test_capture_skips_under_secure_input(words_loaded):
    called = []
    cap = sc.Capture(1, reader=lambda *a, **k: called.append(1), own_name="",
                     secure_check=lambda: True).run_now()
    assert cap.terms() == [] and called == [] and cap.skipped == "secure input"


def test_capture_skips_a_focused_password_field(words_loaded):
    cap = sc.Capture(1, reader=fake_reader(["Ashton"], secure=True), own_name="",
                     secure_check=lambda: False).run_now()
    assert cap.terms() == [] and cap.skipped == "password field"


def test_capture_survives_reader_errors(words_loaded):
    def boom(*a, **k):
        raise RuntimeError("AX gone")
    cap = sc.Capture(1, reader=boom, own_name="", secure_check=lambda: False).run_now()
    assert cap.terms() == [] and cap.skipped.startswith("error")


def test_capture_drops_own_name(words_loaded):
    cap = sc.Capture(1, reader=fake_reader(["Khush Mutha", "Ashton"]),
                     own_name="Khush Mutha", secure_check=lambda: False).run_now()
    assert cap.terms() == ["Ashton"]
