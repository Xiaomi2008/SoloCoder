from __future__ import annotations
import pytest
from unittest.mock import Mock, patch
import asyncio
from src.services.agent_service import AgentService


@pytest.mark.asyncio
async def test_agent_service_creates_session():
    """Test session creation."""
    service = AgentService()

    with patch("src.services.agent_service.OpenAIProvider") as MockProvider:
        mock_provider = Mock()
        MockProvider.return_value = mock_provider

        agent = await service.get_or_create_session(None, "gpt-4o")

        MockProvider.assert_called_once()
        assert agent is not None
        assert service.sessions
