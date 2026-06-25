"""URL building for monster info pages (path style + grimoire search style)."""
from grimoireassist.ui.monster_panel import build_monster_url, is_grimoire

GRIMOIRE = "https://grimoire.laeradsphere.com/game/236/258/notes?fileId=14&st1={name}"
MB = "https://monsterbuddy.app/3/monsters/{name}"


def test_search_single():
    assert build_monster_url(GRIMOIRE, ["Barroth"], "search") == \
        "https://grimoire.laeradsphere.com/game/236/258/notes?fileId=14&st1=barroth"


def test_search_multi_encoding_sorted():
    # terms are sorted, so detection order does not change the URL
    a = build_monster_url(GRIMOIRE, ["Barroth", "Anjanath"], "search")
    b = build_monster_url(GRIMOIRE, ["Anjanath", "Barroth"], "search")
    assert a == b                                              # order-independent
    assert a.endswith("&st1=anjanath%20%7C%7C%20barroth")      # 'anjanath || barroth'


def test_search_empty_is_none():
    assert build_monster_url(GRIMOIRE, [], "search") is None
    assert build_monster_url(GRIMOIRE, ["", "  "], "search") is None


def test_path_style_uses_slug_map():
    slugs = {"Azure Rathalos": "azure-rathalos"}
    url = build_monster_url(MB, ["Azure Rathalos", "Tigrex"], "path", slugs)
    assert url == "https://monsterbuddy.app/3/monsters/azure-rathalos"   # first only


def test_path_style_fallback_slug():
    assert build_monster_url(MB, ["Pink Rathian"], "path") == \
        "https://monsterbuddy.app/3/monsters/pink-rathian"


def test_custom_joiner():
    # a different multi-monster site can use any separator
    url = build_monster_url("https://x.test/?q={name}", ["Barroth", "Anjanath"],
                            "search", joiner=",")
    assert url.endswith("?q=anjanath%2Cbarroth")   # 'anjanath,barroth'


def test_is_grimoire():
    assert is_grimoire("https://grimoire.laeradsphere.com/x")
    assert not is_grimoire("https://monsterbuddy.app/3/monsters/x")
    assert not is_grimoire("")
