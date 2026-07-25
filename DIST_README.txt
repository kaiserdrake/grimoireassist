GrimoireAssist - portable build
===============================

Getting started
---------------
1. Unzip this folder anywhere writable (NOT inside the zip, NOT a read-only
   location like C:\Program Files).
2. Run GrimoireAssist.exe.

Windows SmartScreen may warn because the exe is unsigned: click
"More info" -> "Run anyway".

Your data stays next to the exe
-------------------------------
config.yaml, games\, logs\ and snapshots\ are created in the same folder as
GrimoireAssist.exe. Move the folder, and your settings move with it.

First run
---------
The OCR engine (EasyOCR) downloads its models (~100 MB) on the first read.
This needs internet once; the models are stored in %USERPROFILE%\.EasyOCR.

GPU OCR
-------
The CUDA 12.4 runtime is bundled; you only need a reasonably recent NVIDIA
driver. No NVIDIA GPU? Set "gpu: false" under "ocr:" in config.yaml (or
untick "Use GPU" in the menu) - OCR then runs on CPU.

External requirements
---------------------
- Virtual camera output requires OBS Studio (its virtual-camera driver):
  https://obsproject.com/
- The optional Tesseract OCR fallback requires a system Tesseract install;
  by default the bundled EasyOCR engine is used.

Troubleshooting
---------------
GrimoireAssist-console.exe is the same app with a console window, useful for
error messages and CLI flags, e.g.:

    GrimoireAssist-console.exe --list-devices
