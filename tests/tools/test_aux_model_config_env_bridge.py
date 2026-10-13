"""A config edit after process start must not leave the old aux model live in os.environ.

The config.yaml → env bridges run once per process. Vision readers consult config first and fall
back to ``AUXILIARY_VISION_MODEL`` as a legacy override, so a bridged copy of the startup config
outlives an edit that clears ``auxiliary.vision.model``: the new provider (auto → main) got paired
with the old provider's model (glm-4.6v-flash sent to Anthropic → 404).
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from tools.vision_tools import _handle_video_analyze, _handle_vision_analyze

STARTUP_CFG = {"auxiliary": {"vision": {"provider": "zai", "model": "glm-4.6v-flash"}}}
EDITED_CFG = {"auxiliary": {"vision": {"provider": "auto", "model": ""}}}


def _vision_model_passed(config):
    with patch("tools.vision_tools.vision_analyze_tool", new_callable=AsyncMock) as tool, \
            patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=False), \
            patch("hermes_cli.config.load_config", return_value=config):
        tool.return_value = json.dumps({"success": True})
        asyncio.run(_handle_vision_analyze({"image_url": "/tmp/x.png", "question": "q"}))
        return tool.call_args[0][2]


def _video_model_passed(config):
    with patch("tools.vision_tools.video_analyze_tool", new_callable=AsyncMock) as tool, \
            patch("hermes_cli.config.load_config", return_value=config):
        tool.return_value = json.dumps({"success": True})
        asyncio.run(_handle_video_analyze({"video_url": "/tmp/x.mp4", "question": "q"}))
        return tool.call_args[0][2]


def _browser_model(config):
    from tools.browser_tool import _get_vision_model
    with patch("hermes_cli.config.load_config", return_value=config):
        return _get_vision_model()


def _gateway_bridge(cfg):
    from gateway.run import _bridge_config_to_env
    _bridge_config_to_env(cfg)


def _cli_bridge(cfg):
    from hermes_cli.cli_config_load import _mirror_config_to_env
    _mirror_config_to_env({**cfg, "terminal": {}}, False)


@pytest.fixture
def clean_aux_env(monkeypatch):
    for suffix in ("PROVIDER", "MODEL", "BASE_URL", "API_KEY"):
        monkeypatch.delenv(f"AUXILIARY_VISION_{suffix}", raising=False)
        monkeypatch.delenv(f"AUXILIARY_VIDEO_{suffix}", raising=False)
        monkeypatch.delenv(f"AUXILIARY_APPROVAL_{suffix}", raising=False)


@pytest.mark.parametrize("bridge", [_gateway_bridge, _cli_bridge], ids=["gateway", "cli"])
def test_cleared_model_does_not_resurrect_startup_model(bridge, clean_aux_env):
    bridge(STARTUP_CFG)
    assert _vision_model_passed(EDITED_CFG) is None
    assert _video_model_passed(EDITED_CFG) is None
    assert _browser_model(EDITED_CFG) is None


@pytest.mark.parametrize("bridge", [_gateway_bridge, _cli_bridge], ids=["gateway", "cli"])
def test_changed_model_wins_over_startup_model(bridge, clean_aux_env):
    bridge(STARTUP_CFG)
    edited = {"auxiliary": {"vision": {"provider": "openrouter", "model": "google/gemini-flash"}}}
    assert _vision_model_passed(edited) == "google/gemini-flash"
    assert _browser_model(edited) == "google/gemini-flash"


def test_user_set_env_override_still_applies_when_config_is_empty(clean_aux_env, monkeypatch):
    """The documented legacy override (a var the USER exported) keeps working."""
    monkeypatch.setenv("AUXILIARY_VISION_MODEL", "legacy/model")
    assert _vision_model_passed(EDITED_CFG) == "legacy/model"
    assert _browser_model(EDITED_CFG) == "legacy/model"
