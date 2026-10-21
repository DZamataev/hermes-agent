"""wake_word.follow_up_seconds — keep listening after a wake-word reply.

A turn started by the wake word opens a short second window after TTS, so the
user can answer without saying the phrase again. Silence ends the conversation;
the listener comes back as before. 0 (default) keeps upstream behaviour.
"""

import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.i18n import t


def _make_cli(**overrides):
    """A HermesCLI with only the voice/session state these paths touch."""
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_lock = threading.Lock()
    cli._voice_mode = True
    cli._voice_tts = True
    cli._voice_recorder = None
    cli._voice_recording = False
    cli._voice_processing = False
    cli._voice_continuous = False
    cli._voice_tts_done = threading.Event()
    cli._voice_tts_done.set()
    cli._voice_tts_stop = None
    cli._voice_barge_capture = threading.Event()
    cli._voice_last_tts_text = ""
    cli._voice_barge_phase = None
    cli._pending_input = queue.Queue()
    cli._app = SimpleNamespace(invalidate=lambda: None)
    cli._attached_images = []
    cli._agent_running = False
    cli._no_speech_count = 0
    cli._pending_resume_sessions = []
    cli._wake_conversation = False
    cli._wake_follow_up_pending = False
    cli._wake_follow_up_seconds = 0.0
    cli._conv_hook_open = False
    cli._conv_hook_closer_running = False
    cli._conv_hook_lock = threading.Lock()
    for key, value in overrides.items():
        setattr(cli, key, value)
    return cli


def _after_turn_cli(**overrides):
    """The same CLI, with everything ``_tui_after_turn`` touches beyond voice."""
    cli = _make_cli(**overrides)
    cli._agent_running = False
    cli._interactive_turn = False
    cli._pet_reasoning = False
    cli._spinner_text = ""
    cli._last_scrollback_tool = ""
    cli._tool_start_time = 0.0
    cli._pending_tool_info = {}
    cli._last_turn_interrupted = False
    cli._pet_react_turn_end = lambda: None
    cli._turn_summary_emit = lambda: None
    cli._drain_interrupt_queue_to_pending_input = lambda: None
    cli._maybe_continue_goal_after_turn = lambda: None
    cli._maybe_complete_loop_tick_after_turn = lambda: None
    cli._drain_process_notifications = lambda *_a, **_kw: None
    return cli


@pytest.fixture(autouse=True)
def _quiet_voice_io(monkeypatch):
    """No beeps, no TTS lease threads, no real stop phrases from a dev config."""
    monkeypatch.setattr("cli._cprint", lambda *_a, **_kw: None)
    monkeypatch.setattr("hermes_cli.config.load_config", dict)
    monkeypatch.setattr(
        "tools.voice_mode_transcript._load_voice_stop_phrases", lambda: ("stop",))


# ── Config ────────────────────────────────────────────────────────────────


def test_follow_up_seconds_defaults_off_everywhere():
    from hermes_cli.config import DEFAULT_CONFIG
    from tools.wake_word import _DEFAULTS

    assert _DEFAULTS["follow_up_seconds"] == 0
    assert DEFAULT_CONFIG["wake_word"]["follow_up_seconds"] == 0


@pytest.mark.parametrize("raw, expected", [(8, 8), (2.5, 2.5), ("junk", 0), (True, 0), (None, 0)])
def test_listener_reads_follow_up_seconds_with_numeric_guard(monkeypatch, raw, expected):
    cli = _make_cli()
    monkeypatch.setattr(
        "tools.wake_word.load_wake_word_config", lambda: {"follow_up_seconds": raw})
    monkeypatch.setattr(
        "tools.wake_word.check_wake_word_requirements",
        lambda _cfg: {"available": True, "deps_available": True, "phrase": "hey hermes"})
    monkeypatch.setattr("tools.wake_word.owns_listener", lambda _owner: False)
    monkeypatch.setattr(
        "tools.wake_word.start_listening", lambda _cb, owner=None, config=None: None)
    cli._start_wake_watchdog = lambda: None

    assert cli._start_wake_word_listener() is True
    assert cli._wake_follow_up_seconds == expected


# ── The window itself ─────────────────────────────────────────────────────


def test_wake_word_starts_a_conversation(monkeypatch):
    cli = _make_cli(_voice_mode=False, _wake_start_new_session=False)
    monkeypatch.setattr("tools.wake_word.pause_listening", lambda *_a, **_kw: True)
    monkeypatch.setattr("tools.wake_word.get_last_match", lambda: None)
    started = []
    cli._voice_start_recording = lambda **kw: started.append(kw)

    cli._on_wake_word()

    assert started == [{}]
    assert cli._wake_conversation is True


def test_zero_disables_the_window():
    cli = _after_turn_cli(_wake_follow_up_seconds=0.0, _wake_conversation=True)
    calls = []
    cli._voice_start_recording = lambda **kw: calls.append(kw)

    cli._tui_after_turn()

    assert calls == []
    assert cli._wake_follow_up_pending is False


def test_window_records_after_tts_with_configured_max_wait():
    cli = _after_turn_cli(_wake_follow_up_seconds=8, _wake_conversation=True, _voice_tts=True)
    cli._voice_tts_done.clear()
    calls = []
    recorded = threading.Event()

    def _start(max_wait=None):
        calls.append(max_wait)
        recorded.set()

    cli._voice_start_recording = _start

    cli._tui_after_turn()

    # Pending is latched before the worker starts, so the watchdog stays paused.
    assert cli._wake_follow_up_pending is True
    assert calls == []  # TTS still playing

    cli._voice_tts_done.set()
    assert recorded.wait(5), "follow-up recording never started"
    assert calls == [8]
    for _ in range(100):
        if not cli._wake_follow_up_pending:
            break
        time.sleep(0.02)
    assert cli._wake_follow_up_pending is False


def test_voice_off_during_the_reply_cancels_the_window():
    cli = _after_turn_cli(_wake_follow_up_seconds=8, _wake_conversation=True, _voice_tts=True)
    cli._voice_tts_done.clear()
    calls = []
    cli._voice_start_recording = lambda **kw: calls.append(kw)

    cli._tui_after_turn()
    cli._voice_mode = False  # /voice off while the reply is still playing
    cli._voice_tts_done.set()

    for _ in range(100):
        if not cli._wake_follow_up_pending:
            break
        time.sleep(0.02)
    assert cli._wake_follow_up_pending is False
    assert calls == []


def test_barge_capture_cancels_the_window():
    cli = _after_turn_cli(_wake_follow_up_seconds=8, _wake_conversation=True, _voice_tts=True)
    cli._voice_tts_done.clear()
    calls = []
    cli._voice_start_recording = lambda **kw: calls.append(kw)

    cli._tui_after_turn()
    cli._voice_barge_capture.set()  # the barge monitor already owns the mic
    cli._voice_tts_done.set()

    for _ in range(100):
        if not cli._wake_follow_up_pending:
            break
        time.sleep(0.02)
    assert cli._wake_follow_up_pending is False
    assert calls == []


def test_watchdog_treats_a_pending_window_as_busy(monkeypatch):
    cli = _make_cli(
        _wake_word_active=True, _wake_suspended=True, _wake_follow_up_pending=True,
        _wake_watchdog_started=False)
    resumed = threading.Event()
    monkeypatch.setattr(
        "tools.wake_word.resume_listening", lambda *_a, **_kw: resumed.set() or True)

    # Drive the loop's sleep instead of the wall clock: 6 polls = two full idle rounds.
    polls = threading.Event()
    sleep_calls = 0
    real_sleep = time.sleep

    def _fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 6:
            polls.set()
        real_sleep(0.001)  # yield the GIL, or a failing assert leaves the loop spinning and pytest hangs

    monkeypatch.setattr(
        "hermes_cli.cli_voice_mixin.time", SimpleNamespace(sleep=_fake_sleep))

    cli._start_wake_watchdog()
    try:
        assert polls.wait(5), "watchdog never polled"
        assert not resumed.is_set(), "listener resumed while the follow-up window owned the mic"

        cli._wake_follow_up_pending = False
        assert resumed.wait(5), "listener never resumed after the window closed"
    finally:
        cli._wake_word_active = False


# ── Silence / stop phrase / keyboard ──────────────────────────────────────


def _transcribing_cli(transcript, wav_path="/tmp/follow_up.wav"):
    cli = _make_cli(_voice_recording=True, _wake_conversation=True)
    recorder = MagicMock()
    recorder.stop.return_value = wav_path
    cli._voice_recorder = recorder
    cli._voice_beep = lambda **_kw: None
    return cli


def test_recorder_returning_none_ends_the_conversation():
    cli = _transcribing_cli("/tmp/unused.wav", wav_path=None)
    started = []
    cli._voice_start_recording = lambda **kw: started.append(kw)

    cli._voice_stop_and_transcribe()

    assert cli._wake_conversation is False
    assert started == []


def test_empty_transcript_in_the_window_ends_the_conversation(monkeypatch):
    cli = _transcribing_cli("/tmp/follow_up.wav")
    monkeypatch.setattr(
        "tools.voice_mode.transcribe_recording",
        lambda *_a, **_kw: {"success": True, "transcript": "   "})
    cli._voice_start_recording = lambda **kw: pytest.fail("must not restart on silence")

    cli._voice_stop_and_transcribe()

    assert cli._wake_conversation is False


def test_stop_phrase_inside_the_conversation_only_ends_the_conversation(monkeypatch):
    cli = _transcribing_cli("/tmp/follow_up.wav")
    cli._voice_mode = True
    cli._voice_tts = True
    cli._tts_lease_async = lambda _active: None
    monkeypatch.setattr(
        "tools.voice_mode.transcribe_recording",
        lambda *_a, **_kw: {"success": True, "transcript": "stop"})
    monkeypatch.setattr("tools.voice_mode_transcript.is_voice_stop_phrase", lambda _text: True)
    monkeypatch.setattr("tools.voice_mode.stop_playback", lambda: None)
    printed = []
    monkeypatch.setattr("cli._cprint", lambda *args, **_kw: printed.append(" ".join(map(str, args))))

    cli._voice_stop_and_transcribe()

    assert cli._wake_conversation is False
    # Voice mode and TTS survive: on a headless box _disable_voice_mode would
    # silence every later reply until restart.
    assert cli._voice_mode is True
    assert cli._voice_tts is True
    assert any(t("cli.voice.stop_phrase_conversation") in line for line in printed), printed


def test_stop_phrase_outside_the_conversation_still_disables_voice(monkeypatch):
    cli = _transcribing_cli("/tmp/follow_up.wav")
    cli._wake_conversation = False
    cli._voice_mode = True
    cli._voice_tts = True
    cli._tts_lease_async = lambda _active: None
    monkeypatch.setattr(
        "tools.voice_mode.transcribe_recording",
        lambda *_a, **_kw: {"success": True, "transcript": "stop"})
    monkeypatch.setattr("tools.voice_mode_transcript.is_voice_stop_phrase", lambda _text: True)
    monkeypatch.setattr("tools.voice_mode.stop_playback", lambda: None)

    cli._voice_stop_and_transcribe()

    assert cli._voice_mode is False
    assert cli._voice_tts is False


def test_typed_input_does_not_continue_a_wake_conversation():
    cli = _after_turn_cli(_wake_conversation=True)
    cli._tui_after_turn = lambda: None
    cli._print_user_message_preview = lambda *_a: None
    cli._turn_summary_begin = lambda: None
    cli.handle_bang_shell = lambda _text: False
    cli.chat = lambda *_a, **_kw: None

    cli._tui_process_one_input("typed instead of spoken")

    assert cli._wake_conversation is False


def test_voice_input_keeps_the_conversation_open():
    cli = _after_turn_cli(_wake_conversation=True)
    cli._tui_after_turn = lambda: None
    cli._print_user_message_preview = lambda *_a: None
    cli._turn_summary_begin = lambda: None
    cli.handle_bang_shell = lambda _text: False
    seen = []
    cli.chat = lambda *_a, **kw: seen.append(kw.get("voice_input"))

    from cli import _VoiceInputMessage

    cli._tui_process_one_input(_VoiceInputMessage("a follow-up answer"))

    assert seen == [True]
    assert cli._wake_conversation is True


# ── Recorder max_wait is per start, not sticky ────────────────────────────


def _start_recording_cli(recorder):
    cli = _make_cli(_voice_recorder=None)
    with patch("tools.voice_mode.create_audio_recorder", return_value=recorder), \
         patch("tools.voice_mode.check_voice_requirements",
               return_value={"audio_available": True, "stt_available": True}), \
         patch("cli.threading.Thread", return_value=MagicMock(start=MagicMock())):
        cli._voice_start_recording(max_wait=8)
        assert cli._voice_recorder is recorder
        assert recorder._max_wait == 8
        cli._voice_recording = False
        cli._voice_start_recording()  # reused recorder, normal start
    return recorder


def test_normal_start_restores_the_15_second_wait():
    recorder = MagicMock()
    recorder.supports_silence_autostop = True
    assert _start_recording_cli(recorder)._max_wait == 15.0


def test_recorder_without_the_attribute_gets_one():
    class _BareRecorder:
        supports_silence_autostop = False

        def start(self, on_silence_stop=None):
            pass

    recorder = _BareRecorder()
    assert _start_recording_cli(recorder)._max_wait == 15.0
