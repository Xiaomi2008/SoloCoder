"""Tests for P2 features: Config, CLI bash session reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from openagent.core.config import Config


# ============================================================================
# Config - search config
# ============================================================================


class TestConfig:
    def test_default_config(self):
        """Default config has search settings."""
        config = Config()
        assert config.search.provider == "tavily"

    def test_to_dict(self):
        config = Config()
        d = config.to_dict()
        assert "search" in d
        assert d["search"]["provider"] == "tavily"
