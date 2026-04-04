"""Tests for the bridge module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meshbridge.bridge import Bridge, _serialize_event
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


def test_serialize_includes_source():
    """Source field is included when not default."""
    event = MeshEvent(event_type=EventType.CHANNEL_MESSAGE, source="discord", text="hi")
    result = json.loads(_serialize_event(event))
    assert result["source"] == "discord"


# -- Sender name parsing from channel message text --


def _build(bridge, event_type, payload):
    """Helper to call Bridge._build_mesh_event."""
    return bridge._build_mesh_event(event_type, payload)


def test_channel_msg_extracts_sender_from_text():
    """MeshCore prepends sender to channel text; bridge should split it."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(b, EventType.CHANNEL_MESSAGE, {
        "text": "Greg Alt: Ping",
        "channel_idx": 0,
        "path_len": 0,
    })
    assert event.sender_name == "Greg Alt"
    assert event.text == "Ping"


def test_channel_msg_preserves_explicit_sender_name():
    """When sender_name is in payload, don't re-parse the text."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(b, EventType.CHANNEL_MESSAGE, {
        "text": "Hey: what's up?",
        "channel_idx": 0,
        "sender_name": "Bob",
    })
    assert event.sender_name == "Bob"
    assert event.text == "Hey: what's up?"


def test_channel_msg_no_colon_in_text():
    """Text without ': ' leaves sender_name as None."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(b, EventType.CHANNEL_MESSAGE, {
        "text": "hello",
        "channel_idx": 0,
    })
    assert event.sender_name is None
    assert event.text == "hello"


def test_contact_msg_does_not_split_text():
    """Contact messages should NOT strip sender from text."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(b, EventType.CONTACT_MESSAGE, {
        "text": "Greg Alt: Ping",
        "channel_idx": 0,
    })
    assert event.sender_name is None
    assert event.text == "Greg Alt: Ping"


# -- Bridge outbound handler tests --


@pytest.fixture
def bridge():
    config = {
        "device": {"serial_port": "/dev/ttyUSB0"},
        "mqtt": {"topic_prefix": "meshbridge"},
    }
    mqtt_client = AsyncMock()
    b = Bridge(config, mqtt_client)
    mc = MagicMock()
    mc.commands = AsyncMock()
    b._mc = mc
    return b


# -- Device name on startup --


@pytest.mark.asyncio
async def test_start_sets_device_name_and_advertises_when_configured():
    """Bridge calls set_name + send_advert on startup when device.name is configured."""
    config = {
        "device": {"serial_port": "/dev/ttyUSB0", "name": "RELAY-01"},
        "mqtt": {"topic_prefix": "meshbridge"},
    }
    mqtt = AsyncMock()
    b = Bridge(config, mqtt)

    mc = AsyncMock()
    mc.self_info = {"name": "old"}
    mc.commands = AsyncMock()

    with patch("meshbridge.bridge.MeshCore.create_serial", return_value=mc):
        await b.start()

    mc.commands.set_name.assert_awaited_once_with("RELAY-01")
    mc.commands.send_advert.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_skips_set_name_when_not_configured():
    """Bridge does not call set_name when device.name is absent."""
    config = {
        "device": {"serial_port": "/dev/ttyUSB0"},
        "mqtt": {"topic_prefix": "meshbridge"},
    }
    mqtt = AsyncMock()
    b = Bridge(config, mqtt)

    mc = AsyncMock()
    mc.self_info = {"name": "existing"}
    mc.commands = AsyncMock()

    with patch("meshbridge.bridge.MeshCore.create_serial", return_value=mc):
        await b.start()

    mc.commands.set_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_channel_msg(bridge):
    """Outbound channel message is sent to MeshCore with correct args."""
    payload = json.dumps({"text": "hello mesh", "source_plugin": "discord"}).encode()
    await bridge._on_outbound_channel_msg("meshbridge/outbound/channel/3", payload)

    bridge._mc.commands.send_chan_msg.assert_awaited_once_with(3, "hello mesh")


@pytest.mark.asyncio
async def test_outbound_channel_msg_parses_channel_from_topic(bridge):
    """Channel index is extracted from the last segment of the topic."""
    payload = json.dumps({"text": "test"}).encode()
    await bridge._on_outbound_channel_msg("meshbridge/outbound/channel/7", payload)

    bridge._mc.commands.send_chan_msg.assert_awaited_once_with(7, "test")


@pytest.mark.asyncio
async def test_outbound_channel_msg_bad_json(bridge):
    """Malformed JSON doesn't crash the handler."""
    await bridge._on_outbound_channel_msg("meshbridge/outbound/channel/0", b"not json")
    bridge._mc.commands.send_chan_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_channel_msg_no_device(bridge):
    """No-op when MeshCore device is not connected."""
    bridge._mc = None
    payload = json.dumps({"text": "hello"}).encode()
    await bridge._on_outbound_channel_msg("meshbridge/outbound/channel/0", payload)
    # Should not raise


@pytest.mark.asyncio
async def test_outbound_direct_msg(bridge):
    """Outbound direct message looks up contact and sends."""
    fake_contact = MagicMock()
    bridge._mc.get_contact_by_name.return_value = fake_contact
    payload = json.dumps({
        "text": "hi Bob",
        "contact_name": "Bob",
        "source_plugin": "discord",
    }).encode()

    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/Bob", payload)

    bridge._mc.get_contact_by_name.assert_called_once_with("Bob")
    bridge._mc.commands.send_msg.assert_awaited_once_with(fake_contact, "hi Bob")


@pytest.mark.asyncio
async def test_outbound_direct_msg_contact_not_found(bridge):
    """Unknown contact name logs warning, doesn't crash."""
    bridge._mc.get_contact_by_name.return_value = None
    payload = json.dumps({
        "text": "hi",
        "contact_name": "Unknown",
    }).encode()

    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/Unknown", payload)

    bridge._mc.commands.send_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_direct_msg_no_contact_name(bridge):
    """Missing contact_name in payload is handled safely."""
    payload = json.dumps({"text": "hi"}).encode()
    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/someone", payload)

    bridge._mc.commands.send_msg.assert_not_awaited()
