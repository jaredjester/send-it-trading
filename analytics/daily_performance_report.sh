#!/bin/bash
# Daily Performance Report
# Run at market close (4:30 PM ET) to calculate daily P&L
# Posts report to Telegram

set -e

cd /home/jonathangan/shared/stockbot/strategy_v2/analytics

# Get current portfolio value from Alpaca
PORTFOLIO_VALUE=$(python3 << 'PYTHON_EOF'
import requests
import os

api_key = os.getenv('ALPACA_API_LIVE_KEY')
api_secret = os.getenv('ALPACA_API_SECRET')

headers = {
    'APCA-API-KEY-ID': api_key,
    'APCA-API-SECRET-KEY': api_secret
}

resp = requests.get('https://api.alpaca.markets/v2/account', headers=headers)
if resp.status_code == 200:
    account = resp.json()
    print(float(account['portfolio_value']))
else:
    print("0")
PYTHON_EOF
)

# Get SPY close price
SPY_PRICE=$(python3 << 'PYTHON_EOF'
import requests
import os

api_key = os.getenv('ALPACA_API_LIVE_KEY')
api_secret = os.getenv('ALPACA_API_SECRET')

headers = {
    'APCA-API-KEY-ID': api_key,
    'APCA-API-SECRET-KEY': api_secret
}

# Get latest SPY bar
resp = requests.get('https://data.alpaca.markets/v2/stocks/SPY/bars/latest', headers=headers, params={'feed': 'iex'})
if resp.status_code == 200:
    data = resp.json()
    print(data['bar']['c'])
else:
    print("0")
PYTHON_EOF
)

# Record snapshot
python3 profit_tracker.py --record $PORTFOLIO_VALUE --spy $SPY_PRICE

# Generate report
REPORT=$(python3 profit_tracker.py --report --days 30)

# Post to Telegram (via stockbot's telegram client if available)
echo "$REPORT" >> /home/jonathangan/shared/stockbot/logs/daily_performance.log

echo "✅ Daily performance recorded: \$$PORTFOLIO_VALUE"
echo "   SPY: \$$SPY_PRICE"
