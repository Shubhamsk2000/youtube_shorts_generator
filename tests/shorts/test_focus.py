"""The creator's focus note: stored per project, sent to both AI passes, matches ranked first or kept alone."""
from shorts.config import SelectCfg
from shorts.models import Focus, Selection, VideoMeta, save_json
from shorts.stages import select
from shorts.stages.select import PROMPT, REFINE_PROMPT, SCHEMA, _focus_note, _with_focus, load_focus, save_focus, validate

from test_select import pick, sents

CFG = SelectCfg()


def test_focus_is_saved_cleaned_and_removed(tmp_path):
    assert load_focus(tmp_path) is None
    f = save_focus(tmp_path, "  the   argument about money\n", "only")
    assert f.text == "the argument about money" and f.mode == "only"
    assert load_focus(tmp_path) == f
    assert save_focus(tmp_path, "x" * 900).text == "x" * select.FOCUS_MAX
    assert save_focus(tmp_path, "weird", "sideways").mode == "prefer"
    assert save_focus(tmp_path, "   ") is None and load_focus(tmp_path) is None


def test_without_a_note_the_request_is_unchanged():
    # cached AI answers are keyed on the exact request: no note must mean byte-identical prompts and schema
    assert _focus_note(None) == "" and _with_focus(SCHEMA, None) is SCHEMA
    assert "{lines}\n{focus_note}\nFind EVERY" in PROMPT
    assert "{candidates}\n{focus_note}\nYou are now" in REFINE_PROMPT
    note = _focus_note(Focus(text='the "bird" moments', mode="prefer"))
    assert "the 'bird' moments" in note and "matches_focus" in note
    assert "matches_focus" in _with_focus(SCHEMA, Focus(text="x"))["properties"]["clips"]["items"]["properties"]
    assert "matches_focus" not in SCHEMA["properties"]["clips"]["items"]["properties"]


def test_matches_rank_first_and_only_mode_keeps_just_them():
    ss = sents(60)
    raw = [pick(0, 4, score=9), pick(10, 14, score=5, matches_focus=True), pick(20, 24, score=8)]
    prefer, _ = validate(raw, ss, 400, CFG, focus=Focus(text="x"))
    assert [c.start_sentence for c in prefer] == [10, 0, 20]
    assert [c.focus_match for c in prefer] == [True, False, False]
    only, rej = validate(raw, ss, 400, CFG, focus=Focus(text="x", mode="only"))
    assert [c.start_sentence for c in only] == [10] and rej["outside your focus"] == 2
    plain, _ = validate(raw, ss, 400, CFG)
    assert [c.start_sentence for c in plain] == [0, 20, 10] and plain[0].focus_match is None


def test_a_new_note_picks_again(tmp_path, monkeypatch):
    meta = VideoMeta(id="v", title="v", duration=100, width=1920, height=1080, fps=30, file="a.mp4")
    save_json(Selection(source="gemini", clips=[]), tmp_path / select.OUT)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    calls = []

    def fake_llm(*a, **kw):
        calls.append(kw.get("focus"))
        return Selection(source="gemini", clips=[])

    monkeypatch.setattr(select, "_select_llm", fake_llm)
    monkeypatch.setattr(select, "_audio", lambda *a, **k: (None, None))
    from shorts.models import Sentence, Transcript, Word
    save_json(Transcript(language="en", model="m", duration=10, sentences=[
        Sentence(id=0, start=0, end=1, text="hi", words=[Word(text="hi", start=0, end=1, prob=1)])]),
        tmp_path / "transcript.json")
    from shorts.config import Config
    cfg = Config()
    cfg.select.use_heatmap = cfg.select.refine = False
    select.run(tmp_path, meta, cfg)
    assert calls == []                                   # same (no) note: cached picks
    save_focus(tmp_path, "the landing")
    sel, _ = select.run(tmp_path, meta, cfg)
    assert calls[-1].text == "the landing" and sel.focus.text == "the landing"
    select.run(tmp_path, meta, cfg)
    assert len(calls) == 1                               # picked with this note already
