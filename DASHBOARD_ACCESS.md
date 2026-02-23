# 📱 Dashboard Access Guide

**Dashboard is running on your Pi at port 5555.**

---

## ✅ Working Now: Local Access

### On Your Home WiFi

**URL:** `http://192.168.12.44:5555`

**How to use:**
1. Connect phone/laptop to your home WiFi
2. Open browser
3. Go to `http://192.168.12.44:5555`
4. Dashboard loads instantly ✅

**This works from:**
- ✅ Your phone (on home WiFi)
- ✅ Your laptop (on home WiFi)  
- ✅ Tablet, desktop, any device (on home WiFi)

**This does NOT work from:**
- ❌ Outside your home (different WiFi/cellular)
- ❌ Work, coffee shop, etc.

---

## 🌐 Option 1: Quick Public Access (ngrok)

**If you want access from ANYWHERE:**

### Step 1: SSH to Pi

```bash
ssh jonathangan@192.168.12.44
# Password: Notraspberry123!
```

### Step 2: Start ngrok

```bash
# Kill existing ngrok dashboard tunnel (if any)
pkill -f 'ngrok.*http.*5555'

# Start new tunnel
ngrok http 5555

# Keep this terminal open!
```

### Step 3: Copy the URL

You'll see output like:
```
Forwarding   https://abc-123-def.ngrok-free.app -> http://localhost:5555
```

**Copy that URL** (the `https://...ngrok-free.app` part)

### Step 4: Access from anywhere

Open that URL on your phone/laptop from anywhere in the world.

**Notes:**
- ✅ Free tier works fine
- ✅ HTTPS included
- ⚠️ URL changes when you restart ngrok
- ⚠️ Must keep terminal open (ngrok running)

---

## 🌐 Option 2: Permanent Public Access (Automatic)

### Run This Script (One Time Setup)

```bash
# SSH to Pi
ssh jonathangan@192.168.12.44

# Run setup script
cd ~/shared/stockbot/strategy_v2
bash setup_public_dashboard.sh

# This will:
# 1. Start ngrok for port 5555
# 2. Save the public URL
# 3. Auto-restart ngrok if it crashes
```

### Get Your Public URL

```bash
ssh jonathangan@192.168.12.44
cat ~/dashboard_url.txt
```

**Script creates:**
- Systemd service: `ngrok-dashboard.service`
- Auto-starts on boot
- Saves URL to `~/dashboard_url.txt`

---

## 🔒 Option 3: Secure Public Access (Port Forward)

**Most permanent, requires router access.**

### Step 1: Find Your Public IP

```bash
curl ifconfig.me
```

Example: `73.123.45.67`

### Step 2: Configure Router

1. Open router admin (usually `192.168.1.1`)
2. Find "Port Forwarding" settings
3. Add rule:
   - External port: `5555`
   - Internal IP: `192.168.12.44`
   - Internal port: `5555`
   - Protocol: TCP
4. Save

### Step 3: Access Dashboard

**URL:** `http://YOUR_PUBLIC_IP:5555`

Example: `http://73.123.45.67:5555`

**Notes:**
- ✅ Permanent (doesn't change)
- ✅ No third-party service
- ⚠️ Public IP may change (depends on ISP)
- ⚠️ No HTTPS (HTTP only)
- ⚠️ Requires router access

---

## 🚀 Recommended Setup (Easiest)

### For Now: Use Local Access

**On your home WiFi:**
```
http://192.168.12.44:5555
```

**From your phone:**
1. Connect to home WiFi
2. Open browser (Chrome, Safari, etc.)
3. Type: `192.168.12.44:5555`
4. Bookmark it
5. Add to home screen (looks like an app)

**Works perfectly for:**
- Checking portfolio at home
- Monitoring bot while on couch
- Quick status check before bed

### When You Need Remote Access: Run ngrok

**Just once, when you're away from home:**

```bash
# SSH to Pi (use a different device or cellular)
ssh jonathangan@192.168.12.44

# Start ngrok
ngrok http 5555

# Copy the URL
# Access from anywhere
```

**Total time:** 30 seconds

---

## 📱 Mobile Setup (Add to Home Screen)

### iPhone/iPad

1. Open Safari
2. Go to `http://192.168.12.44:5555`
3. Tap Share button (box with arrow)
4. Tap "Add to Home Screen"
5. Name it "Trading Dashboard"
6. Tap "Add"

**Now it looks like a native app!**

### Android

1. Open Chrome
2. Go to `http://192.168.12.44:5555`
3. Tap menu (3 dots)
4. Tap "Add to Home screen"
5. Name it "Trading Dashboard"
6. Tap "Add"

**Launches like a real app.**

---

## 🔧 Troubleshooting

### "This site can't be reached"

**Check if dashboard is running:**
```bash
ssh jonathangan@192.168.12.44
systemctl status dashboard

# If not running:
sudo systemctl start dashboard
```

### "Connection refused"

**Check if you're on same WiFi:**
- Dashboard requires home WiFi for local access
- Or use ngrok for public access

**Check Pi IP address:**
```bash
ssh jonathangan@192.168.12.44
ip addr show eth0 | grep 'inet '
```

Should show: `192.168.12.44`

### ngrok URL not working

**Check if ngrok is running:**
```bash
ssh jonathangan@192.168.12.44
ps aux | grep ngrok

# If not running:
ngrok http 5555
```

**Check ngrok URL:**
```bash
curl http://localhost:4040/api/tunnels | grep public_url
```

### Dashboard shows old data

**Restart dashboard:**
```bash
ssh jonathangan@192.168.12.44
sudo systemctl restart dashboard
```

**Check if bot is running:**
```bash
systemctl status mybot
```

---

## ✅ Quick Reference

### Local Access (Home WiFi Only)
```
http://192.168.12.44:5555
```

### Public Access (Quick Setup)
```bash
ssh jonathangan@192.168.12.44
ngrok http 5555
# Copy the https://...ngrok-free.app URL
```

### Services
```bash
# Dashboard status
systemctl status dashboard

# Dashboard logs
journalctl -u dashboard -f

# Restart dashboard
sudo systemctl restart dashboard
```

### Files
- Dashboard API: `~/shared/stockbot/strategy_v2/dashboard_api.py`
- Frontend: `~/shared/stockbot/strategy_v2/templates/live_dashboard.html`
- Logs: `journalctl -u dashboard`

---

## 🎯 Summary

**Best for most people:**
- Use local access: `http://192.168.12.44:5555`
- Works on home WiFi
- Fast, reliable, no setup needed
- Access from phone while at home

**When away from home:**
- Run ngrok (30 seconds)
- Get temporary public URL
- Access from anywhere

**For permanent public access:**
- Set up port forwarding (5 minutes)
- Or run setup_public_dashboard.sh (creates ngrok service)

**The dashboard is working perfectly on your local network.** 📱

**Access it now: `http://192.168.12.44:5555`**
