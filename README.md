# MeshBridge

Bridge MeshCore mesh radio devices to Discord (and more) via MQTT.

## Architecture

```
MeshCore Device (USB Companion)
    ↕ Serial (bidirectional)
MeshBridge
    ↕ MQTT
Mosquitto Broker ← external tools (MQTT Explorer, Home Assistant, etc.)
    ↕ MQTT
Plugins
    ├── Discord (webhook / future: bot)
    ├── Slack (planned)
    └── ...
```

MeshBridge uses MQTT as its internal message bus. All mesh events are published
to MQTT topics, making them available to both built-in plugins and external
tools. Plugins can also send messages back to the mesh network.

## Quick Install (Raspberry Pi)

```bash
curl -fsSL https://raw.githubusercontent.com/gregology/MeshBridge/refs/heads/main/install.sh | sudo bash
```

This will:
1. Install system dependencies (Mosquitto, Python 3)
2. Create a Python virtual environment
3. Install MeshBridge
4. Configure Mosquitto with auto-generated credentials
5. Run the interactive setup wizard
6. Install and enable a systemd service

## Prerequisites

- Raspberry Pi (any model with USB port) running Raspberry Pi OS (Bookworm+)
- MeshCore device flashed as **USB Companion**
- Python 3.11+
- Discord server with webhook access (for the Discord plugin)

## Manual Installation

```bash
git clone https://github.com/gregology/MeshBridge.git
cd MeshBridge
python3 -m venv .venv
.venv/bin/pip install .

# Run the setup wizard
.venv/bin/meshbridge setup

# Start the bridge
.venv/bin/meshbridge run
```

## Configuration

MeshBridge uses a YAML config file. The setup wizard generates one, or you
can copy and edit the example:

```bash
cp config.example.yaml config.yaml
```

Config file search order:
1. `./config.yaml`
2. `~/.config/meshbridge/config.yaml`
3. `/etc/meshbridge/config.yaml`

Or specify explicitly: `meshbridge run -c /path/to/config.yaml`

See [config.example.yaml](config.example.yaml) for all options.

## CLI Usage

```bash
meshbridge run              # Start the bridge
meshbridge run --debug      # Start with debug logging
meshbridge setup            # Interactive setup wizard
meshbridge status           # Show systemd service status
meshbridge logs -f          # Follow live logs
```

## MQTT Topics

All topics are prefixed with the configured `topic_prefix` (default: `meshbridge`).

| Topic | Direction | Description |
|-------|-----------|-------------|
| `meshbridge/inbound/channel/{idx}` | Mesh → MQTT | Channel messages |
| `meshbridge/inbound/direct/{key}` | Mesh → MQTT | Direct messages |
| `meshbridge/inbound/telemetry/{node}` | Mesh → MQTT | Telemetry data |
| `meshbridge/inbound/node/online` | Mesh → MQTT | Node advertisements |
| `meshbridge/outbound/channel/{idx}` | MQTT → Mesh | Send to channel |
| `meshbridge/outbound/direct/{contact}` | MQTT → Mesh | Send direct message |
| `meshbridge/status/bridge` | Status | Bridge online/offline |

## Plugins

### Discord

Forwards mesh messages to a Discord channel via webhook. Messages appear as:

> **SenderName**: Hello from the mesh!

With optional metadata embeds showing hop count, channel, and sender key.

**Setup:** Create a webhook in Discord (Server Settings > Integrations > Webhooks)
and add the URL to your config.

### Writing a Plugin

Create a new file in `src/meshbridge/plugins/` implementing `BasePlugin`:

```python
from meshbridge.events import EventType, MeshEvent
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import register_plugin

@register_plugin
class MyPlugin(BasePlugin):
    plugin_name = "myplugin"
    plugin_version = "0.1.0"

    async def start(self):
        self._logger.info("MyPlugin started")

    async def stop(self):
        self._logger.info("MyPlugin stopped")

    async def on_mesh_event(self, event: MeshEvent):
        if event.event_type == EventType.CHANNEL_MESSAGE:
            self._logger.info("Got message: %s", event.text)

        # Send a message back to the mesh:
        # await self.send_to_mesh("Reply from MyPlugin", channel=0)
```

Then add a section to your config:

```yaml
plugins:
  myplugin:
    enabled: true
```

## Service Management

```bash
sudo systemctl start meshbridge      # Start
sudo systemctl stop meshbridge       # Stop
sudo systemctl restart meshbridge    # Restart
sudo systemctl status meshbridge     # Status
journalctl -u meshbridge -f          # Follow logs
```

## Troubleshooting

### Device not found

```bash
# Check if MeshCore device is connected
ls -la /dev/serial/by-id/

# Ensure serial port permissions
sudo usermod -aG dialout $USER
# Log out and back in
```

### MQTT connection issues

```bash
# Check Mosquitto is running
sudo systemctl status mosquitto

# Test MQTT locally
mosquitto_sub -h 127.0.0.1 -u meshbridge -P yourpassword -t "meshbridge/#" -v
```

### Discord not receiving messages

```bash
# Test webhook manually
curl -X POST "YOUR_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{"content": "Test message"}'
```

## Development

```bash
git clone https://github.com/gregology/MeshBridge.git
cd MeshBridge
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Resources

- [MeshCore](https://github.com/meshcore-dev/MeshCore)
- [MeshCore Python Library](https://github.com/meshcore-dev/meshcore_py)
- [Mosquitto](https://mosquitto.org/documentation/)
- [Discord Webhooks](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks)
