"""Discord plugin for MeshBridge (webhook + bot mode)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import register_plugin


@register_plugin
class DiscordPlugin(BasePlugin):
    """Bidirectional Discord integration.

    Webhook mode: mesh/bridge events -> Discord (via webhook URL).
    Bot mode:     Discord messages -> dispatch into MeshBridge event pipeline.
    Both modes can run simultaneously.
    """

    plugin_name = "discord"
    plugin_version = "0.2.0"

    def __init__(self, app, config: dict) -> None:
        super().__init__(app, config)
        self._webhook_url: str = config.get("webhook_url", "")
        self._bot_username: str = config.get("bot_username", "MeshBridge")
        self._avatar_url: str = config.get("avatar_url", "")
        self._include_metadata: bool = config.get("include_metadata", True)
        self._channels: list[int] = config.get("channels", [])
        self._event_types: list[str] = config.get("event_types", ["CHANNEL_MESSAGE"])
        self._session: aiohttp.ClientSession | None = None

        # Bot mode
        self._bot_token: str = config.get("bot_token", "")
        self._bot_channel_id: int | None = (
            int(config["bot_channel_id"]) if config.get("bot_channel_id") else None
        )
        self._bot_client: Any = None  # discord.Client, lazy-imported
        self._bot_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        if self._webhook_url:
            self._logger.info("Discord webhook mode enabled")
        else:
            self._logger.warning("Discord plugin enabled but no webhook_url configured")

        if self._bot_token and self._bot_channel_id:
            self._bot_task = asyncio.create_task(self._start_bot())
        elif self._bot_token:
            self._logger.warning("bot_token set but bot_channel_id missing, skipping bot mode")

    async def stop(self) -> None:
        if self._bot_client and not self._bot_client.is_closed():
            await self._bot_client.close()
        if self._bot_task:
            self._bot_task.cancel()
            self._bot_task = None
        if self._session:
            await self._session.close()
            self._session = None

    async def on_mesh_event(self, event: MeshEvent) -> None:
        """Forward matching events to Discord."""
        if event.event_type.name not in self._event_types:
            return
        if self._channels and event.channel is not None and event.channel not in self._channels:
            return
        if event.source_plugin == self.plugin_name:
            return
        if self._webhook_url and self._session:
            await self._post_webhook(event)

    # -- Bot mode --

    async def _start_bot(self) -> None:
        """Start the discord.py bot client."""
        try:
            import discord
        except ImportError:
            self._logger.error(
                "discord.py is required for bot mode: pip install meshbridge[discord-bot]"
            )
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self._bot_client = discord.Client(intents=intents)

        plugin = self  # capture for closure

        @self._bot_client.event
        async def on_ready():
            plugin._logger.info("Discord bot connected as %s", plugin._bot_client.user)

        @self._bot_client.event
        async def on_message(message):
            # Ignore our own messages
            if message.author == plugin._bot_client.user:
                return
            # Only listen to the configured channel
            if message.channel.id != plugin._bot_channel_id:
                return

            event = MeshEvent(
                event_type=EventType.CHANNEL_MESSAGE,
                source="discord",
                text=message.content,
                channel=0,
                sender_name=message.author.display_name,
                source_plugin=plugin.plugin_name,
            )
            await plugin._app.dispatch_event(event)

        self._logger.info("Starting Discord bot...")
        try:
            await self._bot_client.start(self._bot_token)
        except Exception:
            self._logger.exception("Discord bot failed")

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
                fields.append({"name": "Key", "value": event.sender_key_prefix, "inline": True})
            if fields:
                webhook_data["embeds"] = [{"fields": fields, "color": 0x00B0F0}]

        try:
            async with self._session.post(self._webhook_url, json=webhook_data) as resp:
                if resp.status == 204:
                    self._logger.debug("Posted to Discord: %s", content[:80])
                elif resp.status == 429:
                    body = await resp.json()
                    retry_after = body.get("retry_after", 1)
                    self._logger.warning("Discord rate limited, retry after %ss", retry_after)
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
