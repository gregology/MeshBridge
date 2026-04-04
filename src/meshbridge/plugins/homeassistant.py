"""Home Assistant plugin for MeshBridge.

Responds to configurable regex-matched mesh messages by querying
Home Assistant entity states and broadcasting formatted responses.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Any

import aiohttp

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import register_plugin


@dataclass
class Command:
    """A compiled regex -> HA entity mapping."""

    pattern: re.Pattern[str]
    entities: dict[str, str]
    response: str


class _AttrDict(dict):
    """Dict subclass that supports format_map ``{attributes[key]}`` access."""

    def __getitem__(self, key: str) -> Any:
        value = super().__getitem__(key)
        if isinstance(value, dict):
            return _AttrDict(value)
        return value


class _StateFormatter(string.Formatter):
    """Formatter that gracefully handles missing keys."""

    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs.get(key, f"<{key}?>")
        return super().get_value(key, args, kwargs)

    def get_field(self, field_name, args, kwargs):
        try:
            return super().get_field(field_name, args, kwargs)
        except (KeyError, TypeError, AttributeError):
            return (f"<{field_name}?>", field_name)

    def format_field(self, value, format_spec):
        if format_spec and isinstance(value, str):
            try:
                value = float(value)
            except (ValueError, TypeError):
                format_spec = ""
        return super().format_field(value, format_spec)


_formatter = _StateFormatter()


@register_plugin
class HomeAssistantPlugin(BasePlugin):
    """Query Home Assistant entities in response to mesh messages.

    Commands are defined in config as regex -> entity_id -> response template
    mappings.  The response template is formatted with the HA state object
    fields: ``state``, ``attributes``, ``entity_id``, ``last_changed``, etc.
    """

    plugin_name = "homeassistant"
    plugin_version = "0.1.0"

    def __init__(self, app, config: dict) -> None:
        super().__init__(app, config)
        self._url: str = config["url"].rstrip("/")
        self._token: str = config["token"]
        self._commands: list[Command] = []
        for cmd in config.get("commands", []):
            self._commands.append(
                Command(
                    pattern=re.compile(cmd["pattern"]),
                    entities=cmd["entities"],
                    response=cmd.get("response", "{state}"),
                )
            )
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._logger.info(
            "Home Assistant plugin enabled (%d commands, %s)",
            len(self._commands),
            self._url,
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def on_mesh_event(self, event: MeshEvent) -> None:
        if event.event_type != EventType.CHANNEL_MESSAGE:
            return
        if not event.text:
            return
        if event.source_plugin == self.plugin_name:
            return

        for cmd in self._commands:
            if cmd.pattern.search(event.text):
                context = await self._fetch_entities(cmd.entities)
                if context is None:
                    return
                reply = self._format_response(cmd.response, context)
                await self.broadcast(reply, channel=event.channel or 0)
                return  # first match wins

    async def _fetch_entities(self, entities: dict[str, str]) -> dict[str, Any] | None:
        """Fetch multiple entity states and return as a named dict."""
        context: dict[str, Any] = {}
        for name, entity_id in entities.items():
            state = await self._get_state(entity_id)
            if state is None:
                self._logger.warning("Failed to fetch entity %s", entity_id)
                return None
            context[name] = state
        return context

    async def _get_state(self, entity_id: str) -> dict[str, Any] | None:
        """Fetch entity state from the Home Assistant REST API."""
        if not self._session:
            return None
        headers = {
            "Authorization": f"Bearer {self._token}",
        }
        try:
            async with self._session.get(
                f"{self._url}/api/states/{entity_id}",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                self._logger.warning(
                    "HA API returned %d for %s", resp.status, entity_id
                )
        except aiohttp.ClientError:
            self._logger.exception("Failed to reach Home Assistant at %s", self._url)
        return None

    @staticmethod
    def _format_response(template: str, state: dict[str, Any]) -> str:
        """Format a response template with HA state data."""
        context = _AttrDict(state)
        return _formatter.format(template, **context)
