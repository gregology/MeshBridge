"""Route/path/traceroute responder plugin for MeshBridge."""

from __future__ import annotations

import re

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import register_plugin

_ROUTE_PATTERN = re.compile(
    r"^\s*(route|path|traceroute)\s*$",
    re.IGNORECASE,
)


@register_plugin
class RoutePlugin(BasePlugin):
    """Respond to route/path/traceroute messages with the message path."""

    plugin_name = "route"
    plugin_version = "0.1.0"

    async def start(self) -> None:
        self._logger.info("Route responder enabled")

    async def stop(self) -> None:
        pass

    async def on_mesh_event(self, event: MeshEvent) -> None:
        if event.event_type not in (EventType.CHANNEL_MESSAGE, EventType.CONTACT_MESSAGE):
            return
        if not event.text or not _ROUTE_PATTERN.match(event.text):
            return
        if event.source_plugin == self.plugin_name:
            return

        if event.path:
            reply = " > ".join(event.path)
        else:
            reply = "no path data"

        if event.event_type == EventType.CONTACT_MESSAGE:
            sender = event.sender_name or event.sender_key_prefix
            self._logger.info("Route query DM from %s", sender)
            await self.send_direct_to_mesh(
                reply,
                contact_name=event.sender_name or "",
                contact_key=event.sender_key_prefix or "",
            )
        else:
            channel = event.channel or 0
            self._logger.info("Route query from %s on ch%d", event.sender_name, channel)
            await self.broadcast(reply, channel=channel)
