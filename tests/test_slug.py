"""URL slug formatting for the monster info site."""
from grimoireassist.ui.monster_panel import to_slug


def test_basic():
    assert to_slug("Rathalos") == "rathalos"


def test_spaces_to_hyphen():
    assert to_slug("Azure Rathalos") == "azure-rathalos"


def test_punctuation_and_case():
    assert to_slug("Dreadking  Rathalos!") == "dreadking-rathalos"
    assert to_slug("  Pink Rathian  ") == "pink-rathian"
