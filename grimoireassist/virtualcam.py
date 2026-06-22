"""Virtual-camera sink: re-publishes the clean capture feed as OBS Virtual Camera."""
from __future__ import annotations

from typing import Optional

import numpy as np

try:
    import pyvirtualcam
    from pyvirtualcam import PixelFormat
    _HAVE_PYVCAM = True
except Exception:  # pragma: no cover - import-time only
    _HAVE_PYVCAM = False


class VirtualCamSink:
    """Lazily-initialised wrapper around pyvirtualcam.

    The first frame fixes the camera resolution. Always sends the unmodified
    frame so downstream apps see a clean feed (no overlays).
    """

    def __init__(self, fps: int = 30) -> None:
        self.fps = fps
        self._cam: Optional["pyvirtualcam.Camera"] = None
        self._size: Optional[tuple[int, int]] = None
        self.available = _HAVE_PYVCAM
        self.last_error: Optional[str] = None

    def _ensure(self, width: int, height: int) -> bool:
        if not self.available:
            self.last_error = "pyvirtualcam not installed"
            return False
        if self._cam is not None and self._size == (width, height):
            return True
        self.close()
        try:
            self._cam = pyvirtualcam.Camera(
                width=width, height=height, fps=self.fps,
                fmt=PixelFormat.BGR, print_fps=False,
            )
            self._size = (width, height)
            self.last_error = None
            return True
        except Exception as exc:
            self._cam = None
            self.last_error = (
                f"Could not start virtual camera ({exc}). "
                "Is the OBS Virtual Camera driver installed?"
            )
            return False

    @property
    def device_name(self) -> Optional[str]:
        return getattr(self._cam, "device", None) if self._cam else None

    def send(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        if not self._ensure(w, h):
            return
        try:
            self._cam.send(frame)
            self._cam.sleep_until_next_frame()
        except Exception as exc:
            self.last_error = f"virtual cam send failed: {exc}"
            self.close()

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:
                pass
        self._cam = None
        self._size = None
