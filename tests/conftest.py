"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def sample_config() -> dict:
    """A minimal valid config dict."""
    return {
        "device": {
            "serial_port": "/dev/ttyUSB0",
            "baudrate": 115200,
        },
        "mqtt": {
            "broker": "127.0.0.1",
            "port": 1883,
            "username": "meshbridge",
            "password": "testpass",
            "topic_prefix": "meshbridge",
        },
        "logging": {
            "level": "DEBUG",
            "file": None,
        },
        "plugins": {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.com/api/webhooks/test/token",
                "bot_username": "TestBridge",
                "include_metadata": True,
                "channels": [0],
                "event_types": ["CHANNEL_MESSAGE", "CONTACT_MESSAGE"],
            }
        },
    }


@pytest.fixture
def config_file(sample_config, tmp_path) -> Path:
    """Write sample config to a temp file and return its path."""
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(sample_config, f)
    return config_path
