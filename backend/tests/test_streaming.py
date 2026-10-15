import pytest
from services.streaming import generate_message_id


def test_message_id_generation():
    """Test message ID generation."""
    id1 = generate_message_id()
    id2 = generate_message_id()

    assert len(id1) > 0
    assert id1 != id2
