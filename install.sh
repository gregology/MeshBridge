#!/usr/bin/env bash
# MeshBridge Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/gregology/MeshBridge/refs/heads/main/install.sh | sudo bash
set -euo pipefail

REPO="https://github.com/gregology/MeshBridge.git"
INSTALL_DIR="/opt/meshbridge"
VENV_DIR="$INSTALL_DIR/.venv"
CONFIG_DIR="/etc/meshbridge"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
LOG_DIR="/var/log/meshbridge"
SERVICE_FILE="/etc/systemd/system/meshbridge.service"
MESHBRIDGE_USER="meshbridge"

# -- Helpers --

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; exit 1; }

# -- 1. Pre-flight checks --

echo ""
echo "================================="
echo "  MeshBridge Installer"
echo "================================="
echo ""

[[ $EUID -eq 0 ]] || error "Please run as root: curl ... | sudo bash"
command -v python3 >/dev/null || error "Python 3 not found"

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)"; then
    info "Python $PYTHON_VERSION detected"
else
    error "Python 3.11+ required (found $PYTHON_VERSION)"
fi

# -- 2. System dependencies --

info "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git mosquitto mosquitto-clients

# -- 3. Create meshbridge user --

if ! id "$MESHBRIDGE_USER" &>/dev/null; then
    info "Creating system user: $MESHBRIDGE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$MESHBRIDGE_USER"
fi
# Ensure dialout group membership for serial port access
usermod -aG dialout "$MESHBRIDGE_USER"

# -- 4. Clone or update repository --

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing installation..."
    git config --global --add safe.directory "$INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "Cloning MeshBridge..."
    git clone "$REPO" "$INSTALL_DIR"
fi

# -- 5. Create virtual environment and install --

info "Setting up Python virtual environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet "$INSTALL_DIR"
info "MeshBridge installed: $("$VENV_DIR/bin/meshbridge" --help | head -1)"

# -- 6. Create directories --

mkdir -p "$CONFIG_DIR" "$LOG_DIR"
chown "$MESHBRIDGE_USER:$MESHBRIDGE_USER" "$LOG_DIR"

# -- 7. Configure Mosquitto --

MOSQUITTO_CONF="/etc/mosquitto/conf.d/meshbridge.conf"
MQTT_PASS=""

if [[ ! -f "$MOSQUITTO_CONF" ]]; then
    info "Configuring Mosquitto..."
    MQTT_PASS=$(openssl rand -base64 12)

    # Detect existing Mosquitto config to avoid duplicate directives
    MAIN_CONF="/etc/mosquitto/mosquitto.conf"
    EXISTING_PASSFILE=$(grep -s '^password_file' "$MAIN_CONF" | tail -1 | awk '{print $2}')
    HAS_LISTENER=$(grep -qs '^listener' "$MAIN_CONF" && echo "yes" || echo "")
    HAS_ANON=$(grep -qs '^allow_anonymous' "$MAIN_CONF" && echo "yes" || echo "")

    if [[ -n "$EXISTING_PASSFILE" ]]; then
        # Append meshbridge user to existing password file
        MQTT_PASSFILE="$EXISTING_PASSFILE"
        PASSWD_CREATE_FLAG="-b"
    else
        # Create a new password file
        MQTT_PASSFILE="/etc/mosquitto/meshbridge_passwd"
        PASSWD_CREATE_FLAG="-b -c"
    fi

    # Only write directives that aren't already in mosquitto.conf
    {
        echo "# MeshBridge MQTT configuration (auto-generated)"
        [[ -z "$HAS_LISTENER" ]] && echo "listener 1883 127.0.0.1"
        [[ -z "$HAS_ANON" ]] && echo "allow_anonymous false"
        [[ -z "$EXISTING_PASSFILE" ]] && echo "password_file $MQTT_PASSFILE"
    } > "$MOSQUITTO_CONF"

    mosquitto_passwd $PASSWD_CREATE_FLAG "$MQTT_PASSFILE" meshbridge "$MQTT_PASS"
    chown mosquitto:mosquitto "$MQTT_PASSFILE"
    chmod 600 "$MQTT_PASSFILE"
    systemctl restart mosquitto
    info "Mosquitto configured (user added to $MQTT_PASSFILE)"
else
    warn "Mosquitto already configured, skipping"
fi

# -- 8. Run setup wizard --

if [[ ! -f "$CONFIG_FILE" ]]; then
    info "Running setup wizard..."
    MESHBRIDGE_MQTT_PASS="$MQTT_PASS" "$VENV_DIR/bin/meshbridge" setup -c "$CONFIG_FILE"
else
    warn "Config already exists at $CONFIG_FILE, skipping wizard"
fi

chown -R "$MESHBRIDGE_USER:$MESHBRIDGE_USER" "$INSTALL_DIR" "$CONFIG_DIR"

# -- 9. Install systemd service --

info "Installing systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=MeshBridge - MeshCore Mesh Radio Bridge
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=$MESHBRIDGE_USER
Group=$MESHBRIDGE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/meshbridge run -c $CONFIG_FILE
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=meshbridge

# Hardening
ProtectSystem=strict
ReadWritePaths=$LOG_DIR $CONFIG_DIR
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable meshbridge.service

# -- 10. Done --

echo ""
info "Installation complete!"
echo ""
echo "  Config:       $CONFIG_FILE"
echo "  Service:      meshbridge.service"
echo "  Logs:         journalctl -u meshbridge -f"
echo ""
echo "  Start now:    sudo systemctl start meshbridge"
echo "  Edit config:  sudo nano $CONFIG_FILE"
echo "  Restart:      sudo systemctl restart meshbridge"
echo ""
