"""Copyable terms: what counts as a name, place or item, in prose and on menus."""
import json

from grimoireassist.games import load_terms
from grimoireassist.terms import Lexicon, Term, TermLog, find_terms, label_terms, label_text

LEX = Lexicon(monsters=["Rathalos", "Rey Dau"], locations=["Kamura Village"],
              items=["Iron Sword"], characters=["Hinoa"])


def _found(text, lexicon=LEX):
    return [(text[s:e], t.text, t.kind) for s, e, t in find_terms(text, lexicon)]


# ── prose ──────────────────────────────────────────────────────────────────────
def test_known_names_carry_their_kind():
    assert _found("Head to Kamura Village and speak with Hinoa.") == [
        ("Kamura Village", "Kamura Village", "location"),
        ("Hinoa", "Hinoa", "character"),
    ]


def test_a_known_name_matches_whatever_its_case():
    assert _found("kamura village is quiet.")[0][1:] == ("Kamura Village", "location")


def test_ocr_jitter_snaps_to_the_list_spelling():
    """What gets copied is the real name, not the misread — it's going into a
    search box."""
    assert _found("A Rathal0s was spotted.") == [("Rathal0s", "Rathalos", "monster")]


def test_an_unknown_capitalised_run_is_a_term():
    assert _found("We camped near the Flooded Forest.") == [
        ("Flooded Forest", "Flooded Forest", "term")]


def test_connectors_stay_inside_a_name():
    assert _found("They say the Tower of Dawn is north.", Lexicon()) == [
        ("Tower of Dawn", "Tower of Dawn", "term")]


def test_a_sentence_opening_word_alone_is_not_a_name():
    assert _found("Careful out there. Watch the river.", Lexicon()) == []


def test_grammar_words_are_not_names():
    assert _found("I think You should go. Yes, Please hurry.", Lexicon()) == []


def test_a_name_leading_a_sentence_still_counts_when_longer():
    assert _found("Elder Fugen waits.", Lexicon())[0][0] == "Elder Fugen"


def test_spans_never_overlap_and_known_names_win():
    found = find_terms("Take the Iron Sword Plus to Kamura Village.", LEX)
    spans = [(s, e) for s, e, _t in found]
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2
    texts = [t.text for _s, _e, t in found]
    assert "Iron Sword" in texts and "Kamura Village" in texts


def test_works_with_no_lexicon_at_all():
    assert _found("Ask Hinoa.", None) == [("Hinoa", "Hinoa", "term")]


# ── labels ─────────────────────────────────────────────────────────────────────
def _lines(*texts):
    return [(t, 0.9, (0, i * 20, 100, 18)) for i, t in enumerate(texts)]


def test_menu_labels_become_terms_and_counters_drop():
    terms = label_terms(_lines("Mega Potion x10", "Iron Sword", "Herb 3"), LEX)
    assert [(t.text, t.kind, t.source) for t in terms] == [
        ("Mega Potion", "term", "map/menu"),
        ("Iron Sword", "item", "map/menu"),
        ("Herb", "term", "map/menu"),
    ]


def test_button_hints_and_noise_are_not_labels():
    terms = label_terms(_lines("Related Tips", "Place Marker", "Find Self", "120", "ab",
                               "x3"), LEX)
    assert terms == []      # "Find Self" must not leave "Self" behind


def test_prose_on_a_menu_is_searched_not_taken_whole():
    terms = label_terms(_lines("Restores a little health. Sold in Kamura Village."), LEX)
    assert [t.text for t in terms] == ["Kamura Village"]


def test_an_unpunctuated_hint_is_not_a_label():
    assert label_terms(_lines("Talk to the elder"), Lexicon()) == []


def test_a_long_title_cased_label_is_one_term():
    assert [t.text for t in label_terms(_lines("Tower of the Ancients"))] == [
        "Tower of the Ancients"]


def test_label_text_rejects_sentences():
    assert label_text("Iron Sword") == "Iron Sword"
    assert label_text("You found it!") == ""


def test_duplicate_labels_are_listed_once():
    assert len(label_terms(_lines("Iron Sword", "iron sword"), LEX)) == 1


# ── log ────────────────────────────────────────────────────────────────────────
def test_the_log_is_newest_first_and_dedupes():
    log = TermLog()
    log.add(Term("Hinoa"))
    log.add(Term("Kamura Village"))
    log.add(Term("hinoa"))
    assert [t.text for t in log.terms()] == ["hinoa", "Kamura Village"]


def test_a_batch_keeps_its_order_at_the_front():
    log = TermLog()
    log.add(Term("Old"))
    log.extend([Term("First"), Term("Second")])
    assert [t.text for t in log.terms()] == ["First", "Second", "Old"]


def test_a_known_kind_is_not_lost_to_a_later_guess():
    log = TermLog()
    log.add(Term("Rathalos", "monster"))
    log.add(Term("Rathalos", "term", "screen"))
    assert log.terms()[0].kind == "monster"


def test_the_log_is_capped_at_the_far_end():
    log = TermLog(maxlen=3)
    for name in ("A1", "B2", "C3", "D4"):
        log.add(Term(name))
    assert [t.text for t in log.terms()] == ["D4", "C3", "B2"]


# ── terms.json ─────────────────────────────────────────────────────────────────
def test_term_lists_load_from_the_game_folder(tmp_path):
    cfg = tmp_path / "config.yaml"
    folder = tmp_path / "games" / "mh"
    folder.mkdir(parents=True)
    (folder / "terms.json").write_text(json.dumps(
        {"locations": ["Flooded Forest"], "items": ["Mega Potion", ""]}),
        encoding="utf-8")
    assert load_terms("mh", cfg) == {
        "locations": ["Flooded Forest"], "items": ["Mega Potion"], "characters": []}
    lex = Lexicon.load("mh", cfg)
    assert lex.resolve("flooded forest") == ("Flooded Forest", "location")


def test_a_missing_or_broken_terms_file_means_no_lists(tmp_path):
    cfg = tmp_path / "config.yaml"
    assert load_terms("none", cfg) == {}
    folder = tmp_path / "games" / "bad"
    folder.mkdir(parents=True)
    (folder / "terms.json").write_text("{not json", encoding="utf-8")
    assert load_terms("bad", cfg) == {}
