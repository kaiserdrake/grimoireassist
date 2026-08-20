"""Unit tests for voice selection and the online→offline fallback.

No COM, no network, no audio: the backends are fakes that record what they were
asked to say and can be told to fail on demand.
"""
import time

from grimoireassist.speech import (
    Speaker, _rank, is_wanted_lcid, is_wanted_locale, pick_voice,
)

DAVID = "Microsoft David Desktop - English (United States)"
ZIRA = "Microsoft Zira Desktop - English (United States)"
HELENA = "Microsoft Helena Desktop - Spanish (Spain)"


# ---------------------------------------------------------------- locale filter
def test_only_us_and_uk_online_voices_are_offered():
    assert is_wanted_locale("en-US") and is_wanted_locale("en-GB")
    for other in ("en-AU", "en-CA", "en-IN", "en-IE", "en-NZ", "fr-FR", ""):
        assert not is_wanted_locale(other), other


def test_sapi_lcids_for_us_and_uk_are_accepted():
    assert is_wanted_lcid("409")      # en-US
    assert is_wanted_lcid("809")      # en-GB
    assert is_wanted_lcid("0409")     # zero-padded
    assert is_wanted_lcid("c09;809")  # multi-locale voice including en-GB


def test_other_sapi_lcids_are_rejected():
    for other in ("c09", "1009", "40c", "407", "", None):
        assert not is_wanted_lcid(other), other


# ------------------------------------------------- offline list ↔ token mapping
class _Token:
    def __init__(self, description, language):
        self._description, self._language = description, language

    def GetDescription(self):
        return self._description

    def GetAttribute(self, name):
        if name == "Language":
            return self._language
        raise RuntimeError(f"no attribute {name}")


class _Tokens:
    def __init__(self, tokens):
        self._tokens = tokens
        self.Count = len(tokens)

    def Item(self, i):
        return self._tokens[i]


def _collect_from(tokens):
    """Run SapiBackend._collect against fake tokens (no COM)."""
    from grimoireassist.speech import SapiBackend
    obj = SapiBackend.__new__(SapiBackend)
    obj._tokens = _Tokens(tokens)
    obj._offsets = []
    return SapiBackend._collect(obj), obj._offsets


def test_offline_voices_outside_us_and_uk_are_dropped():
    found, _offsets = _collect_from([
        _Token("Hedda - German (Germany)", "407"),
        _Token(DAVID, "409"),
        _Token("Hazel - English (United Kingdom)", "809"),
    ])
    assert found == [DAVID, "Hazel - English (United Kingdom)"]


def test_the_kept_voices_still_point_at_the_right_tokens():
    """Filtering must not desync the list from the tokens behind it."""
    _found, offsets = _collect_from([
        _Token("Hedda - German (Germany)", "407"),
        _Token(DAVID, "409"),
        _Token("Haruka - Japanese (Japan)", "411"),
        _Token("Hazel - English (United Kingdom)", "809"),
    ])
    assert offsets == [1, 3]        # skips the German and Japanese tokens


def test_a_machine_with_no_us_or_uk_voice_still_speaks():
    """Better a wrong accent than silence."""
    found, offsets = _collect_from([
        _Token("Hedda - German (Germany)", "407"),
        _Token("Haruka - Japanese (Japan)", "411"),
    ])
    assert found == ["Hedda - German (Germany)", "Haruka - Japanese (Japan)"]
    assert offsets == [0, 1]


def test_a_voice_whose_language_attribute_is_unreadable_is_skipped():
    class _Broken(_Token):
        def GetAttribute(self, name):
            raise RuntimeError("attribute unavailable")

    found, _offsets = _collect_from([_Broken("Mystery voice", ""), _Token(DAVID, "409")])
    assert found == [DAVID]


# ---------------------------------------------------------------- voice pick
def test_no_voices_installed():
    assert pick_voice([]) is None


def test_preferred_substring_wins_over_the_default_ranking():
    assert pick_voice([DAVID, ZIRA], "zira") == 1
    assert pick_voice([DAVID, ZIRA], "ZIRA") == 1


def test_unmatched_preference_falls_back_to_the_ranking():
    assert pick_voice([DAVID, ZIRA], "sonia") == 0


def test_named_preference_beats_a_plain_english_voice():
    assert pick_voice([ZIRA, DAVID]) == 1          # David is in VOICE_PREFERENCE
    assert _rank(DAVID) < _rank(ZIRA)


def test_english_beats_a_non_english_voice():
    assert pick_voice([HELENA, ZIRA]) == 1
    assert _rank(ZIRA) < _rank(HELENA)


# ---------------------------------------------------------------- fallback
class _FakeBackend:
    """Stands in for a real synthesiser; `fails` makes speak() raise."""

    name = "?"
    label = "?"
    fails = False
    instances: list = []

    def __init__(self):
        self.spoken = []
        type(self).instances.append(self)

    @classmethod
    def available(cls):
        return True

    def voices(self):
        return [f"{self.name}-voice"]

    def configure(self, voice, rate, volume):
        self.settings = (voice, rate, volume)

    def prepare(self, text):
        return None

    def discard(self, prepared):
        pass

    def speak(self, text, keep_going, prepared=None):
        if type(self).fails:
            raise RuntimeError("network down")
        self.spoken.append(text)

    def close(self):
        pass


class _FakeEdge(_FakeBackend):
    name, label, instances = "edge", "Online neural voice", []


class _FakeSapi(_FakeBackend):
    name, label, instances = "sapi", "Offline Windows voice", []


def _speaker(**kw):
    _FakeEdge.instances, _FakeSapi.instances = [], []
    _FakeEdge.fails = _FakeSapi.fails = False
    switches = []
    spk = Speaker(factories={"edge": _FakeEdge, "sapi": _FakeSapi},
                  on_backend_changed=lambda n, r: switches.append((n, r)), **kw)
    spk.switches = switches
    return spk


def _wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_auto_mode_uses_the_online_voice_when_it_works():
    spk = _speaker(backend="auto")
    try:
        spk.say("Careful, that one bites")
        assert _wait_for(lambda: _FakeEdge.instances and _FakeEdge.instances[0].spoken)
        assert _FakeEdge.instances[0].spoken == ["Careful, that one bites"]
        assert not _FakeSapi.instances
        assert spk.current_backend() == "edge"
    finally:
        spk.stop()


def test_online_failure_speaks_the_same_line_offline():
    """A dropped connection must not cost the line."""
    spk = _speaker(backend="auto")
    try:
        _FakeEdge.fails = True
        spk.say("The village is this way")
        assert _wait_for(lambda: _FakeSapi.instances and _FakeSapi.instances[0].spoken)
        assert _FakeSapi.instances[0].spoken == ["The village is this way"]
        assert spk.current_backend() == "sapi"
        assert any(n == "sapi" for n, _r in spk.switches)
    finally:
        spk.stop()


def test_it_stays_offline_until_the_retry_window_passes():
    spk = _speaker(backend="auto", edge_retry_s=600.0)
    try:
        _FakeEdge.fails = True
        spk.say("first")
        assert _wait_for(lambda: _FakeSapi.instances and _FakeSapi.instances[0].spoken)
        _FakeEdge.fails = False           # network is back, but the window isn't up
        spk.say("second")
        assert _wait_for(lambda: len(_FakeSapi.instances[0].spoken) == 2)
        assert _FakeSapi.instances[0].spoken == ["first", "second"]
    finally:
        spk.stop()


def test_the_online_voice_returns_once_the_window_expires():
    spk = _speaker(backend="auto", edge_retry_s=0.05)
    try:
        _FakeEdge.fails = True
        spk.say("first")
        assert _wait_for(lambda: _FakeSapi.instances and _FakeSapi.instances[0].spoken)
        _FakeEdge.fails = False
        time.sleep(0.1)                   # let the retry window lapse
        spk.say("second")
        assert _wait_for(lambda: "second" in _FakeEdge.instances[0].spoken)
    finally:
        spk.stop()


def test_pinned_offline_never_touches_the_online_backend():
    spk = _speaker(backend="sapi")
    try:
        spk.say("stay local")
        assert _wait_for(lambda: _FakeSapi.instances and _FakeSapi.instances[0].spoken)
        assert not _FakeEdge.instances
    finally:
        spk.stop()


def test_pinned_online_does_not_fall_back():
    """Explicitly choosing the online voice should report failure, not switch."""
    errors = []
    _FakeEdge.instances, _FakeSapi.instances = [], []
    _FakeEdge.fails = True
    spk = Speaker(backend="edge", factories={"edge": _FakeEdge, "sapi": _FakeSapi},
                  on_error=errors.append)
    try:
        spk.say("online only")
        assert _wait_for(lambda: errors)
        assert not _FakeSapi.instances
    finally:
        spk.stop()
        _FakeEdge.fails = False


def test_the_queue_drops_the_oldest_line_when_speech_falls_behind():
    """What's on screen now beats what already scrolled away."""
    class _SlowSapi(_FakeSapi):
        instances: list = []

        def speak(self, text, keep_going, prepared=None):
            time.sleep(0.05)      # speech can't keep up with the lines arriving
            self.spoken.append(text)

    _SlowSapi.instances = []
    spk = Speaker(backend="sapi", max_queue=2,
                  factories={"edge": _FakeEdge, "sapi": _SlowSapi})
    try:
        for line in ("one", "two", "three", "four", "five", "six"):
            spk.say(line)
        assert _wait_for(lambda: _SlowSapi.instances
                         and "six" in _SlowSapi.instances[0].spoken)
        spoken = _SlowSapi.instances[0].spoken
        assert len(spoken) < 6            # the backlog was trimmed, not queued up
        assert spoken[-1] == "six"        # and the newest line survived
    finally:
        spk.stop()


def test_voices_come_from_the_resolved_backend():
    spk = _speaker(backend="sapi")
    try:
        assert spk.resolved_backend() == "sapi"
        assert spk.voices() == ["sapi-voice"]
    finally:
        spk.stop()
