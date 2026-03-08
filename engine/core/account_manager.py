"""
Account Manager
Unified interface for account and portfolio data fetching.
"""

from typing import Dict, List
from engine.core.alpaca_client import AlpacaClient


class AccountManager:
    """Manages account and position data."""

    def __init__(self):
        self.client = AlpacaClient()

    def get_account_summary(self) -> Dict:
        """Get account summary with capital, buying power, etc."""
        account = self.client.get_account()
        return {
            'capital': float(account.get('cash', 0)),
            'options_buying_power': float(account.get('options_buying_power', 0)),
            'equity': float(account.get('portfolio_value', 0)),
            'buying_power': float(account.get('buying_power', 0)),
        }

    def get_positions_list(self) -> List[Dict]:
        """Get list of positions."""
        return self.client.get_positions()

    def get_portfolio_state(self) -> Dict:
        """Get full portfolio state."""
        return self.client.get_portfolio_state()