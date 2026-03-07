"""
Exit conviction play and zombie positions.
Submits market orders against the LIVE Alpaca account.
Orders queue for next market open (Monday) since market is closed.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

import alpaca_env
alpaca_env.bootstrap()

key = os.getenv("APCA_API_KEY_ID")
secret = os.getenv("APCA_API_SECRET_KEY")
BASE = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

EXIT_SYMBOLS = ["GME", "AVGR", "BGXXQ", "MOTS"]

# Get current positions
r = requests.get(f"{BASE}/v2/positions", headers=headers, timeout=10)
positions = {p["symbol"]: p for p in r.json()}

print(f"Current positions: {list(positions.keys())}")
print()

for sym in EXIT_SYMBOLS:
    if sym not in positions:
        print(f"{sym}: no position, skipping")
        continue

    pos = positions[sym]
    qty = pos["qty"]
    val = float(pos["market_value"])
    pl_pct = float(pos["unrealized_plpc"]) * 100

    payload = {
        "symbol": sym,
        "qty": qty,
        "side": "sell",
        "type": "market",
        "time_in_force": "day"
    }

    try:
        r2 = requests.post(f"{BASE}/v2/orders", json=payload, headers=headers, timeout=10)
        r2.raise_for_status()
        order = r2.json()
        print(f"SELL ORDER SUBMITTED: {sym}")
        print(f"  qty={qty}, value=${val:.2f}, P&L={pl_pct:.1f}%")
        print(f"  order_id={order.get('id')}, status={order.get('status')}")
    except Exception as e:
        print(f"ERROR selling {sym}: {e}")
        if hasattr(e, "response") and e.response:
            print(f"  Response: {e.response.text}")

print()
print("Done. Orders will execute at next market open.")
