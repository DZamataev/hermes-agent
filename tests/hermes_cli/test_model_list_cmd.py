"""``hermes model list``: the configured providers' model ids, non-interactive (scripts, agents)."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest

from hermes_cli.subcommands.model import build_model_parser

ROWS = [
    {"slug": "moa", "name": "Mixture of Agents", "models": ["default"]},
    {"slug": "zai", "name": "Z.AI / GLM", "models": ["glm-5.3", "glm-5.3-flash"]},
    {"slug": "opencode-go", "name": "OpenCode Go", "models": ["qwen3.7-max", "glm-5.1"]},
    {"slug": "codex-lb", "name": "Codex LB", "models": ["gpt-6-luna"]},
    {"slug": "empty", "name": "Empty", "models": []},
]


def _cmd_model(args):  # pragma: no cover - identity only
    return "interactive"


def _parse(argv):
    parser = argparse.ArgumentParser()
    build_model_parser(parser.add_subparsers(dest="command"), cmd_model=_cmd_model)
    return parser.parse_args(argv)


def _run(argv, capsys, rows=ROWS):
    args = _parse(argv)
    with patch("hermes_cli.inventory.load_picker_context", return_value=object()), \
            patch("hermes_cli.inventory.build_models_payload", return_value={"providers": rows}):
        rc = args.func(args)
    return rc, capsys.readouterr().out


def test_lists_only_providers_the_user_configured(capsys):
    """Ambient credentials (a stale Nous login, an auto-seeded Copilot token) are hidden, as in the chat picker."""
    args = _parse(["model", "list"])
    with patch("hermes_cli.inventory.load_picker_context", return_value=object()), \
            patch("hermes_cli.inventory.build_models_payload", return_value={"providers": ROWS}) as build:
        args.func(args)
    assert build.call_args.kwargs.get("explicit_only") is True


def test_bare_model_stays_interactive():
    assert _parse(["model"]).func is _cmd_model
    assert _parse(["model", "--refresh"]).func is _cmd_model


def test_lists_every_callable_provider_with_its_models(capsys):
    rc, out = _run(["model", "list"], capsys)
    assert rc == 0
    lines = out.strip().splitlines()
    assert lines == [
        "zai (Z.AI / GLM): glm-5.3, glm-5.3-flash",
        "opencode-go (OpenCode Go): qwen3.7-max, glm-5.1",
        "codex-lb (Codex LB): gpt-6-luna",
    ]


def test_filter_matches_model_ids_case_insensitively(capsys):
    rc, out = _run(["model", "list", "GLM-5"], capsys)
    assert rc == 0
    assert out.strip().splitlines() == [
        "zai (Z.AI / GLM): glm-5.3, glm-5.3-flash",
        "opencode-go (OpenCode Go): glm-5.1",
    ]


def test_filter_matching_a_provider_keeps_all_its_models(capsys):
    _, out = _run(["model", "list", "codex"], capsys)
    assert out.strip().splitlines() == ["codex-lb (Codex LB): gpt-6-luna"]


def test_json_shape(capsys):
    rc, out = _run(["model", "list", "glm", "--json"], capsys)
    assert rc == 0
    assert json.loads(out) == [
        {"provider": "zai", "name": "Z.AI / GLM", "models": ["glm-5.3", "glm-5.3-flash"]},
        {"provider": "opencode-go", "name": "OpenCode Go", "models": ["glm-5.1"]},
    ]


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_no_match_is_a_nonzero_exit(capsys, extra):
    rc, out = _run(["model", "list", "nope", *extra], capsys)
    assert rc == 1
    if extra:
        assert json.loads(out) == []
