"""``hermes model`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_model_parser(subparsers, *, cmd_model: Callable) -> None:
    """Attach the ``model`` subcommand to ``subparsers``."""
    model_parser = subparsers.add_parser(
        "model", help="Select default model and provider",
        description="Interactively select your inference provider and default model")
    model_parser.add_argument(
        "--refresh", action="store_true",
        help="Wipe the model picker disk cache and re-fetch every provider's live /v1/models list.")
    model_parser.add_argument(
        "--portal-url", help="Portal base URL for Nous login (default: production portal)")
    model_parser.add_argument(
        "--inference-url",
        help="Inference API base URL for Nous login (default: production inference API)")
    model_parser.add_argument(
        "--client-id", default=None,
        help="OAuth client id to use for Nous login (default: hermes-cli)")
    model_parser.add_argument("--scope", default=None, help="OAuth scope to request for Nous login")
    model_parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not attempt to open the browser automatically during Nous login")
    model_parser.add_argument(
        "--timeout", type=float, default=15.0,
        help="HTTP request timeout in seconds for Nous login (default: 15)")
    model_parser.add_argument(
        "--ca-bundle", help="Path to CA bundle PEM file for Nous TLS verification")
    model_parser.add_argument(
        "--insecure", action="store_true",
        help="Disable TLS verification for Nous login (testing only)")
    model_parser.set_defaults(func=cmd_model)
    actions = model_parser.add_subparsers(dest="model_action", metavar="{list}")
    list_parser = actions.add_parser(
        "list", help="Print the configured providers and their model ids (non-interactive)",
        description="Print every configured provider with its model ids, as the model picker offers them. "
                    "The provider id and model id are what delegate_task's provider/model take.")
    list_parser.add_argument(
        "query", nargs="?", default="",
        help="Case-insensitive text: a provider id/name keeps all its models, otherwise only matching model ids")
    list_parser.add_argument("--json", action="store_true", help="Print [{provider, name, models}] as JSON")
    list_parser.set_defaults(func=_cmd_model_list)


def _cmd_model_list(args) -> int:
    from hermes_cli.model_list_cmd import model_list_command

    return model_list_command(args)
