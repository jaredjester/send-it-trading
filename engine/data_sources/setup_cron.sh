#!/bin/bash
# Auto-setup cron job for daily alt data scan
# Run on Pi: ./setup_cron.sh

CRON_LINE='0 8 * * * cd /home/jonathangan/shared/stockbot/strategy_v2/data_sources && python3 alt_data_aggregator.py --watchlist GME SPY TSLA NVDA AAPL MSFT >> /home/jonathangan/shared/stockbot/logs/alt_data.log 2>&1'

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "alt_data_aggregator.py"; then
    echo "⚠️  Cron job already exists. Skipping."
    echo "To view: crontab -l"
else
    # Add to crontab
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "✅ Cron job added!"
    echo "   Daily alt data scan: 8:00 AM ET"
    echo "   Logs: ~/shared/stockbot/logs/alt_data.log"
fi

echo ""
echo "Current crontab:"
crontab -l
