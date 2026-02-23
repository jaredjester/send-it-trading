#!/bin/bash
#
# Setup Public Dashboard Access via ngrok
# Creates a systemd service that auto-starts ngrok for dashboard
# Saves public URL to ~/dashboard_url.txt
#

set -e

echo "=========================================="
echo "PUBLIC DASHBOARD SETUP"
echo "=========================================="
echo ""

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo "❌ ngrok is not installed"
    echo ""
    echo "Install ngrok:"
    echo "  wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz"
    echo "  tar -xvzf ngrok-v3-stable-linux-arm64.tgz"
    echo "  sudo mv ngrok /usr/local/bin/"
    echo "  ngrok config add-authtoken YOUR_TOKEN"
    exit 1
fi

echo "✓ ngrok found at $(which ngrok)"
echo ""

# Create systemd service for ngrok dashboard
echo "[1/4] Creating ngrok-dashboard service..."

sudo tee /etc/systemd/system/ngrok-dashboard.service > /dev/null <<'EOF'
[Unit]
Description=ngrok tunnel for trading dashboard
After=network.target dashboard.service

[Service]
ExecStart=/usr/local/bin/ngrok http 5555 --log=stdout
Restart=always
RestartSec=10
User=jonathangan
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "✓ Service file created"
echo ""

# Reload systemd
echo "[2/4] Reloading systemd..."
sudo systemctl daemon-reload
echo "✓ Systemd reloaded"
echo ""

# Enable and start service
echo "[3/4] Starting ngrok-dashboard service..."
sudo systemctl enable ngrok-dashboard
sudo systemctl restart ngrok-dashboard

sleep 10
echo "✓ Service started"
echo ""

# Get public URL
echo "[4/4] Getting public URL..."
sleep 5

# Try to get URL from ngrok API
PUBLIC_URL=$(curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"https://[^"]*"' | head -1 | cut -d'"' -f4)

if [ -n "$PUBLIC_URL" ]; then
    echo "✓ Public URL obtained"
    echo ""
    echo "$PUBLIC_URL" > ~/dashboard_url.txt
    
    echo "=========================================="
    echo "✅ PUBLIC DASHBOARD SETUP COMPLETE"
    echo "=========================================="
    echo ""
    echo "Your public dashboard URL:"
    echo ""
    echo "  $PUBLIC_URL"
    echo ""
    echo "This URL is saved to: ~/dashboard_url.txt"
    echo ""
    echo "Notes:"
    echo "  • URL is permanent (as long as service runs)"
    echo "  • Auto-starts on boot"
    echo "  • Access from anywhere"
    echo ""
    echo "Commands:"
    echo "  • Check status: systemctl status ngrok-dashboard"
    echo "  • View logs: journalctl -u ngrok-dashboard -f"
    echo "  • Get URL: cat ~/dashboard_url.txt"
    echo "  • Restart: sudo systemctl restart ngrok-dashboard"
    echo ""
else
    echo "⚠️  Could not get public URL"
    echo ""
    echo "Service is running, but URL not ready yet."
    echo "Wait 30 seconds and run:"
    echo ""
    echo "  curl -s http://localhost:4040/api/tunnels | grep public_url"
    echo ""
    echo "Or check logs:"
    echo "  journalctl -u ngrok-dashboard -n 50"
fi

echo ""
echo "=========================================="
