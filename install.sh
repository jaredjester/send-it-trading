#!/usr/bin/env bash
# install.sh — One-command setup for send-it-trading
# Usage: bash install.sh
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "📦 Setting up send-it-trading in: $REPO_DIR"

# ─── 1. Python detection ──────────────────────────────────────────────────
if [ -f "$REPO_DIR/venv/bin/python" ]; then
    PYTHON="$REPO_DIR/venv/bin/python"
    echo "✅ Using existing venv: $PYTHON"
else
    PYTHON="$(which python3)"
    echo "🐍 Using system python3: $PYTHON"
fi

# ─── 2. Create venv if it doesn't exist ───────────────────────────────────
if [ ! -f "$REPO_DIR/venv/bin/python" ]; then
    echo "🔧 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ venv created"
fi

# ─── 3. Install requirements ──────────────────────────────────────────────
echo "📥 Installing requirements..."
"$REPO_DIR/venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
echo "✅ Requirements installed"

# ─── 4. Create runtime directories ────────────────────────────────────────
echo "📁 Creating runtime directories..."
for dir in data engine/state engine/logs engine/evaluation logs; do
    mkdir -p "$REPO_DIR/$dir"
    touch "$REPO_DIR/$dir/.gitkeep"
done
echo "✅ Directories ready"

# ─── 5. Copy .env.example → .env if not present ───────────────────────────
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    echo "✅ .env created from .env.example — ⚠️  Edit it before starting services!"
else
    echo "ℹ️  .env already exists, skipping copy"
fi

# ─── 6. Write systemd service files to /tmp/send-it-services/ ─────────────
SERVICES_OUT="/tmp/send-it-services"
mkdir -p "$SERVICES_OUT"

USERNAME="$(whoami)"

# bot service
cat > "$SERVICES_OUT/send-it-bot.service" <<EOF
[Unit]
Description=Send It Trading — Intelligence Bot (news/insider/polymarket scanners)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/venv/bin/python $REPO_DIR/bot/main.py
Restart=on-failure
RestartSec=30
EnvironmentFile=$REPO_DIR/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=send-it-bot

[Install]
WantedBy=multi-user.target
EOF

# engine service
cat > "$SERVICES_OUT/send-it-engine.service" <<EOF
[Unit]
Description=Send It Trading — Options Engine (alpha scoring + execution)
After=network.target send-it-bot.service

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=$REPO_DIR
ExecStartPre=/bin/sleep 15
ExecStart=$REPO_DIR/venv/bin/python $REPO_DIR/engine/main_wrapper_simple.py
Restart=always
RestartSec=10
EnvironmentFile=$REPO_DIR/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=send-it-engine
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# dashboard service
cat > "$SERVICES_OUT/send-it-dashboard.service" <<EOF
[Unit]
Description=Send It Trading — Live Dashboard
After=network.target

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/venv/bin/python $REPO_DIR/dashboard/api.py
Restart=always
RestartSec=10
EnvironmentFile=$REPO_DIR/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=send-it-dashboard

[Install]
WantedBy=multi-user.target
EOF

echo "✅ systemd service files written to $SERVICES_OUT"

# ─── 7. Done ──────────────────────────────────────────────────────────────
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env — add your ALPACA_LIVE_KEY and ALPACA_LIVE_SECRET"
echo ""
echo "  2. Install services (optional):"
echo "     sudo cp /tmp/send-it-services/*.service /etc/systemd/system/"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable --now send-it-bot send-it-engine send-it-dashboard"
echo ""
echo "  3. Or run manually:"
echo "     ./venv/bin/python bot/main.py &"
echo "     ./venv/bin/python engine/main_wrapper_simple.py &"
echo "     ./venv/bin/python dashboard/api.py"
echo ""
echo "  Dashboard: http://localhost:5555"
