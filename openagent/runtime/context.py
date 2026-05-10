from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeContext:
    project_name: str | None = None
