"""Tests for the events module."""

from meshbridge.events import EventType, MeshEvent


def test_event_type_values():
    """EventType enum has the expected members."""
    assert EventType.CHANNEL_MESSAGE is not None
    assert EventType.CONTACT_MESSAGE is not None
    assert EventType.TELEMETRY is not None
    assert EventType.OUTBOUND_CHANNEL is not None
    assert EventType.BRIDGE_CONNECTED is not None


def test_mesh_event_creation():
    """MeshEvent can be created with required and optional fields."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="Hello mesh",
        channel=0,
        sender_name="TestNode",
        path_len=2,
    )
    assert event.event_type == EventType.CHANNEL_MESSAGE
    assert event.text == "Hello mesh"
    assert event.channel == 0
    assert event.sender_name == "TestNode"
    assert event.path_len == 2
    assert event.telemetry is None


def test_mesh_event_is_frozen():
    """MeshEvent should be immutable."""
    event = MeshEvent(event_type=EventType.CHANNEL_MESSAGE, text="test")
    try:
        event.text = "changed"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass


def test_mesh_event_defaults():
    """MeshEvent optional fields default to None."""
    event = MeshEvent(event_type=EventType.TELEMETRY)
    assert event.text is None
    assert event.channel is None
    assert event.sender_name is None
    assert event.raw is None
    assert event.timestamp > 0
