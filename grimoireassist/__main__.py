"""Entry point: parse args, load config, launch the Qt app."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config


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

    # Import Qt lazily so --list-devices works without a display.
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    # QtWebEngine (embedded monster page) wants shared OpenGL contexts, set
    # before the QApplication is created.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    from .games import get_game, load_catalog
    from .ui.game_select import GameSelectDialog
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)

    # Show the game-select page when no valid game is selected yet.
    if get_game(cfg.selected_game) is None:
        dlg = GameSelectDialog(list(load_catalog()), current=cfg.selected_game)
        if not dlg.exec() or not dlg.selected:
            return 0  # user closed without choosing
        cfg.selected_game = dlg.selected
        cfg.save()

    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
