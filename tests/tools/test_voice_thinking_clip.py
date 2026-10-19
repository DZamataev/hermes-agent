"""The waiting sound: a configured clip instead of the blips, from end-of-speech to the reply.

A headless voice box is silent for the whole wait (transcription, then the model), which is
the longest part of a turn. ``voice.thinking_sound_file`` loops a clip (a ticking clock) in
place of the synthesized blips; it starts as soon as the user stops speaking and ends for
good when the reply starts to play.
"""

import array
import logging
import queue
import shutil
import subprocess
import threading
import time

import pytest

import tools.voice_mode as vm


class _Np:
    int16 = "int16"

    @staticmethod
    def frombuffer(raw, dtype=None):
        assert dtype == "int16"
        return array.array("h", raw)


class _Sd:
    """sounddevice stand-in. The waiting loop must play on its OWN OutputStream: the module-level
    sd.play/sd.stop stream belongs to replies and cues, and touching it from the loop's thread
    deadlocked PortAudio (Pa_StopStream) under a concurrent reply."""

    def __init__(self):
        self.played = []  # one entry per clip: samples written to that clip's private stream
        self.on_write = None
        sd = self

        class OutputStream:
            def __init__(self, **kwargs):
                assert kwargs["channels"] == 1 and kwargs["dtype"] == "int16"
                self.samples = 0

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                sd.played.append(self.samples)

            def write(self, data):
                self.samples += len(data)
                if sd.on_write:
                    sd.on_write()

        self.OutputStream = OutputStream

    def play(self, *_a, **_kw):
        raise AssertionError("waiting sound used the global sd.play stream")

    def stop(self, *_a, **_kw):
        raise AssertionError("waiting sound used the global sd.stop")


@pytest.fixture(autouse=True)
def _clean():
    vm.stop_thinking_sound()
    with vm._audio_output_lock:
        vm._audio_output_active_count = 0
    yield
    vm.stop_thinking_sound()
    with vm._audio_output_lock:
        vm._audio_output_active_count = 0


@pytest.fixture
def sd(monkeypatch):
    fake = _Sd()
    monkeypatch.setattr(vm, "_sounddevice_output_allowed", lambda: True)
    monkeypatch.setattr(vm, "_import_audio", lambda: (fake, _Np))
    monkeypatch.setattr(vm, "thinking_sound_enabled", lambda: True)
    return fake


@pytest.fixture
def tick(tmp_path):
    ffmpeg = shutil.which("ffmpeg") or pytest.skip("ffmpeg not installed")
    path = tmp_path / "tick.wav"
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "quiet", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=2000:duration=0.25", "-ar", "48000", str(path)], check=True, timeout=30)
    return path


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_a_configured_clip_loops_instead_of_the_blips(sd, tick, monkeypatch):
    monkeypatch.setattr(vm, "_voice_config", lambda: {"thinking_sound_file": str(tick)})
    monkeypatch.setattr(vm, "_synth_thinking_blip", lambda *_a: pytest.fail("blips must not be synthesized"))

    assert vm.start_thinking_sound() is True
    assert _wait(lambda: len(sd.played) >= 2), "the clip did not loop"
    vm.stop_thinking_sound()
    # Decoded to the loop's 16 kHz: 0.25 s → ~4000 samples, not the file's 48 kHz 12000.
    assert all(3800 <= n <= 4200 for n in sd.played), sd.played


def test_stop_cuts_a_clip_midway(sd, tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg") or pytest.skip("ffmpeg not installed")
    long_clip = tmp_path / "long.wav"
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "quiet", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=500:duration=3", str(long_clip)], check=True, timeout=30)
    monkeypatch.setattr(vm, "_voice_config", lambda: {"thinking_sound_file": str(long_clip)})
    sd.on_write = vm.stop_thinking_sound  # the reply starts right after the first chunk

    assert vm.start_thinking_sound() is True
    assert _wait(lambda: len(sd.played) >= 1)
    assert sd.played[0] < 16000, f"a stopped clip kept playing: {sd.played[0]} of 48000 samples"


def test_a_missing_clip_warns_and_falls_back_to_the_blips(sd, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(vm, "_voice_config", lambda: {"thinking_sound_file": str(tmp_path / "gone.wav")})
    monkeypatch.setattr(vm, "_synth_thinking_blip", lambda _np, f: array.array("h", [1] * 160))

    with caplog.at_level(logging.WARNING, logger="tools.voice_mode"):
        assert vm.start_thinking_sound() is True
        assert _wait(lambda: len(sd.played) >= 1)
    vm.stop_thinking_sound()
    assert sd.played[0] == 160
    assert any("gone.wav" in r.getMessage() for r in caplog.records)


def test_the_reply_starting_to_play_ends_the_waiting_sound(sd, tick, monkeypatch):
    monkeypatch.setattr(vm, "_voice_config", lambda: {"thinking_sound_file": str(tick)})
    assert vm.start_thinking_sound() is True

    vm.mark_audio_output_active(True)
    vm.mark_audio_output_active(False)

    assert vm._thinking_stop is None, "speech started but the waiting sound kept running"


# ── CLI wiring ──────────────────────────────────────────────────────────


def _cli(**overrides):
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_lock = threading.Lock()
    cli._voice_mode = True
    cli._voice_recording = True
    cli._voice_processing = False
    cli._voice_continuous = False
    cli._voice_tts_done = threading.Event()
    cli._voice_barge_capture = threading.Event()
    cli._voice_live_text = ""
    cli._pending_input = queue.Queue()
    cli._attached_images = []
    cli._agent_running = False
    cli._no_speech_count = 0
    cli._wake_conversation = False
    cli._voice_beep = lambda **_kw: None
    cli._voice_invalidate = lambda: None
    cli._voice_stt_model = lambda: "small"
    cli._voice_stt_provider = lambda: "local"

    class _Recorder:
        def stop(self):
            return "/tmp/hermes-thinking-test.wav"

    cli._voice_recorder = _Recorder()
    for key, value in overrides.items():
        setattr(cli, key, value)
    return cli


@pytest.fixture
def thinking(monkeypatch):
    """Record start/stop calls of the waiting sound and the gate the CLI hands over."""
    calls = {"started": [], "stopped": 0}
    monkeypatch.setattr(vm, "start_thinking_sound",
                        lambda should_play=None: calls["started"].append(should_play) or True)
    monkeypatch.setattr(vm, "stop_thinking_sound", lambda: calls.__setitem__("stopped", calls["stopped"] + 1))
    monkeypatch.setattr("cli._cprint", lambda *_a, **_kw: None)
    monkeypatch.setattr("tools.voice_mode_transcript._load_voice_stop_phrases", lambda: ("stop",))
    return calls


def test_the_wait_starts_when_the_user_stops_speaking(thinking, monkeypatch):
    seen = {}

    def _transcribe(_path, model=None):
        seen["started_before_stt"] = len(thinking["started"])
        return {"success": True, "transcript": "what time is it"}

    monkeypatch.setattr(vm, "transcribe_recording", _transcribe)
    cli = _cli()
    cli._voice_stop_and_transcribe()

    assert seen["started_before_stt"] == 1, "transcription ran in silence"
    assert thinking["stopped"] == 0, "a submitted utterance keeps the sound until the reply"
    assert cli._pending_input.qsize() == 1


def test_nothing_to_answer_stops_the_wait(thinking, monkeypatch):
    monkeypatch.setattr(vm, "transcribe_recording", lambda _p, model=None: {"success": True, "transcript": ""})
    _cli()._voice_stop_and_transcribe()
    assert len(thinking["started"]) == 1 and thinking["stopped"] == 1


def test_the_gate_opens_while_streaming_tts_waits_for_text(thinking, monkeypatch):
    # Streaming TTS clears _voice_tts_done at turn start; gating on it kept the sound silent
    # for the whole turn. The gate is "nothing is playing and the mic is not recording".
    monkeypatch.setattr(vm, "transcribe_recording", lambda _p, model=None: {"success": True, "transcript": "hi"})
    cli = _cli()
    cli._voice_stop_and_transcribe()
    gate = thinking["started"][0]

    cli._voice_tts_done.clear()
    assert gate() is True
    cli._voice_recording = True
    assert gate() is False
    cli._voice_recording = False
    vm.mark_audio_output_active(True)
    try:
        assert gate() is False
    finally:
        vm.mark_audio_output_active(False)
