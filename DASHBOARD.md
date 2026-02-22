# 🌐 Live Web Dashboard

**Real-time portfolio monitoring via web browser.**

Access your trading bot's status, positions, and activity from any device.

---

## 🚀 Quick Deploy

### Option 1: Automatic Deployment (Recommended)

```bash
# From Mac
cd ~/.openclaw/workspace/strategy-v2
chmod +x deploy_dashboard.sh
./deploy_dashboard.sh
```

**This will:**
1. ✅ Pull latest code to Pi
2. ✅ Install Flask dependencies
3. ✅ Create systemd service
4. ✅ Start dashboard on port 5555
5. ✅ Verify it's working

**Time:** 2 minutes

---

### Option 2: Manual Deployment

```bash
# SSH to Pi
ssh jonathangan@192.168.12.44

# Go to strategy directory
cd ~/shared/stockbot/strategy_v2

# Install dependencies
~/shared/stockbot/bin/python -m pip install flask flask-cors python-dotenv

# Test dashboard
~/shared/stockbot/bin/python dashboard_api.py
# Should see: "Starting on http://0.0.0.0:5555"
# Press Ctrl+C to stop

# Create systemd service
sudo nano /etc/systemd/system/dashboard.service
```

**Paste this:**
```ini
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
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable dashboard
sudo systemctl start dashboard

# Check status
systemctl status dashboard

# View logs
journalctl -u dashboard -f
```

---

## 🔓 Expose to Internet

### Option 1: ngrok (Easiest)

**If ngrok installed:**
```bash
# On Pi
ngrok http 5555

# Output will show public URL:
# Forwarding  https://abc-123-def.ngrok-free.app -> http://localhost:5555

# Use that URL to access dashboard from anywhere
```

**Install ngrok (if needed):**
```bash
# Download
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz
tar -xvzf ngrok-v3-stable-linux-arm64.tgz
sudo mv ngrok /usr/local/bin/

# Auth (get token from ngrok.com)
ngrok config add-authtoken YOUR_TOKEN_HERE

# Run
ngrok http 5555
```

**Ngrok Free Tier:**
- ✅ 1 online ngrok process
- ✅ 40 connections/min
- ✅ HTTPS included
- ✅ Permanent URL (with account)

---

### Option 2: cloudflared (More Permanent)

**If cloudflared installed:**
```bash
# On Pi
cloudflared tunnel --url http://localhost:5555

# Output will show public URL:
# https://abc-123.trycloudflare.com
```

**Or create permanent tunnel:**
```bash
# Create tunnel
cloudflared tunnel create dashboard

# Configure
nano ~/.cloudflared/config.yml
```

**Paste:**
```yaml
tunnel: dashboard
credentials-file: /home/jonathangan/.cloudflared/credentials.json

ingress:
  - hostname: dashboard.yourdomain.com
    service: http://localhost:5555
  - service: http_status:404
```

**Run:**
```bash
cloudflared tunnel run dashboard
```

**Install cloudflared (if needed):**
```bash
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
chmod +x cloudflared-linux-arm64
sudo mv cloudflared-linux-arm64 /usr/local/bin/cloudflared
```

---

### Option 3: Port Forward (Most Permanent)

**Configure router to forward port 5555 to Pi.**

**Steps:**
1. Log into router admin (usually 192.168.1.1)
2. Find "Port Forwarding" settings
3. Add rule:
   - External port: 5555
   - Internal IP: 192.168.12.44
   - Internal port: 5555
   - Protocol: TCP
4. Save

**Access via:**
- `http://YOUR_PUBLIC_IP:5555`
- Get public IP: `curl ifconfig.me`

**⚠️ Security Warning:**
- No authentication by default
- Anyone with URL can view dashboard
- Recommend ngrok/cloudflared for security

---

## 📊 Dashboard Features

### Real-Time Data (Updates every 10 seconds)

**Portfolio Summary:**
- Total value
- Cash balance
- Buying power
- Total P/L ($ and %)
- Position count

**Positions Table:**
- Symbol
- Quantity
- Market value
- Entry price
- Current price
- Unrealized P/L ($ and %)

**Conviction Positions:**
- Thesis description
- Entry price
- Max pain level
- Support level
- Deadline

**Bot Status:**
- Service running (🟢/🔴)
- Last activity time
- Connection status

**Recent Activity:**
- Live orchestrator logs
- Last 100 lines
- Auto-scroll to bottom

---

## 🎨 UI Features

**Modern Design:**
- ✅ Dark theme (hacker aesthetic)
- ✅ Green glow effects
- ✅ Animated status indicators
- ✅ Responsive (works on phone)
- ✅ Auto-refresh
- ✅ Hover effects
- ✅ Color-coded P/L (green/red)

**Live Updates:**
- ✅ Portfolio data every 10 sec
- ✅ Bot status every 10 sec
- ✅ Logs every 10 sec
- ✅ Connection indicator
- ✅ Timestamp display

---

## 🔧 Troubleshooting

### Dashboard won't start

```bash
# Check service status
systemctl status dashboard

# View logs
journalctl -u dashboard -n 50

# Common issues:
# 1. Flask not installed
~/shared/stockbot/bin/python -m pip install flask flask-cors

# 2. Port in use
sudo lsof -i :5555
# Kill process if needed

# 3. Python path wrong
which python3
# Update ExecStart in service file
```

### Can't access from browser

```bash
# Test locally on Pi
curl http://localhost:5555/api/health

# Should return JSON:
# {"status":"ok","timestamp":"..."}

# If works locally but not externally:
# - Check firewall
# - Check ngrok/cloudflared running
# - Check port forward settings
```

### Dashboard shows old data

```bash
# Check if bot is running
systemctl status mybot

# Check if Alpaca connected
journalctl -u dashboard -n 20 | grep "Alpaca"

# Should see:
# "Alpaca connected: True"
```

### Logs not updating

```bash
# Check if orchestrator is writing logs
ls -lh ~/shared/stockbot/strategy_v2/logs/orchestrator.log

# Should show recent timestamp
# If old, bot may not be running cycles
```

---

## 🔒 Security

### Current Setup (No Auth)

**Dashboard has NO authentication by default.**

**Risk:**
- Anyone with URL can view portfolio
- No password required
- Data is visible

**Mitigation:**
- Use ngrok (random URL, hard to guess)
- Don't share URL publicly
- Monitor access logs

### Add Basic Auth (Optional)

```python
# Add to dashboard_api.py (top of file)
from functools import wraps
from flask import request, Response

def check_auth(username, password):
    return username == 'admin' and password == 'your_password_here'

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                'Login required', 401,
                {'WWW-Authenticate': 'Basic realm="Login Required"'}
            )
        return f(*args, **kwargs)
    return decorated

# Then add @requires_auth above routes:
@app.route('/')
@requires_auth
def index():
    return render_template('live_dashboard.html')
```

---

## 📱 Mobile Access

**Dashboard is responsive and works on:**
- ✅ iPhone/iPad
- ✅ Android phones/tablets
- ✅ Desktop browsers
- ✅ Any device with browser

**Just open the ngrok/cloudflared URL on your phone.**

---

## 🎯 API Endpoints

### `/api/health`
```json
{
  "status": "ok",
  "timestamp": "2026-02-22T15:30:00",
  "alpaca_connected": true
}
```

### `/api/portfolio`
```json
{
  "portfolio_value": 372.15,
  "cash": 57.28,
  "buying_power": 228.84,
  "total_pl": -12.34,
  "total_pl_pct": -3.21,
  "positions": [...],
  "position_count": 12,
  "timestamp": "..."
}
```

### `/api/convictions`
```json
{
  "convictions": {
    "GME": {
      "thesis": "Acquisition catalyst",
      "entry_price": 24.89,
      "max_pain_price": 10.0,
      "support_price": 15.0
    }
  }
}
```

### `/api/status`
```json
{
  "service_running": true,
  "log_age_minutes": 2,
  "last_activity": "Active"
}
```

### `/api/logs`
```json
{
  "logs": [
    "2026-02-22 15:30:00 - Starting cycle",
    "2026-02-22 15:30:05 - Portfolio check complete",
    ...
  ]
}
```

---

## 📈 Next Features (Optional)

**Could add later:**
- Charts (P/L over time)
- Trade history
- Performance metrics (Sharpe, win rate)
- Email/SMS alerts
- Custom watchlist
- Manual trade buttons

**For now: Read-only monitoring is safest.**

---

## ✅ Summary

**Dashboard provides:**
- ✅ Real-time portfolio view
- ✅ Position tracking
- ✅ Bot status monitoring
- ✅ Live logs
- ✅ Mobile access
- ✅ Auto-refresh

**Access:**
- Local: `http://192.168.12.44:5555`
- Public: ngrok/cloudflared URL
- Mobile: Same URL on phone

**Deploy time:** 2 minutes  
**Setup:** One command  
**Cost:** $0 (ngrok free tier)

**Now you can check your bot from anywhere.** 📱
