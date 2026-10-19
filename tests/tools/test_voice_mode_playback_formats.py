"""Compressed TTS replies must be decoded and played through sounddevice.

The bundled ``ffplay`` is an SDL build with no ALSA backend, and ``aplay`` plays
compressed bytes as raw PCM — a loud burst of noise. So a non-WAV reply is
decoded to a temp WAV (s16le) first; ``aplay`` is only ever offered a WAV.
"""

import array
import logging
import os
import shutil
import subprocess
import wave

import pytest

import tools.voice_mode as vm


class _Np:
    """Just enough of numpy for ``_play_wav_via_sounddevice`` (not installed in the test env)."""

    int16 = "int16"

    @staticmethod
    def frombuffer(frames, dtype=None):
        assert dtype == "int16"
        return array.array("h", frames)


@pytest.fixture
def mp3(tmp_path):
    """A real, tiny mp3 (not a fake header) — decode failures must be real."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "reply.mp3"
    try:
        subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "quiet", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
             "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(path)],
            check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        pytest.skip(f"ffmpeg cannot encode mp3: {exc}")
    return path


@pytest.fixture
def sd_capture(monkeypatch):
    """Capture what sounddevice would play, without touching PortAudio."""
    played = {}

    def fake_play(sd, audio, sample_rate, *, timeout, blocksize=0):
        played["audio"] = audio
        played["rate"] = sample_rate

    monkeypatch.setattr(vm, "_sounddevice_output_allowed", lambda: True)
    monkeypatch.setattr(vm, "_import_audio", lambda: (object(), _Np))
    monkeypatch.setattr(vm, "_sd_play_blocking", fake_play)
    return played


def test_mp3_is_decoded_and_played_via_sounddevice(mp3, sd_capture, monkeypatch):
    system_players = []
    monkeypatch.setattr(
        vm, "_run_system_player", lambda cmd: system_players.append(cmd) or False)

    assert vm.play_audio_file(str(mp3)) is True

    audio = sd_capture["audio"]
    assert sd_capture["rate"] == 16000
    assert len(audio) > 1000  # ~0.3 s of 16 kHz audio, not an empty buffer
    assert max(abs(sample) for sample in audio) > 100  # real signal, not silence
    # The fixed path never needs a system player, and never hands mp3 to aplay.
    assert system_players == []


class _OutputDevice:
    """sounddevice stand-in whose default output runs at 48 kHz."""

    @staticmethod
    def query_devices(_device=None, kind=None):
        assert kind == "output"
        return {"default_samplerate": 48000.0}


def test_mp3_is_decoded_at_the_output_device_rate(tmp_path, sd_capture, monkeypatch):
    # PortAudio over ALSA plug+dmix refuses some file rates (22050 Hz, Piper's native one) while
    # the device's own default always opens — decode straight to that rate.
    ffmpeg = shutil.which("ffmpeg") or pytest.skip("ffmpeg not installed")
    mp3 = tmp_path / "piper.mp3"
    subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "quiet", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=0.3", "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", str(mp3)],
        check=True, timeout=30)
    monkeypatch.setattr(vm, "_import_audio", lambda: (_OutputDevice(), _Np))

    assert vm.play_audio_file(str(mp3)) is True
    assert sd_capture["rate"] == 48000
    assert len(sd_capture["audio"]) > 0.25 * 48000


def test_decoded_temp_wav_is_removed(mp3, sd_capture, tmp_path, monkeypatch):
    tmp_wav = tmp_path / "decoded.wav"
    fd = os.open(tmp_wav, os.O_CREAT | os.O_RDWR)
    monkeypatch.setattr(vm.tempfile, "mkstemp", lambda **_kw: (fd, str(tmp_wav)))

    assert vm.play_audio_file(str(mp3)) is True
    assert not tmp_wav.exists()


def test_aplay_is_offered_only_for_wav(monkeypatch):
    monkeypatch.setattr(vm.platform, "system", lambda: "Linux")

    assert ["aplay", "-q", "x.wav"] in vm._system_player_candidates("x.wav")
    assert ["aplay", "-q", "x.mp3"] not in vm._system_player_candidates("x.mp3")


def test_no_working_player_warns_with_the_file_name(tmp_path, monkeypatch, caplog):
    audio = tmp_path / "reply.mp3"
    audio.write_bytes(b"ID3fake")
    monkeypatch.setattr(vm, "_sounddevice_output_allowed", lambda: True)
    monkeypatch.setattr(vm.shutil, "which", lambda _name: None)

    with caplog.at_level(logging.WARNING, logger="tools.voice_mode"):
        assert vm.play_audio_file(str(audio)) is False

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("No audio player" in msg and str(audio) in msg for msg in warnings), warnings


def test_wav_goes_straight_to_sounddevice(tmp_path, monkeypatch):
    wav = tmp_path / "reply.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)

    played = {}
    monkeypatch.setattr(vm, "_sounddevice_output_allowed", lambda: True)
    monkeypatch.setattr(vm, "_import_audio", lambda: (object(), _Np))
    monkeypatch.setattr(
        vm, "_sd_play_blocking", lambda _sd, audio, rate, **_kw: played.update(audio=audio, rate=rate))
    monkeypatch.setattr(
        vm.subprocess, "run", lambda *a, **kw: pytest.fail("WAV must not be decoded via ffmpeg"))

    assert vm.play_audio_file(str(wav)) is True
    assert len(played["audio"]) == 1600
    assert played["rate"] == 16000


def test_file_playback_asks_portaudio_for_a_roomy_buffer():
    # The default low-latency buffer underran on a 4-core box while Piper synthesized the next
    # sentence: a 6 s tone stretched to 7.4 s with 8 dropouts; 0.2 s gave none ("high" still 4-6).
    calls = {}

    class _Sd:
        @staticmethod
        def play(audio, **kwargs):
            calls.update(kwargs)

        @staticmethod
        def get_stream():
            return None

        @staticmethod
        def stop():
            pass

    vm._sd_play_blocking(_Sd, [0] * 160, 16000, timeout=1.0)
    assert calls["latency"] >= 0.2
