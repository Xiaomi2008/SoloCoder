import json
import sys


def test_json_import():
    """Test that json module can be imported and used."""
    data = {"key": "value"}
    json_str = json.dumps(data)
    assert json_str == '{"key": "value"}'


def test_os_import():
    """Test that os module can be imported and used."""
    current_dir = os.getcwd()
    assert isinstance(current_dir, str)
    assert len(current_dir) > 0
