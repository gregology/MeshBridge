"""Tests for the bridge module."""

import json

from meshbridge.bridge import _serialize_event
from meshbridge.events import EventType, MeshEvent


def test_serialize_channel_message():
    """Channel message serializes to JSON with expected fields."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="Hello world",
        channel=0,
        sender_name="TestNode",
        sender_key_prefix="abc123",
        sender_timestamp=1234567890,
        path_len=3,
        raw={"text": "Hello world", "channel_idx": 0},
    )
    result = json.loads(_serialize_event(event))

    assert result["event_type"] == "CHANNEL_MESSAGE"
    assert result["text"] == "Hello world"
    assert result["channel"] == 0
    assert result["sender_name"] == "TestNode"
    assert result["sender_key_prefix"] == "abc123"
    assert result["path_len"] == 3
    assert "timestamp" in result
    assert "raw" in result


def test_serialize_omits_none_fields():
    """Serialization skips fields that are None."""
    event = MeshEvent(event_type=EventType.TELEMETRY, node_name="Repeater1")
    result = json.loads(_serialize_event(event))

    assert result["event_type"] == "TELEMETRY"
    assert result["node_name"] == "Repeater1"
    assert "text" not in result
    assert "channel" not in result
    assert "sender_name" not in result


def test_serialize_minimal_event():
    """A minimal event still produces valid JSON."""
    event = MeshEvent(event_type=EventType.BRIDGE_CONNECTED)
    result = json.loads(_serialize_event(event))

    assert result["event_type"] == "BRIDGE_CONNECTED"
    assert "timestamp" in result
