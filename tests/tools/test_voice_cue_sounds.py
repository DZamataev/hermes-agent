"""voice.start_sound / voice.stop_sound — a sound file instead of the synthesized beep.

The record-start cue (880 Hz once) and the record-stop cue (660 Hz twice) are plain sine
beeps; a headless assistant in a room wants something gentler. When the config names a
file, that file plays; anything wrong with it falls back to the beep so the user is never
left without a cue.
"""

import logging
import threading

import pytest

import tools.voice_mode as vm


@pytest.fixture
def cues(monkeypatch):
    calls = {"beep": [], "file": []}
    monkeypatch.setattr(vm, "play_beep", lambda frequency, count: calls["beep"].append((frequency, count)))
    monkeypatch.setattr(vm, "play_audio_file", lambda path: calls["file"].append(path) or True)
    return calls


def _config(monkeypatch, **voice):
    monkeypatch.setattr(vm, "_voice_config", lambda: voice)


def test_without_a_sound_the_beep_plays(cues, monkeypatch):
    _config(monkeypatch)
    vm.play_cue("start", 880, 1)
    assert cues == {"beep": [(880, 1)], "file": []}


def test_a_configured_file_replaces_the_beep(cues, monkeypatch, tmp_path):
    sound = tmp_path / "open.wav"
    sound.write_bytes(b"RIFF")
    _config(monkeypatch, start_sound=str(sound), stop_sound="")
    vm.play_cue("start", 880, 1)
    vm.play_cue("stop", 660, 2)
    assert cues == {"beep": [(660, 2)], "file": [str(sound)]}


def test_a_home_relative_path_is_expanded(cues, monkeypatch, tmp_path):
    (tmp_path / "close.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("HOME", str(tmp_path))
    _config(monkeypatch, stop_sound="~/close.wav")
    vm.play_cue("stop", 660, 2)
    assert cues["file"] == [str(tmp_path / "close.wav")]


def test_a_missing_file_falls_back_to_the_beep_and_warns(cues, monkeypatch, tmp_path, caplog):
    _config(monkeypatch, start_sound=str(tmp_path / "gone.wav"))
    with caplog.at_level(logging.WARNING, logger="tools.voice_mode"):
        vm.play_cue("start", 880, 1)
    assert cues == {"beep": [(880, 1)], "file": []}
    assert any("gone.wav" in r.getMessage() for r in caplog.records)


def test_an_unplayable_file_falls_back_to_the_beep(monkeypatch, tmp_path):
    sound = tmp_path / "broken.wav"
    sound.write_bytes(b"not audio")
    beeps = []
    monkeypatch.setattr(vm, "play_beep", lambda frequency, count: beeps.append((frequency, count)))
    monkeypatch.setattr(vm, "play_audio_file", lambda _path: False)
    _config(monkeypatch, stop_sound=str(sound))
    vm.play_cue("stop", 660, 2)
    assert beeps == [(660, 2)]


def test_cli_cues_name_their_kind(monkeypatch):
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_beeps_enabled = lambda: True
    played = []
    monkeypatch.setattr(vm, "play_cue", lambda kind, frequency, count: played.append((kind, frequency, count)))
    cli._voice_beep(frequency=880, count=1, kind="start")
    cli._voice_beep(frequency=660, count=2, kind="stop")
    assert played == [("start", 880, 1), ("stop", 660, 2)]


def test_cli_recording_start_and_stop_use_the_right_cues(monkeypatch):
    """The two CLI call sites: start cue before the stream opens, stop cue after it closes."""
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_lock = threading.Lock()
    cli._voice_recording = False
    cli._voice_processing = False
    cli._voice_continuous = False
    cli._voice_live_text = ""
    cli._should_exit = False
    kinds = []
    cli._voice_beep = lambda *, frequency, count, kind: kinds.append(kind)
    cli._voice_invalidate = lambda: None
    cli._voice_record_key_label = lambda: "Ctrl+B"

    class _Recorder:
        supports_silence_autostop = True

        def start(self, on_silence_stop=None):
            pass

        def stop(self):
            return None

    cli._voice_recorder = _Recorder()
    monkeypatch.setattr("cli._cprint", lambda *_a, **_kw: None)
    monkeypatch.setattr(vm, "check_voice_requirements",
                        lambda: {"audio_available": True, "stt_available": True})
    monkeypatch.setattr("hermes_cli.config.load_config", dict)

    cli._voice_start_recording()
    cli._voice_stop_and_transcribe()
    assert kinds == ["start", "stop"]


def test_tui_voice_cues_name_their_kind(monkeypatch):
    from hermes_cli import voice

    monkeypatch.setattr(voice, "_beeps_enabled", lambda: True)
    played = []
    monkeypatch.setattr(vm, "play_cue", lambda kind, frequency, count: played.append((kind, frequency, count)))
    voice._play_beep(kind="start", frequency=880, count=1)
    voice._play_beep(kind="stop", frequency=660, count=2)
    assert played == [("start", 880, 1), ("stop", 660, 2)]
