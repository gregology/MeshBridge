"""Tests for the config module."""

import pytest
import yaml

from meshbridge.config import load_config


def test_load_config(config_file):
    """Config loads and validates successfully from a file."""
    config = load_config(str(config_file))
    assert config["device"]["serial_port"] == "/dev/ttyUSB0"
    assert config["mqtt"]["broker"] == "127.0.0.1"


def test_load_config_missing_file():
    """Raises FileNotFoundError when config file doesn't exist."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_load_config_missing_section(tmp_path):
    """Raises ValueError when a required section is missing."""
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump({"mqtt": {"broker": "127.0.0.1"}}, f)
    with pytest.raises(ValueError, match="device"):
        load_config(str(config_path))


def test_load_config_missing_key(tmp_path):
    """Raises ValueError when a required key is missing."""
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump({"device": {}, "mqtt": {"broker": "127.0.0.1"}}, f)
    with pytest.raises(ValueError, match="serial_port"):
        load_config(str(config_path))
