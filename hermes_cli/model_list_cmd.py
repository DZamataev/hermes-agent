"""``hermes model list``: the configured providers' model ids, for scripts and agents."""

from __future__ import annotations

import json
from typing import Any


def _rows(query: str) -> list[dict[str, Any]]:
    from hermes_cli.inventory import build_models_payload, load_picker_context

    # Disk-cached catalogs only: a degraded provider must not stall a one-line lookup.
    payload = build_models_payload(load_picker_context(), non_blocking_catalogs=True, for_picker=True)
    needle = query.strip().lower()
    out = []
    for row in payload.get("providers") or []:
        slug = str(row.get("slug") or "").strip()
        models = [str(m) for m in row.get("models") or []]
        # moa is a preset ensemble, not a provider a subagent route can name.
        if not slug or slug.lower() == "moa" or not models:
            continue
        name = str(row.get("name") or slug)
        if needle and needle not in slug.lower() and needle not in name.lower():
            models = [m for m in models if needle in m.lower()]
        if models:
            out.append({"provider": slug, "name": name, "models": models})
    return out


def model_list_command(args) -> int:
    rows = _rows(getattr(args, "query", "") or "")
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False))
    elif rows:
        for row in rows:
            print(f"{row['provider']} ({row['name']}): {', '.join(row['models'])}")
    else:
        print("No configured provider or model matches." if args.query else "No configured providers with models.")
    return 0 if rows else 1
