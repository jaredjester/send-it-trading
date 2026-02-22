"""Quick test of scanners with limited universe.

Requires Alpaca credentials via env vars:
  ALPACA_API_LIVE_KEY, ALPACA_API_SECRET
  or APCA_API_KEY_ID, APCA_API_SECRET_KEY

Or create master_config.json from master_config.example.json.
"""
import os
import sys

# Credentials must be set before importing scanners
if not (os.getenv("ALPACA_API_LIVE_KEY") or os.getenv("APCA_API_KEY_ID")):
    print("ERROR: Set ALPACA_API_LIVE_KEY and ALPACA_API_SECRET (or APCA_API_KEY_ID/SECRET)")
    sys.exit(1)

from morning_gap_scanner import GapScanner
from catalyst_scanner import CatalystScanner

# Test gap scanner with just 5 symbols
print("Testing Gap Scanner...")
scanner = GapScanner()
scanner.universe = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA']  # Small test universe
gaps = scanner.scan_gaps(min_gap_pct=2.0)  # Lower threshold for testing

print(f"Found {len(gaps)} gaps:")
for gap in gaps[:3]:
    print(f"  {gap['symbol']}: {gap['gap_pct']:+.1f}% (Score: {gap['score']:.0f})")

print("\nTesting Catalyst Scanner...")
cat_scanner = CatalystScanner()
cat_scanner.universe = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA']
catalysts = cat_scanner.scan_catalysts(min_volume_ratio=1.5)  # Lower threshold

print(f"Found {len(catalysts)} catalysts:")
for cat in catalysts[:3]:
    print(f"  {cat['symbol']}: {cat['catalyst_type']} (Score: {cat['score']:.0f})")

print("\n✅ Scanners working!")
