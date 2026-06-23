"""Capture thread: owns the physical capture-card device and fans out frames.

This is the single owner of the device. It pushes each frame to:
  (a) an optional virtual-camera sink (clean, unmodified) and
  (b) a single-slot latest-frame holder consumed by the GUI and OCR.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np


class FrameBuffer:
    """Thread-safe single-slot holder for the most recent frame."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._seq = 0

    def set(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._seq += 1

    def get(self) -> tuple[Optional[np.ndarray], int]:
        with self._lock:
            if self._frame is None:
                return None, self._seq
            return self._frame.copy(), self._seq

    def current_seq(self) -> int:
        """Frame counter without copying — used to detect whether frames flow."""
        with self._lock:
            return self._seq


def list_devices(max_index: int = 8) -> list[int]:
    """Probe device indices that open successfully."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                found.append(i)
        cap.release()
    return found


def list_named_devices() -> list[tuple[int, str]]:
    """Return (index, friendly_name) for each capture device.

    Uses DirectShow enumeration (pygrabber) so names match what Windows shows and
    line up with OpenCV's CAP_DSHOW indices. Falls back to probing + generic
    labels if pygrabber isn't available.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        if names:
            return [(i, name) for i, name in enumerate(names)]
    except Exception:
        pass
    return [(i, f"Camera {i}") for i in list_devices()]


class CaptureThread(threading.Thread):
    def __init__(
        self,
        device_index: int,
        width: int,
        height: int,
        fps: int,
        buffer: FrameBuffer,
        on_frame: Optional[Callable[[np.ndarray], None]] = None,
        video_file: Optional[str] = None,
    ) -> None:
        super().__init__(name="CaptureThread", daemon=True)
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.buffer = buffer
        self.on_frame = on_frame  # clean-frame sink (e.g. virtual cam)
        self.video_file = video_file
        self._stop_event = threading.Event()
        self.last_error: Optional[str] = None
        self.actual_size: Optional[tuple[int, int]] = None

    def stop(self) -> None:
        self._stop_event.set()

    def _open(self) -> Optional[cv2.VideoCapture]:
        if self.video_file:
            cap = cv2.VideoCapture(self.video_file)
        else:
            cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        if not cap.isOpened():
            cap.release()
            return None
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_size = (w or self.width, h or self.height)
        return cap

    def run(self) -> None:
        backoff = 0.5
        frame_interval = 1.0 / max(1, self.fps)
        while not self._stop_event.is_set():
            cap = self._open()
            if cap is None:
                if self.video_file:
                    self.last_error = f"Could not open video file: {self.video_file}"
                else:
                    self.last_error = (
                        f"can't open device {self.device_index} "
                        f"(in use by another app, or unplugged)"
                    )
                time.sleep(min(backoff, 5.0))
                backoff = min(backoff * 2, 5.0)
                continue
            backoff = 0.5
            self.last_error = None
            while not self._stop_event.is_set():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self.video_file:
                        # loop the test clip
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    self.last_error = "Frame read failed; reconnecting"
                    break
                self.buffer.set(frame)
                if self.on_frame is not None:
                    try:
                        self.on_frame(frame)
                    except Exception as exc:  # never let the sink kill capture
                        self.last_error = f"frame sink error: {exc}"
                # Always pace to the target fps. Some capture cards return frames
                # without blocking, which would otherwise spin a CPU core at 100%.
                dt = time.time() - t0
                if dt < frame_interval:
                    time.sleep(frame_interval - dt)
            cap.release()
