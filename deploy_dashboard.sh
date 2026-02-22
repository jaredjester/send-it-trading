#!/bin/bash
#
# Deploy Web Dashboard to Pi
# Installs dependencies, starts dashboard server, exposes via ngrok/cloudflared
#

set -e

PI_HOST="jonathangan@192.168.12.44"
PI_PASS="Notraspberry123!"
STRATEGY_DIR="/home/jonathangan/shared/stockbot/strategy_v2"

echo "=========================================="
echo "WEB DASHBOARD DEPLOYMENT"
echo "=========================================="
echo ""

# Function to SSH
ssh_pi() {
    sshpass -p "$PI_PASS" ssh -o StrictHostKeyChecking=no "$PI_HOST" "$1"
}

# Function to SCP
scp_pi() {
    sshpass -p "$PI_PASS" scp -o StrictHostKeyChecking=no "$1" "$PI_HOST:$2"
}

# 1. Git pull latest
echo "[1/7] Pulling latest code..."
ssh_pi "cd $STRATEGY_DIR && git pull origin main"

# 2. Install Flask dependencies
echo "[2/7] Installing dependencies..."
ssh_pi "cd $STRATEGY_DIR && /home/jonathangan/shared/stockbot/bin/python -m pip install flask flask-cors python-dotenv"

# 3. Create templates directory
echo "[3/7] Setting up templates..."
ssh_pi "mkdir -p $STRATEGY_DIR/templates"

# 4. Test dashboard
echo "[4/7] Testing dashboard..."
TEST_OUTPUT=$(ssh_pi "cd $STRATEGY_DIR && /home/jonathangan/shared/stockbot/bin/python -c 'from dashboard_api import app; print(\"OK\")' 2>&1")

if [[ "$TEST_OUTPUT" == *"OK"* ]]; then
    echo "✓ Dashboard imports OK"
else
    echo "✗ Dashboard import failed:"
    echo "$TEST_OUTPUT"
    exit 1
fi

# 5. Create systemd service for dashboard
echo "[5/7] Creating dashboard service..."
cat > /tmp/dashboard.service << 'EOF'
[Unit]
Description=Trading Dashboard - Web API
After=network.target mybot.service

[Service]
ExecStart=/home/jonathangan/shared/stockbot/bin/python /home/jonathangan/shared/stockbot/strategy_v2/dashboard_api.py
WorkingDirectory=/home/jonathangan/shared/stockbot/strategy_v2
User=jonathangan
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=10
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

scp_pi "/tmp/dashboard.service" "/tmp/dashboard.service"
ssh_pi "sudo cp /tmp/dashboard.service /etc/systemd/system/dashboard.service"
ssh_pi "sudo systemctl daemon-reload"

# 6. Start dashboard
echo "[6/7] Starting dashboard..."
ssh_pi "sudo systemctl enable dashboard"
ssh_pi "sudo systemctl restart dashboard"
sleep 5

# 7. Verify
echo "[7/7] Verifying dashboard..."
STATUS=$(ssh_pi "systemctl is-active dashboard")

if [ "$STATUS" = "active" ]; then
    echo "✓ Dashboard is running"
    
    # Show logs
    echo ""
    echo "Recent logs:"
    echo "---"
    ssh_pi "journalctl -u dashboard -n 15 --no-pager | tail -10"
    echo ""
    
    # Check if responding
    sleep 3
    HTTP_TEST=$(ssh_pi "curl -s http://localhost:5555/api/health | head -5 || echo 'FAIL'")
    
    if [[ "$HTTP_TEST" != *"FAIL"* ]]; then
        echo "✓ Dashboard responding on port 5555"
        echo ""
        echo "Local access: http://192.168.12.44:5555"
        echo ""
    else
        echo "⚠️  Dashboard not responding to HTTP requests"
    fi
    
else
    echo "✗ Dashboard failed to start"
    echo ""
    echo "Service status:"
    ssh_pi "systemctl status dashboard --no-pager | head -30"
    exit 1
fi

echo "=========================================="
echo "✅ DASHBOARD DEPLOYED"
echo "=========================================="
echo ""
echo "Dashboard running on Pi port 5555"
echo ""
echo "Next steps:"
echo "  1. Expose via ngrok: ngrok http 5555"
echo "  2. OR cloudflared: cloudflared tunnel --url http://localhost:5555"
echo ""
echo "Local access: http://192.168.12.44:5555"
echo "Logs: journalctl -u dashboard -f"
echo ""
