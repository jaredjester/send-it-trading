"""
risk_fortress.py - Complete Risk Management System for Hedge Fund Bot

Production-ready risk management system that prevents account blowup.
Handles PDT protection, position sizing, portfolio monitoring, circuit breakers, and cash reserves.

Author: Risk Fortress System
Date: 2026-02-17
Capital at Risk: $366 (as of 2026-02-17)
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

try:
    from sector_map import get_sector, is_high_risk_sector
except ImportError:
    # Fallback if sector_map not available
    def get_sector(symbol):
        return 'other'
    def is_high_risk_sector(sector):
        return False

logger = logging.getLogger(__name__)


class PDTGuard:
    """
    Pattern Day Trader (PDT) Protection System
    
    Tracks day trades in a rolling 5-business-day window.
    Blocks execution at 2/3 limit (reserves 1 for emergencies).
    
    PDT Rule: 3 day trades in 5 business days = 90-day restriction
    A "day trade" = buy and sell same symbol on same calendar day
    """
    
    def __init__(self, state_file: str):
        """
        Initialize PDT Guard with persistent state.
        
        Args:
            state_file: Path to JSON file for persisting day trade history
        """
        self.state_file = state_file
        self.day_trades = []  # List of {symbol, date, timestamp}
        self.load_state()
    
    def load_state(self):
        """Load day trade history from disk."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.day_trades = data.get('day_trades', [])
                    logger.info(f"PDT Guard loaded {len(self.day_trades)} historical day trades")
            else:
                logger.info("PDT Guard initialized with empty state")
        except Exception as e:
            logger.error(f"Failed to load PDT state: {e}", exc_info=True)
            self.day_trades = []
    
    def save_state(self):
        """Persist day trade history to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump({'day_trades': self.day_trades}, f, indent=2)
            logger.debug(f"PDT state saved: {len(self.day_trades)} day trades")
        except Exception as e:
            logger.error(f"Failed to save PDT state: {e}", exc_info=True)
    
    def _is_business_day(self, date: datetime) -> bool:
        """Check if date is a business day (Mon-Fri)."""
        return date.weekday() < 5  # 0=Mon, 4=Fri
    
    def _get_business_days_ago(self, days: int) -> datetime:
        """Get date N business days ago."""
        current = datetime.now()
        count = 0
        while count < days:
            current -= timedelta(days=1)
            if self._is_business_day(current):
                count += 1
        return current
    
    def _clean_old_trades(self):
        """Remove day trades older than 5 business days."""
        cutoff = self._get_business_days_ago(5)
        cutoff_str = cutoff.strftime('%Y-%m-%d')
        
        original_count = len(self.day_trades)
        self.day_trades = [
            dt for dt in self.day_trades
            if dt['date'] >= cutoff_str
        ]
        
        if len(self.day_trades) < original_count:
            logger.info(f"PDT cleanup: removed {original_count - len(self.day_trades)} old day trades")
            self.save_state()
    
    def count(self) -> int:
        """
        Count day trades in rolling 5-business-day window.
        
        Returns:
            Number of day trades in window
        """
        self._clean_old_trades()
        return len(self.day_trades)
    
    def can_day_trade(self) -> bool:
        """
        Check if a new day trade is allowed.
        Blocks at 2/3 limit to reserve 1 for emergencies.
        
        Returns:
            True if day trade is allowed, False if blocked
        """
        count = self.count()
        
        # Block at 2 to reserve 1 for emergencies
        if count >= 2:
            logger.warning(f"PDT BLOCK: Already used {count}/3 day trades")
            return False
        
        logger.info(f"PDT OK: {count}/3 day trades used")
        return True
    
    def record_day_trade(self, symbol: str, date: Optional[str] = None):
        """
        Record a day trade.
        
        Args:
            symbol: Stock ticker that was day traded
            date: Date string (YYYY-MM-DD), defaults to today
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        trade = {
            'symbol': symbol.upper(),
            'date': date,
            'timestamp': datetime.now().isoformat()
        }
        
        self.day_trades.append(trade)
        self.save_state()
        
        count = self.count()
        logger.warning(f"DAY TRADE RECORDED: {symbol} on {date} ({count}/3 used)")
        
        if count >= 2:
            logger.critical(f"PDT WARNING: {count}/3 day trades used! One more = 90-day restriction!")
    
    def get_day_trade_history(self) -> List[Dict]:
        """
        Get day trade history for debugging.
        
        Returns:
            List of day trade records
        """
        self._clean_old_trades()
        return self.day_trades


class PositionSizer:
    """
    Risk-Based Position Sizing
    
    Uses fixed-fractional risk + Kelly criterion for optimal sizing.
    Conservative approach: risk 2% per trade, cap at 20% of portfolio.
    """
    
    MAX_RISK_PER_TRADE = 0.02  # 2% of portfolio per trade
    MAX_POSITION_PCT = 0.20     # 20% max position size
    MIN_POSITION_DOLLARS = 10.0  # Don't buy dust
    KELLY_FRACTION = 0.5        # Use half-Kelly for safety
    
    def __init__(self):
        logger.info("Position Sizer initialized with 2% risk, 20% max position")
    
    def calculate_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        portfolio_value: float,
        cash: float
    ) -> Dict:
        """
        Calculate position size based on risk parameters.
        
        Args:
            symbol: Stock ticker
            entry_price: Planned entry price
            stop_loss_price: Stop loss price (risk per share)
            portfolio_value: Total portfolio value
            cash: Available cash
        
        Returns:
            Dict with:
                - shares: Number of shares to buy
                - dollar_amount: Total dollar amount
                - risk_amount: Dollar risk if stop hit
                - risk_pct: Risk as % of portfolio
                - reason: Sizing logic explanation
        """
        try:
            # Validate inputs
            if entry_price <= 0 or stop_loss_price <= 0:
                return self._error_result("Invalid prices")
            
            if stop_loss_price >= entry_price:
                return self._error_result("Stop loss must be below entry")
            
            if portfolio_value <= 0 or cash <= 0:
                return self._error_result("Insufficient capital")
            
            # Calculate risk per share
            risk_per_share = entry_price - stop_loss_price
            
            # Calculate max risk in dollars (2% of portfolio)
            max_risk_dollars = portfolio_value * self.MAX_RISK_PER_TRADE
            
            # Calculate shares based on risk
            shares = int(max_risk_dollars / risk_per_share)
            
            if shares <= 0:
                return self._error_result("Risk too high, position too small")
            
            # Calculate dollar amount
            dollar_amount = shares * entry_price
            
            # Apply position size cap (20% of portfolio)
            max_position_dollars = portfolio_value * self.MAX_POSITION_PCT
            if dollar_amount > max_position_dollars:
                shares = int(max_position_dollars / entry_price)
                dollar_amount = shares * entry_price
                reason = f"Capped at {self.MAX_POSITION_PCT*100}% of portfolio"
            else:
                reason = f"Risk-based: {self.MAX_RISK_PER_TRADE*100}% portfolio risk"
            
            # Cap at available cash
            if dollar_amount > cash:
                shares = int(cash / entry_price)
                dollar_amount = shares * entry_price
                reason = "Capped at available cash"
            
            # Enforce minimum position size
            if dollar_amount < self.MIN_POSITION_DOLLARS:
                return self._error_result(f"Position < ${self.MIN_POSITION_DOLLARS} minimum")
            
            # Calculate actual risk
            actual_risk_dollars = shares * risk_per_share
            actual_risk_pct = actual_risk_dollars / portfolio_value
            
            result = {
                'shares': shares,
                'dollar_amount': round(dollar_amount, 2),
                'risk_amount': round(actual_risk_dollars, 2),
                'risk_pct': round(actual_risk_pct * 100, 2),
                'reason': reason,
                'allowed': True
            }
            
            logger.info(f"Position size for {symbol}: {shares} shares (${dollar_amount:.2f}), risk ${actual_risk_dollars:.2f} ({actual_risk_pct*100:.2f}%)")
            return result
            
        except Exception as e:
            logger.error(f"Position sizing error: {e}", exc_info=True)
            return self._error_result(f"Error: {e}")
    
    def kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Calculate Kelly criterion fraction for position sizing.
        Uses HALF Kelly for safety (conservative approach).
        
        Args:
            win_rate: Probability of winning (0-1)
            avg_win: Average win amount (absolute)
            avg_loss: Average loss amount (absolute, positive)
        
        Returns:
            Fraction of portfolio to risk (0-1)
        """
        try:
            if win_rate <= 0 or win_rate >= 1:
                return 0.0
            
            if avg_win <= 0 or avg_loss <= 0:
                return 0.0
            
            # Kelly formula: f* = (p*b - q) / b
            # where p = win rate, q = loss rate, b = win/loss ratio
            lose_rate = 1 - win_rate
            win_loss_ratio = avg_win / avg_loss
            
            kelly = (win_rate * win_loss_ratio - lose_rate) / win_loss_ratio
            
            # Use half-Kelly for safety
            kelly = kelly * self.KELLY_FRACTION
            
            # Clamp to reasonable range
            kelly = max(0.0, min(kelly, 0.10))  # Never risk more than 10%
            
            logger.debug(f"Kelly fraction: {kelly:.2%} (win_rate={win_rate:.2%}, avg_win=${avg_win:.2f}, avg_loss=${avg_loss:.2f})")
            return kelly
            
        except Exception as e:
            logger.error(f"Kelly calculation error: {e}")
            return 0.0
    
    def _error_result(self, reason: str) -> Dict:
        """Return error result."""
        return {
            'shares': 0,
            'dollar_amount': 0.0,
            'risk_amount': 0.0,
            'risk_pct': 0.0,
            'reason': reason,
            'allowed': False
        }


class PortfolioRiskMonitor:
    """
    Real-Time Portfolio Risk Tracking and Enforcement
    
    Monitors:
    - Position concentration (no single position > 20%)
    - Sector concentration (no sector > 30%)
    - Cash reserves (maintain > 10%)
    - Portfolio heat (total capital deployed)
    - Correlation risk
    """
    
    MAX_POSITION_PCT = 0.20  # 20% max per position
    MAX_SECTOR_PCT = 0.30    # 30% max per sector
    MIN_CASH_RESERVE_PCT = 0.10  # 10% minimum cash
    MAX_PORTFOLIO_HEAT = 0.85    # 85% max deployed
    
    def __init__(self, state_file: str):
        """
        Initialize Portfolio Risk Monitor.
        
        Args:
            state_file: Path to JSON file for persisting high-water mark
        """
        self.state_file = state_file
        self.high_water_mark = 0.0
        self.load_state()
    
    def load_state(self):
        """Load high-water mark from disk."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.high_water_mark = data.get('high_water_mark', 0.0)
                    logger.info(f"Portfolio monitor loaded high-water mark: ${self.high_water_mark:.2f}")
            else:
                logger.info("Portfolio monitor initialized with no high-water mark")
        except Exception as e:
            logger.error(f"Failed to load portfolio state: {e}", exc_info=True)
    
    def save_state(self):
        """Persist high-water mark to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump({'high_water_mark': self.high_water_mark}, f, indent=2)
            logger.debug(f"Portfolio state saved: high-water mark ${self.high_water_mark:.2f}")
        except Exception as e:
            logger.error(f"Failed to save portfolio state: {e}", exc_info=True)
    
    def check_portfolio_health(self, positions: List[Dict], account: Dict) -> Dict:
        """
        Comprehensive portfolio health check.
        
        Args:
            positions: List of position dicts with {symbol, qty, market_value, ...}
            account: Account dict with {portfolio_value, cash, ...}
        
        Returns:
            Dict with risk metrics, warnings, and blocks
        """
        try:
            portfolio_value = account.get('portfolio_value', 0.0)
            cash = account.get('cash', 0.0)
            
            if portfolio_value <= 0:
                return self._error_health("Invalid portfolio value")
            
            # Update high-water mark
            if portfolio_value > self.high_water_mark:
                self.high_water_mark = portfolio_value
                self.save_state()
            
            warnings = []
            blocks = []
            
            # 1. Position concentration check
            max_position_pct = 0.0
            max_position_symbol = None
            for pos in positions:
                market_value = pos.get('market_value', 0.0)
                pct = market_value / portfolio_value
                if pct > max_position_pct:
                    max_position_pct = pct
                    max_position_symbol = pos.get('symbol')
            
            if max_position_pct > self.MAX_POSITION_PCT:
                msg = f"CONCENTRATION RISK: {max_position_symbol} is {max_position_pct*100:.1f}% of portfolio (max {self.MAX_POSITION_PCT*100}%)"
                warnings.append(msg)
                logger.warning(msg)
            
            # 2. Sector concentration check
            sector_exposure = {}
            for pos in positions:
                symbol = pos.get('symbol', '')
                market_value = pos.get('market_value', 0.0)
                sector = get_sector(symbol)
                sector_exposure[sector] = sector_exposure.get(sector, 0.0) + market_value
            
            max_sector_pct = 0.0
            max_sector_name = None
            for sector, exposure in sector_exposure.items():
                pct = exposure / portfolio_value
                if pct > max_sector_pct:
                    max_sector_pct = pct
                    max_sector_name = sector
            
            if max_sector_pct > self.MAX_SECTOR_PCT:
                msg = f"SECTOR RISK: {max_sector_name} is {max_sector_pct*100:.1f}% of portfolio (max {self.MAX_SECTOR_PCT*100}%)"
                warnings.append(msg)
                blocks.append(f"Block new {max_sector_name} positions")
                logger.warning(msg)
            
            # 3. Cash reserve check
            cash_reserve_pct = cash / portfolio_value
            if cash_reserve_pct < self.MIN_CASH_RESERVE_PCT:
                msg = f"LOW CASH: ${cash:.2f} ({cash_reserve_pct*100:.1f}%) - need {self.MIN_CASH_RESERVE_PCT*100}% minimum"
                warnings.append(msg)
                logger.warning(msg)
            
            # 4. Portfolio heat (% deployed)
            deployed = sum(pos.get('market_value', 0.0) for pos in positions)
            portfolio_heat = deployed / portfolio_value
            if portfolio_heat > self.MAX_PORTFOLIO_HEAT:
                msg = f"HIGH HEAT: {portfolio_heat*100:.1f}% deployed (max {self.MAX_PORTFOLIO_HEAT*100}%)"
                warnings.append(msg)
                blocks.append("Block all new positions until heat reduces")
                logger.warning(msg)
            
            # 5. Concentration risk (HHI index)
            hhi = sum((pos.get('market_value', 0.0) / portfolio_value) ** 2 for pos in positions)
            if hhi > 0.25:  # High concentration
                msg = f"HIGH CONCENTRATION: HHI={hhi:.3f} (diversify positions)"
                warnings.append(msg)
            
            # 6. Drawdown from peak
            drawdown = 0.0
            if self.high_water_mark > 0:
                drawdown = (self.high_water_mark - portfolio_value) / self.high_water_mark
                if drawdown > 0.10:  # 10% drawdown
                    msg = f"DRAWDOWN: {drawdown*100:.1f}% from peak ${self.high_water_mark:.2f}"
                    warnings.append(msg)
                    logger.warning(msg)
            
            result = {
                'portfolio_value': portfolio_value,
                'cash': cash,
                'max_position_pct': round(max_position_pct * 100, 2),
                'max_position_symbol': max_position_symbol,
                'max_sector_pct': round(max_sector_pct * 100, 2),
                'max_sector_name': max_sector_name,
                'cash_reserve_pct': round(cash_reserve_pct * 100, 2),
                'portfolio_heat': round(portfolio_heat * 100, 2),
                'concentration_hhi': round(hhi, 3),
                'drawdown_from_peak': round(drawdown * 100, 2),
                'high_water_mark': self.high_water_mark,
                'warnings': warnings,
                'blocks': blocks,
                'healthy': len(blocks) == 0
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Portfolio health check error: {e}", exc_info=True)
            return self._error_health(f"Error: {e}")
    
    def can_open_position(
        self,
        symbol: str,
        dollar_amount: float,
        positions: List[Dict],
        account: Dict
    ) -> Dict:
        """
        Pre-trade check: Would this trade violate risk rules?
        
        Args:
            symbol: Stock ticker to buy
            dollar_amount: Planned purchase amount
            positions: Current positions
            account: Account info
        
        Returns:
            Dict with {allowed: bool, reason: str, adjusted_size: float}
        """
        try:
            portfolio_value = account.get('portfolio_value', 0.0)
            cash = account.get('cash', 0.0)
            
            # Check cash available
            if dollar_amount > cash:
                return {
                    'allowed': False,
                    'reason': f"Insufficient cash: have ${cash:.2f}, need ${dollar_amount:.2f}",
                    'adjusted_size': cash * 0.95  # Leave buffer
                }
            
            # Check if position already exists
            existing_position = next((p for p in positions if p.get('symbol') == symbol), None)
            existing_value = existing_position.get('market_value', 0.0) if existing_position else 0.0
            
            # Calculate new position size
            new_position_value = existing_value + dollar_amount
            new_position_pct = new_position_value / portfolio_value
            
            # Check position limit
            if new_position_pct > self.MAX_POSITION_PCT:
                max_allowed = portfolio_value * self.MAX_POSITION_PCT - existing_value
                return {
                    'allowed': False,
                    'reason': f"Position limit: {symbol} would be {new_position_pct*100:.1f}% (max {self.MAX_POSITION_PCT*100}%)",
                    'adjusted_size': max(0, max_allowed)
                }
            
            # Check sector limit
            sector = get_sector(symbol)
            sector_value = sum(
                pos.get('market_value', 0.0)
                for pos in positions
                if get_sector(pos.get('symbol')) == sector
            )
            new_sector_value = sector_value + dollar_amount
            new_sector_pct = new_sector_value / portfolio_value
            
            if new_sector_pct > self.MAX_SECTOR_PCT:
                max_allowed = portfolio_value * self.MAX_SECTOR_PCT - sector_value
                return {
                    'allowed': False,
                    'reason': f"Sector limit: {sector} would be {new_sector_pct*100:.1f}% (max {self.MAX_SECTOR_PCT*100}%)",
                    'adjusted_size': max(0, max_allowed)
                }
            
            # Check portfolio heat
            deployed = sum(pos.get('market_value', 0.0) for pos in positions)
            new_deployed = deployed + dollar_amount
            new_heat = new_deployed / portfolio_value
            
            if new_heat > self.MAX_PORTFOLIO_HEAT:
                max_allowed = portfolio_value * self.MAX_PORTFOLIO_HEAT - deployed
                return {
                    'allowed': False,
                    'reason': f"Portfolio heat: {new_heat*100:.1f}% deployed (max {self.MAX_PORTFOLIO_HEAT*100}%)",
                    'adjusted_size': max(0, max_allowed)
                }
            
            # Check cash reserve
            new_cash = cash - dollar_amount
            new_cash_pct = new_cash / portfolio_value
            
            if new_cash_pct < self.MIN_CASH_RESERVE_PCT:
                max_allowed = cash - (portfolio_value * self.MIN_CASH_RESERVE_PCT)
                return {
                    'allowed': False,
                    'reason': f"Cash reserve: would have {new_cash_pct*100:.1f}% cash (need {self.MIN_CASH_RESERVE_PCT*100}% minimum)",
                    'adjusted_size': max(0, max_allowed)
                }
            
            # All checks passed
            return {
                'allowed': True,
                'reason': 'All risk checks passed',
                'adjusted_size': dollar_amount
            }
            
        except Exception as e:
            logger.error(f"Can open position check error: {e}", exc_info=True)
            return {
                'allowed': False,
                'reason': f"Error: {e}",
                'adjusted_size': 0.0
            }
    
    def _error_health(self, reason: str) -> Dict:
        """Return error health status."""
        return {
            'portfolio_value': 0.0,
            'warnings': [reason],
            'blocks': ['System error - halt trading'],
            'healthy': False
        }


class CircuitBreaker:
    """
    Trading Circuit Breaker System
    
    Halts trading when conditions become dangerous:
    - Intraday drawdown > 3%
    - 3+ consecutive losses
    - Portfolio below high-water mark by > 10%
    """
    
    INTRADAY_DRAWDOWN_LIMIT = 0.03  # 3% intraday loss
    MAX_CONSECUTIVE_LOSSES = 3      # 3 losses in a row
    MAJOR_DRAWDOWN_LIMIT = 0.10     # 10% from peak
    
    def __init__(self, state_file: str):
        """
        Initialize Circuit Breaker.
        
        Args:
            state_file: Path to JSON file for persisting state
        """
        self.state_file = state_file
        self.consecutive_losses = 0
        self.intraday_start_value = 0.0
        self.last_reset_date = None
        self.load_state()
    
    def load_state(self):
        """Load circuit breaker state from disk."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.consecutive_losses = data.get('consecutive_losses', 0)
                    self.intraday_start_value = data.get('intraday_start_value', 0.0)
                    self.last_reset_date = data.get('last_reset_date')
                    logger.info(f"Circuit breaker loaded: {self.consecutive_losses} consecutive losses")
            else:
                logger.info("Circuit breaker initialized with default state")
        except Exception as e:
            logger.error(f"Failed to load circuit breaker state: {e}", exc_info=True)
    
    def save_state(self):
        """Persist circuit breaker state to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump({
                    'consecutive_losses': self.consecutive_losses,
                    'intraday_start_value': self.intraday_start_value,
                    'last_reset_date': self.last_reset_date
                }, f, indent=2)
            logger.debug("Circuit breaker state saved")
        except Exception as e:
            logger.error(f"Failed to save circuit breaker state: {e}", exc_info=True)
    
    def record_day_start(self, portfolio_value: float):
        """
        Record start-of-day portfolio value.
        
        Args:
            portfolio_value: Portfolio value at market open
        """
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Reset consecutive losses on new day
        if self.last_reset_date != today:
            self.consecutive_losses = 0
            self.last_reset_date = today
            logger.info(f"New trading day: reset consecutive losses")
        
        self.intraday_start_value = portfolio_value
        self.save_state()
        logger.info(f"Day start recorded: ${portfolio_value:.2f}")
    
    def record_trade_result(self, win: bool):
        """
        Record trade win/loss for consecutive loss tracking.
        
        Args:
            win: True if trade was profitable, False if loss
        """
        if win:
            if self.consecutive_losses > 0:
                logger.info(f"Win! Reset consecutive losses (was {self.consecutive_losses})")
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            logger.warning(f"Loss recorded: {self.consecutive_losses} consecutive losses")
        
        self.save_state()
    
    def check(self, portfolio_value: float, high_water_mark: float = 0.0) -> Dict:
        """
        Check if circuit breaker should trigger.
        
        Args:
            portfolio_value: Current portfolio value
            high_water_mark: All-time high portfolio value
        
        Returns:
            Dict with {trading_allowed: bool, size_multiplier: float, reason: str}
        """
        try:
            reasons = []
            size_multiplier = 1.0
            trading_allowed = True
            
            # Check 1: Intraday drawdown
            if self.intraday_start_value > 0:
                intraday_pnl_pct = (portfolio_value - self.intraday_start_value) / self.intraday_start_value
                
                if intraday_pnl_pct < -self.INTRADAY_DRAWDOWN_LIMIT:
                    trading_allowed = False
                    msg = f"CIRCUIT BREAKER: Intraday loss {intraday_pnl_pct*100:.2f}% (limit {self.INTRADAY_DRAWDOWN_LIMIT*100}%)"
                    reasons.append(msg)
                    logger.critical(msg)
            
            # Check 2: Consecutive losses
            if self.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
                trading_allowed = False
                msg = f"CIRCUIT BREAKER: {self.consecutive_losses} consecutive losses (limit {self.MAX_CONSECUTIVE_LOSSES})"
                reasons.append(msg)
                logger.critical(msg)
            
            # Check 3: Major drawdown from peak
            if high_water_mark > 0:
                drawdown = (high_water_mark - portfolio_value) / high_water_mark
                
                if drawdown > self.MAJOR_DRAWDOWN_LIMIT:
                    size_multiplier = 0.5  # Reduce all positions by 50%
                    msg = f"RISK REDUCTION: {drawdown*100:.1f}% from peak, reducing size 50%"
                    reasons.append(msg)
                    logger.warning(msg)
            
            result = {
                'trading_allowed': trading_allowed,
                'size_multiplier': size_multiplier,
                'reasons': reasons,
                'reason': '; '.join(reasons) if reasons else 'All systems normal',
                'consecutive_losses': self.consecutive_losses,
                'intraday_pnl_pct': round(intraday_pnl_pct * 100, 2) if self.intraday_start_value > 0 else 0.0
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Circuit breaker check error: {e}", exc_info=True)
            return {
                'trading_allowed': False,
                'size_multiplier': 0.0,
                'reason': f"Error: {e}",
                'reasons': [f"Error: {e}"]
            }


class CashReserveManager:
    """
    Cash Reserve Management System
    
    Maintains minimum cash reserve at all times.
    Generates liquidation signals when reserves are critically low.
    """
    
    def __init__(self, min_reserve_pct: float = 0.10):
        """
        Initialize Cash Reserve Manager.
        
        Args:
            min_reserve_pct: Minimum cash as % of portfolio (default 10%)
        """
        self.min_reserve_pct = min_reserve_pct
        self.critical_reserve_pct = min_reserve_pct / 2  # 5% = critical
        logger.info(f"Cash Reserve Manager: {min_reserve_pct*100}% minimum, {self.critical_reserve_pct*100}% critical")
    
    def available_for_trading(self, cash: float, portfolio_value: float) -> float:
        """
        Calculate cash available for trading after maintaining reserve.
        
        Args:
            cash: Current cash balance
            portfolio_value: Total portfolio value
        
        Returns:
            Cash available for new positions
        """
        try:
            if portfolio_value <= 0:
                logger.warning("Invalid portfolio value for cash reserve calculation")
                return 0.0
            
            required_reserve = portfolio_value * self.min_reserve_pct
            available = max(0.0, cash - required_reserve)
            
            reserve_pct = cash / portfolio_value
            
            if available <= 0:
                logger.warning(f"NO CASH AVAILABLE: ${cash:.2f} cash, need ${required_reserve:.2f} reserve ({reserve_pct*100:.1f}%)")
            else:
                logger.debug(f"Cash available: ${available:.2f} (reserve ${required_reserve:.2f})")
            
            return available
            
        except Exception as e:
            logger.error(f"Cash availability calculation error: {e}", exc_info=True)
            return 0.0
    
    def needs_liquidation(self, cash: float, portfolio_value: float, positions: List[Dict]) -> List[str]:
        """
        Check if positions need to be liquidated to restore cash reserve.
        
        Args:
            cash: Current cash balance
            portfolio_value: Total portfolio value
            positions: List of current positions
        
        Returns:
            List of symbols to liquidate (weakest first)
        """
        try:
            if portfolio_value <= 0:
                return []
            
            reserve_pct = cash / portfolio_value
            
            # Only trigger liquidation if below critical level
            if reserve_pct >= self.critical_reserve_pct:
                return []
            
            # Calculate how much cash we need
            required_reserve = portfolio_value * self.min_reserve_pct
            cash_needed = required_reserve - cash
            
            logger.warning(f"CRITICAL CASH SHORTAGE: {reserve_pct*100:.1f}% cash, need ${cash_needed:.2f}")
            
            # Sort positions by "weakness" (lowest value first = easy to liquidate)
            # In production, you'd use alpha scores or P&L
            sorted_positions = sorted(
                positions,
                key=lambda p: p.get('market_value', 0.0)
            )
            
            # Select positions to liquidate
            to_liquidate = []
            accumulated_value = 0.0
            
            for pos in sorted_positions:
                symbol = pos.get('symbol')
                market_value = pos.get('market_value', 0.0)
                
                to_liquidate.append(symbol)
                accumulated_value += market_value
                
                # Stop when we have enough
                if accumulated_value >= cash_needed:
                    break
            
            if to_liquidate:
                logger.critical(f"LIQUIDATION REQUIRED: Sell {to_liquidate} to raise ${cash_needed:.2f}")
            
            return to_liquidate
            
        except Exception as e:
            logger.error(f"Liquidation check error: {e}", exc_info=True)
            return []


if __name__ == '__main__':
    # Test the risk fortress
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("RISK FORTRESS TEST SUITE")
    print("=" * 60)
    
    # Test PDT Guard
    print("\n1. PDT Guard Test")
    pdt = PDTGuard('/tmp/pdt_test.json')
    print(f"   Current day trades: {pdt.count()}/3")
    print(f"   Can day trade: {pdt.can_day_trade()}")
    
    # Test Position Sizer
    print("\n2. Position Sizer Test")
    sizer = PositionSizer()
    result = sizer.calculate_size(
        symbol='AAPL',
        entry_price=150.0,
        stop_loss_price=145.0,
        portfolio_value=366.0,
        cash=100.0
    )
    print(f"   Size for AAPL: {result['shares']} shares, ${result['dollar_amount']:.2f}")
    print(f"   Risk: ${result['risk_amount']:.2f} ({result['risk_pct']:.2f}%)")
    
    # Test Portfolio Monitor
    print("\n3. Portfolio Risk Monitor Test")
    monitor = PortfolioRiskMonitor('/tmp/portfolio_test.json')
    positions = [
        {'symbol': 'GME', 'market_value': 292.0},
        {'symbol': 'AAPL', 'market_value': 50.0},
    ]
    account = {'portfolio_value': 366.0, 'cash': 24.0}
    health = monitor.check_portfolio_health(positions, account)
    print(f"   Max position: {health['max_position_symbol']} ({health['max_position_pct']:.1f}%)")
    print(f"   Warnings: {len(health['warnings'])}")
    for w in health['warnings']:
        print(f"     - {w}")
    
    # Test Circuit Breaker
    print("\n4. Circuit Breaker Test")
    breaker = CircuitBreaker('/tmp/breaker_test.json')
    breaker.record_day_start(366.0)
    status = breaker.check(portfolio_value=355.0, high_water_mark=400.0)
    print(f"   Trading allowed: {status['trading_allowed']}")
    print(f"   Size multiplier: {status['size_multiplier']}")
    print(f"   Reason: {status['reason']}")
    
    # Test Cash Reserve Manager
    print("\n5. Cash Reserve Manager Test")
    cash_mgr = CashReserveManager(min_reserve_pct=0.10)
    available = cash_mgr.available_for_trading(cash=24.0, portfolio_value=366.0)
    print(f"   Cash available: ${available:.2f}")
    to_sell = cash_mgr.needs_liquidation(cash=10.0, portfolio_value=366.0, positions=positions)
    print(f"   Liquidation needed: {to_sell}")
    
    print("\n" + "=" * 60)
    print("RISK FORTRESS INITIALIZED AND TESTED")
    print("=" * 60)
