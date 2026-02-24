#!/usr/bin/env python3
"""
Simple Complete Orchestrator - Strategy V2
All trading features, minimal dependencies
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime
import sys
import json
import os

# Add strategy_v2 to path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from core.alpaca_client import AlpacaClient
from conviction_manager import ConvictionManager

# Try to import optional components
try:
    from alpha_engine import AlphaEngine
    HAS_ALPHA_ENGINE = True
except:
    HAS_ALPHA_ENGINE = False

try:
    from core.monte_carlo import MonteCarloSimulator
    HAS_MONTE_CARLO = True
except:
    HAS_MONTE_CARLO = False

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


class SimpleOrchestrator:
    """Complete trading orchestrator with graceful degradation"""
    
    def __init__(self):
        logger.info("=" * 60)
        logger.info("Initializing Simple Orchestrator")
        logger.info("=" * 60)
        
        # Core (required)
        self.alpaca = AlpacaClient(base_url="https://api.alpaca.markets")
        self.conviction_mgr = ConvictionManager()
        logger.info("✓ Core components loaded")
        
        # Optional components
        if HAS_ALPHA_ENGINE:
            try:
                self.alpha_engine = AlphaEngine()
                logger.info("✓ Alpha Engine loaded")
            except Exception as e:
                logger.warning(f"Alpha Engine failed: {e}")
                self.alpha_engine = None
        else:
            self.alpha_engine = None
            logger.info("⊗ Alpha Engine not available")
        
        if HAS_MONTE_CARLO:
            try:
                self.monte_carlo = MonteCarloSimulator()
                logger.info("✓ Monte Carlo loaded")
            except Exception as e:
                logger.warning(f"Monte Carlo failed: {e}")
                self.monte_carlo = None
        else:
            self.monte_carlo = None
            logger.info("⊗ Monte Carlo not available")
        
        # Config
        self.config = {
            'max_position_pct': 0.15,
            'max_total_exposure': 0.95,
            'zombie_loss_threshold': -0.90,
            'min_position_value': 1.0,
            'min_cash_reserve': 50.0,
            'min_trade_notional': 10.0
        }
        
        logger.info("=" * 60)
    
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
            logger.error(f"Market hours check failed: {e}")
            return False
    
    async def get_portfolio_state(self):
        """Get current portfolio state"""
        try:
            account = self.alpaca.get_account()
            positions = self.alpaca.get_positions()
            
            return {
                'portfolio_value': float(account.get('portfolio_value', 0)),
                'cash': float(account.get('cash', 0)),
                'positions': positions,
                'position_count': len(positions)
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio: {e}")
            return None
    
    def _submit_order(self, symbol, qty=None, notional=None, side='buy'):
        """Submit order to Alpaca"""
        import requests
        
        url = "https://api.alpaca.markets/v2/orders"
        headers = {
            "APCA-API-KEY-ID": self.alpaca.api_key,
            "APCA-API-SECRET-KEY": self.alpaca.api_secret
        }
        
        payload = {
            "symbol": symbol,
            "side": side,
            "type": "market",
            "time_in_force": "day"
        }
        
        if notional:
            payload["notional"] = notional
        elif qty:
            payload["qty"] = qty
        
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Order submission failed: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise
    
    async def execute_sell(self, symbol, qty):
        """Execute sell order"""
        try:
            order = self._submit_order(symbol=symbol, qty=qty, side='sell')
            logger.info(f"✓ SOLD {symbol} x{qty}")
            return True
        except Exception as e:
            logger.error(f"Sell failed for {symbol}: {e}")
            return False
    
    async def execute_buy(self, symbol, notional):
        """Execute buy order"""
        try:
            order = self._submit_order(symbol=symbol, notional=notional, side='buy')
            logger.info(f"✓ BOUGHT {symbol} ${notional:.2f}")
            return True
        except Exception as e:
            logger.error(f"Buy failed for {symbol}: {e}")
            return False
    
    async def clean_zombies(self, portfolio):
        """Clean up zombie positions"""
        zombies = []
        
        for pos in portfolio['positions']:
            symbol = pos.get('symbol', '')
            loss_pct = float(pos.get('unrealized_plpc', 0))
            value = float(pos.get('market_value', 0))
            qty = float(pos.get('qty', 0))
            
            # Zombie = >90% loss OR < $1 value
            is_zombie = (loss_pct < self.config['zombie_loss_threshold'] or 
                        value < self.config['min_position_value'])
            
            # Don't clean convictions
            is_conviction = self.conviction_mgr.is_conviction_symbol(symbol)
            
            if is_zombie and not is_conviction:
                zombies.append({
                    'symbol': symbol,
                    'qty': qty,
                    'loss': loss_pct,
                    'value': value
                })
        
        if zombies:
            logger.info(f"Found {len(zombies)} zombies to clean:")
            for z in zombies:
                logger.info(f"  {z['symbol']}: {z['loss']:.1%} / ${z['value']:.2f}")
                await self.execute_sell(z['symbol'], z['qty'])
        else:
            logger.info("No zombies found")
        
        return len(zombies)
    
    async def scan_opportunities(self):
        """Scan for trading opportunities"""
        opportunities = []
        
        try:
            # Try to import scanners
            sys.path.insert(0, str(BASE_DIR / 'scanners'))
            
            try:
                from morning_gap_scanner import run_morning_scan
                gaps = run_morning_scan()
                if gaps:
                    logger.info(f"Gap scanner: {len(gaps)} opportunities")
                    opportunities.extend(gaps)
            except Exception as e:
                logger.debug(f"Gap scanner failed: {e}")
            
            try:
                from catalyst_scanner import run_catalyst_scan
                catalysts = run_catalyst_scan()
                if catalysts:
                    logger.info(f"Catalyst scanner: {len(catalysts)} opportunities")
                    opportunities.extend(catalysts)
            except Exception as e:
                logger.debug(f"Catalyst scanner failed: {e}")
        
        except Exception as e:
            logger.warning(f"Scanner import failed: {e}")
        
        return opportunities
    
    async def score_opportunity(self, opp):
        """Score a trading opportunity"""
        if self.alpha_engine:
            try:
                symbol = opp.get('symbol')
                score = self.alpha_engine.score_symbol(symbol)
                return score
            except Exception as e:
                logger.debug(f"Alpha scoring failed: {e}")
        
        # Fallback: use scanner score
        return opp.get('score', 50)
    
    async def check_risk_limits(self, portfolio, symbol, notional):
        """Check if trade passes risk limits"""
        # Check cash
        if notional > portfolio['cash'] - self.config['min_cash_reserve']:
            logger.warning(f"  ❌ Insufficient cash")
            return False
        
        # Check if already have position
        for pos in portfolio['positions']:
            if pos.get('symbol') == symbol:
                existing_value = float(pos.get('market_value', 0))
                existing_pct = existing_value / portfolio['portfolio_value']
                if existing_pct >= self.config['max_position_pct']:
                    logger.warning(f"  ❌ Already at max position")
                    return False
        
        # Check total exposure
        total_long = sum(float(p.get('market_value', 0)) for p in portfolio['positions'])
        new_exposure = (total_long + notional) / portfolio['portfolio_value']
        
        if new_exposure >= self.config['max_total_exposure']:
            logger.warning(f"  ❌ Total exposure limit")
            return False
        
        return True
    
    async def calculate_size(self, portfolio, score):
        """Calculate position size based on score"""
        # Simple sizing: 5-15% based on score
        # Score 50 = 5%, Score 100 = 15%
        size_pct = 0.05 + (score - 50) / 100 * 0.10
        size_pct = max(0.05, min(size_pct, self.config['max_position_pct']))
        
        notional = portfolio['portfolio_value'] * size_pct
        return notional
    
    async def execute_opportunities(self, portfolio, opportunities):
        """Execute best opportunities"""
        if not opportunities:
            logger.info("No opportunities to execute")
            return
        
        logger.info(f"Evaluating {len(opportunities)} opportunities")
        
        executed = 0
        for opp in opportunities[:5]:  # Max 5 per cycle
            symbol = opp.get('symbol')
            
            # Score it
            score = await self.score_opportunity(opp)
            if score < 60:  # Minimum threshold
                logger.info(f"  {symbol}: Score {score:.0f} < 60 threshold")
                continue
            
            logger.info(f"  {symbol}: Score {score:.0f}")
            
            # Calculate size
            notional = await self.calculate_size(portfolio, score)
            
            if notional < self.config['min_trade_notional']:
                logger.info(f"  {symbol}: Position too small ${notional:.2f}")
                continue
            
            # Check risk
            passes = await self.check_risk_limits(portfolio, symbol, notional)
            if not passes:
                continue
            
            # Execute
            success = await self.execute_buy(symbol, notional)
            if success:
                executed += 1
                # Update cash for next iteration
                portfolio['cash'] -= notional
        
        if executed > 0:
            logger.info(f"✓ Executed {executed} trades")
    
    async def run_cycle(self):
        """Run one complete trading cycle"""
        logger.info("")
        logger.info("=" * 80)
        logger.info("CYCLE START")
        logger.info("=" * 80)
        
        # 1. Market check
        is_open = await self.is_market_open()
        if not is_open:
            logger.info("Market CLOSED - skipping cycle")
            logger.info("=" * 80)
            return
        
        logger.info("Market OPEN")
        
        # 2. Get portfolio
        portfolio = await self.get_portfolio_state()
        if not portfolio:
            logger.error("Failed to get portfolio")
            return
        
        logger.info(f"Portfolio: ${portfolio['portfolio_value']:.2f} | "
                   f"Cash: ${portfolio['cash']:.2f} | "
                   f"Positions: {portfolio['position_count']}")
        
        # 3. Clean zombies (always first)
        cleaned = await self.clean_zombies(portfolio)
        
        # 4. Check convictions
        convictions = self.conviction_mgr.get_active_convictions()
        if convictions:
            logger.info(f"Active convictions: {list(convictions.keys())}")
        
        # 5. Scan opportunities
        opportunities = await self.scan_opportunities()
        
        # 6. Execute best opportunities
        await self.execute_opportunities(portfolio, opportunities)
        
        logger.info("=" * 80)
        logger.info("CYCLE COMPLETE")
        logger.info("=" * 80)


async def main():
    """Main entry point"""
    orchestrator = SimpleOrchestrator()
    
    try:
        await orchestrator.run_cycle()
    except Exception as e:
        logger.error(f"Cycle failed: {e}", exc_info=True)


if __name__ == '__main__':
    asyncio.run(main())
