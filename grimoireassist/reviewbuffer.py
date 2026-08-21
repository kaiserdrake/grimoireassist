"""Rolling review buffer: a disk-backed DVR of the last N minutes of capture.

Why not RAM: 30 minutes of 1080p30 BGR frames is ~340 GB. Instead each kept
frame is downscaled and JPEG-encoded into an append-only *segment* file under
the buffer directory, and only the index (segment, byte offset, length,
timestamp) lives in memory. At the defaults (720p, 15 fps, quality 75) half an
hour costs roughly 1 GB of disk and a few MB of RAM, and any frame can be
fetched by index — which is what makes frame-accurate scrubbing cheap.

Thread ownership:
  * `feed()` runs on the capture thread. It only decimates to the target fps,
    downscales, and hands the frame to a bounded queue — a slow disk drops
    frames instead of stalling capture.
  * one daemon encoder thread JPEG-encodes, appends, and evicts anything past
    the retention window (whichever of `minutes` / `max_disk_mb` binds first).
  * `snapshot()` hands out a frozen, *pinned* view of the index that the review
    UI can read from any thread while recording continues underneath it.

Nothing here imports Qt, so it is unit-testable headless.
"""
from __future__ import annotations

import bisect
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Callable, Deque, Dict, List, Optional

import cv2
import numpy as np

DEFAULT_MINUTES = 30.0
DEFAULT_FPS = 15.0
DEFAULT_MAX_HEIGHT = 720   # 0 = keep the source resolution
DEFAULT_QUALITY = 75
DEFAULT_MAX_DISK_MB = 4096

_SEGMENT_SECONDS = 20.0    # roll to a new file this often (eviction granularity)
_QUEUE_DEPTH = 8           # frames in flight before feed() starts dropping
_HANDLE_CACHE = 8          # open segment handles kept per reader
_SUFFIX = ".gaseg"


class ExportCancelled(Exception):
    """Raised by `export` when the cancel event is set mid-write."""


@dataclass(frozen=True)
class FrameRef:
    """Where one encoded frame lives, and when it was captured."""
    seg: int
    offset: int
    length: int
    t: float      # time.monotonic() at capture


@dataclass
class _Segment:
    seg_id: int
    path: Path
    frames: int = 0    # index entries still pointing here
    nbytes: int = 0


class _SegmentReader:
    """Reads frame bytes out of segment files, caching a few open handles.

    Segment files are append-only, so reading a file the recorder is still
    writing is safe: any offset already in the index is immutable.
    """

    def __init__(self, paths: Dict[int, Path]) -> None:
        self._paths = dict(paths)
        self._open: "OrderedDict[int, object]" = OrderedDict()
        self._lock = threading.Lock()

    def read(self, ref: FrameRef) -> Optional[bytes]:
        with self._lock:
            fh = self._handle(ref.seg)
            if fh is None:
                return None
            try:
                fh.seek(ref.offset)
                data = fh.read(ref.length)
            except OSError:
                return None
        return data if data and len(data) == ref.length else None

    def _handle(self, seg_id: int):
        fh = self._open.get(seg_id)
        if fh is not None:
            self._open.move_to_end(seg_id)
            return fh
        path = self._paths.get(seg_id)
        if path is None:
            return None
        try:
            fh = open(path, "rb")
        except OSError:
            return None
        self._open[seg_id] = fh
        while len(self._open) > _HANDLE_CACHE:
            _, old = self._open.popitem(last=False)
            try:
                old.close()
            except OSError:
                pass
        return fh

    def close(self) -> None:
        with self._lock:
            for fh in self._open.values():
                try:
                    fh.close()
                except OSError:
                    pass
            self._open.clear()


class BufferSnapshot:
    """A frozen view of the buffer: the frame list at snapshot time.

    Holding one pins its segment files, so the recorder will not delete data
    out from under a review session even if it runs longer than the retention
    window. Call `close()` (or use it as a context manager) to release the pin.
    """

    def __init__(self, frames: List[FrameRef], paths: Dict[int, Path],
                 fps: float, wall_offset: float,
                 owner: Optional["RollingRecorder"] = None) -> None:
        self._frames = frames
        self.seg_ids = {f.seg for f in frames}
        self.fps = fps
        self._wall_offset = wall_offset
        self._owner = owner
        self._reader = _SegmentReader(paths)
        self._paths = dict(paths)
        self._closed = False
        t0 = frames[0].t if frames else 0.0
        # Relative capture times, ascending — the axis both the scrub bar and
        # timestamp-driven playback work in.
        self._times = [f.t - t0 for f in frames]
        if owner is not None:
            owner._attach(self)   # pins these segments until close()

    # ---- geometry --------------------------------------------------------
    def __len__(self) -> int:
        return len(self._frames)

    def __bool__(self) -> bool:
        return bool(self._frames)

    @property
    def duration_s(self) -> float:
        return self._times[-1] if self._times else 0.0

    @property
    def size_bytes(self) -> int:
        return sum(f.length for f in self._frames)

    def rel_time(self, i: int) -> float:
        """Seconds from the start of the buffer to frame `i`."""
        if not self._times:
            return 0.0
        return self._times[max(0, min(i, len(self._times) - 1))]

    def wall_time(self, i: int) -> float:
        """Unix time at which frame `i` was captured (for a clock readout)."""
        if not self._frames:
            return time.time()
        f = self._frames[max(0, min(i, len(self._frames) - 1))]
        return f.t + self._wall_offset

    def index_for_time(self, rel: float) -> int:
        """Index of the frame shown at `rel` seconds — the last frame at or
        before it, so playback holds a frame across a capture gap."""
        if not self._times:
            return 0
        i = bisect.bisect_right(self._times, rel) - 1
        return max(0, min(i, len(self._times) - 1))

    # ---- reading ---------------------------------------------------------
    def jpeg(self, i: int) -> Optional[bytes]:
        if self._closed or not (0 <= i < len(self._frames)):
            return None
        return self._reader.read(self._frames[i])

    def frame(self, i: int) -> Optional[np.ndarray]:
        """Decode frame `i` to BGR, or None if the bytes are unreadable."""
        data = self.jpeg(i)
        if not data:
            return None
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        return img

    def independent_reader(self) -> "BufferSnapshot":
        """A second view over the same frames with its own file handles and its
        own pin, so a long export neither contends with the UI's scrubbing reads
        nor loses its data when the review screen closes. Must also be closed."""
        return BufferSnapshot(list(self._frames), self._paths, self.fps,
                              self._wall_offset, owner=self._owner)

    # ---- lifetime --------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reader.close()
        if self._owner is not None:
            self._owner._detach(self)

    def __enter__(self) -> "BufferSnapshot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class RollingRecorder:
    """Keeps the last `minutes` of the capture feed on disk, newest-wins."""

    def __init__(self, directory: str | Path,
                 minutes: float = DEFAULT_MINUTES,
                 fps: float = DEFAULT_FPS,
                 max_height: int = DEFAULT_MAX_HEIGHT,
                 quality: int = DEFAULT_QUALITY,
                 max_disk_mb: int = DEFAULT_MAX_DISK_MB) -> None:
        self.dir = Path(directory)
        self.retention_s = max(1.0, float(minutes) * 60.0)
        self.fps = min(max(float(fps), 1.0), 60.0)
        self.max_height = max(0, int(max_height))
        self.quality = int(min(max(int(quality), 30), 95))
        self.max_bytes = int(max(64, int(max_disk_mb))) * 1024 * 1024
        # Wall clock matching monotonic zero, so stored monotonic stamps can be
        # shown as clock times.
        self.wall_offset = time.time() - time.monotonic()

        self.dropped = 0                       # frames feed() had to discard
        self.last_error: Optional[str] = None

        # Re-entrant: snapshot() builds a BufferSnapshot while holding the lock,
        # and the snapshot registers itself back through _attach.
        self._lock = threading.RLock()
        self._index: Deque[FrameRef] = deque()
        self._segments: Dict[int, _Segment] = {}
        self._snapshots: List[BufferSnapshot] = []
        self._doomed: List[_Segment] = []      # unlink failed; retry next sweep
        self._indexed_bytes = 0
        self._disk_bytes = 0
        self._next_seg = 0
        self._cur: Optional[_Segment] = None
        self._cur_fh = None
        self._cur_start_t = 0.0
        self._queue: "Queue[tuple[np.ndarray, float]]" = Queue(maxsize=_QUEUE_DEPTH)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_kept_t = 0.0
        self._idle = threading.Event()         # set while the queue is drained
        self._idle.set()

    # ================= lifecycle =================
    def start(self) -> bool:
        """Create the buffer directory and spin up the encoder thread."""
        if self._running:
            return True
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._purge_stale()
        except OSError as exc:
            self.last_error = f"cannot use review buffer directory: {exc}"
            return False
        self._running = True
        self._thread = threading.Thread(target=self._encode_loop,
                                        name="ReviewRecorder", daemon=True)
        self._thread.start()
        return True

    def stop(self, discard: bool = True) -> None:
        """Stop recording. `discard` deletes the buffered segments (the buffer
        is a live cache, not user data — saved clips go elsewhere).

        Segments a live snapshot is still reading survive the discard; they go
        when that snapshot closes, or at worst are cleaned by the next `start`.
        """
        self._running = False
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        with self._lock:
            self._close_current()
            if discard:
                self._index.clear()
                self._indexed_bytes = 0
                for seg in self._segments.values():
                    seg.frames = 0
                self._sweep_locked()

    @property
    def running(self) -> bool:
        return self._running

    def _purge_stale(self) -> None:
        """Drop segments left behind by a previous run (or a crash)."""
        for path in self.dir.glob(f"*{_SUFFIX}"):
            try:
                path.unlink()
            except OSError:
                pass

    # ================= capture-thread side =================
    def feed(self, frame: np.ndarray, t: Optional[float] = None,
             block: bool = False) -> bool:
        """Offer a capture frame to the buffer. Returns True if it was queued.

        Called on the capture thread: decimates to the target fps, downscales
        (which also bounds the queue's memory), and by default never blocks — if
        the encoder is behind, the frame is dropped and counted, because a live
        feed must not be held up by the disk.

        `block=True` waits for room instead, for callers feeding faster than real
        time (offline sources, tests) that want every frame kept.
        """
        if not self._running or frame is None:
            return False
        now = time.monotonic() if t is None else float(t)
        # 1 ms of slack so a source running at exactly the target fps isn't
        # decimated to half rate by jitter.
        if self._last_kept_t and (now - self._last_kept_t) < (1.0 / self.fps - 0.001):
            return False
        try:
            small = self._downscale(frame)
        except cv2.error as exc:
            self.last_error = f"review buffer resize failed: {exc}"
            return False
        try:
            self._queue.put((small, now), block=block, timeout=5.0 if block else None)
        except Full:
            self.dropped += 1
            return False
        self._idle.clear()
        self._last_kept_t = now
        return True

    def _downscale(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if self.max_height and h > self.max_height:
            scale = self.max_height / float(h)
            # Even dimensions keep the video encoders used by `export` happy.
            tw = max(2, int(round(w * scale)) & ~1)
            th = max(2, int(round(h * scale)) & ~1)
            return cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        # The capture thread may reuse its frame array, so never queue the
        # original — and trim odd dimensions for the same reason as above.
        return frame[:h & ~1, :w & ~1].copy()

    # ================= encoder thread =================
    def _encode_loop(self) -> None:
        while self._running:
            try:
                frame, t = self._queue.get(timeout=0.25)
            except Empty:
                self._idle.set()
                continue
            try:
                ok, buf = cv2.imencode(
                    ".jpg", frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
                if ok:
                    self._append(buf.tobytes(), t)
            except Exception as exc:  # a bad frame must not kill the recorder
                self.last_error = f"review buffer encode failed: {exc}"
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    def _append(self, data: bytes, t: float) -> None:
        with self._lock:
            seg = self._ensure_segment(t)
            if seg is None:
                return
            try:
                offset = self._cur_fh.tell()
                self._cur_fh.write(data)
                # Flush per frame so a snapshot taken right now can read it.
                self._cur_fh.flush()
            except OSError as exc:
                self.last_error = f"review buffer write failed: {exc}"
                self._close_current()
                return
            self._index.append(FrameRef(seg.seg_id, offset, len(data), t))
            seg.frames += 1
            seg.nbytes += len(data)
            self._indexed_bytes += len(data)
            self._disk_bytes += len(data)
            self._evict_locked(t)

    def _ensure_segment(self, t: float) -> Optional[_Segment]:
        if self._cur is not None and (t - self._cur_start_t) < _SEGMENT_SECONDS:
            return self._cur
        self._close_current()
        seg_id = self._next_seg
        self._next_seg += 1
        path = self.dir / f"seg_{seg_id:06d}{_SUFFIX}"
        try:
            self._cur_fh = open(path, "wb")
        except OSError as exc:
            self.last_error = f"cannot open review segment: {exc}"
            self._cur_fh = None
            return None
        self._cur = _Segment(seg_id, path)
        self._cur_start_t = t
        self._segments[seg_id] = self._cur
        return self._cur

    def _close_current(self) -> None:
        if self._cur_fh is not None:
            try:
                self._cur_fh.close()
            except OSError:
                pass
        self._cur_fh = None
        self._cur = None

    # ================= retention =================
    def _evict_locked(self, now: float) -> None:
        """Drop index entries past the time or size budget, then free files.

        The size budget is checked against the *indexed* bytes; actual disk use
        trails it by at most the tail segment still holding evicted frames.
        """
        while self._index and (
                (now - self._index[0].t) > self.retention_s
                or self._indexed_bytes > self.max_bytes):
            ref = self._index.popleft()
            self._indexed_bytes -= ref.length
            seg = self._segments.get(ref.seg)
            if seg is not None:
                seg.frames -= 1
        self._sweep_locked()

    def _sweep_locked(self) -> None:
        """Delete segment files no index entry and no snapshot still needs."""
        pinned: set = set()
        for snap in self._snapshots:
            pinned |= snap.seg_ids
        doomed = [s for s in self._doomed]
        self._doomed.clear()
        for seg in list(self._segments.values()):
            if seg.frames <= 0 and seg is not self._cur and seg.seg_id not in pinned:
                del self._segments[seg.seg_id]
                doomed.append(seg)
        for seg in doomed:
            try:
                seg.path.unlink(missing_ok=True)
            except OSError:
                # Windows can refuse while a handle lingers; retry next sweep.
                self._doomed.append(seg)
                continue
            self._disk_bytes -= seg.nbytes

    # ================= readers =================
    def snapshot(self) -> BufferSnapshot:
        """Freeze the current frame list into a pinned, readable view."""
        with self._lock:
            frames = list(self._index)
            paths = {sid: seg.path for sid, seg in self._segments.items()}
            return BufferSnapshot(frames, paths, self.fps, self.wall_offset,
                                  owner=self)

    def _attach(self, snap: BufferSnapshot) -> None:
        with self._lock:
            self._snapshots.append(snap)

    def _detach(self, snap: BufferSnapshot) -> None:
        with self._lock:
            if snap in self._snapshots:
                self._snapshots.remove(snap)
            self._sweep_locked()

    def drain(self, timeout: float = 5.0) -> bool:
        """Block until every queued frame has been written. Test/UI helper —
        the review screen calls it so a click captures the newest frames."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.empty() and self._idle.is_set():
                return True
            time.sleep(0.01)
        return self._queue.empty()

    def stats(self) -> dict:
        with self._lock:
            frames = len(self._index)
            span = (self._index[-1].t - self._index[0].t) if frames > 1 else 0.0
            return {
                "frames": frames,
                "seconds": span,
                "indexed_bytes": self._indexed_bytes,
                "disk_bytes": self._disk_bytes,
                "segments": len(self._segments),
                "dropped": self.dropped,
                "running": self._running,
            }


# ================= export =================

def export(snap: BufferSnapshot, path: str | Path,
           fps: Optional[float] = None,
           progress: Optional[Callable[[int, int], None]] = None,
           cancel: Optional[threading.Event] = None,
           start: int = 0, end: Optional[int] = None) -> Path:
    """Write a snapshot — or a chosen span of it — as a playable video file.

    `start`/`end` are inclusive frame indices; omitted, the whole buffer is
    written. Both are clamped to the snapshot, and a reversed pair is put back
    in order, so a caller cannot ask for an empty or backwards range.

    Tries mp4v/.mp4 and falls back to MJPG/.avi, since which codecs an OpenCV
    build can actually open varies per machine. The written file uses the
    buffer's nominal fps, so a stretch where the capture source stalled plays
    back without its real-time gap. Returns the path actually written (the
    suffix may differ from `path` if the fallback was used).
    """
    frames = len(snap)
    if frames == 0:
        raise ValueError("nothing captured yet — the review buffer is empty")
    last = frames - 1
    first_i = min(max(int(start), 0), last)
    last_i = last if end is None else min(max(int(end), 0), last)
    if last_i < first_i:
        first_i, last_i = last_i, first_i
    total = last_i - first_i + 1
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = float(fps or snap.fps or DEFAULT_FPS)

    first = snap.frame(first_i)
    if first is None:
        raise ValueError("the buffered frames could not be read back")
    h, w = first.shape[:2]

    writer = None
    for fourcc, suffix in (("mp4v", ".mp4"), ("MJPG", ".avi")):
        candidate = out.with_suffix(suffix)
        w_try = cv2.VideoWriter(str(candidate),
                                cv2.VideoWriter_fourcc(*fourcc), rate, (w, h))
        if w_try.isOpened():
            writer, out = w_try, candidate
            break
        w_try.release()
    if writer is None:
        raise RuntimeError("no usable video encoder (tried mp4v and MJPG)")

    try:
        for offset, i in enumerate(range(first_i, last_i + 1)):
            if cancel is not None and cancel.is_set():
                raise ExportCancelled()
            img = snap.frame(i)
            if img is None:
                continue
            if img.shape[:2] != (h, w):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
            writer.write(img)
            if progress is not None and (offset % 15 == 0 or offset == total - 1):
                progress(offset + 1, total)
    except ExportCancelled:
        writer.release()
        writer = None
        out.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.release()
    return out
