import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List
import numpy as np
import pandas as pd
from send import send_email
from utils import api_caller
from json_logger import JsonLogger
import time

from alpacaFunctions import (
    buy_stock_asset,
    sell_stock_asset,
    buy_option,
    sell_option,
    get_all_positions,
    is_market_hours,
    get_buying_power,
    fetch_options_chain,
    get_historical_data,
    calculate_volatility,
    get_corporate_actions_by_symbol,
    fetch_stock_news,
    get_account_value,
    get_all_positions,
    trading_client,
    place_trailing_stop_order
)

from alpaca.trading.enums import OrderSide, QueryOrderStatus

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# add this in your code (maybe after your logging.basicConfig setup)

class EmailLogHandler(logging.Handler):
    def __init__(self, level=logging.ERROR):
        super().__init__(level)

    def emit(self, record):
        try:
            log_entry = self.format(record)
            send_email(log_entry)
        except Exception as e:
            print(f"❌ Failed to send log email: {e}")
        
        # Email handler (emails ERROR and above)
email_handler = EmailLogHandler(level=logging.ERROR)
email_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(email_handler)

SHOULD_CLOSE_POSITIONS = False

_last_recommendations = None
_last_fetch_time = None
_daily_request_count = 0
_last_request_day = None

def fetch_portfolio_recommendations(target_income: float = 50000, min_interval: int = 44):
    global _last_recommendations, _last_fetch_time, _daily_request_count, _last_request_day
    now = time.time()
    today = datetime.now().date()
    if _last_request_day != today:
        _daily_request_count = 0
        _last_request_day = today
    if _daily_request_count >= 1000:
        logger.warning("Daily API request limit reached. Returning cached recommendations.")
        return _last_recommendations
    if _last_fetch_time is not None and (now - _last_fetch_time) < min_interval:
        return _last_recommendations
    URL = "https://www.investassist.app/api/options/options-enhanced"
    try:
        response = api_caller(URL, "POST", params={"targetIncome": target_income})
        _last_recommendations = response
        _last_fetch_time = now
        _daily_request_count += 1
        return response
    except Exception as e:
        logger.error(f"Error fetching portfolio recommendations: {e}")
        return _last_recommendations  # Return last known if error

class PerformanceTracker:
    def __init__(self):
        self.trades = []
        self.daily_returns = []
        self.portfolio_values = []
        self.strategy_metrics = {
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "avg_trade_duration": 0.0,
            "avg_profit_per_trade": 0.0
        }
        self.position_metrics = {}
        self.last_update = None

    def add_trade(self, trade_data: Dict):
        """Add a completed trade to the tracker."""
        try:
            # Ensure all required fields are present
            required_fields = ["symbol", "entry_time", "shares", "entry_price"]
            for field in required_fields:
                if field not in trade_data:
                    logger.error(f"Missing required field '{field}' in trade data")
                    return

            # Initialize missing fields with default values
            trade_data.setdefault("exit_time", datetime.now())  # Use current time instead of None
            trade_data.setdefault("exit_price", trade_data["entry_price"])  # Use entry price as default
            trade_data.setdefault("profit", 0.0)

            # Ensure entry_time is a datetime object
            if isinstance(trade_data["entry_time"], str):
                trade_data["entry_time"] = datetime.fromisoformat(trade_data["entry_time"])
            elif not isinstance(trade_data["entry_time"], datetime):
                trade_data["entry_time"] = datetime.now()

            # Ensure exit_time is a datetime object
            if isinstance(trade_data["exit_time"], str):
                trade_data["exit_time"] = datetime.fromisoformat(trade_data["exit_time"])
            elif not isinstance(trade_data["exit_time"], datetime):
                trade_data["exit_time"] = datetime.now()

            self.trades.append({
                **trade_data,
                "timestamp": datetime.now()
            })
            self._update_metrics()
        except Exception as e:
            logger.error(f"Error adding trade to tracker: {e}")

    def add_daily_return(self, return_value: float):
        """Add daily portfolio return."""
        self.daily_returns.append({
            "date": datetime.now().date(),
            "return": return_value
        })
        self._update_metrics()

    def add_portfolio_value(self, value: float):
        """Add current portfolio value."""
        self.portfolio_values.append({
            "timestamp": datetime.now(),
            "value": value
        })
        self._update_metrics()

    def _update_metrics(self):
        """Update all performance metrics."""
        if not self.trades:
            return

        # Calculate win rate
        winning_trades = [t for t in self.trades if t["profit"] > 0]
        self.strategy_metrics["win_rate"] = len(winning_trades) / len(self.trades)

        # Calculate profit factor
        gross_profit = sum(t["profit"] for t in winning_trades)
        gross_loss = abs(sum(t["profit"] for t in self.trades if t["profit"] < 0))
        self.strategy_metrics["profit_factor"] = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Calculate Sharpe ratio
        if self.daily_returns:
            returns = pd.Series([d["return"] for d in self.daily_returns])
            self.strategy_metrics["sharpe_ratio"] = self._calculate_sharpe_ratio(returns)

        # Calculate max drawdown
        if self.portfolio_values:
            values = pd.Series([v["value"] for v in self.portfolio_values])
            self.strategy_metrics["max_drawdown"] = self._calculate_max_drawdown(values)

        # Calculate average trade duration and profit
        if self.trades:
            durations = [(t["exit_time"] - t["entry_time"]).total_seconds() / 3600 for t in self.trades]
            self.strategy_metrics["avg_trade_duration"] = sum(durations) / len(durations)
            self.strategy_metrics["avg_profit_per_trade"] = sum(t["profit"] for t in self.trades) / len(self.trades)

        self.last_update = datetime.now()

    def _calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.05) -> float:
        """Calculate Sharpe ratio from returns series."""
        if len(returns) < 2:
            return 0.0
        excess_returns = returns - (risk_free_rate / 252)
        if excess_returns.std() == 0:
            return 0.0
        return np.sqrt(252) * (excess_returns.mean() / excess_returns.std())

    def _calculate_max_drawdown(self, values: pd.Series) -> float:
        """Calculate maximum drawdown from value series."""
        rolling_max = values.expanding().max()
        drawdowns = (values - rolling_max) / rolling_max
        return abs(drawdowns.min())

    def get_performance_report(self) -> Dict:
        """Generate a comprehensive performance report."""
        return {
            "metrics": self.strategy_metrics,
            "trade_count": len(self.trades),
            "winning_trades": len([t for t in self.trades if t["profit"] > 0]),
            "losing_trades": len([t for t in self.trades if t["profit"] < 0]),
            "total_profit": sum(t["profit"] for t in self.trades),
            "avg_trade_duration_hours": self.strategy_metrics["avg_trade_duration"],
            "last_update": self.last_update
        }

class OptionsTradingBot:
    def __init__(self, target_income: float = 50000):
        self.target_income = target_income
        self.portfolio: Dict = {}
        self.json_logger = JsonLogger("options_strategy.json")
        self.cache = {
            "market_data": {},
            "options_data": {},
            "last_update": {},
            "cache_duration": 300  # 5 minutes
        }
        self.circuit_breaker = {
            "active": False,
            "triggered_at": None,
            "max_daily_loss": 0.05,  # 5% max daily loss
            "max_drawdown": 0.15,    # 15% max drawdown
            "recovery_threshold": 0.02  # 2% recovery threshold
        }
        self.risk_management = {
            "max_position_size": 0.20,  # 20% of portfolio
            "max_options_exposure": {
                "calls": 0.25,  # 25% of shares in covered calls
                "puts": 0.50    # 50% of shares in cash-secured puts
            },
            "min_strike_distance": {
                "calls": 0.05,  # 5% OTM for calls
                "puts": 0.05    # 5% OTM for puts
            },
            "max_expiration": 45,  # 45 days
            "min_premium": {
                "calls": 0.02,  # 2% of stock price
                "puts": 0.015   # 1.5% of stock price
            },
            "volatility_thresholds": {
                "low": 0.15,    # Below 15% volatility
                "medium": 0.25, # 15-25% volatility
                "high": 0.35    # Above 35% volatility
            },
            "stop_loss": {
                "stock": 0.10,  # 10% stop loss for stocks
                "options": 0.30  # 30% stop loss for options (reduced from 50%)
            },
            "take_profit": {
                "stock": 0.20,  # 20% take profit for stocks
                "options": 0.75  # 75% take profit for options (reduced from 100%)
            },
            "min_holding_period": {
                "stock": 24,    # 24 hours minimum holding period
                "options": 24   # 24 hours minimum holding period
            },
            "max_roll_cost": 0.15,  # Maximum cost of rolling positions (15% of premium)
            "sharpe_ratio": {
                "min_threshold": 0.5,    # Minimum acceptable Sharpe ratio
                "target": 1.0,           # Target Sharpe ratio
                "excellent": 1.5,        # Excellent Sharpe ratio
                "risk_free_rate": 0.05   # Risk-free rate (5% annual)
            },
            "max_drawdown": {
                "threshold": 0.15,       # Maximum allowed drawdown (15%)
                "window": 252,           # Lookback window (1 year of trading days)
                "warning": 0.10          # Warning threshold (10%)
            },
            "portfolio_risk": {
                "max_leverage": 1.5,      # Maximum portfolio leverage
                "max_correlation": 0.7,    # Maximum correlation between positions
                "min_diversification": 5,   # Minimum number of positions
                "max_sector_exposure": 0.25 # Maximum exposure per sector
            }
        }
        self.market_regime = "unknown"  # unknown, trending, sideways, volatile
        self.sentiment_indicators = {
            "vix": None,  # Volatility Index
            "put_call_ratio": None,  # Options market sentiment
            "market_breadth": None,  # Market breadth indicators
            "sector_rotation": None,  # Sector performance
            "last_update": None
        }
        self.sentiment_thresholds = {
            "vix": {
                "low": 15,    # Low volatility
                "medium": 25, # Medium volatility
                "high": 35    # High volatility
            },
            "put_call_ratio": {
                "bullish": 0.8,   # Below 0.8 indicates bullish sentiment
                "neutral": 1.2,   # Between 0.8 and 1.2 is neutral
                "bearish": 1.5    # Above 1.5 indicates bearish sentiment
            },
            "market_breadth": {
                "bullish": 0.6,   # Above 60% of stocks above moving average
                "neutral": 0.4,   # Between 40-60% is neutral
                "bearish": 0.3    # Below 30% is bearish
            }
        }
        self.sector_exposure = {
            "technology": 0.0,
            "healthcare": 0.0,
            "financial": 0.0,
            "consumer": 0.0,
            "industrial": 0.0,
            "energy": 0.0,
            "materials": 0.0,
            "utilities": 0.0,
            "real_estate": 0.0,
            "communication": 0.0
        }
        self.max_sector_exposure = 0.25  # Maximum 25% exposure per sector
        self.correlation_threshold = 0.7  # Maximum correlation between positions
        self.buying_power_management = {
            "min_cash_buffer": 0.10,        # 10% minimum cash buffer
            "options_margin_buffer": 0.20,   # 20% buffer for options margin
            "assignment_buffer": 0.15,       # 15% buffer for potential assignments
            "max_position_size": 0.15,       # 15% max position size
        }
        self.performance_tracker = PerformanceTracker()
        self.risk_scoring = {
            "position_risk": {},
            "portfolio_risk": 0.0,
            "market_risk": 0.0,
            "last_update": None
        }
        self.earnings_calendar = {}
        self.news_sentiment = {}
        self.sector_rotation = {
            "last_update": None,
            "sector_performance": {},
            "rotation_signals": {}
        }

    def calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.05) -> float:
        """
        Calculate the Sharpe ratio for a series of returns.
        
        Args:
            returns (pd.Series): Series of returns
            risk_free_rate (float): Annual risk-free rate
            
        Returns:
            float: Sharpe ratio
        """
        if len(returns) < 2:
            return 0.0
            
        # Calculate excess returns
        excess_returns = returns - (risk_free_rate / 252)  # Daily risk-free rate
        
        # Calculate annualized Sharpe ratio
        if excess_returns.std() == 0:
            return 0.0
            
        sharpe = np.sqrt(252) * (excess_returns.mean() / excess_returns.std())
        return sharpe

    def calculate_max_drawdown(self, prices: pd.Series) -> float:
        """
        Calculate the maximum drawdown from a series of prices.
        
        Args:
            prices (pd.Series): Series of prices
            
        Returns:
            float: Maximum drawdown as a percentage
        """
        rolling_max = prices.expanding().max()
        drawdowns = (prices - rolling_max) / rolling_max
        return abs(drawdowns.min())

    async def analyze_risk_metrics(self, symbol: str) -> Dict:
        """
        Analyze both Sharpe ratio and maximum drawdown for a symbol.
        
        Returns:
            Dict: Analysis results including Sharpe ratio, drawdown, and recommendations
        """
        try:
            # Get historical data
            historical_data = await get_historical_data(symbol)
            if historical_data is None or historical_data.empty:
                return {
                    "sharpe_ratio": 0.0,
                    "max_drawdown": 0.0,
                    "quality": "unknown",
                    "recommendation": "insufficient_data"
                }

            # Calculate daily returns
            returns = historical_data['close'].pct_change().dropna()
            
            # Calculate Sharpe ratio
            sharpe_ratio = self.calculate_sharpe_ratio(
                returns,
                self.risk_management["sharpe_ratio"]["risk_free_rate"]
            )
            
            # Calculate maximum drawdown
            max_drawdown = self.calculate_max_drawdown(historical_data['close'])
            
            # Determine quality based on both metrics
            if (sharpe_ratio >= self.risk_management["sharpe_ratio"]["excellent"] and 
                max_drawdown <= self.risk_management["max_drawdown"]["warning"]):
                quality = "excellent"
            elif (sharpe_ratio >= self.risk_management["sharpe_ratio"]["target"] and 
                  max_drawdown <= self.risk_management["max_drawdown"]["threshold"]):
                quality = "good"
            elif (sharpe_ratio >= self.risk_management["sharpe_ratio"]["min_threshold"] and 
                  max_drawdown <= self.risk_management["max_drawdown"]["threshold"] * 1.2):
                quality = "acceptable"
            else:
                quality = "poor"
            
            # Generate recommendation
            if quality == "excellent":
                recommendation = "increase_position"
            elif quality == "good":
                recommendation = "maintain_position"
            elif quality == "acceptable":
                recommendation = "reduce_position"
            else:
                recommendation = "consider_exit"
            
            return {
                "sharpe_ratio": sharpe_ratio,
                "max_drawdown": max_drawdown,
                "quality": quality,
                "recommendation": recommendation,
                "returns_mean": returns.mean() * 252,  # Annualized return
                "returns_std": returns.std() * np.sqrt(252)  # Annualized volatility
            }
            
        except Exception as e:
            logger.error(f"Error analyzing risk metrics for {symbol}: {e}")
            return {
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "quality": "unknown",
                "recommendation": "error"
            }

    def fetch_portfolio_recommendations(self) -> Optional[Dict]:
        """Fetch portfolio recommendations from the API."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return fetch_portfolio_recommendations(self.target_income)
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed after {max_retries} attempts: {e}")
                    return None
                asyncio.sleep(2 ** attempt)  # Exponential backoff

    async def analyze_options_chain(self, symbol: str, current_price: float) -> Dict:
        """Analyze options chain to find optimal strikes and premiums."""
        try:
            logger.info(f"Fetching options chain for {symbol}")
            calls_data, puts_data = await asyncio.gather(
                fetch_options_chain(symbol, "call"),
                fetch_options_chain(symbol, "put")
            )
            
            # Add detailed logging
            logger.info(f"Options data received for {symbol}:")
            logger.info(f"Calls data structure: {type(calls_data)}")
            logger.info(f"Puts data structure: {type(puts_data)}")
            if calls_data:
                logger.info(f"Number of call contracts: {len(calls_data.get('snapshots', {}))}")
            if puts_data:
                logger.info(f"Number of put contracts: {len(puts_data.get('snapshots', {}))}")
            
            if not calls_data or not puts_data:
                logger.warning(f"Missing options data for {symbol}: calls={bool(calls_data)}, puts={bool(puts_data)}")
                return None

            def analyze_options(options_data: Dict, is_call: bool) -> Optional[Dict]:
                if not options_data or "snapshots" not in options_data:
                    logger.warning(f"No valid options data for {symbol} {is_call} options")
                    return None

                target_strike = current_price * (1.05 if is_call else 0.95)
                best_option = None
                best_score = float('-inf')
                min_premium = current_price * (self.risk_management["min_premium"]["calls"] if is_call else self.risk_management["min_premium"]["puts"])

                logger.debug(f"Analyzing {is_call} options for {symbol} with target strike ${target_strike:.2f}")

                # Analyze volatility surface
                volatility_surface = []
                for contract, data in options_data.get("snapshots", {}).items():
                    if "latestQuote" not in data or "greeks" not in data:
                        continue
                    
                    quote = data["latestQuote"]
                    greeks = data["greeks"]
                    strike = float(contract[-8:]) / 1000
                    premium = (quote["bp"] + quote["ap"]) / 2
                    
                    # Skip if premium is too low
                    if premium < min_premium:
                        continue
                    
                    # Advanced Greeks analysis
                    delta = abs(float(greeks.get("delta", 0)))
                    theta = float(greeks.get("theta", 0))
                    vega = float(greeks.get("vega", 0))
                    gamma = float(greeks.get("gamma", 0))
                    rho = float(greeks.get("rho", 0))
                    implied_vol = float(greeks.get("implied_volatility", 0))
                    
                    # Volatility surface analysis
                    volatility_surface.append({
                        "strike": strike,
                        "implied_vol": implied_vol,
                        "premium": premium
                    })
                    
                    # Calculate Greeks-based score
                    greeks_score = self._calculate_greeks_score(
                        delta=delta,
                        theta=theta,
                        vega=vega,
                        gamma=gamma,
                        rho=rho,
                        is_call=is_call
                    )
                    
                    # Calculate strike distance score
                    strike_score = 1 - (abs(strike - target_strike) / current_price)
                    
                    # Calculate premium score
                    premium_score = premium / current_price
                    
                    # Calculate volatility score
                    vol_score = 1 - (abs(implied_vol - self._get_target_volatility(symbol)) / 0.5)
                    
                    # Weighted total score
                    total_score = (
                        0.3 * greeks_score +
                        0.2 * strike_score +
                        0.2 * premium_score +
                        0.3 * vol_score
                    )
                    
                    if total_score > best_score:
                        best_option = {
                            "strike": strike,
                            "premium": premium,
                            "contract": contract,
                            "greeks": {
                                "delta": delta,
                                "theta": theta,
                                "vega": vega,
                                "gamma": gamma,
                                "rho": rho,
                                "implied_volatility": implied_vol
                            }
                        }
                        best_score = total_score

                # Analyze volatility surface
                if volatility_surface:
                    self._analyze_volatility_surface(volatility_surface, is_call)

                if best_option:
                    logger.info(f"Found best {is_call} option for {symbol}: strike=${best_option['strike']:.2f}, premium=${best_option['premium']:.2f}")
                else:
                    logger.warning(f"No suitable {is_call} options found for {symbol}")

                return best_option

            # Analyze both options types
            best_call = analyze_options(calls_data, True)
            best_put = analyze_options(puts_data, False)

            # Allow partial success - return what we have
            if not best_call and not best_put:
                logger.warning(f"Could not find any suitable options for {symbol}")
                return None
            elif not best_call or not best_put:
                logger.info(f"Found partial options for {symbol}: calls={bool(best_call)}, puts={bool(best_put)}")

            return {
                "calls": best_call,
                "puts": best_put
            }
        except Exception as e:
            logger.error(f"Error analyzing options chain for {symbol}: {e}")
            return None

    def _calculate_greeks_score(self, delta: float, theta: float, vega: float, gamma: float, rho: float, is_call: bool) -> float:
        """Calculate a score based on Greeks values."""
        try:
            # Delta score (higher for calls, lower for puts)
            delta_score = delta if is_call else (1 - delta)
            
            # Theta score (higher is better)
            theta_score = min(1, theta / 0.01)  # Normalize to 0-1 range
            
            # Vega score (lower is better for stability)
            vega_score = 1 - min(1, vega / 100)  # Normalize to 0-1 range
            
            # Gamma score (balanced approach)
            gamma_score = 1 - abs(gamma - 0.01) / 0.01  # Target 0.01 gamma
            
            # Rho score (less important for short-term options)
            rho_score = 0.5  # Neutral score for rho
            
            # Weighted average of scores
            return (
                0.3 * delta_score +
                0.3 * theta_score +
                0.2 * vega_score +
                0.1 * gamma_score +
                0.1 * rho_score
            )
            
        except Exception as e:
            logger.error(f"Error calculating Greeks score: {e}")
            return 0.0

    def _get_target_volatility(self, symbol: str) -> float:
        """Get target volatility for a symbol based on historical data."""
        try:
            # This would typically be calculated from historical data
            # For now, return a reasonable default
            return 0.25  # 25% target volatility
        except Exception as e:
            logger.error(f"Error getting target volatility for {symbol}: {e}")
            return 0.25

    def _analyze_volatility_surface(self, volatility_surface: List[Dict], is_call: bool):
        """Analyze the volatility surface for potential opportunities."""
        try:
            # Sort by strike price
            sorted_surface = sorted(volatility_surface, key=lambda x: x["strike"])
            
            # Calculate volatility skew
            if len(sorted_surface) >= 2:
                vol_skew = sorted_surface[-1]["implied_vol"] - sorted_surface[0]["implied_vol"]
                logger.info(f"Volatility skew for {is_call} options: {vol_skew:.2%}")
                
                # Log significant skew
                if abs(vol_skew) > 0.1:  # 10% skew threshold
                    logger.warning(f"Significant volatility skew detected for {is_call} options: {vol_skew:.2%}")
            
            # Analyze volatility term structure
            # This would typically look at different expiration dates
            # For now, we'll just log the surface
            logger.debug(f"Volatility surface for {is_call} options:")
            for point in sorted_surface:
                logger.debug(f"Strike: ${point['strike']:.2f}, IV: {point['implied_vol']:.1%}")
                
        except Exception as e:
            logger.error(f"Error analyzing volatility surface: {e}")

    async def detect_market_regime(self, symbol: str) -> str:
        """Detect current market regime using volatility, trend analysis, and Sharpe ratio."""
        try:
            # Get historical data for volatility calculation
            historical_data = await get_historical_data(symbol)
            if historical_data is None or historical_data.empty:
                return "unknown"

            # Calculate volatility (20-day)
            returns = historical_data['close'].pct_change()
            volatility = returns.std() * np.sqrt(252)  # Annualized volatility

            # Calculate trend (20-day moving average)
            ma20 = historical_data['close'].rolling(window=20).mean()
            current_price = historical_data['close'].iloc[-1]
            trend_strength = (current_price - ma20.iloc[-1]) / ma20.iloc[-1]

            # Get Sharpe ratio analysis
            sharpe_analysis = await self.analyze_risk_metrics(symbol)
            sharpe_ratio = sharpe_analysis["sharpe_ratio"]
            sharpe_quality = sharpe_analysis["quality"]

            # Determine regime with Sharpe ratio consideration
            if volatility > self.risk_management["volatility_thresholds"]["high"]:
                if sharpe_quality in ["excellent", "good"]:
                    return "trending"  # High volatility but good risk-adjusted returns
                return "volatile"
            elif abs(trend_strength) > 0.05:  # 5% trend threshold
                if sharpe_quality == "poor":
                    return "volatile"  # Strong trend but poor risk-adjusted returns
                return "trending"
            else:
                if sharpe_quality in ["excellent", "good"]:
                    return "trending"  # Sideways but good risk-adjusted returns
                return "sideways"

        except Exception as e:
            logger.error(f"Error detecting market regime for {symbol}: {e}")
            return "unknown"

    async def analyze_market_sentiment(self) -> Dict:
        """
        Analyze overall market sentiment using multiple indicators.
        
        Returns:
            Dict: Market sentiment analysis including VIX, put/call ratio, and market breadth
        """
        try:
            # Get VIX data
            vix_data = await get_historical_data("VIX")
            if vix_data is not None:
                self.sentiment_indicators["vix"] = vix_data['close'].iloc[-1]

            # Get put/call ratio
            options_data = await fetch_options_chain("SPY")
            if options_data:
                total_puts = sum(1 for contract in options_data.get("puts", {}).values() 
                               if contract.get("volume", 0) > 0)
                total_calls = sum(1 for contract in options_data.get("calls", {}).values() 
                                if contract.get("volume", 0) > 0)
                self.sentiment_indicators["put_call_ratio"] = (
                    total_puts / total_calls if total_calls > 0 else 1.5
                )

            # Get market breadth (using SPY as proxy)
            spy_data = await get_historical_data("SPY")
            if spy_data is not None:
                ma20 = spy_data['close'].rolling(window=20).mean()
                ma50 = spy_data['close'].rolling(window=50).mean()
                current_price = spy_data['close'].iloc[-1]
                
                # Calculate percentage of stocks above moving averages
                above_ma20 = (current_price > ma20.iloc[-1])
                above_ma50 = (current_price > ma50.iloc[-1])
                
                self.sentiment_indicators["market_breadth"] = {
                    "above_ma20": above_ma20,
                    "above_ma50": above_ma50
                }

            # Determine overall sentiment
            sentiment_score = 0
            sentiment_factors = []

            # VIX contribution
            if self.sentiment_indicators["vix"]:
                if self.sentiment_indicators["vix"] < self.sentiment_thresholds["vix"]["low"]:
                    sentiment_score += 1
                    sentiment_factors.append("Low VIX")
                elif self.sentiment_indicators["vix"] > self.sentiment_thresholds["vix"]["high"]:
                    sentiment_score -= 1
                    sentiment_factors.append("High VIX")

            # Put/Call ratio contribution
            if self.sentiment_indicators["put_call_ratio"]:
                if self.sentiment_indicators["put_call_ratio"] < self.sentiment_thresholds["put_call_ratio"]["bullish"]:
                    sentiment_score += 1
                    sentiment_factors.append("Bullish Put/Call ratio")
                elif self.sentiment_indicators["put_call_ratio"] > self.sentiment_thresholds["put_call_ratio"]["bearish"]:
                    sentiment_score -= 1
                    sentiment_factors.append("Bearish Put/Call ratio")

            # Market breadth contribution
            if self.sentiment_indicators["market_breadth"]:
                breadth = self.sentiment_indicators["market_breadth"]
                if breadth["above_ma20"] and breadth["above_ma50"]:
                    sentiment_score += 1
                    sentiment_factors.append("Strong market breadth")
                elif not breadth["above_ma20"] and not breadth["above_ma50"]:
                    sentiment_score -= 1
                    sentiment_factors.append("Weak market breadth")

            # Determine overall sentiment
            if sentiment_score >= 2:
                overall_sentiment = "bullish"
            elif sentiment_score <= -1:
                overall_sentiment = "bearish"
            else:
                overall_sentiment = "neutral"

            self.sentiment_indicators["last_update"] = datetime.now()
            
            return {
                "overall_sentiment": overall_sentiment,
                "sentiment_score": sentiment_score,
                "factors": sentiment_factors,
                "indicators": self.sentiment_indicators
            }

        except Exception as e:
            logger.error(f"Error analyzing market sentiment: {e}")
            return {
                "overall_sentiment": "unknown",
                "sentiment_score": 0,
                "factors": ["Error analyzing sentiment"],
                "indicators": self.sentiment_indicators
            }

    async def analyze_sector_exposure(self, symbol: str) -> Dict:
        """
        Analyze sector exposure and correlation with existing positions.
        
        Returns:
            Dict: Sector analysis including exposure and correlation metrics
        """
        try:
            # Get sector information for the symbol
            # This would typically come from a sector classification API
            # For now, we'll use a simplified approach
            sector = await self._get_sector(symbol)
            
            # Calculate current sector exposure
            current_exposure = self.sector_exposure.get(sector, 0.0)
            
            # Calculate correlation with existing positions
            correlations = []
            for existing_symbol in self.portfolio.keys():
                if existing_symbol != symbol:
                    correlation = await self._calculate_correlation(symbol, existing_symbol)
                    correlations.append({
                        "symbol": existing_symbol,
                        "correlation": correlation
                    })
            
            # Find highest correlation
            max_correlation = max((c["correlation"] for c in correlations), default=0.0)
            
            return {
                "sector": sector,
                "current_exposure": current_exposure,
                "max_allowed": self.max_sector_exposure,
                "correlations": correlations,
                "max_correlation": max_correlation,
                "is_acceptable": (
                    current_exposure < self.max_sector_exposure and
                    max_correlation < self.correlation_threshold
                )
            }
            
        except Exception as e:
            logger.error(f"Error analyzing sector exposure for {symbol}: {e}")
            return {
                "sector": "unknown",
                "current_exposure": 0.0,
                "max_allowed": self.max_sector_exposure,
                "correlations": [],
                "max_correlation": 0.0,
                "is_acceptable": False
            }

    async def _get_sector(self, symbol: str) -> str:
        """Get sector classification for a symbol."""
        # This would typically come from a sector classification API
        # For now, we'll use a simplified mapping
        sector_mapping = {
            "AAPL": "technology",
            "MSFT": "technology",
            "GOOGL": "technology",
            "AMZN": "consumer",
            "META": "communication",
            "NVDA": "technology",
            "TSLA": "consumer",
            "JPM": "financial",
            "V": "financial",
            "WMT": "consumer",
            "JNJ": "healthcare",
            "PG": "consumer",
            "XOM": "energy",
            "MA": "financial",
            "HD": "consumer"
        }
        return sector_mapping.get(symbol, "unknown")

    async def _calculate_correlation(self, symbol1: str, symbol2: str) -> float:
        """Calculate correlation between two symbols."""
        try:
            # Get historical data for both symbols
            data1 = await get_historical_data(symbol1)
            data2 = await get_historical_data(symbol2)
            
            if data1 is None or data2 is None:
                return 0.0
            
            # Calculate returns
            returns1 = data1['close'].pct_change()
            returns2 = data2['close'].pct_change()
            
            # Calculate correlation
            correlation = returns1.corr(returns2)
            return abs(correlation)  # Use absolute correlation
            
        except Exception as e:
            logger.error(f"Error calculating correlation between {symbol1} and {symbol2}: {e}")
            return 0.0

    async def adjust_position_size(self, symbol: str, base_size: float) -> float:
        """Adjust position size based on all risk factors including sector exposure."""
        try:
            # Get market regime and sentiment
            regime = await self.detect_market_regime(symbol)
            sentiment = await self.analyze_market_sentiment()
            
            # Get risk metrics
            risk_metrics = await self.analyze_risk_metrics(symbol)
            sharpe_quality = risk_metrics['quality']
            max_drawdown = risk_metrics['max_drawdown']
            
            # Base multiplier on market conditions
            multiplier = 1.0
            
            # Adjust based on market regime
            if regime == "trending":
                multiplier *= 1.2
            elif regime == "volatile":
                multiplier *= 0.8
            
            # Adjust based on sentiment (convert sentiment score to float)
            sentiment_score = float(sentiment.get('sentiment_score', 0))
            if sentiment_score > 0.7:
                multiplier *= 1.1
            elif sentiment_score < 0.3:
                multiplier *= 0.9
            
            # Adjust based on risk metrics
            if sharpe_quality == "excellent":
                multiplier *= 1.2
            elif sharpe_quality == "poor":
                multiplier *= 0.5
            
            # Reduce size if drawdown is high
            if max_drawdown > self.risk_management['max_drawdown']['threshold']:
                multiplier *= 0.5
            elif max_drawdown > self.risk_management['max_drawdown']['warning']:
                multiplier *= 0.75
            
            return base_size * multiplier

        except Exception as e:
            logger.error(f"Error adjusting position size for {symbol}: {e}")
            return base_size

    async def implement_options_strategy(self, stock: Dict) -> Dict:
        """Implement options strategy for a single stock."""
        try:
            # Check buying power first
            buying_power = await get_buying_power()
            base_shares = stock["shares"]
            current_price = stock["currentPrice"]

            # Adjust position size based on market conditions
            shares = await self.adjust_position_size(stock["symbol"], base_shares)
            total_investment = shares * current_price

            # Scale down position size if we don't have enough buying power
            if total_investment > buying_power:
                max_shares = int(buying_power / current_price)
                if max_shares < 1:
                    logger.warning(f"Cannot buy any shares of {stock['symbol']} with available buying power: ${buying_power}")
                    return None
                shares = max_shares
                logger.info(f"Scaled down position for {stock['symbol']} to {shares} shares due to buying power constraints")

            # Buy the stock if we don't have a position
            position = await self._get_position(stock["symbol"])
            if not position:
                send_email(f"🚀 {stock['symbol']} bought {shares} shares")
                await buy_stock_asset(stock["symbol"], qty=shares)

            # Analyze options chain for optimal strikes
            options_analysis = await self.analyze_options_chain(stock["symbol"], current_price)
            
            # Add retry logic for options analysis
            if not options_analysis:
                logger.warning(f"First attempt failed for {stock['symbol']}, retrying...")
                await asyncio.sleep(2)  # Wait 2 seconds before retry
                options_analysis = await self.analyze_options_chain(stock["symbol"], current_price)
                
            if not options_analysis:
                logger.error(f"Failed to analyze options chain for {stock['symbol']} after retry")
                return None  # Return None instead of stock-only strategy
            
            # Use analyzed options data
            call_strike = options_analysis["calls"]["strike"]
            monthly_call_premium = options_analysis["calls"]["premium"]
            put_strike = options_analysis["puts"]["strike"]
            monthly_put_premium = options_analysis["puts"]["premium"]

            # Calculate Monthly Income
            monthly_income = {
                "dividends": (stock["annualDividendIncome"] / 12) * (shares / stock["shares"]),
                "call_premium": monthly_call_premium * shares,
                "put_premium": monthly_put_premium * shares,
                "total": stock["monthlyIncome"] * (shares / stock["shares"])
            }

            strategy_data = {
                "stock": {**stock, "shares": shares},
                "monthly_income": monthly_income,
                "options_strategy": {
                    "call_strike": call_strike,
                    "put_strike": put_strike,
                    "monthly_call_premium": monthly_call_premium,
                    "monthly_put_premium": monthly_put_premium,
                    "call_contract": options_analysis["calls"]["contract"],
                    "put_contract": options_analysis["puts"]["contract"],
                    "greeks": {
                        "calls": options_analysis["calls"]["greeks"],
                        "puts": options_analysis["puts"]["greeks"]
                    }
                }
            }

            # Track the trade with initial profit of 0
            self.performance_tracker.add_trade({
                "symbol": stock["symbol"],
                "entry_time": datetime.now(),
                "exit_time": None,  # Will be updated when position is closed
                "shares": shares,
                "entry_price": current_price,
                "exit_price": None,  # Will be updated when position is closed
                "profit": 0.0,  # Initial profit is 0
                "strategy": strategy_data
            })

            return strategy_data

        except Exception as e:
            logger.error(f"Error implementing options strategy for {stock['symbol']}: {e}")
            return None

    async def _get_position(self, symbol: str) -> Optional[Dict]:
        """Get current position for a symbol."""
        positions = get_all_positions()
        return next((p for p in positions if p.symbol == symbol), None)

    async def monitor_prices(self):
        """Monitor stock prices and manage options positions."""
        while True:
            try:
                for symbol, stock_data in self.portfolio.items():
                    position = await self._get_position(symbol)
                    if not position:
                        logger.debug(f"No active position for {symbol}")
                        continue

                    current_price = stock_data["stock"]["currentPrice"]
                    entry_price = float(position.avg_entry_price)
                    unrealized_pl = float(position.unrealized_pl)
                    unrealized_plpc = float(position.unrealized_plpc)
                    
                    # Log price data to JSON
                    self.json_logger.add_price_data(
                        symbol=symbol,
                        price=current_price,
                        timestamp=datetime.now()
                    )
                    
                    logger.info(f"Position Update - {symbol}: Price=${current_price:.2f}, Entry=${entry_price:.2f}, P/L=${unrealized_pl:.2f} ({unrealized_plpc*100:.1f}%)")
                    
                    # Only check options if we have an options strategy
                    options_strategy = stock_data.get("options_strategy")
                    if options_strategy and isinstance(options_strategy, dict):
                        call_strike = options_strategy.get("call_strike")
                        put_strike = options_strategy.get("put_strike")

                        if call_strike:
                            distance_to_call = (call_strike - current_price) / current_price * 100
                            logger.debug(f"{symbol} Call Strike: ${call_strike:.2f} ({distance_to_call:.1f}% OTM)")
                            if current_price >= call_strike * 0.99:  # Within 1% of call strike
                                logger.info(f"Rolling covered call for {symbol} - Price (${current_price:.2f}) near strike (${call_strike:.2f})")
                                await self._roll_covered_call(stock_data)
                        
                        if put_strike:
                            distance_to_put = (current_price - put_strike) / current_price * 100
                            logger.debug(f"{symbol} Put Strike: ${put_strike:.2f} ({distance_to_put:.1f}% OTM)")
                            if current_price <= put_strike * 1.01:  # Within 1% of put strike
                                logger.info(f"Rolling cash-secured put for {symbol} - Price (${current_price:.2f}) near strike (${put_strike:.2f})")
                                await self._roll_cash_secured_put(stock_data)

                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error in price monitoring: {e}")
                await asyncio.sleep(60)

    async def _roll_covered_call(self, stock_data: Dict):
        """Roll up and out the covered call position."""
        try:
            symbol = stock_data["stock"]["symbol"]
            # First check if we have an existing call position
            positions = get_all_positions()
            existing_call = next((p for p in positions if p.symbol.startswith(symbol) and p.symbol.endswith('C')), None)
            
            if existing_call:
                # Close existing call
                await sell_option(
                    symbol,
                    qty=1,
                    option_type="call",
                    contract_type="short_term"
                )
                logger.info(f"Closed existing call position for {symbol}")
                
                # Log options action
                self.json_logger.add_options_data(
                    symbol=symbol,
                    action="close_call",
                    details={
                        "position": existing_call.symbol,
                        "quantity": 1,
                        "reason": "rolling"
                    },
                    timestamp=datetime.now()
                )
            else:
                logger.info(f"No existing call position found for {symbol}, proceeding to open new position")
            
            # Open new call at higher strike
            new_strike = stock_data["stock"]["currentPrice"] * 1.05  # 5% OTM
            send_email(f"🚀 {symbol} opened new covered call for {new_strike}")
            await buy_option(
                symbol,
                qty=1,
                option_type="call",
                contract_type="short_term",
                strike_price=new_strike
            )
            
            # Log new options position
            self.json_logger.add_options_data(
                symbol=symbol,
                action="open_call",
                details={
                    "strike_price": new_strike,
                    "quantity": 1,
                    "reason": "rolling"
                },
                timestamp=datetime.now()
            )
            
            logger.info(f"Opened new covered call for {symbol} at strike {new_strike}")
        except Exception as e:
            logger.error(f"Error rolling covered call for {stock_data['stock']['symbol']}: {e}")

    async def _roll_cash_secured_put(self, stock_data: Dict):
        """Roll down and out the cash-secured put position."""
        try:
            # Close existing put
            await sell_option(
                stock_data["stock"]["symbol"],
                qty=1,
                option_type="put",
                contract_type="short_term"
            )
            
            # Open new put at lower strike
            new_strike = stock_data["stock"]["currentPrice"] * 0.95  # 5% OTM
            send_email(f"🚀 {stock_data['stock']['symbol']} opened new cash-secured put for {new_strike}")
            await buy_option(
                stock_data["stock"]["symbol"],
                qty=1,
                option_type="put",
                contract_type="short_term",
                strike_price=new_strike
            )
            
            logger.info(f"Rolled cash-secured put for {stock_data['stock']['symbol']} to strike {new_strike}")
        except Exception as e:
            logger.error(f"Error rolling cash-secured put for {stock_data['stock']['symbol']}: {e}")

    async def monthly_options_management(self):
        """Manage monthly options positions."""
        while True:
            try:
                if is_market_hours():
                    for _, stock_data in self.portfolio.items():
                        # Close expiring options
                        await self._close_expiring_options(stock_data)
                        
                        # Open new options positions
                        await self._open_new_options_positions(stock_data)
                
                # Wait until next month
                next_month = datetime.now().replace(day=1) + timedelta(days=32)
                await asyncio.sleep((next_month - datetime.now()).total_seconds())
            except Exception as e:
                logger.error(f"Error in monthly options management: {e}")
                await asyncio.sleep(3600)  # Wait an hour before retrying

    async def _close_expiring_options(self, stock_data: Dict):
        """Close expiring options positions."""
        try:
            # Close calls
            await sell_option(
                stock_data["stock"]["symbol"],
                qty=1,
                option_type="call",
                contract_type="short_term"
            )
            
            # Close puts
            await sell_option(
                stock_data["stock"]["symbol"],
                qty=1,
                option_type="put",
                contract_type="short_term"
            )
            
            logger.info(f"Closed expiring options for {stock_data['stock']['symbol']}")
        except Exception as e:
            logger.error(f"Error closing expiring options for {stock_data['stock']['symbol']}: {e}")

    async def _open_new_options_positions(self, stock_data: Dict):
        """Open new options positions."""
        try:
            # Open new call
            send_email(f"🚀 {stock_data['stock']['symbol']} opened new call for {stock_data['options_strategy']['call_strike']}")
            await buy_option(
                stock_data["stock"]["symbol"],
                qty=1,
                option_type="call",
                contract_type="short_term",
                strike_price=stock_data["options_strategy"]["call_strike"]
            )
            
            # Open new put
            send_email(f"🚀 {stock_data['stock']['symbol']} opened new put for {stock_data['options_strategy']['put_strike']}")
            await buy_option(
                stock_data["stock"]["symbol"],
                qty=1,
                option_type="put",
                contract_type="short_term",
                strike_price=stock_data["options_strategy"]["put_strike"]
            )
            
            logger.info(f"Opened new options positions for {stock_data['stock']['symbol']}")
        except Exception as e:
            logger.error(f"Error opening new options positions for {stock_data['stock']['symbol']}: {e}")

    async def monitor_stop_losses(self):
        """Monitor positions for stop-loss triggers and risk metric deterioration."""
        while True:
            try:
                for symbol, stock_data in self.portfolio.items():
                    # Check stock position
                    position = await self._get_position(symbol)
                    if not position:
                        continue
                        
                    entry_price = float(position.avg_entry_price)
                    current_price = stock_data["stock"]["currentPrice"]
                    loss_percentage = (entry_price - current_price) / entry_price

                    # Get risk metrics analysis
                    risk_metrics = await self.analyze_risk_metrics(symbol)
                    sharpe_quality = risk_metrics["quality"]
                    max_drawdown = risk_metrics["max_drawdown"]

                    # Log stop loss data
                    self.json_logger.add_stop_loss_data(
                        symbol=symbol,
                        status="monitoring",
                        details={
                            "entry_price": entry_price,
                            "current_price": current_price,
                            "loss_percentage": loss_percentage,
                            "sharpe_quality": sharpe_quality,
                            "max_drawdown": max_drawdown
                        },
                        timestamp=datetime.now()
                    )

                    # Handle options positions differently from stock positions
                    if 'C' in position.symbol or 'P' in position.symbol:
                        # For options, use market/limit orders instead of trailing stops
                        if loss_percentage >= self.risk_management["stop_loss"]["options"]:
                            try:
                                # Place market order to close position
                                await sell_option(
                                    symbol=symbol,
                                    qty=float(position.qty),
                                    option_type="calls" if 'C' in position.symbol else "puts"
                                )
                                logger.info(f"Closed options position {symbol} at stop loss")
                                
                                # Log the stop loss execution
                                self.json_logger.add_stop_loss_data(
                                    symbol=symbol,
                                    status="options_stop_loss_executed",
                                    details={
                                        "loss_percentage": loss_percentage,
                                        "position_size": float(position.qty),
                                        "sharpe_quality": sharpe_quality,
                                        "max_drawdown": max_drawdown
                                    },
                                    timestamp=datetime.now()
                                )
                            except Exception as e:
                                logger.error(f"Failed to execute options stop loss for {symbol}: {str(e)}")
                    else:
                        # For stocks, use trailing stops
                        base_stop_loss = self.risk_management["stop_loss"]["stock"]
                        if sharpe_quality == "excellent" and max_drawdown <= self.risk_management["max_drawdown"]["warning"]:
                            base_stop_loss *= 1.2  # More lenient stop loss
                        elif sharpe_quality == "poor" or max_drawdown > self.risk_management["max_drawdown"]["threshold"]:
                            base_stop_loss *= 0.8  # Tighter stop loss

                        # Place trailing stop order if not already exists
                        open_orders = trading_client.get_orders(status=QueryOrderStatus.OPEN)
                        has_trailing_stop = any(
                            order.symbol == symbol and 
                            order.type == "trailing_stop" and 
                            order.side == OrderSide.SELL 
                            for order in open_orders
                        )

                        if not has_trailing_stop:
                            try:
                                # Calculate trail percentage based on risk metrics
                                trail_percent = base_stop_loss * 100  # Convert to percentage
                                
                                # Place trailing stop order
                                await place_trailing_stop_order(
                                    symbol=symbol,
                                    qty=float(position.qty),
                                    side=OrderSide.SELL,
                                    trail_percent=trail_percent
                                )
                                
                                logger.info(f"Placed trailing stop order for {symbol} with {trail_percent}% trail")
                                
                                # Log trailing stop placement
                                self.json_logger.add_stop_loss_data(
                                    symbol=symbol,
                                    status="trailing_stop_placed",
                                    details={
                                        "trail_percent": trail_percent,
                                        "position_size": float(position.qty),
                                        "sharpe_quality": sharpe_quality,
                                        "max_drawdown": max_drawdown
                                    },
                                    timestamp=datetime.now()
                                )
                            except Exception as e:
                                logger.error(f"Failed to place trailing stop order for {symbol}: {str(e)}")
                                continue

                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Error in monitor_stop_losses: {str(e)}")
                await asyncio.sleep(60)  # Wait before retrying

    async def _roll_option_position(self, stock_data: Dict, option_type: str):
        """Roll an option position to a safer strike."""
        try:
            symbol = stock_data["stock"]["symbol"]
            current_price = stock_data["stock"]["currentPrice"]
            
            # Get current position
            positions = get_all_positions()
            existing_position = next((p for p in positions if p.symbol.startswith(symbol) and p.symbol.endswith('C' if option_type == "calls" else 'P')), None)
            
            if not existing_position:
                logger.warning(f"No existing {option_type} position found for {symbol}")
                return
            
            # Calculate time held
            time_held = datetime.now(timezone.utc) - existing_position.opened_at
            hours_held = time_held.total_seconds() / 3600
            
            # Check minimum holding period
            if hours_held < self.risk_management["min_holding_period"]["options"]:
                logger.info(f"Position {existing_position.symbol} held for {hours_held:.1f} hours, minimum required: {self.risk_management['min_holding_period']['options']}")
                return
            
            # Calculate roll cost
            entry_price = float(existing_position.avg_entry_price)
            current_market_price = float(existing_position.market_value) / float(existing_position.qty)
            roll_cost = abs(current_market_price - entry_price)
            roll_cost_percentage = roll_cost / entry_price
            
            logger.info(f"Roll cost analysis for {existing_position.symbol}:")
            logger.info(f"Entry Price: ${entry_price:.2f}")
            logger.info(f"Current Market Price: ${current_market_price:.2f}")
            logger.info(f"Roll Cost: ${roll_cost:.2f} ({roll_cost_percentage*100:.1f}%)")
            
            # Check if roll cost is acceptable
            if roll_cost_percentage > self.risk_management["max_roll_cost"]:
                logger.warning(f"Roll cost {roll_cost_percentage*100:.1f}% exceeds maximum {self.risk_management['max_roll_cost']*100:.1f}%")
                return
            
            # Close existing position
            await sell_option(
                symbol,
                qty=1,
                option_type=option_type,
                contract_type="short_term"
            )
            
            # Open new position with safer strike
            if option_type == "calls":
                new_strike = current_price * 1.10  # 10% OTM
            else:
                new_strike = current_price * 0.90  # 10% OTM
            send_email(f"🚀 {symbol} rolled {option_type} to {new_strike}")
            await buy_option(
                symbol,
                qty=1,
                option_type=option_type,
                contract_type="short_term",
                strike_price=new_strike
            )
            
            logger.info(f"Rolled {option_type} for {symbol} to strike {new_strike}")
        except Exception as e:
            logger.error(f"Error rolling {option_type} position for {symbol}: {e}")

    async def adjust_stop_loss(self, symbol: str, base_stop: float) -> float:
        # Get risk metrics
        risk_metrics = await self.analyze_risk_metrics(symbol)
        sharpe_quality = risk_metrics['quality']
        volatility = await calculate_volatility(symbol)
        
        # Base stop loss adjustment
        stop_multiplier = 1.0
        
        # Adjust based on Sharpe ratio quality
        if sharpe_quality == "excellent":
            stop_multiplier *= 1.2  # Wider stop for high-quality trades
        elif sharpe_quality == "poor":
            stop_multiplier *= 0.8  # Tighter stop for low-quality trades
        
        # Adjust based on volatility
        if volatility > self.risk_management['volatility_thresholds']['high']:
            stop_multiplier *= 1.2  # Wider stop in high volatility
        elif volatility < self.risk_management['volatility_thresholds']['low']:
            stop_multiplier *= 0.8  # Tighter stop in low volatility
        
        return base_stop * stop_multiplier

    async def optimize_options_strategy(self, symbol: str, current_price: float) -> Dict:
        # Get market conditions
        regime = await self.detect_market_regime(symbol)
        sentiment = await self.analyze_market_sentiment()
        volatility = await self.calculate_volatility(symbol)
        
        # Adjust strategy parameters based on conditions
        strategy_params = {
            "min_strike_distance": self.risk_management["min_strike_distance"].copy(),
            "max_expiration": self.risk_management["max_expiration"],
            "min_premium": self.risk_management["min_premium"].copy()
        }
        
        # Adjust for market regime
        if regime == "trending":
            strategy_params["min_strike_distance"]["calls"] *= 1.2
            strategy_params["min_strike_distance"]["puts"] *= 0.8
        elif regime == "volatile":
            strategy_params["min_strike_distance"]["calls"] *= 0.8
            strategy_params["min_strike_distance"]["puts"] *= 1.2
        
        # Adjust for volatility
        if volatility > self.risk_management['volatility_thresholds']['high']:
            strategy_params["min_premium"]["calls"] *= 1.2
            strategy_params["min_premium"]["puts"] *= 1.2
        
        return strategy_params

    async def check_circuit_breaker(self) -> bool:
        """Check if circuit breaker should be triggered."""
        try:
            # Get daily P/L
            positions = get_all_positions()  # Not async
            daily_pl = sum(float(p.unrealized_pl) for p in positions)
            account_value = float(get_account_value())  # Remove await since this is not async
            daily_loss = daily_pl / account_value

            # Get portfolio drawdown
            portfolio_value_history = await self._get_portfolio_value_history()
            if len(portfolio_value_history) < 2:
                # If we don't have enough data for drawdown calculation,
                # only check daily loss
                max_drawdown = 0.0
            else:
                max_drawdown = self.calculate_max_drawdown(portfolio_value_history)

            # Check conditions
            if (daily_loss < -self.circuit_breaker["max_daily_loss"] or 
                max_drawdown > self.circuit_breaker["max_drawdown"]):
                self.circuit_breaker["active"] = True
                self.circuit_breaker["triggered_at"] = datetime.now()
                logger.warning(f"Circuit breaker triggered: Daily Loss={daily_loss:.2%}, Max Drawdown={max_drawdown:.2%}")
                return True

            return False
        except Exception as e:
            logger.error(f"Error checking circuit breaker: {e}")
            return False

    async def recover_from_circuit_breaker(self) -> bool:
        """Attempt to recover from circuit breaker state."""
        try:
            if not self.circuit_breaker["active"]:
                return True

            # Get current portfolio performance
            positions = get_all_positions()  # Not async
            current_pl = sum(float(p.unrealized_pl) for p in positions)
            account_value = float(get_account_value())  # Remove await since this is not async
            recovery = current_pl / account_value

            # Check if we've recovered enough
            if recovery > self.circuit_breaker["recovery_threshold"]:
                self.circuit_breaker["active"] = False
                logger.info("Circuit breaker deactivated - portfolio recovered")
                return True

            return False
        except Exception as e:
            logger.error(f"Error recovering from circuit breaker: {e}")
            return False

    async def get_cached_data(self, symbol: str, data_type: str) -> Optional[Dict]:
        """Get cached market data if available and not expired."""
        try:
            cache_key = f"{symbol}_{data_type}"
            if cache_key in self.cache["market_data"]:
                last_update = self.cache["last_update"].get(cache_key)
                if last_update and (datetime.now() - last_update).seconds < self.cache["cache_duration"]:
                    return self.cache["market_data"][cache_key]
            return None
        except Exception as e:
            logger.error(f"Error accessing cache for {symbol}: {e}")
            return None

    async def update_cache(self, symbol: str, data_type: str, data: Dict):
        """Update cache with new market data."""
        try:
            cache_key = f"{symbol}_{data_type}"
            self.cache["market_data"][cache_key] = data
            self.cache["last_update"][cache_key] = datetime.now()
        except Exception as e:
            logger.error(f"Error updating cache for {symbol}: {e}")

    async def adjust_portfolio_leverage(self) -> float:
        """Dynamically adjust portfolio leverage based on market conditions."""
        try:
            # Get current leverage
            positions = get_all_positions()  # Not async
            total_exposure = sum(float(p.market_value) for p in positions)
            account_value = float(get_account_value())  # Remove await since this is not async
            current_leverage = total_exposure / account_value

            # Get market conditions
            regime = await self.detect_market_regime("SPY")  # Use SPY as market proxy
            sentiment = await self.analyze_market_sentiment()
            volatility = await calculate_volatility("SPY")

            # Adjust leverage based on conditions
            target_leverage = self.risk_management["portfolio_risk"]["max_leverage"]
            
            if regime == "volatile" or sentiment["overall_sentiment"] == "bearish":
                target_leverage *= 0.8
            elif regime == "trending" and sentiment["overall_sentiment"] == "bullish":
                target_leverage *= 1.2

            if volatility > self.risk_management["volatility_thresholds"]["high"]:
                target_leverage *= 0.8

            return min(target_leverage, self.risk_management["portfolio_risk"]["max_leverage"])
        except Exception as e:
            logger.error(f"Error adjusting portfolio leverage: {e}")
            return self.risk_management["portfolio_risk"]["max_leverage"]

    async def calculate_position_risk_score(self, symbol: str) -> float:
        """Calculate risk score for a specific position."""
        try:
            # Get position data
            position = await self._get_position(symbol)
            if not position:
                return 0.0

            # Get market data
            historical_data = await get_historical_data(symbol)
            if historical_data is None:
                return 0.0

            # Calculate various risk factors
            volatility = historical_data['close'].pct_change().std() * np.sqrt(252)
            beta = await self._calculate_beta(historical_data)
            correlation = await self._calculate_correlation(symbol, "SPY")
            drawdown = self.calculate_max_drawdown(historical_data['close'])
            
            # Get Greeks for options positions
            options_risk = 0.0
            if symbol in self.portfolio and "options_strategy" in self.portfolio[symbol]:
                options_data = self.portfolio[symbol]["options_strategy"]
                for option_type in ["calls", "puts"]:
                    if option_type in options_data and "greeks" in options_data[option_type]:
                        greeks = options_data[option_type]["greeks"]
                        options_risk += abs(float(greeks.get("delta", 0))) * 0.3  # Delta risk
                        options_risk += abs(float(greeks.get("gamma", 0))) * 0.2  # Gamma risk
                        options_risk += abs(float(greeks.get("vega", 0))) * 0.2   # Vega risk
                        options_risk += abs(float(greeks.get("theta", 0))) * 0.3  # Theta risk

            # Weight the risk factors
            risk_score = (
                0.3 * volatility +
                0.2 * beta +
                0.2 * correlation +
                0.2 * drawdown +
                0.1 * options_risk
            )

            # Update risk scoring
            self.risk_scoring["position_risk"][symbol] = risk_score
            self.risk_scoring["last_update"] = datetime.now()

            return risk_score

        except Exception as e:
            logger.error(f"Error calculating risk score for {symbol}: {e}")
            return 0.0

    async def _calculate_beta(self, historical_data: pd.DataFrame) -> float:
        """Calculate beta relative to SPY."""
        try:
            # Get SPY data from API
            spy_data = await get_historical_data("SPY")
            if spy_data is None:
                logger.warning("Could not fetch SPY data for beta calculation")
                return 1.0
                
            # Calculate returns
            spy_returns = spy_data['close'].pct_change()
            stock_returns = historical_data['close'].pct_change()
            
            # Align the dates
            aligned_returns = pd.concat([stock_returns, spy_returns], axis=1).dropna()
            if aligned_returns.empty:
                logger.warning("No overlapping data for beta calculation")
                return 1.0
                
            # Calculate beta
            covariance = aligned_returns.iloc[:, 0].cov(aligned_returns.iloc[:, 1])
            spy_variance = aligned_returns.iloc[:, 1].var()
            
            beta = covariance / spy_variance if spy_variance != 0 else 1.0
            logger.debug(f"Calculated beta: {beta:.2f}")
            return beta
            
        except Exception as e:
            logger.error(f"Error calculating beta: {e}")
            return 1.0

    async def update_earnings_calendar(self):
        """Update earnings calendar for all positions."""
        try:
            for symbol in self.portfolio.keys():
                # Get corporate actions including earnings dates
                actions = get_corporate_actions_by_symbol(symbol)
                
                # Parse earnings dates
                earnings_dates = []
                if isinstance(actions, str):
                    # If actions is a string (error message), skip this symbol
                    logger.warning(f"Could not get corporate actions for {symbol}: {actions}")
                    continue
                    
                for action in actions:
                    if isinstance(action, dict) and action.get("type") == "earnings":
                        earnings_dates.append({
                            "date": action.get("date"),
                            "estimate": action.get("estimate"),
                            "actual": action.get("actual")
                        })
                
                # Update earnings calendar
                self.earnings_calendar[symbol] = {
                    "dates": earnings_dates,
                    "last_update": datetime.now()
                }
                
                # Log earnings dates
                if earnings_dates:
                    logger.info(f"Earnings dates for {symbol}: {earnings_dates}")
                    
        except Exception as e:
            logger.error(f"Error updating earnings calendar: {e}")

    async def check_earnings_risk(self, symbol: str) -> Dict:
        """Check earnings risk for a symbol."""
        try:
            if symbol not in self.earnings_calendar:
                await self.update_earnings_calendar()
            
            earnings_data = self.earnings_calendar.get(symbol, {})
            if not earnings_data or "dates" not in earnings_data:
                return {"has_earnings": False}
            
            current_date = datetime.now().date()
            upcoming_earnings = [
                date for date in earnings_data["dates"]
                if date["date"] > current_date
            ]
            
            if not upcoming_earnings:
                return {"has_earnings": False}
            
            next_earnings = upcoming_earnings[0]
            days_until_earnings = (next_earnings["date"] - current_date).days
            
            # Calculate earnings risk
            risk_level = "low"
            if days_until_earnings <= 5:
                risk_level = "high"
            elif days_until_earnings <= 10:
                risk_level = "medium"
            
            return {
                "has_earnings": True,
                "next_earnings_date": next_earnings["date"],
                "days_until_earnings": days_until_earnings,
                "risk_level": risk_level,
                "estimate": next_earnings.get("estimate"),
                "actual": next_earnings.get("actual")
            }
            
        except Exception as e:
            logger.error(f"Error checking earnings risk for {symbol}: {e}")
            return {"has_earnings": False}

    async def adjust_for_earnings(self, symbol: str) -> Dict:
        """Adjust position and options strategy for upcoming earnings."""
        try:
            earnings_risk = await self.check_earnings_risk(symbol)
            if not earnings_risk["has_earnings"]:
                return {"adjusted": False}
            
            risk_level = earnings_risk["risk_level"]
            days_until_earnings = earnings_risk["days_until_earnings"]
            
            adjustments = {
                "position_size": 1.0,
                "options_strategy": {
                    "calls": {"adjustment": 1.0},
                    "puts": {"adjustment": 1.0}
                }
            }
            
            # Adjust based on risk level
            if risk_level == "high":
                adjustments["position_size"] = 0.5  # Reduce position size by 50%
                adjustments["options_strategy"]["calls"]["adjustment"] = 0.0  # Close calls
                adjustments["options_strategy"]["puts"]["adjustment"] = 0.0  # Close puts
            elif risk_level == "medium":
                adjustments["position_size"] = 0.75  # Reduce position size by 25%
                adjustments["options_strategy"]["calls"]["adjustment"] = 0.5  # Reduce calls by 50%
                adjustments["options_strategy"]["puts"]["adjustment"] = 0.5  # Reduce puts by 50%
            
            # Log adjustments
            logger.info(f"Earnings adjustments for {symbol}: {adjustments}")
            
            return {
                "adjusted": True,
                "risk_level": risk_level,
                "days_until_earnings": days_until_earnings,
                "adjustments": adjustments
            }
            
        except Exception as e:
            logger.error(f"Error adjusting for earnings: {e}")
            return {"adjusted": False}

    async def analyze_sector_rotation(self):
        """Analyze sector rotation and performance."""
        try:
            # Define sector ETFs
            sector_etfs = {
                "technology": "XLK",
                "healthcare": "XLV",
                "financial": "XLF",
                "consumer": "XLP",
                "industrial": "XLI",
                "energy": "XLE",
                "materials": "XLB",
                "utilities": "XLU",
                "real_estate": "XLRE",
                "communication": "XLC"
            }
            
            # Get performance data for each sector
            sector_performance = {}
            for sector, etf in sector_etfs.items():
                historical_data = await get_historical_data(etf)
                if historical_data is not None and len(historical_data) >= 22:  # Ensure we have enough data
                    try:
                        # Calculate 1-month return
                        one_month_return = (historical_data['close'].iloc[-1] / historical_data['close'].iloc[-22] - 1)
                        # Calculate relative strength
                        ma20 = historical_data['close'].rolling(window=20).mean()
                        relative_strength = historical_data['close'].iloc[-1] / ma20.iloc[-1]
                        
                        sector_performance[sector] = {
                            "return": one_month_return,
                            "relative_strength": relative_strength,
                            "momentum": self._calculate_momentum(historical_data)
                        }
                    except Exception as e:
                        logger.error(f"Error calculating performance for sector {sector}: {e}")
                        continue
            
            # Update sector rotation data
            self.sector_rotation["sector_performance"] = sector_performance
            self.sector_rotation["last_update"] = datetime.now()
            
            # Generate rotation signals
            self._generate_rotation_signals()
            
            return sector_performance
            
        except Exception as e:
            logger.error(f"Error analyzing sector rotation: {e}")
            return {}

    def _calculate_momentum(self, historical_data: pd.DataFrame) -> float:
        """Calculate momentum indicator."""
        try:
            # Calculate 12-month momentum
            returns = historical_data['close'].pct_change()
            momentum = (1 + returns).rolling(window=252).apply(lambda x: x.prod()) - 1
            return momentum.iloc[-1]
        except Exception as e:
            logger.error(f"Error calculating momentum: {e}")
            return 0.0

    def _generate_rotation_signals(self):
        """Generate sector rotation signals based on performance."""
        try:
            sector_performance = self.sector_rotation["sector_performance"]
            if not sector_performance:
                return
            
            # Sort sectors by performance metrics
            sorted_sectors = sorted(
                sector_performance.items(),
                key=lambda x: (
                    x[1]["return"],
                    x[1]["relative_strength"],
                    x[1]["momentum"]
                ),
                reverse=True
            )
            
            # Generate signals
            rotation_signals = {
                "leading_sectors": sorted_sectors[:3],  # Top 3 performing sectors
                "lagging_sectors": sorted_sectors[-3:],  # Bottom 3 performing sectors
                "neutral_sectors": sorted_sectors[3:-3]  # Middle performing sectors
            }
            
            self.sector_rotation["rotation_signals"] = rotation_signals
            
            # Log rotation signals
            logger.info("Sector Rotation Signals:")
            logger.info(f"Leading Sectors: {[s[0] for s in rotation_signals['leading_sectors']]}")
            logger.info(f"Lagging Sectors: {[s[0] for s in rotation_signals['lagging_sectors']]}")
            
        except Exception as e:
            logger.error(f"Error generating rotation signals: {e}")

    async def analyze_news_sentiment(self, symbol: str) -> Dict:
        """Analyze news sentiment for a symbol."""
        try:
            # Fetch recent news
            news_data = await fetch_stock_news()
            if not news_data:
                return {"sentiment": "neutral", "score": 0.0}
            
            # Filter news for the specific symbol
            symbol_news = [n for n in news_data if n.get("symbols", []) == [symbol]]
            
            if not symbol_news:
                return {"sentiment": "neutral", "score": 0.0}
            
            # Calculate sentiment score
            sentiment_scores = []
            for news in symbol_news:
                # Use headline and summary for sentiment analysis
                text = f"{news.get('headline', '')} {news.get('summary', '')}"
                score = self._analyze_text_sentiment(text)
                sentiment_scores.append(score)
            
            # Calculate average sentiment
            avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
            
            # Determine sentiment category
            if avg_sentiment > 0.2:
                sentiment = "positive"
            elif avg_sentiment < -0.2:
                sentiment = "negative"
            else:
                sentiment = "neutral"
            
            # Update news sentiment data
            self.news_sentiment[symbol] = {
                "sentiment": sentiment,
                "score": avg_sentiment,
                "last_update": datetime.now(),
                "news_count": len(symbol_news)
            }
            
            return self.news_sentiment[symbol]
            
        except Exception as e:
            logger.error(f"Error analyzing news sentiment for {symbol}: {e}")
            return {"sentiment": "neutral", "score": 0.0}

    def _analyze_text_sentiment(self, text: str) -> float:
        """Analyze sentiment of text using simple keyword matching."""
        try:
            # Define sentiment keywords
            positive_words = {"surge", "jump", "rise", "gain", "up", "higher", "positive", "growth", "profit", "success"}
            negative_words = {"drop", "fall", "decline", "down", "lower", "negative", "loss", "risk", "concern", "problem"}
            
            # Convert text to lowercase and split into words
            words = set(text.lower().split())
            
            # Count positive and negative words
            positive_count = len(words.intersection(positive_words))
            negative_count = len(words.intersection(negative_words))
            
            # Calculate sentiment score (-1 to 1)
            total = positive_count + negative_count
            if total == 0:
                return 0.0
                
            return (positive_count - negative_count) / total
            
        except Exception as e:
            logger.error(f"Error analyzing text sentiment: {e}")
            return 0.0

    async def adjust_for_sector_rotation(self, symbol: str) -> Dict:
        """Adjust position based on sector rotation signals."""
        try:
            # Get sector for the symbol
            sector = await self._get_sector(symbol)
            if sector == "unknown":
                return {"adjusted": False}
            
            # Update sector rotation analysis
            await self.analyze_sector_rotation()
            
            # Get rotation signals
            rotation_signals = self.sector_rotation.get("rotation_signals", {})
            if not rotation_signals:
                return {"adjusted": False}
            
            # Check if sector is leading or lagging
            leading_sectors = [s[0] for s in rotation_signals.get("leading_sectors", [])]
            lagging_sectors = [s[0] for s in rotation_signals.get("lagging_sectors", [])]
            
            adjustments = {
                "position_size": 1.0,
                "options_strategy": {
                    "calls": {"adjustment": 1.0},
                    "puts": {"adjustment": 1.0}
                }
            }
            
            # Adjust based on sector performance
            if sector in leading_sectors:
                adjustments["position_size"] = 1.2  # Increase position size by 20%
            elif sector in lagging_sectors:
                adjustments["position_size"] = 0.8  # Decrease position size by 20%
            
            # Log adjustments
            logger.info(f"Sector rotation adjustments for {symbol}: {adjustments}")
            
            return {
                "adjusted": True,
                "sector": sector,
                "sector_performance": self.sector_rotation["sector_performance"].get(sector),
                "adjustments": adjustments
            }
            
        except Exception as e:
            logger.error(f"Error adjusting for sector rotation: {e}")
            return {"adjusted": False}

    async def cleanup(self):
        """Clean up resources and close positions before shutting down."""
        try:
            logger.info("Starting cleanup process...")
            
            if SHOULD_CLOSE_POSITIONS:      
                # Close all open positions
                positions = get_all_positions()  # Not async
                for position in positions:
                    try:
                        symbol = position.symbol
                        qty = float(position.qty)
                        logger.info(f"Closing position for {symbol}: {qty} shares")
                    
                        # Close stock position if not GME
                        if symbol != "GME":
                            await sell_stock_asset(symbol, reason="cleanup", qty_to_sell=qty)
                            send_email(f"🚀 {symbol} closed {qty} shares")
                        # Close any associated options positions
                        if symbol in self.portfolio and "options_strategy" in self.portfolio[symbol]:
                            options_strategy = self.portfolio[symbol]["options_strategy"]
                            for option_type in ["calls", "puts"]:
                                if option_type in options_strategy:
                                    send_email(f"🚀 {symbol} closed {qty} {option_type} options")
                                    await sell_option(
                                        symbol,
                                        qty=1,
                                        option_type=option_type,
                                        contract_type="short_term"
                                    )
                    except Exception as e:
                        logger.error(f"Error closing position for {symbol}: {e}")
            
            # Clear portfolio and cache
            self.portfolio.clear()
            self.cache["market_data"].clear()
            self.cache["options_data"].clear()
            self.cache["last_update"].clear()
            
            # Save final performance metrics
            if self.performance_tracker:
                final_report = self.performance_tracker.get_performance_report()
                logger.info("Final Performance Report:")
                logger.info(f"Total Trades: {final_report['trade_count']}")
                logger.info(f"Win Rate: {final_report['metrics']['win_rate']:.2%}")
                logger.info(f"Total Profit: ${final_report['total_profit']:,.2f}")
            
            logger.info("Cleanup completed successfully")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise

    async def run(self):
        """Main bot execution loop with enhanced error handling and recovery."""
        max_retries = 3
        retry_delay = 5  # seconds
        
        try:
            while True:  # Continuous operation with recovery
                try:
                    # Check circuit breaker
                    if await self.check_circuit_breaker():
                        logger.warning("Circuit breaker active - pausing trading")
                        await asyncio.sleep(300)  # Wait 5 minutes
                        if not await self.recover_from_circuit_breaker():
                            continue

                    # Update market analysis
                    await self.analyze_sector_rotation()
                    await self.update_earnings_calendar()

                    # Fetch and implement portfolio recommendations
                    recommendations = fetch_portfolio_recommendations(self.target_income)
                    if recommendations and "portfolio" in recommendations:
                        # Adjust portfolio leverage
                        target_leverage = await self.adjust_portfolio_leverage()
                        
                        # Implement strategies with adjusted position sizes
                        for stock in recommendations["portfolio"]:
                            try:
                                # Get risk score
                                risk_score = await self.calculate_position_risk_score(stock["symbol"])
                                
                                # Check earnings risk
                                earnings_risk = await self.check_earnings_risk(stock["symbol"])
                                
                                # Get news sentiment
                                news_sentiment = await self.analyze_news_sentiment(stock["symbol"])
                                
                                # Get sector rotation adjustments
                                sector_adjustments = await self.adjust_for_sector_rotation(stock["symbol"])
                                
                                # Scale position size based on all factors
                                base_shares = stock["shares"]
                                adjusted_shares = base_shares * target_leverage
                                
                                # Apply risk-based adjustments
                                if risk_score > 0.7:  # High risk
                                    adjusted_shares *= 0.8
                                elif risk_score < 0.3:  # Low risk
                                    adjusted_shares *= 1.2
                                
                                # Apply earnings-based adjustments
                                if earnings_risk.get("has_earnings", False):
                                    earnings_adjustments = await self.adjust_for_earnings(stock["symbol"])
                                    if earnings_adjustments.get("adjusted", False):
                                        adjusted_shares *= earnings_adjustments["adjustments"]["position_size"]
                                
                                # Apply sector rotation adjustments
                                if sector_adjustments.get("adjusted", False):
                                    adjusted_shares *= sector_adjustments["adjustments"]["position_size"]
                                
                                # Apply sentiment-based adjustments
                                if news_sentiment["sentiment"] == "positive":
                                    adjusted_shares *= 1.1
                                elif news_sentiment["sentiment"] == "negative":
                                    adjusted_shares *= 0.9
                                
                                # Update stock shares
                                stock["shares"] = int(adjusted_shares)
                                
                                # Implement strategy
                                strategy = await self.implement_options_strategy(stock)
                                if strategy:
                                    self.portfolio[stock["symbol"]] = strategy
                                    
                                    # Track performance
                                    self.performance_tracker.add_trade({
                                        "symbol": stock["symbol"],
                                        "entry_time": datetime.now(),
                                        "shares": adjusted_shares,
                                        "entry_price": stock["currentPrice"],
                                        "strategy": strategy
                                    })
                                    
                                    # Log performance metrics
                                    performance_report = self.performance_tracker.get_performance_report()
                                    self.json_logger.add_performance_metrics(
                                        metrics=performance_report,
                                        timestamp=datetime.now()
                                    )
                                    
                                    logger.info(f"Successfully implemented strategy for {stock['symbol']}")
                                    
                                    # Log implementation details
                                    logger.info(f"Implementation details for {stock['symbol']}:")
                                    logger.info(f"Risk Score: {risk_score:.2f}")
                                    logger.info(f"Earnings Risk: {earnings_risk}")
                                    logger.info(f"News Sentiment: {news_sentiment}")
                                    logger.info(f"Sector Adjustments: {sector_adjustments}")
                                    
                            except Exception as e:
                                logger.error(f"Error implementing strategy for {stock['symbol']}: {e}")
                                continue

                    # Update portfolio value and performance metrics
                    portfolio_value = await self._get_account_value()
                    self.performance_tracker.add_portfolio_value(portfolio_value)
                    
                    # Generate performance report
                    performance_report = self.performance_tracker.get_performance_report()
                    logger.info("Performance Report:")
                    logger.info(f"Win Rate: {performance_report['metrics']['win_rate']:.2%}")
                    logger.info(f"Profit Factor: {performance_report['metrics']['profit_factor']:.2f}")
                    logger.info(f"Sharpe Ratio: {performance_report['metrics']['sharpe_ratio']:.2f}")
                    logger.info(f"Max Drawdown: {performance_report['metrics']['max_drawdown']:.2%}")
                    logger.info(f"Total Profit: ${performance_report['total_profit']:,.2f}")
                    send_email(f"🚀 Performance Report: {performance_report}")
                    # Start monitoring tasks with error handling
                    monitoring_tasks = [
                        self.monitor_prices(),
                        self.monthly_options_management(),
                        self.monitor_stop_losses()
                    ]
                    
                    await asyncio.gather(*monitoring_tasks)
                    
                except Exception as e:
                    logger.error(f"Error in main bot loop: {e}")
                    await asyncio.sleep(retry_delay)
                    continue
                    
        except KeyboardInterrupt:
            logger.info("Received shutdown signal, starting cleanup...")
            await self.cleanup()
        except Exception as e:
            logger.error(f"Fatal error in bot execution: {e}")
            await self.cleanup()
            raise

    async def _get_portfolio_value_history(self) -> pd.Series:
        """Get historical portfolio values."""
        try:
            if not self.performance_tracker.portfolio_values:
                # If no historical values, use current account value
                current_value = float(get_account_value())
                return pd.Series([current_value])
            return pd.Series([v["value"] for v in self.performance_tracker.portfolio_values])
        except Exception as e:
            logger.error(f"Error getting portfolio value history: {e}")
            # Return current account value as fallback
            try:
                current_value = float(get_account_value())
                return pd.Series([current_value])
            except:
                return pd.Series([0.0])  # Last resort fallback

    async def _get_account_value(self) -> float:
        """Get current account value."""
        try:
            return get_account_value()  # Remove await since get_account_value is not async
        except Exception as e:
            logger.error(f"Error getting account value: {e}")
            return 0.0

async def run_options_strategy():
    bot = OptionsTradingBot(target_income=50000)
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot stopped due to error: {e}")
    finally:
        await bot.cleanup()

if __name__ == "__main__":
    asyncio.run(run_options_strategy()) 