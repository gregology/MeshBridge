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
    event = _build(
        b,
        EventType.CHANNEL_MESSAGE,
        {
            "text": "Greg Alt: Ping",
            "channel_idx": 0,
            "path_len": 0,
        },
    )
    assert event.sender_name == "Greg Alt"
    assert event.text == "Ping"


def test_channel_msg_preserves_explicit_sender_name():
    """When sender_name is in payload, don't re-parse the text."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(
        b,
        EventType.CHANNEL_MESSAGE,
        {
            "text": "Hey: what's up?",
            "channel_idx": 0,
            "sender_name": "Bob",
        },
    )
    assert event.sender_name == "Bob"
    assert event.text == "Hey: what's up?"


def test_channel_msg_no_colon_in_text():
    """Text without ': ' leaves sender_name as None."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(
        b,
        EventType.CHANNEL_MESSAGE,
        {
            "text": "hello",
            "channel_idx": 0,
        },
    )
    assert event.sender_name is None
    assert event.text == "hello"


def test_contact_msg_does_not_split_text():
    """Contact messages should NOT strip sender from text."""
    config = {"device": {"serial_port": "/dev/ttyUSB0"}, "mqtt": {}}
    b = Bridge(config, AsyncMock())
    event = _build(
        b,
        EventType.CONTACT_MESSAGE,
        {
            "text": "Greg Alt: Ping",
            "channel_idx": 0,
        },
    )
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
    payload = json.dumps(
        {
            "text": "hi Bob",
            "contact_name": "Bob",
            "source_plugin": "discord",
        }
    ).encode()

    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/Bob", payload)

    bridge._mc.get_contact_by_name.assert_called_once_with("Bob")
    bridge._mc.commands.send_msg.assert_awaited_once_with(fake_contact, "hi Bob")


@pytest.mark.asyncio
async def test_outbound_direct_msg_by_key(bridge):
    """Outbound DM falls back to contact_key when contact_name is absent."""
    fake_contact = MagicMock()
    bridge._mc.get_contact_by_key_prefix.return_value = fake_contact
    payload = json.dumps(
        {
            "text": "hi",
            "contact_key": "abc123",
            "source_plugin": "ping",
        }
    ).encode()

    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/abc123", payload)

    bridge._mc.get_contact_by_name.assert_not_called()
    bridge._mc.get_contact_by_key_prefix.assert_called_once_with("abc123")
    bridge._mc.commands.send_msg.assert_awaited_once_with(fake_contact, "hi")


@pytest.mark.asyncio
async def test_outbound_direct_msg_name_fallback_to_key(bridge):
    """When contact_name lookup fails, falls back to contact_key."""
    fake_contact = MagicMock()
    bridge._mc.get_contact_by_name.return_value = None
    bridge._mc.get_contact_by_key_prefix.return_value = fake_contact
    payload = json.dumps(
        {
            "text": "hi",
            "contact_name": "Unknown",
            "contact_key": "abc123",
        }
    ).encode()

    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/Unknown", payload)

    bridge._mc.get_contact_by_key_prefix.assert_called_once_with("abc123")
    bridge._mc.commands.send_msg.assert_awaited_once_with(fake_contact, "hi")


@pytest.mark.asyncio
async def test_outbound_direct_msg_falls_back_to_raw_key(bridge):
    """When contact lookups fail, sends directly using the raw key prefix."""
    bridge._mc.get_contact_by_name.return_value = None
    bridge._mc.get_contact_by_key_prefix.return_value = None
    payload = json.dumps(
        {
            "text": "hi",
            "contact_name": "Unknown",
            "contact_key": "abc123def456",
        }
    ).encode()

    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/Unknown", payload)

    bridge._mc.commands.send_msg.assert_awaited_once_with("abc123def456", "hi")


@pytest.mark.asyncio
async def test_outbound_direct_msg_no_contact_name(bridge):
    """Missing contact_name and contact_key in payload is handled safely."""
    payload = json.dumps({"text": "hi"}).encode()
    await bridge._on_outbound_direct_msg("meshbridge/outbound/direct/someone", payload)

    bridge._mc.commands.send_msg.assert_not_awaited()


# -- Trace request handler tests --


def _make_path_response_event(payload: dict):
    ev = MagicMock()
    ev.payload = payload
    return ev


@pytest.mark.asyncio
async def test_trace_request_unknown_contact(bridge):
    """Unknown contact returns an error trace_result."""
    bridge._mc.get_contact_by_key_prefix.return_value = None
    bridge._mc.get_contact_by_name.return_value = None

    payload = json.dumps({"corr_id": "abc", "key_or_name": "ghost"}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    bridge._mqtt.publish.assert_awaited_once()
    topic, body = bridge._mqtt.publish.await_args.args
    assert topic == "meshbridge/inbound/trace_result/abc"
    data = json.loads(body)
    assert data["corr_id"] == "abc"
    assert "unknown contact" in data["error"]


@pytest.mark.asyncio
async def test_trace_request_uses_cached_path(bridge):
    """Cached out_path on the contact is returned without hitting discovery."""
    contact = {
        "public_key": "a" * 64,
        "adv_name": "TestNode",
        "out_path_len": 3,
        "out_path": "23ab5f",
    }
    bridge._mc.get_contact_by_key_prefix.return_value = contact

    payload = json.dumps({"corr_id": "c1", "key_or_name": "a" * 12}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    bridge._mc.commands.send_path_discovery.assert_not_awaited()
    topic, body = bridge._mqtt.publish.await_args.args
    assert topic == "meshbridge/inbound/trace_result/c1"
    data = json.loads(body)
    assert data["corr_id"] == "c1"
    assert data["path_text"] == "23 > ab > 5f"
    assert data["hops"] == 3
    assert data["contact_name"] == "TestNode"


@pytest.mark.asyncio
async def test_trace_request_cached_zero_hops(bridge):
    """Cached out_path_len of 0 reports 'direct (0 hops)'."""
    contact = {
        "public_key": "b" * 64,
        "adv_name": "Neighbor",
        "out_path_len": 0,
        "out_path": "",
    }
    bridge._mc.get_contact_by_key_prefix.return_value = contact

    payload = json.dumps({"corr_id": "c2", "key_or_name": "b" * 12}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    bridge._mc.commands.send_path_discovery.assert_not_awaited()
    data = json.loads(bridge._mqtt.publish.await_args.args[1])
    assert data["path_text"] == "direct (0 hops)"
    assert data["hops"] == 0


@pytest.mark.asyncio
async def test_trace_request_discovers_when_no_cached_path(bridge):
    """out_path_len of -1 falls back to active path discovery."""
    contact = {
        "public_key": "c" * 64,
        "adv_name": "Unknown",
        "out_path_len": -1,
        "out_path": "",
    }
    bridge._mc.get_contact_by_key_prefix.return_value = contact
    bridge._mc.wait_for_event = AsyncMock(
        return_value=_make_path_response_event(
            {"out_path_len": 2, "out_path": "1122"}
        )
    )

    payload = json.dumps({"corr_id": "c3", "key_or_name": "c" * 12}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    bridge._mc.commands.send_path_discovery.assert_awaited_once_with(contact)
    data = json.loads(bridge._mqtt.publish.await_args.args[1])
    assert data["path_text"] == "11 > 22"
    assert data["hops"] == 2


@pytest.mark.asyncio
async def test_trace_request_discovery_timeout(bridge):
    """Discovery timeout when no cached path surfaces a clear error."""
    contact = {
        "public_key": "d" * 64,
        "adv_name": "Silent",
        "out_path_len": -1,
        "out_path": "",
    }
    bridge._mc.get_contact_by_key_prefix.return_value = contact
    bridge._mc.wait_for_event = AsyncMock(return_value=None)

    payload = json.dumps({"corr_id": "c4", "key_or_name": "d" * 12}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    data = json.loads(bridge._mqtt.publish.await_args.args[1])
    assert "discovery timed out" in data["error"]


@pytest.mark.asyncio
async def test_trace_request_refreshes_contacts_on_miss(bridge):
    """First lookup misses; after get_contacts refresh the contact resolves."""
    contact = {
        "public_key": "e" * 64,
        "adv_name": "NewNode",
        "out_path_len": 1,
        "out_path": "11",
    }
    bridge._mc.get_contact_by_key_prefix.return_value = None
    bridge._mc.get_contact_by_name.side_effect = [None, contact]

    payload = json.dumps({"corr_id": "c5", "key_or_name": "NewNode"}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    bridge._mc.commands.get_contacts.assert_awaited_once()
    data = json.loads(bridge._mqtt.publish.await_args.args[1])
    assert data["path_text"] == "11"


@pytest.mark.asyncio
async def test_trace_request_falls_back_to_name_lookup(bridge):
    """When key_prefix lookup misses, falls back to name lookup."""
    contact = {
        "public_key": "f" * 64,
        "adv_name": "ByName",
        "out_path_len": 1,
        "out_path": "ff",
    }
    bridge._mc.get_contact_by_key_prefix.return_value = None
    bridge._mc.get_contact_by_name.return_value = contact

    payload = json.dumps({"corr_id": "c6", "key_or_name": "ByName"}).encode()
    await bridge._on_trace_request("meshbridge/outbound/trace_request", payload)

    bridge._mc.get_contact_by_name.assert_called_with("ByName")
    data = json.loads(bridge._mqtt.publish.await_args.args[1])
    assert data["path_text"] == "ff"
