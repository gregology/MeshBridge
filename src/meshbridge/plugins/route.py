"""Route/path/traceroute responder plugin for MeshBridge.

On receiving ``route``/``path``/``traceroute`` from a sender, issues a live
path discovery against that sender and replies with the discovered hops.
"""

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
    """Respond to route/path/traceroute messages with a live trace to the sender."""

    plugin_name = "route"
    plugin_version = "0.2.0"

    def __init__(self, app, config: dict) -> None:
        super().__init__(app, config)
        self._timeout: float = float(config.get("timeout", 30.0))

    async def start(self) -> None:
        self._logger.info("Route responder enabled (timeout=%.1fs)", self._timeout)

    async def stop(self) -> None:
        pass

    async def on_mesh_event(self, event: MeshEvent) -> None:
        if event.event_type not in (EventType.CHANNEL_MESSAGE, EventType.CONTACT_MESSAGE):
            return
        if not event.text or not _ROUTE_PATTERN.match(event.text):
            return
        if event.source_plugin == self.plugin_name:
            return

        key_or_name = event.sender_key_prefix or event.sender_name
        if not key_or_name:
            self._logger.info("Route query with no identifiable sender, ignoring")
            return

        if event.event_type == EventType.CONTACT_MESSAGE:
            sender = event.sender_name or event.sender_key_prefix
            self._logger.info("Route query DM from %s", sender)
        else:
            channel = event.channel or 0
            self._logger.info(
                "Route query from %s on ch%d", event.sender_name, channel
            )

        result = await self.request_trace(key_or_name, timeout=self._timeout)
        reply = _format_reply(result)

        if event.event_type == EventType.CONTACT_MESSAGE:
            await self.send_direct_to_mesh(
                reply,
                contact_name=event.sender_name or "",
                contact_key=event.sender_key_prefix or "",
            )
        else:
            await self.broadcast(reply, channel=event.channel or 0)


def _format_reply(result: dict) -> str:
    if not result or "error" in result:
        return f"trace failed: {result.get('error', 'unknown')}" if result else "trace failed"
    return str(result.get("path_text") or "trace failed")
