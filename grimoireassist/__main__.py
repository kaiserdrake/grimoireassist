"""Entry point: parse args, load config, launch the Qt app."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config


def _migrate_webengine_storage() -> None:
    """One-time copy of QtWebEngine storage (cookies, logins) from the old
    interpreter-named location (…/Roaming/python[w]/QtWebEngine) to the
    GrimoireAssist one. Must run after setApplicationName and before any web
    profile is created; a no-op once the new directory exists."""
    import shutil
    from PyQt6.QtCore import QStandardPaths
    new_root = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)) / "QtWebEngine"
    if new_root.exists():
        return
    old_roots = [p for name in ("pythonw", "python")
                 if (p := new_root.parent.parent / name / "QtWebEngine").is_dir()]
    if not old_roots:
        return

    def freshness(root: Path) -> float:
        stamps = [c.stat().st_mtime for c in root.glob("*/Cookies")]
        return max(stamps) if stamps else root.stat().st_mtime

    try:
        shutil.copytree(max(old_roots, key=freshness), new_root)
    except Exception:
        pass  # worst case the user logs in again


def _list_devices() -> int:
    from .capture import list_devices
    found = list_devices()
    if found:
        print("Available capture device indices:", ", ".join(map(str, found)))
    else:
        print("No capture devices found.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grimoireassist")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--list-devices", action="store_true",
                        help="list capture device indices and exit")
    parser.add_argument("--video", help="read from a video file instead of a device (testing)")
    parser.add_argument("--device", type=int, help="override capture device index")
    args = parser.parse_args(argv)

    if args.list_devices:
        return _list_devices()

    cfg = Config.load(args.config)
    if not Path(args.config).exists():
        cfg.save(args.config)  # write defaults so the user has something to edit
    if args.video:
        cfg.capture.video_file = args.video
    if args.device is not None:
        cfg.capture.device_index = args.device

    # Pre-load torch DLLs (c10.dll, libgomp, MKL …) BEFORE PyQt6 loads its own
    # copies of those same runtime DLLs.  On Windows, whichever library wins the
    # DLL load-order race becomes the "owner" of those DLLs; the second loader
    # (whichever arrives later, in a thread pool worker) gets WinError 1114.
    # Importing torch here — in the main thread, before Qt — ensures torch's
    # DLLs are registered first and the conflict never occurs.
    try:
        import torch as _torch  # noqa: F401
    except Exception:
        pass

    # Import Qt lazily so --list-devices works without a display.
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPalette
    from PyQt6.QtWidgets import QApplication

    def _apply_dark_theme(app: QApplication) -> None:
        app.setStyle("Fusion")
        p = QPalette()
        # backgrounds
        p.setColor(QPalette.ColorRole.Window,          QColor("#15151b"))
        p.setColor(QPalette.ColorRole.Base,            QColor("#1e1e28"))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#1a1a24"))
        p.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#2a2a36"))
        # text
        p.setColor(QPalette.ColorRole.WindowText,      QColor("#e8e8ec"))
        p.setColor(QPalette.ColorRole.Text,            QColor("#e8e8ec"))
        p.setColor(QPalette.ColorRole.ToolTipText,     QColor("#e8e8ec"))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b6b75"))
        # buttons
        p.setColor(QPalette.ColorRole.Button,          QColor("#2a2a36"))
        p.setColor(QPalette.ColorRole.ButtonText,      QColor("#e8e8ec"))
        # highlights
        p.setColor(QPalette.ColorRole.Highlight,       QColor("#5b3fa6"))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        # disabled state
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#4a4a55"))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor("#4a4a55"))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#4a4a55"))
        app.setPalette(p)
        app.setStyleSheet("""
            QMainWindow, QDialog {
                background: #15151b;
            }
            QToolBar {
                background: #1a1a24;
                border-bottom: 1px solid #2a2a36;
                spacing: 2px;
            }
            QToolButton {
                background: transparent;
                color: #e8e8ec;
                border: none;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 16px;
            }
            QToolButton:hover  { background: #2a2a36; }
            QToolButton:pressed { background: #3a3a50; }
            QMenuBar {
                background: #1a1a24;
                color: #e8e8ec;
            }
            QMenuBar::item:selected { background: #2a2a36; }
            QMenu {
                background: #1e1e28;
                color: #e8e8ec;
                border: 1px solid #2a2a36;
            }
            QMenu::item:selected { background: #5b3fa6; color: #fff; }
            QMenu::separator { height: 1px; background: #2a2a36; margin: 4px 8px; }
            QStatusBar {
                background: #1a1a24;
                color: #6b6b75;
                border-top: 1px solid #2a2a36;
                font-size: 11px;
            }
            QStatusBar QLabel { color: #6b6b75; }
            QScrollBar:vertical {
                background: #1e1e28; width: 8px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #3a3a50; border-radius: 4px; min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #1e1e28; height: 8px; margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #3a3a50; border-radius: 4px; min-width: 20px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QMessageBox { background: #15151b; color: #e8e8ec; }
            QMessageBox QLabel { color: #e8e8ec; }
            QPushButton {
                background: #2a2a36; border: none; border-radius: 6px;
                padding: 6px 16px; color: #e8e8ec;
            }
            QPushButton:hover   { background: #3a3a50; }
            QPushButton:pressed { background: #5b3fa6; }
            QPushButton:default {
                background: #5b3fa6; color: #fff; font-weight: 600;
            }
            QComboBox {
                background: #2a2a36; color: #e8e8ec;
                border: 1px solid #3a3a50; border-radius: 4px; padding: 4px 8px;
            }
            QComboBox QAbstractItemView {
                background: #1e1e28; color: #e8e8ec;
                selection-background-color: #5b3fa6;
            }
            QSpinBox {
                background: #2a2a36; color: #e8e8ec;
                border: 1px solid #3a3a50; border-radius: 4px; padding: 2px 6px;
            }
            QLabel { color: #e8e8ec; }
            QCheckBox { color: #e8e8ec; }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border: 1px solid #3a3a50; border-radius: 3px; background: #2a2a36;
            }
            QCheckBox::indicator:checked { background: #5b3fa6; border-color: #5b3fa6; }
        """)
    # QtWebEngine (embedded monster page) wants shared OpenGL contexts, set
    # before the QApplication is created.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    from PyQt6.QtGui import QIcon
    from .games import get_game, load_catalog, icon_path
    from .ui.game_select import GameSelectDialog
    from .ui.main_window import MainWindow

    # On Windows, give the process its own AppUserModelID so the taskbar uses our
    # window icon instead of the generic python.exe icon.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GrimoireAssist")
    except Exception:
        pass

    app = QApplication(sys.argv)
    # A stable application name gives QtWebEngine one storage directory
    # (…/AppData/Roaming/GrimoireAssist) regardless of which interpreter
    # (python.exe / pythonw.exe) launched us, so web logins persist either way.
    app.setApplicationName("GrimoireAssist")
    _migrate_webengine_storage()
    _icon = icon_path()
    if _icon:
        app.setWindowIcon(QIcon(_icon))
    _apply_dark_theme(app)

    # Show the game-select page when no valid game is selected yet.
    if get_game(cfg.selected_game, cfg._path) is None:
        dlg = GameSelectDialog(list(load_catalog(cfg._path)), current=cfg.selected_game)
        if not dlg.exec() or not dlg.selected:
            return 0  # user closed without choosing
        cfg.selected_game = dlg.selected
        cfg.save()

    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
