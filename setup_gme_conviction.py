#!/usr/bin/env python3
"""Set up GME conviction for acquisition catalyst"""

from conviction_manager import ConvictionManager
import json
import os

cm = ConvictionManager()

# Set GME conviction with October 2026 acquisition deadline
conv = cm.set_conviction(
    symbol='GME',
    thesis='GameStop acquisition by major tech company (Ryan Cohen positioning)',
    catalyst='Acquisition announcement or confirmed bid',
    catalyst_deadline='2026-10-31T23:59:59Z',  # October 2026
    target_price=45.0,
    max_pain_price=10.0,  # Below $10 = thesis dead
    base_score=82,  # Strong conviction
    catalyst_type='event',
    max_position_pct=45.0,  # Allow higher than normal 20% limit
    entry_price=24.89,  # Current price from dashboard
    notes='Strong acquisition rumors. Ryan Cohen making moves. Must materialize by Oct 2026 or conviction expires. Hold through volatility, DCA on dips.'
)

print('✅ GME Conviction Set:')
print(f'   Symbol: {conv["symbol"]}')
print(f'   Score: {conv["current_score"]}/100')
print(f'   Phase: {conv["phase"]}')
print(f'   Deadline: {conv["catalyst_deadline"]}')
print(f'   Target: ${conv["target_price"]:.2f}')
print(f'   Max Pain: ${conv["max_pain_price"]:.2f}')
print(f'   Max Position: {conv["max_position_pct"]}%')
print()

# Show the generated state file
state_dir = os.path.join(os.path.dirname(__file__), 'state')
convictions_file = os.path.join(state_dir, 'convictions.json')
if os.path.exists(convictions_file):
    with open(convictions_file) as f:
        data = json.load(f)
    print(f'State file: {convictions_file}')
    print(f'Active convictions: {len(data)}')
    for sym, c in data.items():
        print(f'  {sym}: score={c["current_score"]}, phase={c["phase"]}')
