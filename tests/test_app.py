"""Tests for the app orchestrator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from meshbridge.app import App
from meshbridge.events import EventType, MeshEvent


@pytest.fixture
def app(sample_config):
    a = App()
    a._config = sample_config
    a._mqtt = AsyncMock()
    return a


# -- dispatch_event --


@pytest.mark.asyncio
async def test_dispatch_event_calls_all_plugins(app):
    """dispatch_event delivers the event to every loaded plugin."""
    p1 = AsyncMock()
    p1.plugin_name = "p1"
    p2 = AsyncMock()
    p2.plugin_name = "p2"
    app._plugins = [p1, p2]

    event = MeshEvent(event_type=EventType.CHANNEL_MESSAGE, text="hello")
    await app.dispatch_event(event)

    p1.on_mesh_event.assert_awaited_once_with(event)
    p2.on_mesh_event.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_dispatch_event_continues_after_plugin_error(app):
    """A failing plugin doesn't prevent delivery to subsequent plugins."""
    p1 = AsyncMock()
    p1.plugin_name = "p1"
    p1.on_mesh_event.side_effect = RuntimeError("boom")
    p2 = AsyncMock()
    p2.plugin_name = "p2"
    app._plugins = [p1, p2]

    event = MeshEvent(event_type=EventType.CHANNEL_MESSAGE, text="hello")
    await app.dispatch_event(event)

    p2.on_mesh_event.assert_awaited_once_with(event)


# -- broadcast --


@pytest.mark.asyncio
async def test_broadcast_sends_to_mesh_and_dispatches(app):
    """broadcast() sends to mesh via MQTT and dispatches to plugins."""
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    await app.broadcast("pong", channel=2, source_plugin="ping")

    # Verify mesh send via MQTT outbound
    app._mqtt.publish.assert_awaited_once()
    topic, payload = app._mqtt.publish.call_args.args
    assert topic == "meshbridge/outbound/channel/2"
    data = json.loads(payload)
    assert data["text"] == "pong"
    assert data["source_plugin"] == "ping"

    # Verify plugin dispatch
    plugin.on_mesh_event.assert_awaited_once()
    event = plugin.on_mesh_event.call_args.args[0]
    assert event.text == "pong"
    assert event.source == "meshbridge"
    assert event.source_plugin == "ping"
    assert event.channel == 2


# -- display_name --


def test_display_name_defaults_to_meshbridge(app):
    """display_name defaults to 'MeshBridge' when bridge section is absent."""
    assert app.display_name == "MeshBridge"


def test_display_name_from_config(app):
    """display_name reads from bridge.display_name config."""
    app._config["bridge"] = {"display_name": "MyRelay"}
    assert app.display_name == "MyRelay"


@pytest.mark.asyncio
async def test_broadcast_sets_sender_name_from_display_name(app):
    """broadcast() sets sender_name to display_name on the dispatched event."""
    app._config["bridge"] = {"display_name": "RELAY-01"}
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    await app.broadcast("pong", channel=0, source_plugin="ping")

    event = plugin.on_mesh_event.call_args.args[0]
    assert event.sender_name == "RELAY-01"


# -- send_to_mesh --


@pytest.mark.asyncio
async def test_send_to_mesh_publishes_correct_topic(app):
    """send_to_mesh publishes JSON to the correct MQTT outbound topic."""
    await app.send_to_mesh("hello mesh", channel=3, source_plugin="discord")

    app._mqtt.publish.assert_awaited_once()
    topic, payload = app._mqtt.publish.call_args.args
    assert topic == "meshbridge/outbound/channel/3"
    data = json.loads(payload)
    assert data["text"] == "hello mesh"
    assert data["source_plugin"] == "discord"


@pytest.mark.asyncio
async def test_send_to_mesh_noop_without_mqtt(app):
    """send_to_mesh does nothing if MQTT isn't connected."""
    app._mqtt = None
    await app.send_to_mesh("hello")  # should not raise


# -- send_direct_to_mesh --


@pytest.mark.asyncio
async def test_send_direct_to_mesh_publishes_correct_topic(app):
    """send_direct_to_mesh targets the correct contact topic."""
    await app.send_direct_to_mesh("hi", contact_name="Bob", source_plugin="discord")

    topic, payload = app._mqtt.publish.call_args.args
    assert topic == "meshbridge/outbound/direct/Bob"
    data = json.loads(payload)
    assert data["text"] == "hi"
    assert data["contact_name"] == "Bob"


# -- _dispatch_to_plugins (MQTT message parsing) --


@pytest.mark.asyncio
async def test_dispatch_parses_mqtt_payload(app):
    """_dispatch_to_plugins deserializes MQTT JSON into a MeshEvent."""
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    payload = json.dumps(
        {
            "event_type": "CHANNEL_MESSAGE",
            "text": "hello",
            "channel": 0,
            "source": "mesh",
            "sender_name": "Node1",
        }
    ).encode()

    await app._dispatch_to_plugins("meshbridge/inbound/channel/0", payload)

    event = plugin.on_mesh_event.call_args.args[0]
    assert event.event_type == EventType.CHANNEL_MESSAGE
    assert event.text == "hello"
    assert event.source == "mesh"
    assert event.sender_name == "Node1"


@pytest.mark.asyncio
async def test_dispatch_handles_malformed_json(app):
    """Malformed JSON doesn't crash the dispatch loop."""
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    await app._dispatch_to_plugins("meshbridge/inbound/channel/0", b"not json")

    plugin.on_mesh_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_handles_missing_event_type(app):
    """Payload missing event_type key doesn't crash."""
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    payload = json.dumps({"text": "hello"}).encode()
    await app._dispatch_to_plugins("meshbridge/inbound/channel/0", payload)

    plugin.on_mesh_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_parses_path_field(app):
    """_dispatch_to_plugins deserializes the path list into MeshEvent.path."""
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    payload = json.dumps(
        {
            "event_type": "CHANNEL_MESSAGE",
            "text": "hello",
            "channel": 0,
            "source": "mesh",
            "sender_name": "Node1",
            "path": ["!abcd1234", "!efgh5678", "!ijkl9012"],
            "path_len": 3,
        }
    ).encode()

    await app._dispatch_to_plugins("meshbridge/inbound/channel/0", payload)

    event = plugin.on_mesh_event.call_args.args[0]
    assert event.path == ["!abcd1234", "!efgh5678", "!ijkl9012"]


@pytest.mark.asyncio
async def test_dispatch_defaults_source_to_mesh(app):
    """Events without an explicit source field default to 'mesh'."""
    plugin = AsyncMock()
    plugin.plugin_name = "test"
    app._plugins = [plugin]

    payload = json.dumps(
        {
            "event_type": "CHANNEL_MESSAGE",
            "text": "hello",
        }
    ).encode()

    await app._dispatch_to_plugins("meshbridge/inbound/channel/0", payload)

    event = plugin.on_mesh_event.call_args.args[0]
    assert event.source == "mesh"
