#!/usr/bin/env python3
"""
Portfolio Optimizer - Portfolio Management and Risk Control
Production-ready portfolio management for Pi Hedge Fund

Handles rebalancing, tax-loss harvesting, correlation monitoring,
zombie position cleanup, and benchmark tracking.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import requests

from core.config import load_config


class PortfolioOptimizer:
    """
    Portfolio management system handling:
    - Position sizing and rebalancing
    - Tax-loss harvesting
    - Correlation monitoring
    - Zombie position cleanup
    - Benchmark tracking
    """
    
    def __init__(self, config_path: str | None = None):
        """Initialize portfolio optimizer with configuration."""
        self.config = load_config(config_path)
        acct = self.config.get("account", {})
        self.api_key = acct.get("alpaca_api_key") or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_LIVE_KEY")
        self.api_secret = acct.get("alpaca_secret_key") or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
        self.data_url = acct.get("alpaca_data_url", "https://data.alpaca.markets")
        self.feed = acct.get("data_feed", "iex")
        
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret
        }
        
        # Track wash sale windows
        self.wash_sale_tracker = {}
        
        # Benchmark state (load_config resolves path)
        benchmark_cfg = self.config.get("benchmark", {})
        self.benchmark_state_file = benchmark_cfg.get("state_file", "./state/benchmark_state.json")
        self.load_benchmark_state()
        
        log_cfg = self.config.get("logging", {})
        logging.basicConfig(
            level=log_cfg.get("level", "INFO"),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def load_benchmark_state(self):
        """Load benchmark tracking state from file."""
        try:
            # Ensure state directory exists
            Path(self.benchmark_state_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.benchmark_state_file, 'r') as f:
                self.benchmark_state = json.load(f)
        except FileNotFoundError:
            self.benchmark_state = {
                "initial_portfolio_value": 0,
                "initial_spy_price": 0,
                "start_date": datetime.utcnow().isoformat(),
                "history": []
            }
        except Exception as e:
            self.logger.warning(f"Failed to load benchmark state: {e}")
            self.benchmark_state = {
                "initial_portfolio_value": 0,
                "initial_spy_price": 0,
                "start_date": datetime.utcnow().isoformat(),
                "history": []
            }
    
    def save_benchmark_state(self):
        """Save benchmark tracking state to file."""
        Path(self.benchmark_state_file).parent.mkdir(parents=True, exist_ok=True)
        with open(self.benchmark_state_file, 'w') as f:
            json.dump(self.benchmark_state, f, indent=2)
    
    def _fetch_bars(self, symbol: str, days: int = 100) -> List[Dict]:
        """Fetch historical bar data from Alpaca."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT23:59:59Z"),
            "feed": self.feed,
            "limit": days
        }
        
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("bars", [])
        except Exception as e:
            self.logger.error(f"Failed to fetch bars for {symbol}: {e}")
            return []
    
    def check_rebalancing_needs(self, positions: List[Dict], portfolio_value: float) -> List[Dict]:
        """
        Check if portfolio needs rebalancing based on position and sector limits.
        
        Args:
            positions: List of position dicts with keys: symbol, qty, market_value, sector
            portfolio_value: Total portfolio value including cash
            
        Returns:
            List of rebalancing actions: [
                {"action": "trim", "symbol": str, "reason": str, "current_pct": float, "target_pct": float},
                {"action": "raise_cash", "symbol": str, "reason": str, "amount": float},
                ...
            ]
        """
        cfg = self.config['portfolio']
        actions = []
        
        if portfolio_value <= 0:
            return actions
        
        # Calculate current cash
        total_position_value = sum(p['market_value'] for p in positions)
        cash = portfolio_value - total_position_value
        cash_pct = cash / portfolio_value
        
        # Check minimum cash reserve
        if cash_pct < cfg['min_cash_reserve_pct']:
            needed_cash = portfolio_value * cfg['min_cash_reserve_pct'] - cash
            
            # Find weakest positions to sell
            positions_sorted = sorted(positions, key=lambda p: p.get('unrealized_pl_pct', 0))
            
            for pos in positions_sorted:
                if needed_cash <= 0:
                    break
                    
                actions.append({
                    "action": "raise_cash",
                    "symbol": pos['symbol'],
                    "reason": f"Cash reserve below {cfg['min_cash_reserve_pct']*100:.0f}%",
                    "amount": min(pos['market_value'], needed_cash),
                    "current_cash_pct": cash_pct
                })
                needed_cash -= pos['market_value']
        
        # Check individual position limits
        for pos in positions:
            pos_pct = pos['market_value'] / portfolio_value
            
            if pos_pct > cfg['max_position_pct']:
                target_value = portfolio_value * cfg['max_position_pct']
                trim_amount = pos['market_value'] - target_value
                
                actions.append({
                    "action": "trim",
                    "symbol": pos['symbol'],
                    "reason": f"Position exceeds {cfg['max_position_pct']*100:.0f}% limit",
                    "current_pct": pos_pct,
                    "target_pct": cfg['max_position_pct'],
                    "trim_amount": trim_amount
                })
        
        # Check sector limits
        sector_exposure = {}
        for pos in positions:
            sector = pos.get('sector', 'unknown')
            sector_exposure[sector] = sector_exposure.get(sector, 0) + pos['market_value']
        
        for sector, exposure in sector_exposure.items():
            sector_pct = exposure / portfolio_value
            
            if sector_pct > cfg['max_sector_pct']:
                # Find positions in this sector to trim
                sector_positions = [p for p in positions if p.get('sector') == sector]
                excess_value = exposure - (portfolio_value * cfg['max_sector_pct'])
                
                # Trim largest positions first
                sector_positions_sorted = sorted(sector_positions, 
                                                key=lambda p: p['market_value'], 
                                                reverse=True)
                
                for pos in sector_positions_sorted:
                    if excess_value <= 0:
                        break
                    
                    trim_amount = min(pos['market_value'], excess_value)
                    actions.append({
                        "action": "trim",
                        "symbol": pos['symbol'],
                        "reason": f"Sector {sector} exceeds {cfg['max_sector_pct']*100:.0f}% limit",
                        "current_pct": sector_pct,
                        "target_pct": cfg['max_sector_pct'],
                        "trim_amount": trim_amount
                    })
                    excess_value -= trim_amount
        
        return actions
    
    def scan_tax_loss_harvest(self, positions: List[Dict]) -> List[Dict]:
        """
        Scan for tax-loss harvesting opportunities.
        
        Args:
            positions: List of position dicts with keys: symbol, qty, cost_basis, market_value, 
                      unrealized_pl_pct, entry_date
            
        Returns:
            List of harvest recommendations: [
                {"symbol": str, "reason": str, "loss_pct": float, "loss_amount": float, 
                 "wash_sale_clear_date": str},
                ...
            ]
        """
        cfg = self.config['tax_loss_harvesting']
        if not cfg['enabled']:
            return []
        
        recommendations = []
        today = datetime.utcnow()
        
        # Check if we're in year-end window
        year_end = datetime(today.year, 12, 31)
        days_to_year_end = (year_end - today).days
        in_year_end_window = days_to_year_end <= cfg['year_end_window_days']
        
        for pos in positions:
            symbol = pos['symbol']
            unrealized_pl_pct = pos.get('unrealized_pl_pct', 0)
            
            # Check if position has sufficient loss
            if unrealized_pl_pct >= -cfg['loss_threshold_pct']:
                continue
            
            # Check hold period
            entry_date = datetime.fromisoformat(pos.get('entry_date', today.isoformat()))
            hold_days = (today - entry_date).days
            
            if hold_days < cfg['min_hold_days']:
                continue
            
            # Check wash sale window
            if symbol in self.wash_sale_tracker:
                last_sale_date = datetime.fromisoformat(self.wash_sale_tracker[symbol])
                days_since_sale = (today - last_sale_date).days
                
                if days_since_sale < cfg['wash_sale_days']:
                    continue
            
            # Calculate wash sale clear date
            wash_sale_clear_date = (today + timedelta(days=cfg['wash_sale_days'])).isoformat()
            
            # Higher priority if we're near year-end
            priority = "high" if in_year_end_window else "normal"
            
            loss_amount = pos['market_value'] - pos['cost_basis']
            
            recommendations.append({
                "symbol": symbol,
                "reason": f"Unrealized loss of {abs(unrealized_pl_pct)*100:.1f}%",
                "loss_pct": unrealized_pl_pct,
                "loss_amount": loss_amount,
                "wash_sale_clear_date": wash_sale_clear_date,
                "priority": priority,
                "days_to_year_end": days_to_year_end if in_year_end_window else None
            })
        
        return recommendations
    
    def record_tax_loss_sale(self, symbol: str):
        """Record a tax-loss sale to track wash sale window."""
        self.wash_sale_tracker[symbol] = datetime.utcnow().isoformat()
    
    def check_correlation(self, positions: List[Dict]) -> List[Dict]:
        """
        Monitor correlation between positions and flag highly correlated pairs.
        
        Args:
            positions: List of position dicts with keys: symbol, market_value
            
        Returns:
            List of correlation warnings: [
                {"symbol_a": str, "symbol_b": str, "correlation": float, 
                 "recommendation": str},
                ...
            ]
        """
        cfg = self.config['correlation']
        if not cfg['enabled'] or len(positions) < 2:
            return []
        
        warnings = []
        symbols = [p['symbol'] for p in positions]
        
        # Fetch price data for all symbols
        price_data = {}
        for symbol in symbols:
            bars = self._fetch_bars(symbol, cfg['lookback_days'])
            if bars:
                price_data[symbol] = np.array([float(b['c']) for b in bars])
        
        # Calculate pairwise correlations
        for i, symbol_a in enumerate(symbols):
            for symbol_b in symbols[i+1:]:
                if symbol_a not in price_data or symbol_b not in price_data:
                    continue
                
                prices_a = price_data[symbol_a]
                prices_b = price_data[symbol_b]
                
                # Align lengths
                min_len = min(len(prices_a), len(prices_b))
                if min_len < 20:
                    continue
                
                prices_a = prices_a[-min_len:]
                prices_b = prices_b[-min_len:]
                
                # Calculate correlation
                returns_a = np.diff(prices_a) / prices_a[:-1]
                returns_b = np.diff(prices_b) / prices_b[:-1]
                
                if len(returns_a) > 0 and len(returns_b) > 0:
                    correlation = np.corrcoef(returns_a, returns_b)[0, 1]
                    
                    if abs(correlation) > cfg['max_correlation']:
                        # Recommend reducing the smaller position
                        pos_a = next(p for p in positions if p['symbol'] == symbol_a)
                        pos_b = next(p for p in positions if p['symbol'] == symbol_b)
                        
                        smaller_symbol = symbol_a if pos_a['market_value'] < pos_b['market_value'] else symbol_b
                        
                        warnings.append({
                            "symbol_a": symbol_a,
                            "symbol_b": symbol_b,
                            "correlation": correlation,
                            "recommendation": f"Consider reducing {smaller_symbol} - high correlation with {symbol_a if smaller_symbol == symbol_b else symbol_b}",
                            "reduce_symbol": smaller_symbol
                        })
        
        return warnings
    
    def kill_zombies(self, positions: List[Dict]) -> List[Dict]:
        """
        Identify and flag zombie positions for automatic liquidation.
        
        Args:
            positions: List of position dicts with keys: symbol, market_value, 
                      unrealized_pl_pct, avg_daily_volume
            
        Returns:
            List of zombie positions to liquidate: [
                {"symbol": str, "reason": str, "market_value": float},
                ...
            ]
        """
        cfg = self.config['risk']
        zombies = []
        
        for pos in positions:
            symbol = pos['symbol']
            market_value = pos['market_value']
            unrealized_pl_pct = pos.get('unrealized_pl_pct', 0)
            
            # Check for massive loss + low value
            if (unrealized_pl_pct < -cfg['zombie_loss_threshold'] and 
                market_value < cfg['zombie_min_value']):
                zombies.append({
                    "symbol": symbol,
                    "reason": f"Down {abs(unrealized_pl_pct)*100:.0f}% with value < ${cfg['zombie_min_value']}",
                    "market_value": market_value,
                    "action": "liquidate"
                })
                continue
            
            # Check for no volume (illiquid position)
            bars = self._fetch_bars(symbol, cfg['zombie_no_volume_days'] + 1)
            if bars:
                recent_volumes = [float(b['v']) for b in bars[-cfg['zombie_no_volume_days']:]]
                if all(v == 0 for v in recent_volumes):
                    zombies.append({
                        "symbol": symbol,
                        "reason": f"No volume for {cfg['zombie_no_volume_days']}+ days",
                        "market_value": market_value,
                        "action": "flag_for_review"
                    })
        
        return zombies
    
    def update_benchmark(self, portfolio_value: float) -> Dict:
        """
        Track portfolio performance vs SPY benchmark and adjust risk parameters.
        
        Args:
            portfolio_value: Current total portfolio value
            
        Returns:
            Dict with benchmark comparison and risk adjustment recommendations
        """
        cfg = self.config['benchmark']
        
        # Fetch current SPY price
        spy_bars = self._fetch_bars(cfg['symbol'], 2)
        if not spy_bars:
            return {"error": "Could not fetch SPY data"}
        
        current_spy_price = float(spy_bars[-1]['c'])
        
        # Initialize benchmark tracking if first run
        if self.benchmark_state['initial_portfolio_value'] == 0:
            self.benchmark_state['initial_portfolio_value'] = portfolio_value
            self.benchmark_state['initial_spy_price'] = current_spy_price
            self.benchmark_state['start_date'] = datetime.utcnow().isoformat()
            self.save_benchmark_state()
            
            return {
                "status": "initialized",
                "message": "Benchmark tracking started"
            }
        
        # Calculate returns
        portfolio_return = (portfolio_value / self.benchmark_state['initial_portfolio_value']) - 1
        spy_return = (current_spy_price / self.benchmark_state['initial_spy_price']) - 1
        
        outperformance = portfolio_return - spy_return
        
        # Calculate rolling 30-day comparison
        history_entry = {
            "date": datetime.utcnow().isoformat(),
            "portfolio_value": portfolio_value,
            "portfolio_return": portfolio_return,
            "spy_price": current_spy_price,
            "spy_return": spy_return,
            "outperformance": outperformance
        }
        
        self.benchmark_state['history'].append(history_entry)
        
        # Keep only recent history
        if len(self.benchmark_state['history']) > cfg['lookback_days']:
            self.benchmark_state['history'] = self.benchmark_state['history'][-cfg['lookback_days']:]
        
        self.save_benchmark_state()
        
        # Determine risk adjustment
        risk_adjustment = None
        if outperformance < -cfg['trailing_threshold_pct']:
            risk_adjustment = {
                "action": "tighten",
                "reason": f"Trailing SPY by {abs(outperformance)*100:.1f}%",
                "recommendations": [
                    "Reduce position sizes",
                    "Tighten stop losses",
                    "Increase cash reserve",
                    "Focus on higher-confidence trades only"
                ]
            }
        elif outperformance > cfg['beating_threshold_pct']:
            risk_adjustment = {
                "action": "loosen",
                "reason": f"Beating SPY by {outperformance*100:.1f}%",
                "recommendations": [
                    "Can slightly increase position sizes",
                    "Maintain current strategy",
                    "Consider adding to winners"
                ]
            }
        
        return {
            "portfolio_return": portfolio_return,
            "spy_return": spy_return,
            "outperformance": outperformance,
            "days_tracked": len(self.benchmark_state['history']),
            "risk_adjustment": risk_adjustment
        }
    
    def generate_portfolio_report(self, positions: List[Dict], portfolio_value: float) -> Dict:
        """
        Generate comprehensive portfolio health report.
        
        Args:
            positions: List of all current positions
            portfolio_value: Total portfolio value
            
        Returns:
            Dict with complete portfolio analysis and recommendations
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "portfolio_value": portfolio_value,
            "position_count": len(positions),
            "checks": {}
        }
        
        # Run all checks
        report['checks']['rebalancing'] = self.check_rebalancing_needs(positions, portfolio_value)
        report['checks']['tax_loss_harvest'] = self.scan_tax_loss_harvest(positions)
        report['checks']['correlation'] = self.check_correlation(positions)
        report['checks']['zombies'] = self.kill_zombies(positions)
        report['checks']['benchmark'] = self.update_benchmark(portfolio_value)
        
        # Calculate summary
        total_actions = (
            len(report['checks']['rebalancing']) +
            len(report['checks']['tax_loss_harvest']) +
            len(report['checks']['correlation']) +
            len(report['checks']['zombies'])
        )
        
        report['summary'] = {
            "total_actions_needed": total_actions,
            "rebalancing_needed": len(report['checks']['rebalancing']) > 0,
            "tax_harvest_opportunities": len(report['checks']['tax_loss_harvest']),
            "correlation_warnings": len(report['checks']['correlation']),
            "zombie_positions": len(report['checks']['zombies']),
            "health_score": self._calculate_health_score(report)
        }
        
        return report
    
    def _calculate_health_score(self, report: Dict) -> float:
        """
        Calculate portfolio health score (0-100).
        Higher is better.
        """
        score = 100.0
        
        # Deduct points for issues
        score -= len(report['checks']['rebalancing']) * 10  # -10 per rebalancing issue
        score -= len(report['checks']['correlation']) * 5   # -5 per correlation warning
        score -= len(report['checks']['zombies']) * 15      # -15 per zombie
        
        # Bonus for tax efficiency
        if len(report['checks']['tax_loss_harvest']) > 0:
            score += 5  # Tax optimization opportunity is good
        
        # Benchmark adjustment
        benchmark = report['checks'].get('benchmark', {})
        if 'outperformance' in benchmark:
            if benchmark['outperformance'] > 0:
                score += min(benchmark['outperformance'] * 100, 20)  # Up to +20 for beating SPY
            else:
                score += max(benchmark['outperformance'] * 100, -20)  # Up to -20 for trailing
        
        return max(0, min(100, score))


if __name__ == "__main__":
    # Test the portfolio optimizer
    optimizer = PortfolioOptimizer()
    
    # Sample positions (simulate current portfolio state)
    test_positions = [
        {
            "symbol": "GME",
            "qty": 10,
            "market_value": 300,
            "cost_basis": 350,
            "unrealized_pl_pct": -0.14,
            "sector": "Consumer Cyclical",
            "entry_date": (datetime.utcnow() - timedelta(days=45)).isoformat(),
            "avg_daily_volume": 5000000
        },
        {
            "symbol": "AAPL",
            "qty": 5,
            "market_value": 50,
            "cost_basis": 45,
            "unrealized_pl_pct": 0.11,
            "sector": "Technology",
            "entry_date": (datetime.utcnow() - timedelta(days=60)).isoformat(),
            "avg_daily_volume": 50000000
        },
        {
            "symbol": "MSFT",
            "qty": 2,
            "market_value": 15,
            "cost_basis": 20,
            "unrealized_pl_pct": -0.25,
            "sector": "Technology",
            "entry_date": (datetime.utcnow() - timedelta(days=35)).isoformat(),
            "avg_daily_volume": 30000000
        }
    ]
    
    portfolio_value = 366
    
    print("="*60)
    print("PORTFOLIO HEALTH REPORT")
    print("="*60)
    
    report = optimizer.generate_portfolio_report(test_positions, portfolio_value)
    
    print(f"\nPortfolio Value: ${report['portfolio_value']:.2f}")
    print(f"Positions: {report['position_count']}")
    print(f"Health Score: {report['summary']['health_score']:.1f}/100")
    print(f"\nActions Needed: {report['summary']['total_actions_needed']}")
    
    print("\n--- Rebalancing ---")
    for action in report['checks']['rebalancing']:
        print(f"  {action['action'].upper()}: {action['symbol']} - {action['reason']}")
    
    print("\n--- Tax Loss Harvest ---")
    for harvest in report['checks']['tax_loss_harvest']:
        print(f"  {harvest['symbol']}: {harvest['reason']} (Priority: {harvest['priority']})")
    
    print("\n--- Correlation Warnings ---")
    for warning in report['checks']['correlation']:
        print(f"  {warning['symbol_a']} <-> {warning['symbol_b']}: {warning['correlation']:.2f}")
    
    print("\n--- Zombies ---")
    for zombie in report['checks']['zombies']:
        print(f"  {zombie['symbol']}: {zombie['reason']} ({zombie['action']})")
    
    print("\n--- Benchmark ---")
    benchmark = report['checks']['benchmark']
    if 'portfolio_return' in benchmark:
        print(f"  Portfolio Return: {benchmark['portfolio_return']*100:+.2f}%")
        print(f"  SPY Return: {benchmark['spy_return']*100:+.2f}%")
        print(f"  Outperformance: {benchmark['outperformance']*100:+.2f}%")
        if benchmark.get('risk_adjustment'):
            print(f"  Risk Adjustment: {benchmark['risk_adjustment']['action'].upper()}")
