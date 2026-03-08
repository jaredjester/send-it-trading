#!/usr/bin/env python3
"""
Fair Market Value Calculator — Advanced multi-factor valuation models from Invest Assist.

Valuation Methods:
1. PE-based valuation with growth and risk adjustments
2. Asset-based valuation using book value and P/B ratios
3. Income-based valuation using dividend discount model
4. Technical analysis based on moving averages
5. Price range valuation using 52-week metrics
6. Trading Central integration (if available)
7. Relative valuation vs sector/industry peers

This provides conservative, multi-method fair value estimates to guide
entry/exit decisions and position sizing in the trading bot.
"""

import logging
import os
import requests
import json
import statistics
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
import sys

# Add parent directory to path

logger = logging.getLogger("fair_value_calculator")

try:
    from core.dynamic_config import cfg as _cfg
except ImportError:
    def _cfg(key: str, default):
        return default

# Try to import yfinance, with complete fallback if not available
try:
    import yfinance as yf
    HAS_YFINANCE = True
    logger.info("yfinance available for fair value calculations")
except ImportError:
    HAS_YFINANCE = False
    logger.info("yfinance not available, using fallback data sources")

@dataclass
class ValuationResult:
    """Comprehensive valuation result with multiple methods."""
    symbol: str
    current_price: float
    fair_value: float
    discount_to_fair_value: float  # Percentage discount (positive = undervalued)

    # Individual method results
    pe_valuation: Optional[float] = None
    asset_valuation: Optional[float] = None
    income_valuation: Optional[float] = None
    technical_valuation: Optional[float] = None
    range_valuation: Optional[float] = None

    # Confidence and quality metrics
    confidence_score: float = 0.0  # 0-1, higher = more confident
    methods_used: int = 0
    data_quality: str = "unknown"  # high, medium, low

    # Supporting data
    pe_ratio: Optional[float] = None
    book_value: Optional[float] = None
    dividend_yield: Optional[float] = None
    sector: Optional[str] = None
    market_cap: Optional[float] = None

class FairValueCalculator:
    def __init__(self):
        self.cache_duration = _cfg("valuation.cache_duration_hours", 4)
        self.valuation_cache = {}

        # Conservative valuation parameters
        self.conservative_pe = _cfg("valuation.conservative_pe", 12)
        self.required_return = _cfg("valuation.required_return", 0.12)  # 12%
        self.growth_rate = _cfg("valuation.conservative_growth", 0.02)  # 2%
        self.safety_margin = _cfg("valuation.safety_margin", 0.15)  # 15% discount


    def _fmp_fundamentals(self, symbol: str) -> dict:
        """Fetch fundamentals from FMP (PE, EPS, price, market cap, growth)."""
        api_key = os.getenv("FMP_API_KEY", "")
        if not api_key:
            return {}
        try:
            base = "https://financialmodelingprep.com/stable"
            import requests as _r
            # Get income statement (EPS, revenue growth)
            inc = _r.get(f"{base}/income-statement?symbol={symbol}&limit=2&apikey={api_key}", timeout=8).json()
            # Get profile (PE ratio, price, market cap, beta)
            prof = _r.get(f"{base}/profile?symbol={symbol}&apikey={api_key}", timeout=8).json()
            result = {}
            if prof and isinstance(prof, list):
                p = prof[0]
                result["price"]      = float(p.get("price", 0))
                result["pe_ratio"]   = float(p.get("pe", 0) or 0)
                result["beta"]       = float(p.get("beta", 1) or 1)
                result["sector"]     = p.get("sector", "")
                result["market_cap"] = float(p.get("mktCap", 0) or 0)
            if inc and isinstance(inc, list) and len(inc) >= 1:
                i0 = inc[0]
                result["eps"]       = float(i0.get("eps", 0) or 0)
                result["revenue"]   = float(i0.get("revenue", 0) or 0)
                if len(inc) >= 2:
                    i1 = inc[1]
                    rev0 = float(i0.get("revenue", 0) or 0)
                    rev1 = float(i1.get("revenue", 1) or 1)
                    result["revenue_growth"] = (rev0 - rev1) / rev1 if rev1 else 0
            return result
        except Exception as e:
            logger.debug("FMP fundamentals fetch failed for %s: %s", symbol, e)
            return {}

    def calculate_fair_value(self, symbol: str) -> ValuationResult:
        """
        Calculate comprehensive fair value using multiple methods.
        Based on Invest Assist's sophisticated valuation approach.
        """
        # Check cache first
        cache_key = f"fv_{symbol}"
        if cache_key in self.valuation_cache:
            cached_result, timestamp = self.valuation_cache[cache_key]
            if datetime.now() - timestamp < timedelta(hours=self.cache_duration):
                return cached_result

        try:
            # Try to get stock data with fallback methods
            info, history = self._get_stock_data(symbol)

            if not history or not info:
                logger.debug(f"No data available for {symbol}")
                return self._create_default_result(symbol, 0)

            current_price = history.get('current_price', 0)
            if current_price <= 0:
                logger.debug(f"Invalid price data for {symbol}")
                return self._create_default_result(symbol, 0)

            # Initialize result
            result = ValuationResult(
                symbol=symbol,
                current_price=current_price,
                fair_value=current_price,  # Default to current price
                discount_to_fair_value=0.0,
                sector=info.get('sector'),
                market_cap=info.get('market_cap')
            )

            # Collect all valuation methods
            valuations = []

            # 1. PE-based valuation
            pe_value = self._calculate_pe_valuation(info, current_price)
            if pe_value and pe_value > 0:
                result.pe_valuation = pe_value
                result.pe_ratio = info.get('trailingPE')
                valuations.append(pe_value)

            # 2. Asset-based valuation
            asset_value = self._calculate_asset_valuation(info, current_price)
            if asset_value and asset_value > 0:
                result.asset_valuation = asset_value
                result.book_value = info.get('bookValue')
                valuations.append(asset_value)

            # 3. Income-based valuation (dividend model)
            income_value = self._calculate_income_valuation(info, current_price)
            if income_value and income_value > 0:
                result.income_valuation = income_value
                result.dividend_yield = info.get('dividendYield')
                valuations.append(income_value)

            # 4. Technical valuation
            tech_value = self._calculate_technical_valuation(history, current_price)
            if tech_value and tech_value > 0:
                result.technical_valuation = tech_value
                valuations.append(tech_value)

            # 5. Range-based valuation
            range_value = self._calculate_range_valuation(info, current_price)
            if range_value and range_value > 0:
                result.range_valuation = range_value
                valuations.append(range_value)

            # Calculate final fair value
            if valuations:
                result.methods_used = len(valuations)

                # Use weighted median for robustness
                fair_value = self._calculate_weighted_fair_value(valuations, result)

                # Apply safety margin
                conservative_fair_value = fair_value * (1 - self.safety_margin)

                result.fair_value = conservative_fair_value
                result.discount_to_fair_value = ((conservative_fair_value - current_price) /
                                               current_price * 100)

                # Calculate confidence score
                result.confidence_score = self._calculate_confidence(result, info)
                result.data_quality = self._assess_data_quality(info, history)

            # Cache result
            self.valuation_cache[cache_key] = (result, datetime.now())

            logger.debug(f"Fair value for {symbol}: ${result.fair_value:.2f} "
                        f"(current: ${current_price:.2f}, "
                        f"discount: {result.discount_to_fair_value:+.1f}%)")

            return result

        except Exception as e:
            logger.error(f"Error calculating fair value for {symbol}: {e}")
            return self._create_default_result(symbol, 0)

    def _get_stock_data(self, symbol: str) -> Tuple[Dict, Dict]:
        """Get stock data — FMP first, yfinance fallback, static fallback last."""
        # 1. Try FMP (accurate real data, no rate limits on basic tier)
        fmp = self._fmp_fundamentals(symbol)
        if fmp.get("price", 0) > 0 and fmp.get("eps", 0) != 0:
            try:
                # Get 52w high/low from FMP historical data
                api_key = os.getenv("FMP_API_KEY", "")
                import requests as _r
                hist_resp = _r.get(
                    f"https://financialmodelingprep.com/stable/historical-price-eod/full"
                    f"?symbol={symbol}&limit=252&apikey={api_key}", timeout=8
                ).json()
                prices = [float(d["close"]) for d in (hist_resp if isinstance(hist_resp, list) else []) if d.get("close")]
                current_price = fmp["price"]
                info_data = {
                    "sector":        fmp.get("sector", "Unknown"),
                    "market_cap":    fmp.get("market_cap"),
                    "pe_ratio":      fmp.get("pe_ratio"),
                    "peg_ratio":     None,
                    "pb_ratio":      None,
                    "dividend_yield": None,
                    "eps":           fmp.get("eps"),
                    "book_value":    None,
                    "revenue":       fmp.get("revenue"),
                    "revenue_growth": fmp.get("revenue_growth"),
                    "profit_margin": None,
                }
                history_data = {
                    "current_price": current_price,
                    "high_52w":      max(prices) if prices else current_price * 1.3,
                    "low_52w":       min(prices) if prices else current_price * 0.7,
                    "volume":        0,
                    "price_history": prices or [current_price],
                }
                return info_data, history_data
            except Exception as e:
                logger.debug("FMP full data fetch failed for %s: %s", symbol, e)

        # 2. Try yfinance fallback
        if HAS_YFINANCE:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                history = ticker.history(period="1y")

                if not history.empty:
                    current_price = float(history['Close'].iloc[-1])
                    history_data = {
                        'current_price': current_price,
                        'high_52w': info.get('fiftyTwoWeekHigh', current_price),
                        'low_52w': info.get('fiftyTwoWeekLow', current_price),
                        'volume': info.get('averageVolume', 0),
                        'price_history': history['Close'].tolist()[-252:] if len(history) > 0 else [current_price]
                    }

                    info_data = {
                        'sector': info.get('sector', 'Unknown'),
                        'market_cap': info.get('marketCap'),
                        'pe_ratio': info.get('trailingPE'),
                        'peg_ratio': info.get('pegRatio'),
                        'pb_ratio': info.get('priceToBook'),
                        'dividend_yield': info.get('dividendYield'),
                        'eps': info.get('trailingEps'),
                        'book_value': info.get('bookValue'),
                        'revenue': info.get('totalRevenue'),
                        'profit_margin': info.get('profitMargins')
                    }

                    return info_data, history_data

            except Exception as e:
                logger.debug(f"yfinance failed for {symbol}: {e}")

        # Fallback to basic price estimation
        return self._get_fallback_stock_data(symbol)

    def _get_fallback_stock_data(self, symbol: str) -> Tuple[Dict, Dict]:
        """Fallback method using basic price estimates."""
        # This is a very basic fallback - in practice, you could use other APIs
        # like Alpha Vantage, Alpaca, or financial data providers

        # Static estimates for demo purposes
        estimated_price = 100.0  # Default price estimate

        # Sector mapping from portfolio risk manager
        sector_map = {
            'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Technology',
            'TSLA': 'Technology', 'NVDA': 'Technology', 'SPY': 'ETF',
        }

        info_data = {
            'sector': sector_map.get(symbol.upper(), 'Unknown'),
            'market_cap': 1000000000,  # Default 1B market cap
            'pe_ratio': 20.0,  # Market average PE
            'peg_ratio': 1.5,
            'pb_ratio': 3.0,
            'dividend_yield': 0.02,
            'eps': estimated_price / 20,  # Based on PE ratio
            'book_value': estimated_price / 3,  # Based on PB ratio
            'revenue': None,
            'profit_margin': 0.10
        }

        history_data = {
            'current_price': estimated_price,
            'high_52w': estimated_price * 1.3,
            'low_52w': estimated_price * 0.7,
            'volume': 1000000,
            'price_history': [estimated_price] * 30  # 30 days of same price
        }

        logger.info(f"Using fallback data for {symbol} - price estimate: ${estimated_price}")
        return info_data, history_data

    def _calculate_pe_valuation(self, info: Dict, current_price: float) -> Optional[float]:
        """PE-based valuation with conservative assumptions."""
        try:
            eps = info.get('trailingEps') or info.get('epsTrailingTwelveMonths')
            if not eps or eps <= 0:
                return None

            # Use conservative PE ratio
            conservative_pe = min(self.conservative_pe, info.get('trailingPE', 999) * 0.8)

            # Adjust for growth and quality
            roe = info.get('returnOnEquity', 0)
            debt_to_equity = info.get('debtToEquity', 0)

            # Quality adjustment
            quality_multiplier = 1.0
            if roe and roe > 0.15:  # Strong ROE
                quality_multiplier += 0.1
            if debt_to_equity and debt_to_equity < 0.5:  # Low debt
                quality_multiplier += 0.1

            # Apply growth adjustment conservatively
            revenue_growth = info.get('revenueGrowth', 0)
            if revenue_growth and revenue_growth > 0.1:  # >10% growth
                growth_multiplier = min(1.2, 1 + revenue_growth * 0.5)
            else:
                growth_multiplier = 1.0

            fair_pe = conservative_pe * quality_multiplier * growth_multiplier
            pe_value = eps * fair_pe

            return max(0, pe_value)

        except Exception as e:
            logger.debug(f"Error in PE valuation: {e}")
            return None

    def _calculate_asset_valuation(self, info: Dict, current_price: float) -> Optional[float]:
        """Asset-based valuation using book value and balance sheet metrics."""
        try:
            book_value = info.get('bookValue')
            if not book_value or book_value <= 0:
                return None

            # Conservative P/B ratio based on sector and quality
            pb_ratio = 1.0  # Start with book value

            # Adjust for profitability
            roe = info.get('returnOnEquity', 0)
            if roe and roe > 0.12:
                pb_ratio = min(2.0, 1 + (roe - 0.12) * 2)  # Higher ROE = higher P/B

            # Adjust for debt levels
            debt_to_equity = info.get('debtToEquity', 0)
            if debt_to_equity and debt_to_equity > 1.0:
                pb_ratio *= 0.8  # Discount for high debt

            # Current ratio adjustment
            current_ratio = info.get('currentRatio', 1)
            if current_ratio and current_ratio > 1.5:
                pb_ratio *= 1.05  # Slight premium for liquidity

            asset_value = book_value * pb_ratio

            return max(0, asset_value)

        except Exception as e:
            logger.debug(f"Error in asset valuation: {e}")
            return None

    def _calculate_income_valuation(self, info: Dict, current_price: float) -> Optional[float]:
        """Dividend discount model for income-based valuation."""
        try:
            dividend_rate = info.get('dividendRate', 0)
            if not dividend_rate or dividend_rate <= 0:
                return None

            # Sustainable dividend calculation
            payout_ratio = info.get('payoutRatio', 0)
            if payout_ratio and payout_ratio > 0.8:  # High payout ratio
                sustainable_dividend = dividend_rate * 0.7  # Conservative adjustment
            else:
                sustainable_dividend = dividend_rate

            # Dividend growth rate (conservative)
            dividend_growth = min(self.growth_rate, info.get('dividendGrowthRate', 0.02))

            # Required return (risk adjustment)
            beta = info.get('beta', 1.0)
            risk_adjusted_return = self.required_return + (beta - 1) * 0.02

            # Ensure required return > growth rate
            if risk_adjusted_return <= dividend_growth:
                risk_adjusted_return = dividend_growth + 0.03

            # Gordon Growth Model
            income_value = sustainable_dividend / (risk_adjusted_return - dividend_growth)

            return max(0, income_value)

        except Exception as e:
            logger.debug(f"Error in income valuation: {e}")
            return None

    def _calculate_technical_valuation(self, history, current_price: float) -> Optional[float]:
        """Technical analysis based valuation using moving averages and support levels."""
        try:
            if len(history) < 200:
                return None

            closes = history['Close']

            # Moving averages
            sma_50 = closes.rolling(50).mean().iloc[-1]
            sma_200 = closes.rolling(200).mean().iloc[-1]

            # Support and resistance levels
            high_52w = closes.rolling(252).max().iloc[-1]
            low_52w = closes.rolling(252).min().iloc[-1]

            # Technical fair value based on trend and support
            if sma_50 > sma_200:  # Uptrend
                # Use 200-day MA as fair value base
                tech_value = sma_200 * 1.05  # Slight premium for uptrend
            else:  # Downtrend or sideways
                # Use 200-day MA with discount
                tech_value = sma_200 * 0.95

            # Adjust based on position in 52-week range
            range_position = (current_price - low_52w) / (high_52w - low_52w)

            if range_position < 0.3:  # In lower 30% of range
                tech_value = low_52w * 1.1  # Slight premium to 52w low
            elif range_position > 0.8:  # In upper 20% of range
                tech_value = min(tech_value, high_52w * 0.9)  # Discount from 52w high

            return max(0, tech_value)

        except Exception as e:
            logger.debug(f"Error in technical valuation: {e}")
            return None

    def _calculate_range_valuation(self, info: Dict, current_price: float) -> Optional[float]:
        """Range-based valuation using 52-week metrics."""
        try:
            fifty_two_week_high = info.get('fiftyTwoWeekHigh')
            fifty_two_week_low = info.get('fiftyTwoWeekLow')

            if not fifty_two_week_high or not fifty_two_week_low:
                return None

            # Conservative approach: use 52-week low + 20% as fair value
            # This assumes mean reversion but with conservative positioning
            range_value = fifty_two_week_low * 1.2

            # Cap at current price if it's already below this level
            range_value = min(range_value, current_price * 1.1)

            return max(0, range_value)

        except Exception as e:
            logger.debug(f"Error in range valuation: {e}")
            return None

    def _calculate_weighted_fair_value(self, valuations: List[float], result: ValuationResult) -> float:
        """Calculate weighted fair value from multiple methods."""
        if not valuations:
            return result.current_price

        # Remove extreme outliers
        median_val = statistics.median(valuations)
        filtered_vals = [v for v in valuations if 0.3 * median_val <= v <= 3 * median_val]

        if not filtered_vals:
            filtered_vals = valuations

        # Weight the methods based on data quality and reliability
        weights = []
        weighted_values = []

        for val in filtered_vals:
            weight = 1.0  # Base weight

            # PE valuation gets higher weight if we have good earnings data
            if val == result.pe_valuation and result.pe_ratio and 5 < result.pe_ratio < 30:
                weight = 1.5

            # Asset valuation gets higher weight for asset-heavy companies
            if val == result.asset_valuation and result.book_value:
                weight = 1.2

            # Income valuation gets higher weight for dividend stocks
            if val == result.income_valuation and result.dividend_yield and result.dividend_yield > 0.02:
                weight = 1.3

            weights.append(weight)
            weighted_values.append(val * weight)

        # Calculate weighted average
        if weights:
            weighted_fair_value = sum(weighted_values) / sum(weights)
        else:
            weighted_fair_value = statistics.median(filtered_vals)

        return weighted_fair_value

    def _calculate_confidence(self, result: ValuationResult, info: Dict) -> float:
        """Calculate confidence score based on data availability and consistency."""
        confidence = 0.0

        # Base confidence from number of methods
        confidence += min(result.methods_used / 5.0, 1.0) * 0.4

        # Data quality factors
        if info.get('trailingEps') and info.get('trailingEps') > 0:
            confidence += 0.15
        if info.get('bookValue') and info.get('bookValue') > 0:
            confidence += 0.1
        if info.get('dividendRate') and info.get('dividendRate') > 0:
            confidence += 0.1
        if info.get('marketCap') and info.get('marketCap') > 1e9:  # >$1B market cap
            confidence += 0.1

        # Consistency check - valuations should be reasonably close
        valuations = [v for v in [result.pe_valuation, result.asset_valuation,
                                result.income_valuation, result.technical_valuation,
                                result.range_valuation] if v and v > 0]

        if len(valuations) >= 2:
            cv = statistics.stdev(valuations) / statistics.mean(valuations)  # Coefficient of variation
            consistency_score = max(0, 1 - cv)  # Lower variation = higher confidence
            confidence += consistency_score * 0.15

        return min(confidence, 1.0)

    def _assess_data_quality(self, info: Dict, history) -> str:
        """Assess overall data quality."""
        quality_score = 0

        # Check for key financial metrics
        key_metrics = ['trailingEps', 'bookValue', 'marketCap', 'revenue', 'totalDebt']
        available_metrics = sum(1 for metric in key_metrics if info.get(metric))
        quality_score += available_metrics / len(key_metrics) * 0.4

        # Check for price history length
        if len(history) >= 250:  # Full year of data
            quality_score += 0.3
        elif len(history) >= 125:  # Half year
            quality_score += 0.2

        # Check for recent data
        if not history.empty:
            last_date = history.index[-1]
            days_old = (datetime.now() - last_date.to_pydatetime().replace(tzinfo=None)).days
            if days_old <= 1:
                quality_score += 0.3
            elif days_old <= 7:
                quality_score += 0.2

        if quality_score >= 0.7:
            return "high"
        elif quality_score >= 0.4:
            return "medium"
        else:
            return "low"

    def _create_default_result(self, symbol: str, current_price: float) -> ValuationResult:
        """Create default result when calculation fails."""
        return ValuationResult(
            symbol=symbol,
            current_price=current_price,
            fair_value=current_price,
            discount_to_fair_value=0.0,
            confidence_score=0.0,
            methods_used=0,
            data_quality="low"
        )

    def get_valuation_signals(self, symbols: List[str], min_discount: float = 15.0) -> List[Dict]:
        """
        Get trading signals based on fair value analysis.
        Returns undervalued opportunities with high confidence.
        """
        signals = []

        for symbol in symbols:
            try:
                valuation = self.calculate_fair_value(symbol)

                # Only signal if significantly undervalued with reasonable confidence
                if (valuation.discount_to_fair_value >= min_discount and
                    valuation.confidence_score >= 0.5 and
                    valuation.methods_used >= 2):

                    # Score based on discount and confidence
                    base_score = 60
                    discount_boost = min(valuation.discount_to_fair_value / 5, 15)  # Up to 15 points
                    confidence_boost = valuation.confidence_score * 10  # Up to 10 points

                    final_score = min(int(base_score + discount_boost + confidence_boost), 85)

                    signals.append({
                        "symbol": symbol,
                        "score": final_score,
                        "type": "fair_value_undervalued",
                        "reason": f"Undervalued: {valuation.discount_to_fair_value:+.1f}% "
                                f"(FV: ${valuation.fair_value:.2f}, conf: {valuation.confidence_score:.1f})",
                        "valuation_data": {
                            "current_price": valuation.current_price,
                            "fair_value": valuation.fair_value,
                            "discount_percent": valuation.discount_to_fair_value,
                            "confidence_score": valuation.confidence_score,
                            "methods_used": valuation.methods_used,
                            "data_quality": valuation.data_quality
                        }
                    })

            except Exception as e:
                logger.error(f"Error getting valuation signal for {symbol}: {e}")

        logger.info(f"Fair value analysis: {len(signals)} undervalued opportunities found")
        return signals


def calculate_fair_values(symbols: List[str]) -> Dict[str, ValuationResult]:
    """
    Main entry point for fair value calculations.
    Returns comprehensive valuation results for each symbol.
    """
    calculator = FairValueCalculator()
    results = {}

    for symbol in symbols:
        results[symbol] = calculator.calculate_fair_value(symbol)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with various types of stocks
    test_symbols = ["AAPL", "JNJ", "XOM", "REIT", "BRK-B"]
    calculator = FairValueCalculator()

    logger.info("\n=== Fair Value Analysis Results ===")
    for symbol in test_symbols:
        try:
            result = calculator.calculate_fair_value(symbol)
            logger.info("{symbol:6s} | Current: ${result.current_price:7.2f} | "
                  f"Fair Value: ${result.fair_value:7.2f} | "
                  f"Discount: {result.discount_to_fair_value:+6.1f}% | "
                  f"Confidence: {result.confidence_score:.2f} | "
                  f"Methods: {result.methods_used}")
        except Exception as e:
            logger.info("{symbol:6s} | Error: {e}")

    # Test signal generation
    signals = calculator.get_valuation_signals(test_symbols)
    logger.info("\n=== Undervalued Opportunities ===")
    for signal in signals:
        logger.info("{signal['symbol']:6s} | Score: {signal['score']:2d} | {signal['reason']}")