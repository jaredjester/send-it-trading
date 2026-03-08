#!/usr/bin/env python3
"""
Enhanced Options Analyzer — Advanced options analytics from Invest Assist integration.

Features from Invest Assist:
1. High Open Interest (OI) contract screening
2. Options-enhanced income calculations for covered calls/puts
3. Fair market value integration with options pricing
4. Multi-factor options scoring (liquidity, IV, delta, theta)
5. Premium income optimization strategies
6. Options flow analysis and unusual activity detection

This enhances the existing options_trader.py with better contract selection
and comprehensive options analytics.
"""

import logging
import requests
import json
import math
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
import sys

# Add parent directory to path

logger = logging.getLogger("enhanced_options_analyzer")

try:
    from core.dynamic_config import cfg as _cfg
    from core.alpaca_client import AlpacaClient
except ImportError:
    def _cfg(key: str, default):
        return default
    AlpacaClient = None

# Try to import yfinance, with complete fallback if not available
try:
    import yfinance as yf
    HAS_YFINANCE = True
    logger.info("yfinance available for options analysis")
except ImportError:
    HAS_YFINANCE = False
    logger.info("yfinance not available, using fallback options data")

@dataclass
class EnhancedOptionContract:
    """Enhanced option contract with comprehensive analytics."""
    symbol: str
    option_symbol: str
    strike: float
    expiration: str
    option_type: str  # 'call' or 'put'

    # Basic pricing
    bid: float
    ask: float
    last: float
    mid_price: float

    # Volume and interest metrics
    volume: int
    open_interest: int

    # Greeks (if available)
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None

    # Enhanced scoring metrics
    liquidity_score: float = 0.0
    premium_efficiency: float = 0.0
    time_decay_risk: float = 0.0
    moneyness: float = 0.0  # How ITM/OTM the option is
    fair_value: Optional[float] = None

    # Strategy-specific metrics
    income_potential: float = 0.0  # For covered call/put strategies
    risk_reward_ratio: float = 0.0
    breakeven_price: float = 0.0

class EnhancedOptionsAnalyzer:
    def __init__(self, alpaca_client=None):
        self.alpaca_client = alpaca_client
        self.cache_duration = _cfg("options.cache_duration_minutes", 5)
        self.contract_cache = {}

        # Scoring weights (configurable via dynamic_config)
        self.weights = {
            "liquidity": _cfg("options.weight_liquidity", 0.3),
            "premium_efficiency": _cfg("options.weight_premium", 0.25),
            "greeks": _cfg("options.weight_greeks", 0.2),
            "time_decay": _cfg("options.weight_time", 0.15),
            "income_potential": _cfg("options.weight_income", 0.1)
        }

    def get_high_oi_contracts(self, symbol: str, option_type: str = "both",
                            min_oi: int = None) -> List[EnhancedOptionContract]:
        """
        Get options contracts with high open interest for better liquidity.
        Based on Invest Assist's get-high-oi-options API.
        """
        if min_oi is None:
            min_oi = int(_cfg("options.min_high_oi", 100))

        try:
            # Check if yfinance is available
            if not HAS_YFINANCE:
                logger.debug(f"yfinance not available for {symbol} options")
                return self._create_mock_options_contracts(symbol, option_type)

            # Get options chain from yfinance
            ticker = yf.Ticker(symbol)
            options_dates = ticker.options

            if not options_dates:
                logger.debug(f"No options dates available for {symbol}")
                return self._create_mock_options_contracts(symbol, option_type)

            all_contracts = []

            # Analyze options for next 2-3 expiration dates
            for exp_date in options_dates[:3]:
                try:
                    option_chain = ticker.option_chain(exp_date)

                    # Process calls if requested
                    if option_type in ["call", "both"]:
                        for _, row in option_chain.calls.iterrows():
                            if row.get('openInterest', 0) >= min_oi:
                                contract = self._create_enhanced_contract(
                                    symbol, row, exp_date, "call"
                                )
                                if contract:
                                    all_contracts.append(contract)

                    # Process puts if requested
                    if option_type in ["put", "both"]:
                        for _, row in option_chain.puts.iterrows():
                            if row.get('openInterest', 0) >= min_oi:
                                contract = self._create_enhanced_contract(
                                    symbol, row, exp_date, "put"
                                )
                                if contract:
                                    all_contracts.append(contract)

                except Exception as e:
                    logger.debug(f"Error processing options for {exp_date}: {e}")
                    continue

            # Score and sort contracts
            scored_contracts = []
            for contract in all_contracts:
                score = self._calculate_contract_score(contract, symbol)
                if score > 0:
                    contract.liquidity_score = score
                    scored_contracts.append(contract)

            # Sort by score and return top contracts
            scored_contracts.sort(key=lambda x: x.liquidity_score, reverse=True)
            max_contracts = int(_cfg("options.max_high_oi_results", 20))

            logger.info(f"Found {len(scored_contracts)} high-OI contracts for {symbol}")
            return scored_contracts[:max_contracts]

        except Exception as e:
            logger.error(f"Error getting high-OI contracts for {symbol}: {e}")
            return []

    def _create_mock_options_contracts(self, symbol: str, option_type: str) -> List[EnhancedOptionContract]:
        """Create mock options contracts for testing when yfinance is unavailable."""
        mock_contracts = []

        # Create a few mock contracts for testing
        base_price = 100.0  # Assume $100 underlying price

        if option_type in ["call", "both"]:
            # Mock call options at different strikes
            for i, strike in enumerate([95, 100, 105, 110]):
                mock_contracts.append(EnhancedOptionContract(
                    symbol=symbol,
                    option_symbol=f"{symbol}240315C{strike:08.0f}000",
                    strike=strike,
                    expiration_date="2024-03-15",
                    option_type="call",
                    bid=max(0.1, base_price - strike + 2 - i),
                    ask=max(0.2, base_price - strike + 3 - i),
                    last_price=max(0.15, base_price - strike + 2.5 - i),
                    volume=1000 + i * 500,
                    open_interest=500 + i * 200,
                    implied_volatility=0.25 + i * 0.05,
                    delta=0.8 - i * 0.2,
                    gamma=0.05,
                    theta=-0.02,
                    vega=0.15,
                    premium_efficiency=75 - i * 5,
                    liquidity_score=80 - i * 10,
                    total_score=70 - i * 5
                ))

        if option_type in ["put", "both"]:
            # Mock put options at different strikes
            for i, strike in enumerate([90, 95, 100, 105]):
                mock_contracts.append(EnhancedOptionContract(
                    symbol=f"{symbol}240315P{strike:08.0f}000",
                    underlying_symbol=symbol,
                    strike_price=strike,
                    expiration_date="2024-03-15",
                    option_type="put",
                    bid=max(0.1, strike - base_price + 2 - i),
                    ask=max(0.2, strike - base_price + 3 - i),
                    last_price=max(0.15, strike - base_price + 2.5 - i),
                    volume=800 + i * 300,
                    open_interest=400 + i * 150,
                    implied_volatility=0.25 + i * 0.05,
                    delta=-0.2 - i * 0.2,
                    gamma=0.05,
                    theta=-0.02,
                    vega=0.15,
                    premium_efficiency=70 - i * 5,
                    liquidity_score=75 - i * 10,
                    total_score=65 - i * 5
                ))

        logger.info(f"Created {len(mock_contracts)} mock options contracts for {symbol}")
        return mock_contracts

    def _create_enhanced_contract(self, symbol: str, row: Any, exp_date: str,
                                option_type: str) -> Optional[EnhancedOptionContract]:
        """Create enhanced contract object from options data row."""
        try:
            # Extract basic data
            strike = float(row.get('strike', 0))
            bid = float(row.get('bid', 0))
            ask = float(row.get('ask', 0))
            last = float(row.get('lastPrice', 0))
            volume = int(row.get('volume', 0))
            open_interest = int(row.get('openInterest', 0))

            # Calculate mid price
            if bid > 0 and ask > 0:
                mid_price = (bid + ask) / 2
            else:
                mid_price = last

            # Skip contracts with no meaningful pricing
            if mid_price <= 0.01:
                return None

            # Create option symbol (simplified format)
            option_symbol = f"{symbol}_{exp_date}_{option_type.upper()}_{strike}"

            contract = EnhancedOptionContract(
                symbol=symbol,
                option_symbol=option_symbol,
                strike=strike,
                expiration=exp_date,
                option_type=option_type,
                bid=bid,
                ask=ask,
                last=last,
                mid_price=mid_price,
                volume=volume,
                open_interest=open_interest,
                delta=row.get('delta'),
                gamma=row.get('gamma'),
                theta=row.get('theta'),
                vega=row.get('vega'),
                implied_volatility=row.get('impliedVolatility')
            )

            # Calculate enhanced metrics
            self._calculate_enhanced_metrics(contract, symbol)

            return contract

        except Exception as e:
            logger.debug(f"Error creating contract from row: {e}")
            return None

    def _calculate_enhanced_metrics(self, contract: EnhancedOptionContract, underlying_symbol: str):
        """Calculate enhanced metrics for option contract."""
        try:
            # Get current stock price with fallback
            current_price = 0
            if HAS_YFINANCE:
                try:
                    ticker = yf.Ticker(underlying_symbol)
                    current_price = ticker.info.get('currentPrice', 0)
                    if not current_price:
                        hist = ticker.history(period="1d")
                        if not hist.empty:
                            current_price = hist['Close'].iloc[-1]
                except:
                    pass

            # Use fallback if yfinance fails or unavailable
            if not current_price:
                current_price = 100.0  # Default price fallback
                logger.debug(f"Using fallback price ${current_price} for {underlying_symbol}")

            # Calculate moneyness (how much ITM/OTM)
            if contract.option_type == "call":
                contract.moneyness = (current_price - contract.strike) / current_price
                contract.breakeven_price = contract.strike + contract.mid_price
            else:  # put
                contract.moneyness = (contract.strike - current_price) / current_price
                contract.breakeven_price = contract.strike - contract.mid_price

            # Premium efficiency (premium per $ of intrinsic value)
            intrinsic_value = max(0,
                current_price - contract.strike if contract.option_type == "call"
                else contract.strike - current_price
            )
            time_value = contract.mid_price - intrinsic_value

            if intrinsic_value > 0:
                contract.premium_efficiency = time_value / intrinsic_value
            else:
                contract.premium_efficiency = time_value / contract.mid_price if contract.mid_price > 0 else 0

            # Time decay risk (higher theta = more risk)
            days_to_expiry = self._days_to_expiry(contract.expiration)
            if contract.theta and days_to_expiry > 0:
                contract.time_decay_risk = abs(contract.theta) * days_to_expiry
            else:
                # Approximate time decay based on days to expiry
                contract.time_decay_risk = max(0, (35 - days_to_expiry) / 35)

            # Income potential for covered strategies
            if contract.option_type == "call" and contract.moneyness < 0.05:  # OTM calls
                # Covered call income potential
                contract.income_potential = (contract.mid_price / current_price) * 100  # Annualized %
            elif contract.option_type == "put" and contract.moneyness < 0.05:  # OTM puts
                # Cash-secured put income potential
                contract.income_potential = (contract.mid_price / contract.strike) * 100

            # Risk-reward ratio
            max_profit = contract.mid_price  # For credit strategies
            max_loss = current_price - contract.strike if contract.option_type == "call" else contract.strike
            if max_loss > 0:
                contract.risk_reward_ratio = max_profit / max_loss

        except Exception as e:
            logger.debug(f"Error calculating enhanced metrics: {e}")

    def _calculate_contract_score(self, contract: EnhancedOptionContract, symbol: str) -> float:
        """Calculate comprehensive score for option contract."""
        try:
            score = 0.0

            # Liquidity score (based on volume and OI)
            volume_score = min(contract.volume / 100, 10) / 10  # Cap at 100 volume = max score
            oi_score = min(contract.open_interest / 1000, 10) / 10  # Cap at 1000 OI = max score
            liquidity_component = (volume_score + oi_score) / 2
            score += liquidity_component * self.weights["liquidity"]

            # Premium efficiency score (lower is better for buyers)
            if contract.premium_efficiency > 0:
                efficiency_score = max(0, (2 - contract.premium_efficiency)) / 2
                score += efficiency_score * self.weights["premium_efficiency"]

            # Greeks score (delta around 0.3-0.7 is often optimal)
            if contract.delta:
                delta_score = 1 - abs(abs(contract.delta) - 0.5) * 2  # Best score at 0.5 delta
                score += max(0, delta_score) * self.weights["greeks"]

            # Time decay score (less decay risk is better)
            time_score = max(0, 1 - contract.time_decay_risk)
            score += time_score * self.weights["time_decay"]

            # Income potential score (higher is better)
            if contract.income_potential > 0:
                income_score = min(contract.income_potential / 10, 1)  # Cap at 10% = max score
                score += income_score * self.weights["income_potential"]

            return score

        except Exception as e:
            logger.debug(f"Error calculating contract score: {e}")
            return 0.0

    def _days_to_expiry(self, exp_date: str) -> int:
        """Calculate days until option expiration."""
        try:
            exp_dt = datetime.strptime(exp_date, "%Y-%m-%d")
            return (exp_dt - datetime.now()).days
        except:
            return 0

    def calculate_options_enhanced_income(self, symbol: str, investment_amount: float) -> Dict:
        """
        Calculate potential income from options strategies (covered calls, cash-secured puts).
        Based on Invest Assist's options-enhanced income calculations.
        """
        try:
            # Get current stock data with fallback
            current_price = 0
            dividend_rate = 0

            if HAS_YFINANCE:
                try:
                    ticker = yf.Ticker(symbol)
                    current_price = ticker.info.get('currentPrice', 0)

                    if not current_price:
                        hist = ticker.history(period="1d")
                        if not hist.empty:
                            current_price = hist['Close'].iloc[-1]

                    dividend_rate = ticker.info.get('dividendRate', 0) or 0
                except:
                    pass

            # Use fallback data if needed
            if not current_price:
                current_price = 100.0  # Default price fallback
                dividend_rate = 2.0    # Default 2% dividend
                logger.debug(f"Using fallback price ${current_price} for {symbol}")
            shares = int(investment_amount / current_price)

            # Get high-OI call and put options
            call_contracts = self.get_high_oi_contracts(symbol, "call", min_oi=50)
            put_contracts = self.get_high_oi_contracts(symbol, "put", min_oi=50)

            # Find best covered call opportunity (slightly OTM)
            best_call = None
            for contract in call_contracts:
                if (contract.moneyness > 0.02 and contract.moneyness < 0.1 and  # 2-10% OTM
                    self._days_to_expiry(contract.expiration) >= 14 and
                    self._days_to_expiry(contract.expiration) <= 45):
                    best_call = contract
                    break

            # Find best cash-secured put opportunity (slightly OTM)
            best_put = None
            for contract in put_contracts:
                if (contract.moneyness > 0.02 and contract.moneyness < 0.1 and  # 2-10% OTM
                    self._days_to_expiry(contract.expiration) >= 14 and
                    self._days_to_expiry(contract.expiration) <= 45):
                    best_put = contract
                    break

            # Calculate income scenarios
            base_dividend_income = shares * dividend_rate

            # Covered call income (monthly, assuming we can repeat)
            call_income = 0
            call_details = {}
            if best_call:
                monthly_call_premium = best_call.mid_price * shares
                call_income = monthly_call_premium * 12  # Annualized
                call_details = {
                    "strike": best_call.strike,
                    "premium": best_call.mid_price,
                    "monthly_income": monthly_call_premium,
                    "annualized_income": call_income,
                    "expiration": best_call.expiration
                }

            # Cash-secured put income (monthly)
            put_income = 0
            put_details = {}
            if best_put:
                monthly_put_premium = best_put.mid_price * shares
                put_income = monthly_put_premium * 12  # Annualized
                put_details = {
                    "strike": best_put.strike,
                    "premium": best_put.mid_price,
                    "monthly_income": monthly_put_premium,
                    "annualized_income": put_income,
                    "expiration": best_put.expiration
                }

            # Calculate total enhanced income
            total_income = base_dividend_income + call_income + put_income
            effective_yield = (total_income / investment_amount) * 100 if investment_amount > 0 else 0

            return {
                "symbol": symbol,
                "investment_amount": investment_amount,
                "shares": shares,
                "current_price": current_price,
                "base_dividend_income": base_dividend_income,
                "covered_call_income": call_income,
                "cash_secured_put_income": put_income,
                "total_enhanced_income": total_income,
                "effective_yield_percent": round(effective_yield, 2),
                "monthly_income": total_income / 12,
                "covered_call_details": call_details,
                "cash_secured_put_details": put_details
            }

        except Exception as e:
            logger.error(f"Error calculating options-enhanced income for {symbol}: {e}")
            return {"error": str(e)}

    def find_optimal_options_trade(self, symbol: str, direction: str, budget: float) -> Optional[EnhancedOptionContract]:
        """
        Find the optimal options contract for a trade based on comprehensive analysis.
        This enhances the existing options_trader.py contract selection.
        """
        option_type = "call" if direction.lower() in ["bullish", "up", "call"] else "put"

        # Get high-quality contracts
        contracts = self.get_high_oi_contracts(symbol, option_type, min_oi=20)

        if not contracts:
            return None

        # Filter by budget
        affordable_contracts = [
            c for c in contracts
            if c.mid_price * 100 <= budget  # 100 shares per contract
        ]

        if not affordable_contracts:
            return None

        # Find contracts with optimal characteristics
        optimal_contracts = []
        for contract in affordable_contracts:
            days_to_expiry = self._days_to_expiry(contract.expiration)

            # Prefer contracts with 14-35 days to expiry
            if 14 <= days_to_expiry <= 35:
                # Prefer delta between 0.3-0.7 for good probability of profit
                if contract.delta and 0.3 <= abs(contract.delta) <= 0.7:
                    optimal_contracts.append(contract)
                elif not contract.delta:  # Include if no delta data
                    optimal_contracts.append(contract)

        # If no optimal contracts, use affordable ones
        if not optimal_contracts:
            optimal_contracts = affordable_contracts

        # Return highest scoring contract
        best_contract = max(optimal_contracts, key=lambda x: x.liquidity_score)

        logger.info(f"Selected optimal {option_type} contract for {symbol}: "
                   f"{best_contract.option_symbol} (score: {best_contract.liquidity_score:.2f})")

        return best_contract

    def analyze_unusual_options_activity(self, symbols: List[str]) -> List[Dict]:
        """
        Detect unusual options activity that might indicate informed trading.
        Returns signals based on volume/OI ratios and other flow metrics.
        """
        signals = []

        for symbol in symbols:
            try:
                # Get recent options data
                contracts = self.get_high_oi_contracts(symbol, "both", min_oi=10)

                if not contracts:
                    continue

                unusual_activity = []

                for contract in contracts:
                    # Check for unusual volume relative to open interest
                    if (contract.open_interest > 0 and
                        contract.volume > contract.open_interest * 0.5):  # Volume > 50% of OI

                        activity_ratio = contract.volume / max(contract.open_interest, 1)
                        unusual_activity.append({
                            "contract": contract,
                            "activity_ratio": activity_ratio,
                            "volume": contract.volume,
                            "open_interest": contract.open_interest
                        })

                # If we found unusual activity, create signals
                if unusual_activity:
                    # Sort by activity ratio
                    unusual_activity.sort(key=lambda x: x["activity_ratio"], reverse=True)

                    top_activity = unusual_activity[0]
                    base_score = min(70 + int(top_activity["activity_ratio"] * 5), 85)

                    signals.append({
                        "symbol": symbol,
                        "score": base_score,
                        "type": "unusual_options_activity",
                        "reason": f"Unusual options flow: {top_activity['volume']} vol / {top_activity['open_interest']} OI",
                        "contract_details": {
                            "option_symbol": top_activity["contract"].option_symbol,
                            "strike": top_activity["contract"].strike,
                            "option_type": top_activity["contract"].option_type,
                            "activity_ratio": top_activity["activity_ratio"]
                        }
                    })

            except Exception as e:
                logger.error(f"Error analyzing unusual activity for {symbol}: {e}")

        logger.info(f"Found unusual options activity in {len(signals)} symbols")
        return signals


def analyze_options_opportunities(symbols: List[str]) -> List[Dict]:
    """
    Main entry point for enhanced options analysis.
    Returns trading signals based on comprehensive options analytics.
    """
    analyzer = EnhancedOptionsAnalyzer()

    signals = []

    # Analyze unusual options activity
    unusual_signals = analyzer.analyze_unusual_options_activity(symbols)
    signals.extend(unusual_signals)

    # Analyze options-enhanced income opportunities
    for symbol in symbols[:5]:  # Limit to avoid too many API calls
        try:
            income_analysis = analyzer.calculate_options_enhanced_income(symbol, 10000)  # $10K example

            if (income_analysis.get("effective_yield_percent", 0) > 15 and  # >15% yield
                not income_analysis.get("error")):

                signals.append({
                    "symbol": symbol,
                    "score": min(65 + int(income_analysis["effective_yield_percent"]), 80),
                    "type": "options_income_opportunity",
                    "reason": f"High options income potential: {income_analysis['effective_yield_percent']:.1f}% yield",
                    "income_analysis": income_analysis
                })

        except Exception as e:
            logger.error(f"Error analyzing income opportunities for {symbol}: {e}")

    return signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with some popular optionable stocks
    test_symbols = ["AAPL", "TSLA", "SPY", "QQQ", "NVDA"]
    signals = analyze_options_opportunities(test_symbols)

    logger.info("\n=== Enhanced Options Analysis Results ===")
    for signal in signals:
        logger.info("{signal['symbol']:6s} | Score: {signal['score']:2d} | "
              f"{signal['type']:30s} | {signal['reason']}")

    # Test individual contract analysis
    analyzer = EnhancedOptionsAnalyzer()
    contracts = analyzer.get_high_oi_contracts("AAPL", "call", min_oi=100)
    logger.info("\n=== High-OI Call Contracts for AAPL ===")
    for i, contract in enumerate(contracts[:5]):
        logger.info("{i+1}. {contract.option_symbol} | Strike: ${contract.strike:.0f} | "
              f"OI: {contract.open_interest} | Score: {contract.liquidity_score:.2f}")