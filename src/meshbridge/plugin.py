"""Base class for MeshBridge plugins."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meshbridge.app import App
    from meshbridge.events import MeshEvent


class BasePlugin(ABC):
    """Abstract base class for MeshBridge plugins.

    Plugins are bidirectional: they receive events FROM the mesh network
    and can send messages TO the mesh network.

    Lifecycle:
        1. ``__init__(app, config)`` -- store references
        2. ``start()`` -- connect to external services
        3. ``on_mesh_event(event)`` -- handle each mesh event
        4. ``stop()`` -- disconnect and clean up
    """

    plugin_name: str = "base"
    plugin_version: str = "0.1.0"

    def __init__(self, app: App, config: dict) -> None:
        self._app = app
        self._config = config
        self._logger = logging.getLogger(f"meshbridge.plugins.{self.plugin_name}")

    @abstractmethod
    async def start(self) -> None:
        """Initialize plugin resources (HTTP sessions, bot connections, etc)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Tear down plugin resources. Called during graceful shutdown."""
        ...

    @abstractmethod
    async def on_mesh_event(self, event: MeshEvent) -> None:
        """Handle an event originating from the mesh network."""
        ...

    async def send_to_mesh(self, text: str, channel: int = 0) -> None:
        """Send a channel message to the mesh network."""
        await self._app.send_to_mesh(
            text=text,
            channel=channel,
            source_plugin=self.plugin_name,
        )

    async def send_direct_to_mesh(self, text: str, contact_name: str) -> None:
        """Send a direct message to a specific mesh contact."""
        await self._app.send_direct_to_mesh(
            text=text,
            contact_name=contact_name,
            source_plugin=self.plugin_name,
        )

    @property
    def config(self) -> dict:
        return self._config
