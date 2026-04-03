"""Ping/pong responder plugin for MeshBridge."""

from __future__ import annotations

import re

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import register_plugin

# Patterns that trigger a pong response (matched case-insensitively)
_PING_PATTERN = re.compile(
    r"^\s*(ping|radio\s*check\??)\s*$",
    re.IGNORECASE,
)


@register_plugin
class PingPlugin(BasePlugin):
    """Respond to ping / radio check messages with pong."""

    plugin_name = "ping"
    plugin_version = "0.1.0"

    async def start(self) -> None:
        self._logger.info("Ping responder enabled")

    async def stop(self) -> None:
        pass

    async def on_mesh_event(self, event: MeshEvent) -> None:
        if event.event_type != EventType.CHANNEL_MESSAGE:
            return
        if not event.text or not _PING_PATTERN.match(event.text):
            return
        if event.source_plugin == self.plugin_name:
            return

        channel = event.channel or 0
        self._logger.info("Ping from %s on ch%d, sending pong", event.sender_name, channel)
        await self.broadcast("pong", channel=channel)
