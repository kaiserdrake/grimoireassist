"""Text-to-speech for the dialogue region: online neural voice, offline fallback.

Two backends sit behind one small interface:

* `EdgeNeuralBackend` — the "Online Natural" voices (en-GB-RyanNeural and friends)
  via the `edge-tts` package. This is the default because it is the only thing
  available that sounds like an assistant rather than a 2010 screen reader. It
  needs the network, and it sends each dialogue line to a Microsoft endpoint to
  be synthesised.
* `SapiBackend` — `SAPI.SpVoice` through `comtypes` (already a dependency via
  pygrabber). Fully offline, no download, and limited to whatever voices are
  installed under Windows Speech settings.

In the default "auto" mode `Speaker` prefers the online backend and falls back to
the offline one the moment synthesis fails — speaking the *same* statement rather
than dropping it — then re-probes the network every `edge_retry_s` so the good
voice comes back on its own.

Threading: all synthesis and playback happens on the speaker's own worker thread,
never the Qt thread. `SpVoice` is apartment-threaded, so the COM object is created
and used only there; MCI playback likewise opens and closes on one thread. The one
exception is `prepare()`, which runs on a single-worker pool so the *next*
statement can be synthesised while the current one is still playing — without it,
every online line would stall for a network round-trip before making a sound.
"""
from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Deque, List, Optional

# SpVoice.Speak flags
_ASYNC = 1
_PURGE = 2

# Preferred offline voices for the calm-assistant read this feature is after,
# best first. These are the male English SAPI voices that ship with (or are
# commonly installed on) Windows.
VOICE_PREFERENCE = ("david", "guy", "ryan", "mark", "george", "james", "richard")

# Only US and UK English are offered. Other locales read game dialogue with the
# wrong vowels and pacing, and the full online list runs to ~50 entries, most of
# which nobody here wants to scroll past.
ONLINE_LOCALES = ("en-US", "en-GB")
# SAPI reports locale as a hex LCID rather than a tag, and a voice may list
# several, ";"-separated: 409 = en-US, 809 = en-GB.
OFFLINE_LCIDS = ("409", "809")

# Shown when the online voice list can't be fetched (no network yet). These are
# stable edge-tts short names; the real list replaces them once a fetch succeeds.
FALLBACK_ONLINE_VOICES = (
    "en-GB-RyanNeural", "en-GB-SoniaNeural", "en-GB-ThomasNeural",
    "en-GB-LibbyNeural", "en-US-GuyNeural", "en-US-AriaNeural",
    "en-US-ChristopherNeural", "en-US-EricNeural",
)


def is_wanted_locale(locale: str) -> bool:
    """Whether an edge-tts voice locale is one we offer."""
    return str(locale or "").strip() in ONLINE_LOCALES


def is_wanted_lcid(language: str) -> bool:
    """Whether a SAPI voice's Language attribute covers US or UK English."""
    codes = {c.strip().lstrip("0").lower() or "0"
             for c in str(language or "").split(";")}
    return any(c in codes for c in OFFLINE_LCIDS)


class SpeechBackend(ABC):
    """One way of turning text into sound."""

    name = "?"
    label = "?"

    @classmethod
    def available(cls) -> bool:
        """Whether this backend can be used on this machine at all."""
        return True

    def voices(self) -> List[str]:
        """Selectable voice names/descriptions, for the menu."""
        return []

    def configure(self, voice: str, rate: int, volume: int) -> None:
        """Apply settings. `rate` is -10..10, `volume` 0..100."""

    def prepare(self, text: str):
        """Optional off-thread work (synthesis) returning an opaque handle.

        Called on a worker pool while an earlier statement is still playing, so
        it must not touch anything `speak` is using."""
        return None

    def discard(self, prepared) -> None:
        """Throw away a `prepare` result that will never be spoken."""

    @abstractmethod
    def speak(self, text: str, keep_going: Callable[[], bool], prepared=None) -> None:
        """Speak `text`, returning early once `keep_going()` goes False."""

    def close(self) -> None:
        """Release any resources."""


# --------------------------------------------------------------------------
# Offline: Windows SAPI
# --------------------------------------------------------------------------
def _rank(description: str):
    """Sort key for offline auto voice selection — lower is more preferred."""
    low = (description or "").lower()
    for pos, name in enumerate(VOICE_PREFERENCE):
        if name in low:
            return (0, pos)
    # Only reached when the US/UK filter found nothing and every installed voice
    # is on offer, so still prefer anything that looks English.
    english = ("english" in low or "en-" in low or "en_" in low
               or "united states" in low or "united kingdom" in low)
    return (1 if english else 2, 99)


def pick_voice(descriptions: List[str], preferred: str = "") -> Optional[int]:
    """Index of the offline voice to use, or None when the system has none.

    `preferred` is matched as a case-insensitive substring of the voice
    description (so "david" or "Zira" both work); an empty or unmatched value
    falls back to the most assistant-like installed voice."""
    if not descriptions:
        return None
    want = (preferred or "").strip().lower()
    if want:
        for i, d in enumerate(descriptions):
            if want in (d or "").lower():
                return i
    return min(range(len(descriptions)), key=lambda i: _rank(descriptions[i]))


def _new_voice():
    """Create a SpVoice on the *calling* thread (COM-initialised for it first)."""
    import comtypes
    import comtypes.client
    try:
        comtypes.CoInitialize()
    except Exception:
        pass  # already initialised on this thread — fine
    return comtypes.client.CreateObject("SAPI.SpVoice")


class SapiBackend(SpeechBackend):
    """Offline Windows voices. Created lazily on the thread that speaks."""

    name = "sapi"
    label = "Offline Windows voice"

    def __init__(self) -> None:
        self._voice = _new_voice()
        self._offsets: List[int] = []   # position in _descriptions -> token index
        try:
            self._tokens = self._voice.GetVoices()
            self._descriptions = self._collect()
        except Exception:
            self._tokens, self._descriptions = None, []
        self._chosen: Optional[str] = None

    def _collect(self) -> List[str]:
        """US/UK English voices, remembering which token each one came from.

        Falls back to every installed voice if the filter leaves nothing — a
        machine with only a non-English voice should still speak rather than
        fall silent."""
        every, wanted = [], []
        for i in range(self._tokens.Count):
            token = self._tokens.Item(i)
            description = token.GetDescription()
            every.append((description, i))
            try:
                language = token.GetAttribute("Language")
            except Exception:
                language = ""
            if is_wanted_lcid(language):
                wanted.append((description, i))
        chosen = wanted or every
        self._offsets = [i for _d, i in chosen]
        return [d for d, _i in chosen]

    @classmethod
    def available(cls) -> bool:
        try:
            import comtypes  # noqa: F401
            return True
        except Exception:
            return False

    def voices(self) -> List[str]:
        return list(self._descriptions)

    def configure(self, voice: str, rate: int, volume: int) -> None:
        if self._tokens is not None and voice != self._chosen:
            idx = pick_voice(self._descriptions, voice)
            if idx is not None:
                # _descriptions is filtered, so map back to the token it came from
                self._voice.Voice = self._tokens.Item(self._offsets[idx])
            self._chosen = voice
        self._voice.Rate = int(rate)
        self._voice.Volume = int(volume)

    def speak(self, text: str, keep_going: Callable[[], bool], prepared=None) -> None:
        self._voice.Speak(text, _ASYNC)
        while keep_going():
            if self._voice.WaitUntilDone(120):
                return
        self._voice.Speak("", _ASYNC | _PURGE)

    def close(self) -> None:
        try:
            self._voice.Speak("", _ASYNC | _PURGE)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Online: Edge neural voices
# --------------------------------------------------------------------------
def _mci(command: str) -> str:
    """Send one MCI command string, raising on error.

    Playback goes through winmm rather than QtMultimedia because this runs on a
    plain worker thread, where a QObject-based player would have the wrong
    thread affinity."""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.WinDLL("winmm").mciSendStringW(command, buf, 255, None)
    if err:
        raise RuntimeError(f"MCI error {err} for {command!r}")
    return buf.value


class EdgeNeuralBackend(SpeechBackend):
    """Microsoft's online neural voices, synthesised to MP3 and played locally."""

    name = "edge"
    label = "Online neural voice"

    _TIMEOUT_S = 8.0   # a dead network must degrade in seconds, not minutes

    def __init__(self, cache_dir=None) -> None:
        import edge_tts  # noqa: F401  — fail here if the package is missing
        from pathlib import Path
        self._dir_hint = Path(cache_dir) if cache_dir else None
        self._dir = None            # resolved on first use, never here
        self._voice = "en-GB-RyanNeural"
        self._rate = 0
        self._volume = 90
        self._seq = 0
        self._cached_voices: List[str] = []

    @classmethod
    def available(cls) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def _clip_dir(self):
        """Directory for synthesised clips, resolved on first use.

        Deliberately not resolved in __init__: a portable copy unzipped
        somewhere read-only (Program Files, a network share, a mounted image)
        would otherwise raise there and take the whole online voice down before
        a single word was spoken — leaving only the offline voices on offer.
        The system temp directory is the fallback."""
        if self._dir is not None:
            return self._dir
        import tempfile
        from pathlib import Path
        from . import app_root
        candidates = [self._dir_hint] if self._dir_hint else [
            app_root() / "cache" / "tts"]
        candidates.append(Path(tempfile.gettempdir()) / "grimoireassist-tts")
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write-probe"
                probe.write_bytes(b"")
                probe.unlink()
            except Exception:
                continue
            self._dir = candidate
            self._sweep()
            return candidate
        raise RuntimeError("no writable directory for speech clips")

    def _sweep(self) -> None:
        """Drop clips left behind by a previous run (a crash mid-playback)."""
        if self._dir is None:
            return
        for stale in self._dir.glob("*.mp3"):
            try:
                stale.unlink()
            except OSError:
                pass

    def voices(self) -> List[str]:
        """Online voice short names. Network-backed, so cached after one fetch."""
        if self._cached_voices:
            return list(self._cached_voices)
        try:
            import asyncio
            import edge_tts
            found = asyncio.run(
                asyncio.wait_for(edge_tts.list_voices(), timeout=self._TIMEOUT_S))
            names = sorted(v["ShortName"] for v in found
                           if is_wanted_locale(v.get("Locale")))
            if names:
                self._cached_voices = names
        except Exception:
            return list(FALLBACK_ONLINE_VOICES)
        return list(self._cached_voices) or list(FALLBACK_ONLINE_VOICES)

    def configure(self, voice: str, rate: int, volume: int) -> None:
        self._voice = voice or "en-GB-RyanNeural"
        self._rate = int(rate)
        self._volume = int(volume)

    def prepare(self, text: str):
        """Synthesise to an MP3 and return its path. Raises if the network is down."""
        import asyncio
        import edge_tts
        self._seq += 1
        path = self._clip_dir() / f"line{self._seq:04d}.mp3"
        # SSML-free percentage form; -10..10 maps to a +/-50% spread.
        communicate = edge_tts.Communicate(text, self._voice,
                                           rate=f"{self._rate * 5:+d}%")

        async def _synth():
            await asyncio.wait_for(communicate.save(str(path)), timeout=self._TIMEOUT_S)

        asyncio.run(_synth())
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError("empty synthesis result")
        return path

    def discard(self, prepared) -> None:
        if prepared:
            try:
                os.unlink(prepared)
            except OSError:
                pass

    def speak(self, text: str, keep_going: Callable[[], bool], prepared=None) -> None:
        path = prepared or self.prepare(text)
        alias = f"ga_tts_{os.getpid()}_{self._seq}"
        try:
            _mci(f'open "{path}" type mpegvideo alias {alias}')
        except Exception:
            self.discard(path)
            raise
        try:
            try:  # MCI volume is 0..1000; not every device honours it
                _mci(f"setaudio {alias} volume to {max(0, min(self._volume, 100)) * 10}")
            except Exception:
                pass
            _mci(f"play {alias}")
            while keep_going():
                if _mci(f"status {alias} mode").strip() != "playing":
                    break
                time.sleep(0.08)
        finally:
            try:
                _mci(f"close {alias}")
            except Exception:
                pass
            self.discard(path)

    def close(self) -> None:
        self._sweep()


BACKENDS = {SapiBackend.name: SapiBackend, EdgeNeuralBackend.name: EdgeNeuralBackend}


# --------------------------------------------------------------------------
# Speaker
# --------------------------------------------------------------------------
class Speaker:
    """Queued text-to-speech with online-first backend selection.

    `say` never blocks the caller: statements land in a small buffer that drops
    the *oldest* entry when speech falls behind, because during a busy scene the
    line still on screen matters more than one that has already scrolled away.
    """

    def __init__(self, backend: str = "auto", voice_online: str = "",
                 voice_offline: str = "", rate: int = 0, volume: int = 90,
                 max_queue: int = 3, edge_retry_s: float = 60.0,
                 on_error: Optional[Callable[[str], None]] = None,
                 on_backend_changed: Optional[Callable[[str, str], None]] = None,
                 factories: Optional[dict] = None) -> None:
        self.on_error = on_error
        self.on_backend_changed = on_backend_changed
        self.edge_retry_s = edge_retry_s
        self._factories = factories or BACKENDS
        self._lock = threading.Lock()
        self._pending: Deque[str] = deque(maxlen=max(1, max_queue))
        self._settings = {"backend": backend, "voice_online": voice_online,
                          "voice_offline": voice_offline, "rate": rate,
                          "volume": volume}
        self._wake = threading.Event()
        self._interrupt = False
        self._running = True
        self._instances: dict = {}          # name -> backend | None (failed)
        self._applied: dict = {}            # name -> settings last pushed to it
        self._edge_down_until = 0.0
        self._active = ""                   # backend that last spoke
        self._prefetch: Optional[tuple] = None   # (backend_name, text, prepared)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-prep")
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    # ---- public API (any thread) --------------------------------------
    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text or not self._running:
            return
        with self._lock:
            self._pending.append(text)
        self._wake.set()

    def configure(self, **settings) -> None:
        """Apply backend / voice / rate / volume; effective from the next line."""
        with self._lock:
            self._settings.update(settings)
        self._wake.set()

    def flush(self) -> None:
        """Drop everything queued and cut the line currently being spoken."""
        with self._lock:
            self._pending.clear()
            self._interrupt = True
        self._wake.set()

    def stop(self, timeout: float = 3.0) -> None:
        self._running = False
        self.flush()
        self._thread.join(timeout=timeout)
        self._pool.shutdown(wait=False)

    def current_backend(self) -> str:
        """Name of the backend that last spoke ("" before the first statement)."""
        return self._active

    def resolved_backend(self) -> str:
        """Name of the backend the next statement would use."""
        return self._resolve_name(time.time())

    def voices(self, backend: Optional[str] = None) -> List[str]:
        """Voice list for a backend. May hit the network for the online one, so
        call it from a menu that is being opened, not at startup."""
        name = backend or self._resolve_name(time.time())
        inst = self._instance(name)
        return inst.voices() if inst else []

    # ---- backend resolution --------------------------------------------
    def _instance(self, name: str) -> Optional[SpeechBackend]:
        """Construct a backend once, caching both success and failure."""
        if name in self._instances:
            return self._instances[name]
        factory = self._factories.get(name)
        if factory is None:
            return None
        try:
            inst = factory()
        except Exception as exc:
            # Deliberately NOT cached. A construction failure is often transient
            # — a network hiccup, a directory not yet writable — and caching it
            # would pin the session to the offline voice forever, silently
            # defeating the edge_retry_s re-probe that exists to recover.
            if name == "sapi":  # the fallback itself failing is worth saying
                self._fail(f"Offline speech unavailable: {exc}")
            return None
        self._instances[name] = inst
        return inst

    def _resolve_name(self, now: float) -> str:
        mode = self._settings.get("backend", "auto")
        if mode in ("sapi", "edge"):
            return mode
        if now < self._edge_down_until:
            return "sapi"
        return "edge"

    def _resolve(self, now: float):
        """The backend to use, plus whether a fallback is still allowed."""
        mode = self._settings.get("backend", "auto")
        name = self._resolve_name(now)
        inst = self._instance(name)
        if inst is None and mode == "auto" and name == "edge":
            # edge-tts missing entirely: stop retrying it every line
            self._edge_down_until = now + self.edge_retry_s
            self._switch("sapi", "online voice unavailable")
            return self._instance("sapi"), False
        return inst, (mode == "auto" and name == "edge")

    def _switch(self, name: str, reason: str) -> None:
        if self._active != name:
            self._active = name
            if self.on_backend_changed:
                try:
                    self.on_backend_changed(name, reason)
                except Exception:
                    pass

    def _push_settings(self, backend: SpeechBackend, settings: dict) -> None:
        if self._applied.get(backend.name) == settings:
            return
        voice = (settings.get("voice_online") if backend.name == "edge"
                 else settings.get("voice_offline"))
        try:
            backend.configure(voice or "", settings.get("rate", 0),
                              settings.get("volume", 90))
            self._applied[backend.name] = dict(settings)
        except Exception as exc:
            self._fail(f"Voice settings rejected: {exc}")

    # ---- worker thread -------------------------------------------------
    def _run(self) -> None:
        while self._running:
            with self._lock:
                settings = dict(self._settings)
                self._interrupt = False
                text = self._pending.popleft() if self._pending else None
                upcoming = self._pending[0] if self._pending else None
            if text is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            now = time.time()
            backend, may_fall_back = self._resolve(now)
            if backend is None:
                continue
            self._push_settings(backend, settings)
            self._switch(backend.name, "selected")
            prepared = self._claim_prefetch(backend, text)
            future = (self._pool.submit(backend.prepare, upcoming)
                      if upcoming and prepared is not None else None)
            try:
                backend.speak(text, self._keep_going, prepared)
            except Exception as exc:
                self._on_speak_failed(backend, text, exc, may_fall_back, settings, now)
            self._collect_prefetch(backend, upcoming, future)
        self._shutdown()

    def _keep_going(self) -> bool:
        if not self._running:
            return False
        with self._lock:
            return not self._interrupt

    def _claim_prefetch(self, backend: SpeechBackend, text: str):
        """The pre-synthesised clip for `text`, or a fresh (inline) one.

        Returns None only when preparation failed, which the caller treats as
        "let speak() do it and surface the error"."""
        stashed, self._prefetch = self._prefetch, None
        if stashed is not None:
            name, stashed_text, prepared = stashed
            if name == backend.name and stashed_text == text:
                return prepared
            inst = self._instances.get(name)
            if inst is not None:
                inst.discard(prepared)
        try:
            return backend.prepare(text)
        except Exception:
            return None

    def _collect_prefetch(self, backend: SpeechBackend, upcoming, future) -> None:
        if future is None:
            return
        try:
            # Bounded by prepare()'s own timeout; a slow synthesis is dropped
            # rather than holding the thread past a stop/flush.
            self._prefetch = (backend.name, upcoming, future.result(timeout=5.0))
        except Exception:
            self._prefetch = None  # speak() will retry inline and report properly

    def _on_speak_failed(self, backend: SpeechBackend, text: str, exc: Exception,
                         may_fall_back: bool, settings: dict, now: float) -> None:
        """Online failure must not cost the line — say it offline instead."""
        if not may_fall_back or backend.name != "edge":
            self._fail(f"Speech failed: {exc}")
            return
        self._edge_down_until = now + self.edge_retry_s
        self._applied.pop("edge", None)
        fallback = self._instance("sapi")
        self._switch("sapi", "network unavailable — using the offline voice")
        if fallback is None:
            return
        self._push_settings(fallback, settings)
        try:
            fallback.speak(text, self._keep_going)
        except Exception as fallback_exc:
            self._fail(f"Speech failed: {fallback_exc}")

    def _shutdown(self) -> None:
        stashed, self._prefetch = self._prefetch, None
        if stashed is not None:
            inst = self._instances.get(stashed[0])
            if inst is not None:
                inst.discard(stashed[2])
        for inst in self._instances.values():
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass

    def _fail(self, message: str) -> None:
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                pass
