"""voice.on_conversation_start / voice.on_conversation_end — shell hooks around a
wake-word conversation.

The start hook runs before the recording bell so a player can be paused first; the
end hook runs once, when the conversation closes (watchdog resume or listener stop).
Both default to "" = upstream behaviour, no subprocess at all.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from tests.hermes_cli.test_voice_wake_follow_up import _make_cli


@pytest.fixture(autouse=True)
def _quiet_io(monkeypatch):
    monkeypatch.setattr("cli._cprint", lambda *_a, **_kw: None)


def _with_hooks(monkeypatch, start="", end=""):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"voice": {"on_conversation_start": start, "on_conversation_end": end}})


class _Recorder:
    """Stands in for subprocess.run: records the command line, runs nothing."""

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, **_kw):
        self.cmds.append(cmd)
        return SimpleNamespace(returncode=0)


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _woken_cli(monkeypatch, **overrides):
    """A CLI that opens a wake conversation for real, with the mic capture faked."""
    cli = _make_cli(_voice_mode=False, _wake_start_new_session=False, **overrides)
    monkeypatch.setattr("tools.wake_word.pause_listening", lambda *_a, **_kw: True)
    monkeypatch.setattr("tools.wake_word.get_last_match", lambda: None)
    events = []
    cli._voice_start_recording = lambda **_kw: events.append("record")
    return cli, events


def _resume_via_watchdog(monkeypatch, cli, resumed):
    """Drive one idle round of the watchdog loop on a fake clock."""
    monkeypatch.setattr(
        "tools.wake_word.resume_listening", lambda *_a, **_kw: resumed.set() or True)
    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.time",
        SimpleNamespace(sleep=lambda _s: time.sleep(0.001)))
    cli._start_wake_watchdog()
    assert resumed.wait(5), "watchdog never resumed the listener"


# ── Config ────────────────────────────────────────────────────────────────


def test_hook_timeouts_are_the_production_bounds():
    """The hangs tests monkeypatch these; the shipped deadlines stay pinned."""
    from hermes_cli import cli_voice_mixin as mixin

    assert mixin._CONVERSATION_START_TIMEOUT == 2.0
    assert mixin._CONVERSATION_END_TIMEOUT == 10.0


def test_hooks_default_off_everywhere():
    from hermes_cli.config import DEFAULT_CONFIG

    voice = DEFAULT_CONFIG["voice"]
    assert voice["on_conversation_start"] == ""
    assert voice["on_conversation_end"] == ""


# ── Start hook ────────────────────────────────────────────────────────────


def test_start_hook_runs_before_the_recording_bell(monkeypatch):
    _with_hooks(monkeypatch, start="music hold")
    cli, events = _woken_cli(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.subprocess.run",
        lambda cmd, **_kw: events.append(f"hook:{cmd}") or SimpleNamespace(returncode=0))

    cli._on_wake_word()

    assert events == ["hook:music hold", "record"]


def test_disabled_hooks_start_no_process(monkeypatch):
    _with_hooks(monkeypatch)
    cli, events = _woken_cli(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.subprocess.run",
        lambda *_a, **_kw: pytest.fail("subprocess started with both hooks empty"))

    cli._on_wake_word()

    assert events == ["record"]
    assert cli._wake_conv_hook_open is False


def test_profile_hint_early_return_fires_no_hook(monkeypatch):
    _with_hooks(monkeypatch, start="music hold", end="music release")
    cli, events = _woken_cli(monkeypatch)
    monkeypatch.setattr("tools.wake_word.get_last_match", lambda: ("hey hermes", "work"))
    monkeypatch.setattr("tools.wake_word._active_profile_name", lambda: "default")
    run = _Recorder()
    monkeypatch.setattr("hermes_cli.cli_voice_mixin.subprocess.run", run)

    cli._on_wake_word()

    assert run.cmds == []
    assert events == []  # no recording either — the conversation never opened


def test_hanging_start_hook_is_bounded(monkeypatch):
    _with_hooks(monkeypatch, start="sleep 5", end="music release")
    cli, events = _woken_cli(monkeypatch)
    monkeypatch.setattr("hermes_cli.cli_voice_mixin._CONVERSATION_START_TIMEOUT", 0.05)

    started = time.monotonic()
    cli._on_wake_word()

    assert time.monotonic() - started < 2.0, "the hook held the conversation up"
    assert events == ["record"]
    assert cli._wake_conv_hook_open is True  # a half-run hold must still be released


def test_failing_start_hook_does_not_break_the_conversation(monkeypatch):
    _with_hooks(monkeypatch, start="music hold")
    cli, events = _woken_cli(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.subprocess.run",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no shell")))

    cli._on_wake_word()

    assert events == ["record"]


# ── End hook ──────────────────────────────────────────────────────────────


def test_end_hook_fires_once_after_the_watchdog_resumes(monkeypatch):
    _with_hooks(monkeypatch, start="music hold", end="music release")
    cli, _events = _woken_cli(monkeypatch)
    run = _Recorder()
    monkeypatch.setattr("hermes_cli.cli_voice_mixin.subprocess.run", run)
    cli._on_wake_word()

    cli._wake_word_active = True
    cli._wake_suspended = True
    cli._wake_watchdog_started = False
    resumed = threading.Event()
    try:
        _resume_via_watchdog(monkeypatch, cli, resumed)
        assert _wait(lambda: len(run.cmds) == 2), run.cmds
        time.sleep(0.05)
        assert run.cmds == ["music hold", "music release"]
    finally:
        cli._wake_word_active = False


def test_stop_listener_closes_an_open_conversation_once(monkeypatch):
    _with_hooks(monkeypatch, start="music hold", end="music release")
    cli, _events = _woken_cli(monkeypatch)
    run = _Recorder()
    monkeypatch.setattr("hermes_cli.cli_voice_mixin.subprocess.run", run)
    monkeypatch.setattr("tools.wake_word.stop_listening", lambda *_a, **_kw: None)
    cli._wake_word_active = True
    cli._on_wake_word()

    cli._stop_wake_word_listener()
    assert _wait(lambda: len(run.cmds) == 2), run.cmds
    cli._stop_wake_word_listener()
    time.sleep(0.05)

    assert run.cmds == ["music hold", "music release"]


def test_end_hook_does_not_fire_without_a_start_hook(monkeypatch):
    _with_hooks(monkeypatch, end="music release")
    cli, _events = _woken_cli(monkeypatch)
    run = _Recorder()
    monkeypatch.setattr("hermes_cli.cli_voice_mixin.subprocess.run", run)
    monkeypatch.setattr("tools.wake_word.stop_listening", lambda *_a, **_kw: None)

    cli._on_wake_word()
    cli._stop_wake_word_listener()
    time.sleep(0.05)

    assert run.cmds == []


def test_end_hook_is_quiet_for_a_stop_phrase_conversation(monkeypatch):
    """The stop phrase ends the conversation; the hook waits for the listener to come back."""
    _with_hooks(monkeypatch, start="music hold", end="music release")
    cli, _events = _woken_cli(monkeypatch, _voice_recording=True, _wake_conversation=True)
    run = _Recorder()
    monkeypatch.setattr("hermes_cli.cli_voice_mixin.subprocess.run", run)
    recorder = SimpleNamespace(stop=lambda: "/tmp/conv.wav")
    cli._voice_recorder = recorder
    cli._voice_beep = lambda **_kw: None
    monkeypatch.setattr(
        "tools.voice_mode.transcribe_recording",
        lambda *_a, **_kw: {"success": True, "transcript": "stop"})
    monkeypatch.setattr("tools.voice_mode_transcript.is_voice_stop_phrase", lambda _t: True)
    cli._wake_conv_hook_open = True  # the start hook opened this conversation

    cli._voice_stop_and_transcribe()

    assert cli._wake_conversation is False
    assert run.cmds == []  # still playing out the reply / follow-up window
    assert cli._wake_conv_hook_open is True


def test_end_hook_never_blocks_the_caller(monkeypatch):
    _with_hooks(monkeypatch, start="music hold", end="sleep 5")
    cli, _events = _woken_cli(monkeypatch)
    cli._wake_conv_hook_open = True

    started = time.monotonic()
    cli._close_wake_conversation_hook()

    assert time.monotonic() - started < 0.5, "the end hook ran on the caller's thread"
    assert cli._wake_conv_hook_open is False


# ── Every way a conversation can end releases it ──────────────────────────


def _watchdog_cli(monkeypatch):
    """A CLI with a conversation open and a watchdog that hasn't started yet."""
    _with_hooks(monkeypatch, start="music hold", end="music release")
    cli, _events = _woken_cli(monkeypatch)
    run = _Recorder()
    monkeypatch.setattr("hermes_cli.cli_voice_mixin.subprocess.run", run)
    cli._on_wake_word()
    cli._wake_word_active = True
    cli._wake_suspended = True
    cli._wake_watchdog_started = False
    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.time",
        SimpleNamespace(sleep=lambda _s: time.sleep(0.001)))
    return cli, run


def test_failed_resume_still_releases_the_conversation(monkeypatch):
    cli, run = _watchdog_cli(monkeypatch)
    monkeypatch.setattr("tools.wake_word.resume_listening", lambda *_a, **_kw: False)
    try:
        cli._start_wake_watchdog()
        assert _wait(lambda: len(run.cmds) == 2), run.cmds
        assert cli._wake_word_active is False, "the watchdog should give up"
        time.sleep(0.05)
        assert run.cmds == ["music hold", "music release"]
    finally:
        cli._wake_word_active = False


def test_watchdog_waits_for_tts_before_resuming_and_releasing(monkeypatch):
    cli, run = _watchdog_cli(monkeypatch)
    cli._voice_tts_done.clear()  # the reply is still playing
    resumed = threading.Event()
    monkeypatch.setattr(
        "tools.wake_word.resume_listening", lambda *_a, **_kw: resumed.set() or True)
    try:
        cli._start_wake_watchdog()
        time.sleep(0.3)  # ~100 watchdog rounds: plenty to resume if TTS were ignored
        assert not resumed.is_set(), "mic re-armed over the assistant's own voice"
        assert run.cmds == ["music hold"]

        cli._voice_tts_done.set()
        assert resumed.wait(5), "listener never resumed after TTS"
        assert _wait(lambda: len(run.cmds) == 2), run.cmds
        assert run.cmds == ["music hold", "music release"]
    finally:
        cli._wake_word_active = False


def test_cli_exit_releases_the_conversation_synchronously(monkeypatch):
    """Process exit kills daemon threads, so shutdown must run the hook inline."""
    import cli as cli_module

    from hermes_cli.cli_shutdown import _stop_cli_wake_word

    _with_hooks(monkeypatch, start="music hold", end="music release")
    cli, _events = _woken_cli(monkeypatch)
    ran = []
    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.subprocess.run",
        lambda cmd, **_kw: ran.append((cmd, threading.current_thread().name)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr("tools.wake_word.stop_listening", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli_module, "_cli_wake_owner", cli)
    cli._on_wake_word()

    try:
        _stop_cli_wake_word()
        # Both inline on the shutdown thread: the hold from the start, the release from exit.
        assert ran == [("music hold", "MainThread"), ("music release", "MainThread")]
        assert cli._wake_conv_hook_open is False
    finally:
        monkeypatch.setattr(cli_module, "_cli_wake_owner", None)

    ran.clear()
    _stop_cli_wake_word()  # no conversation open → nothing to release
    assert ran == []
