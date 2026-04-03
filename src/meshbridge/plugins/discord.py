"""Discord webhook plugin for MeshBridge."""

from __future__ import annotations

from typing import Any

import aiohttp

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import register_plugin


@register_plugin
class DiscordPlugin(BasePlugin):
    """Forward mesh events to Discord via webhook.

    Current: Webhook mode (mesh -> Discord, unidirectional).
    Future:  Bot mode (bidirectional, Discord -> mesh via discord.py).
    """

    plugin_name = "discord"
    plugin_version = "0.1.0"

    def __init__(self, app, config: dict) -> None:
        super().__init__(app, config)
        self._webhook_url: str = config.get("webhook_url", "")
        self._bot_username: str = config.get("bot_username", "MeshBridge")
        self._avatar_url: str = config.get("avatar_url", "")
        self._include_metadata: bool = config.get("include_metadata", True)
        self._channels: list[int] = config.get("channels", [])
        self._event_types: list[str] = config.get(
            "event_types", ["CHANNEL_MESSAGE", "CONTACT_MESSAGE"]
        )
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        if self._webhook_url:
            self._logger.info("Discord webhook mode enabled")
        else:
            self._logger.warning("Discord plugin enabled but no webhook_url configured")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def on_mesh_event(self, event: MeshEvent) -> None:
        """Forward matching mesh events to Discord."""
        if event.event_type.name not in self._event_types:
            return
        if self._channels and event.channel is not None and event.channel not in self._channels:
            return
        if event.source_plugin == self.plugin_name:
            return
        if self._webhook_url and self._session:
            await self._post_webhook(event)

    async def _post_webhook(self, event: MeshEvent) -> None:
        """Post a mesh event to Discord via webhook."""
        sender = event.sender_name or event.sender_key_prefix or "Unknown"

        if event.event_type == EventType.CHANNEL_MESSAGE:
            content = f"**{sender}**: {event.text}"
        elif event.event_type == EventType.CONTACT_MESSAGE:
            content = f"**[DM] {sender}**: {event.text}"
        elif event.event_type == EventType.TELEMETRY:
            content = f"**Telemetry from {event.node_name}**: ```json\n{event.telemetry}\n```"
        else:
            content = f"**{event.event_type.name}**: {event.text or event.raw}"

        webhook_data: dict[str, Any] = {
            "content": content,
            "username": self._bot_username,
        }
        if self._avatar_url:
            webhook_data["avatar_url"] = self._avatar_url

        if self._include_metadata and event.event_type in (
            EventType.CHANNEL_MESSAGE,
            EventType.CONTACT_MESSAGE,
        ):
            fields = []
            if event.path_len is not None:
                fields.append({"name": "Hops", "value": str(event.path_len), "inline": True})
            if event.channel is not None:
                fields.append({"name": "Channel", "value": str(event.channel), "inline": True})
            if event.sender_key_prefix:
                fields.append(
                    {"name": "Key", "value": event.sender_key_prefix, "inline": True}
                )
            if fields:
                webhook_data["embeds"] = [{"fields": fields, "color": 0x00B0F0}]

        try:
            async with self._session.post(self._webhook_url, json=webhook_data) as resp:
                if resp.status == 204:
                    self._logger.debug("Posted to Discord: %s", content[:80])
                elif resp.status == 429:
                    body = await resp.json()
                    retry_after = body.get("retry_after", 1)
                    self._logger.warning(
                        "Discord rate limited, retry after %ss", retry_after
                    )
                else:
                    self._logger.warning("Discord webhook error: %d", resp.status)
        except aiohttp.ClientError:
            self._logger.exception("Failed to post to Discord webhook")

    # -- Future: Bot mode (Discord -> mesh) --
    #
    # When bot_token is configured, start a discord.py client that watches
    # a specific channel. Messages from Discord users are formatted as
    # "[DiscordUsername] message text" and sent to the mesh via
    # self.send_to_mesh(). Messages are truncated to ~200 chars for LoRa.
