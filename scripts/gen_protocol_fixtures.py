#!/usr/bin/env python3
"""Regenerate the protocol fixtures the Swift package decodes.

    .venv/bin/python scripts/gen_protocol_fixtures.py

Run this after any change to `openagent/runtime/events.py` or
`openagent/service/protocol.py`, then commit the fixtures with it: a client
generated from stale fixtures fails the contract tests, which is the point.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.support.protocol_samples import protocol_manifest  # noqa: E402

FIXTURES = ROOT / "tests" / "service" / "fixtures"


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    manifest = protocol_manifest()
    (FIXTURES / "protocol.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (FIXTURES / "events.json").write_text(
        json.dumps({"events": manifest["events"]}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(manifest['events'])} event fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
