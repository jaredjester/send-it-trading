#!/usr/bin/env python3
"""
Clean Orchestrator - Strategy V2
Self-contained trading bot using ONLY strategy_v2 code
No legacy dependencies
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime
import sys

# Add strategy_v2 to path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from core.alpaca_client import AlpacaClient
from conviction_manager import ConvictionManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / 'logs/trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class CleanOrchestrator:
    """Self-contained orchestrator for stock trading"""
    
    def __init__(self):
        self.alpaca = AlpacaClient(base_url="https://api.alpaca.markets")
        self.conviction_mgr = ConvictionManager()
        
    async def is_market_open(self):
        """Check if market is open"""
        try:
            import requests
            url = "https://api.alpaca.markets/v2/clock"
            headers = {
                "APCA-API-KEY-ID": self.alpaca.api_key,
                "APCA-API-SECRET-KEY": self.alpaca.api_secret
            }
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get('is_open', False)
        except Exception as e:
            logger.error(f"Failed to check market hours: {e}")
            return False
    
    async def get_portfolio_state(self):
        """Get current portfolio state"""
        try:
            account = self.alpaca.get_account()
            positions = self.alpaca.get_positions()
            
            portfolio_value = float(account.get('portfolio_value', 0))
            cash = float(account.get('cash', 0))
            
            return {
                'portfolio_value': portfolio_value,
                'cash': cash,
                'positions': positions,
                'position_count': len(positions)
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio state: {e}")
            return None
    
    async def clean_zombie_positions(self, positions):
        """Clean up zombie positions (>90% loss or near zero value)"""
        zombies = []
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            unrealized_plpc = float(pos.get('unrealized_plpc', 0))
            market_value = float(pos.get('market_value', 0))
            
            # Zombie if >90% loss OR value < $1
            if unrealized_plpc < -0.90 or market_value < 1.0:
                # Check if it's a conviction position
                if not self.conviction_mgr.is_conviction_symbol(symbol):
                    zombies.append({
                        'symbol': symbol,
                        'qty': float(pos.get('qty', 0)),
                        'loss': unrealized_plpc,
                        'value': market_value
                    })
        
        if zombies:
            logger.info(f"Found {len(zombies)} zombie positions to clean")
            for z in zombies:
                logger.info(f"  {z['symbol']}: {z['loss']:.1%} loss, ${z['value']:.2f} value")
                # Would sell here when trading enabled
                # For now just log
        
        return zombies
    
    async def check_convictions(self, positions):
        """Check and manage conviction positions"""
        convictions = self.conviction_mgr.get_active_convictions()
        
        if not convictions:
            logger.info("No active convictions")
            return
        
        logger.info(f"Active convictions: {list(convictions.keys())}")
        
        for symbol, conv in convictions.items():
            logger.info(f"Conviction {symbol}: phase={conv.get('phase', 'UNKNOWN')}, "
                       f"score={conv.get('score', 0)}, "
                       f"pnl={conv.get('pnl_pct', 0):.1%}")
    
    async def run_cycle(self):
        """Run one trading cycle"""
        logger.info("=" * 60)
        logger.info("CYCLE START")
        logger.info("=" * 60)
        
        # 1. Check market hours
        is_open = await self.is_market_open()
        if not is_open:
            logger.info("Market is CLOSED - skipping cycle")
            return
        
        logger.info("Market is OPEN")
        
        # 2. Get portfolio state
        portfolio = await self.get_portfolio_state()
        if not portfolio:
            logger.error("Failed to get portfolio state")
            return
        
        logger.info(f"Portfolio: ${portfolio['portfolio_value']:.2f} | "
                   f"Cash: ${portfolio['cash']:.2f} | "
                   f"Positions: {portfolio['position_count']}")
        
        # 3. Check convictions
        await self.check_convictions(portfolio['positions'])
        
        # 4. Clean zombies
        await self.clean_zombie_positions(portfolio['positions'])
        
        # 5. Scan for opportunities
        # TODO: Add scanning logic
        
        logger.info("Cycle complete")
        logger.info("")


async def main():
    """Main entry point"""
    logger.info("=" * 80)
    logger.info("CLEAN ORCHESTRATOR - STARTING")
    logger.info("=" * 80)
    logger.info(f"Base directory: {BASE_DIR}")
    logger.info("")
    
    orchestrator = CleanOrchestrator()
    
    try:
        await orchestrator.run_cycle()
    except Exception as e:
        logger.error(f"Cycle failed: {e}", exc_info=True)


if __name__ == '__main__':
    asyncio.run(main())
