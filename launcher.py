"""PyInstaller entry point.

The real entry point is grimoireassist/__main__.py, but its relative imports
need the package context, so the frozen exe starts here. freeze_support() keeps
any torch/easyocr multiprocessing use from re-launching the whole app.
"""
import multiprocessing
import sys

from grimoireassist.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
