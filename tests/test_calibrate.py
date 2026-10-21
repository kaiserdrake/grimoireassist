"""Calibrate dialog's fixed region slots (draw / delete / save) and the multi-entry
labels setting it writes, including settings saved when labels was one region.

Runs on Qt's offscreen platform, so it needs no display.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from grimoireassist.config import (Config, GameSettings, Region,
                                   _load_game_settings, _save_game_settings)
from grimoireassist.ui.calibrate import CalibrateDialog, display_name


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---- settings ----------------------------------------------------------
def test_old_single_labels_region_loads_as_a_list(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"regions": {
        "labels": {"x": 1, "y": 2, "w": 30, "h": 40}}}), encoding="utf-8")
    assert _load_game_settings(path).labels == [Region(1, 2, 30, 40)]


def test_an_unset_old_labels_region_loads_as_no_regions(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"regions": {
        "labels": {"x": 0, "y": 0, "w": 0, "h": 0}}}), encoding="utf-8")
    assert _load_game_settings(path).labels == []


def test_labels_regions_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    regions = [Region(1, 2, 30, 40), Region(5, 6, 70, 80)]
    _save_game_settings(path, GameSettings(labels=regions))
    assert _load_game_settings(path).labels == regions


def test_config_yaml_games_section_takes_either_shape():
    cfg = Config.from_dict({"games": {
        "old": {"regions": {"labels": {"x": 1, "y": 2, "w": 30, "h": 40}}},
        "new": {"regions": {"labels": [{"x": 1, "y": 2, "w": 30, "h": 40},
                                       {"x": 5, "y": 6, "w": 70, "h": 80}]}},
    }})
    assert cfg.games["old"].labels == [Region(1, 2, 30, 40)]
    assert len(cfg.games["new"].labels) == 2


# ---- the dialog ----------------------------------------------------------
_SLOTS = ["monster_1", "monster_2", "monster_3", "battle_end", "dialogue",
          "labels_1", "labels_2", "labels_3", "labels_4"]


def _dialog(app, **ocr):
    cfg = Config()
    for name, value in ocr.items():
        setattr(cfg.ocr, name, value)
    return CalibrateDialog(cfg, np.zeros((360, 640, 3), dtype=np.uint8))


def _entries(dlg):
    return [dlg.region_picker.itemData(i)
            for i in range(dlg.region_picker.count())]


def _select(dlg, key):
    dlg.region_picker.setCurrentIndex(dlg.region_picker.findData(key))


def _r(n):
    return Region(n, n, 10, 10)


def test_the_picker_always_lists_every_slot(app):
    assert _entries(_dialog(app)) == _SLOTS
    dlg = _dialog(app, regions_monster_names=[_r(1), _r(2)],
                  regions_labels=[_r(3)])
    assert _entries(dlg) == _SLOTS
    texts = [dlg.region_picker.itemText(i) for i in range(len(_SLOTS))]
    assert texts[:3] == ["Monster 1", "Monster 2", "Monster 3 — not set"]
    assert texts[3] == "Battle end — not set"
    assert texts[5:7] == ["Labels 1", "Labels 2 — not set"]


def test_display_names():
    assert display_name("monster_3") == "Monster 3"
    assert display_name("labels_4") == "Labels 4"
    assert display_name("battle_end") == "Battle end"
    assert display_name("dialogue") == "Dialogue"


def test_extra_regions_past_the_slots_are_dropped(app):
    dlg = _dialog(app, regions_monster_names=[_r(i) for i in range(1, 6)],
                  regions_labels=[_r(i) for i in range(10, 16)])
    assert _entries(dlg) == _SLOTS
    dlg._save()
    assert dlg.cfg.ocr.regions_monster_names == [_r(1), _r(2), _r(3)]
    assert dlg.cfg.ocr.regions_labels == [_r(10), _r(11), _r(12), _r(13)]


def test_drawing_on_an_unset_slot_adds_it(app):
    dlg = _dialog(app)
    _select(dlg, "labels_3")
    assert not dlg.delete_btn.isEnabled()        # nothing to delete yet
    dlg.canvas.regions["labels_3"] = _r(7)       # as a draw does
    dlg.canvas.changed.emit()
    assert dlg.region_picker.currentText() == "Labels 3"
    assert dlg.current_key() == "labels_3"
    assert dlg.delete_btn.isEnabled()
    dlg._save()
    assert dlg.cfg.ocr.regions_labels == [_r(7)]


def test_delete_clears_the_slot_in_place(app):
    dlg = _dialog(app, regions_labels=[_r(1), _r(2), _r(3)])
    _select(dlg, "labels_2")
    dlg._delete()
    assert _entries(dlg) == _SLOTS                # nothing renumbered
    assert not dlg.canvas.regions["labels_2"].is_set()
    assert dlg.canvas.regions["labels_3"] == _r(3)
    assert dlg.region_picker.currentText() == "Labels 2 — not set"
    assert not dlg.delete_btn.isEnabled()


def test_single_regions_are_cleared_too(app):
    dlg = _dialog(app, regions_dialogue=Region(5, 5, 50, 50))
    _select(dlg, "dialogue")
    assert dlg.delete_btn.isEnabled()
    dlg._delete()
    assert not dlg.canvas.regions["dialogue"].is_set()


def test_save_writes_only_drawn_slots_in_order(app):
    dlg = _dialog(app, regions_monster_names=[_r(1), _r(2)],
                  regions_labels=[_r(3), _r(4)],
                  regions_dialogue=Region(5, 5, 50, 50))
    _select(dlg, "monster_1")
    dlg._delete()
    _select(dlg, "dialogue")
    dlg._delete()
    dlg.canvas.regions["labels_4"] = _r(9)
    dlg._save()
    ocr = dlg.cfg.ocr
    assert ocr.regions_monster_names == [_r(2)]
    assert ocr.regions_labels == [_r(3), _r(4), _r(9)]
    assert not ocr.regions_dialogue.is_set()


def test_saving_with_no_monster_regions_keeps_an_empty_slot(app):
    dlg = _dialog(app, regions_monster_names=[_r(1)])
    _select(dlg, "monster_1")
    dlg._delete()
    dlg._save()
    assert dlg.cfg.ocr.regions_monster_names == [Region()]
