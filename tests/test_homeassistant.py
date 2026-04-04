"""Tests for the Home Assistant plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugins.homeassistant import HomeAssistantPlugin


@pytest.fixture
def mock_app():
    return AsyncMock()


@pytest.fixture
def ha_config():
    return {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "test-token",
        "commands": [
            {
                "pattern": "(?i)^\\s*(weather|forecast)\\s*$",
                "entity_id": "weather.home",
                "response": "{state} — {attributes[temperature]}°{attributes[temperature_unit]}",
            },
            {
                "pattern": "(?i)^\\s*(temp|temperature)\\s*$",
                "entity_id": "sensor.outdoor_temperature",
                "response": "Temp: {state}°F",
            },
            {
                "pattern": "(?i)^\\s*humidity\\s*$",
                "entity_id": "sensor.outdoor_humidity",
                "response": "Humidity: {state}%",
            },
        ],
    }


@pytest.fixture
def ha_plugin(mock_app, ha_config):
    return HomeAssistantPlugin(mock_app, ha_config)


def _mock_session(state_data, status=200):
    """Create a mock aiohttp session that returns state_data from GET."""
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=state_data)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = mock_resp
    session.get.return_value = ctx
    session.close = AsyncMock()
    return session


# -- Metadata --

def test_plugin_metadata(ha_plugin):
    assert ha_plugin.plugin_name == "homeassistant"


def test_commands_compiled(ha_plugin):
    assert len(ha_plugin._commands) == 3
    assert ha_plugin._commands[0].entity_id == "weather.home"


# -- Pattern matching --

@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["weather", "Weather", "WEATHER", " weather ", "forecast"])
async def test_matches_weather(ha_plugin, text):
    """Weather/forecast triggers the weather command."""
    ha_plugin._session = _mock_session({
        "state": "sunny",
        "attributes": {"temperature": 72, "temperature_unit": "F"},
        "entity_id": "weather.home",
    })

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_awaited_once_with(
        text="sunny — 72°F", channel=0, source_plugin="homeassistant"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["temp", "temperature", "Temperature", " TEMP "])
async def test_matches_temp(ha_plugin, text):
    """Temp/temperature triggers the temperature command."""
    ha_plugin._session = _mock_session({
        "state": "68",
        "attributes": {},
        "entity_id": "sensor.outdoor_temperature",
    })

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_awaited_once_with(
        text="Temp: 68°F", channel=0, source_plugin="homeassistant"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["hello", "what is the weather today", "temp check", "check temperature please"],
)
async def test_ignores_non_matching_messages(ha_plugin, text):
    """Messages that don't match any command pattern are ignored."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignores_non_channel_messages(ha_plugin):
    event = MeshEvent(
        event_type=EventType.CONTACT_MESSAGE,
        text="weather",
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignores_own_messages(ha_plugin):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="weather",
        channel=0,
        source_plugin="homeassistant",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignores_empty_text(ha_plugin):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=None,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


# -- First match wins --

@pytest.mark.asyncio
async def test_first_match_wins(mock_app):
    """When multiple patterns could match, only the first triggers."""
    config = {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "t",
        "commands": [
            {"pattern": "(?i)temp", "entity_id": "sensor.first", "response": "first: {state}"},
            {"pattern": "(?i)temperature", "entity_id": "sensor.second", "response": "second: {state}"},
        ],
    }
    plugin = HomeAssistantPlugin(mock_app, config)
    plugin._session = _mock_session({"state": "70", "attributes": {}})

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="temperature",
        channel=0,
        sender_name="TestNode",
    )
    await plugin.on_mesh_event(event)
    mock_app.broadcast.assert_awaited_once_with(
        text="first: 70", channel=0, source_plugin="homeassistant"
    )


# -- Response formatting --

def test_format_simple():
    result = HomeAssistantPlugin._format_response("{state}", {"state": "sunny", "attributes": {}})
    assert result == "sunny"


def test_format_nested_attributes():
    state = {"state": "sunny", "attributes": {"temperature": 72, "temperature_unit": "F"}}
    result = HomeAssistantPlugin._format_response(
        "{state} — {attributes[temperature]}°{attributes[temperature_unit]}", state
    )
    assert result == "sunny — 72°F"


def test_format_missing_key_graceful():
    state = {"state": "sunny", "attributes": {}}
    result = HomeAssistantPlugin._format_response("{state} {attributes[missing]}", state)
    assert "sunny" in result
    assert "missing" in result  # placeholder rather than crash


def test_format_default_response(mock_app):
    """Commands without a response template default to {state}."""
    config = {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "t",
        "commands": [
            {"pattern": "test", "entity_id": "sensor.test"},
        ],
    }
    plugin = HomeAssistantPlugin(mock_app, config)
    assert plugin._commands[0].response == "{state}"


# -- HA API error handling --

@pytest.mark.asyncio
async def test_api_error_does_not_broadcast(ha_plugin):
    """When HA returns an error, no message is broadcast."""
    ha_plugin._session = _mock_session(None, status=404)

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="weather",
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


# -- Channel forwarding --

@pytest.mark.asyncio
async def test_responds_on_correct_channel(ha_plugin):
    """Response is broadcast on the same channel the message came from."""
    ha_plugin._session = _mock_session({"state": "45", "attributes": {}})

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="humidity",
        channel=5,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_awaited_once_with(
        text="Humidity: 45%", channel=5, source_plugin="homeassistant"
    )
