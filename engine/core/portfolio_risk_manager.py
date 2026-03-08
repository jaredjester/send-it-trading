#!/usr/bin/env python3
"""
Portfolio Risk Manager — Advanced portfolio management from Invest Assist integration.

Features:
1. Sector diversification controls and analysis
2. Position sizing with risk-adjusted allocation
3. Correlation analysis between holdings
4. Portfolio-level risk metrics (beta, volatility, VaR)
5. Concentration risk monitoring
6. Dynamic risk adjustment based on market conditions

This prevents over-concentration and manages portfolio-level risk
while maintaining the options-first aggressive trading approach.
"""

import logging
import json
import statistics
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from dataclasses import dataclass
from pathlib import Path
import sys
import requests

# Add parent directory to path

logger = logging.getLogger("portfolio_risk_manager")

try:
    from core.dynamic_config import cfg as _cfg
    from core.alpaca_client import AlpacaClient
except ImportError:
    def _cfg(key: str, default):
        return default
    AlpacaClient = None

# Try to import numpy/pandas, fallback to basic math if not available
import numpy as np
import pandas as pd
# Try to import yfinance, with complete fallback if not available
import yfinance as yf
@dataclass
class PortfolioPosition:
    """Portfolio position with risk metrics."""
    symbol: str
    quantity: float
    market_value: float
    weight: float  # Portfolio weight
    sector: str
    beta: Optional[float] = None
    volatility: Optional[float] = None
    correlation_to_spy: Optional[float] = None

@dataclass
class SectorAllocation:
    """Sector allocation metrics."""
    sector: str
    symbols: List[str]
    total_value: float
    weight: float
    target_weight: float
    over_under: float  # Difference from target

@dataclass
class RiskMetrics:
    """Portfolio-level risk metrics."""
    portfolio_beta: float
    portfolio_volatility: float
    diversification_ratio: float
    concentration_risk: float  # 0-1, higher = more concentrated
    var_95: float  # Value at Risk (95% confidence)
    max_sector_weight: float
    total_positions: int
    correlation_risk: float  # Average correlation between positions

class PortfolioRiskManager:
    def __init__(self, alpaca_client=None):
        self.alpaca_client = alpaca_client

        # Risk limits (configurable)
        self.max_sector_weight = _cfg("risk.max_sector_weight", 0.25)  # 25%
        self.max_position_weight = _cfg("risk.max_position_weight", 0.10)  # 10%
        self.target_positions = _cfg("risk.target_positions", 8)
        self.max_correlation = _cfg("risk.max_correlation", 0.7)
        self.max_portfolio_beta = _cfg("risk.max_portfolio_beta", 1.3)

        # Sector targets (can be dynamic)
        self.target_sector_weights = {
            "Technology": 0.30,
            "Financials": 0.15,
            "Healthcare": 0.15,
            "Consumer Cyclical": 0.10,
            "Communication Services": 0.10,
            "Industrials": 0.08,
            "Energy": 0.05,
            "Utilities": 0.04,
            "Real Estate": 0.03
        }

        # Cache for market data
        self.market_data_cache = {}
        self.spy_data = None

    def analyze_portfolio_risk(self, positions: List[Dict]) -> RiskMetrics:
        """
        Comprehensive portfolio risk analysis.
        Takes current positions and calculates risk metrics.
        """
        if not positions:
            return self._create_empty_risk_metrics()

        try:
            # Convert positions to PortfolioPosition objects
            portfolio_positions = []
            total_value = sum(pos.get("market_value", 0) for pos in positions)

            for pos in positions:
                symbol = pos.get("symbol", "")
                market_value = pos.get("market_value", 0)
                quantity = pos.get("quantity", 0)

                if not symbol or market_value <= 0:
                    continue

                # Get additional data for each position
                sector, beta, volatility = self._get_position_metrics(symbol)

                portfolio_positions.append(PortfolioPosition(
                    symbol=symbol,
                    quantity=quantity,
                    market_value=market_value,
                    weight=market_value / total_value if total_value > 0 else 0,
                    sector=sector,
                    beta=beta,
                    volatility=volatility
                ))

            if not portfolio_positions:
                return self._create_empty_risk_metrics()

            # Calculate portfolio-level metrics
            portfolio_beta = self._calculate_portfolio_beta(portfolio_positions)
            portfolio_vol = self._calculate_portfolio_volatility(portfolio_positions)
            diversification_ratio = self._calculate_diversification_ratio(portfolio_positions)
            concentration_risk = self._calculate_concentration_risk(portfolio_positions)
            var_95 = self._calculate_var_95(portfolio_positions, total_value)
            max_sector_weight = self._get_max_sector_weight(portfolio_positions)
            correlation_risk = self._calculate_correlation_risk(portfolio_positions)

            risk_metrics = RiskMetrics(
                portfolio_beta=portfolio_beta,
                portfolio_volatility=portfolio_vol,
                diversification_ratio=diversification_ratio,
                concentration_risk=concentration_risk,
                var_95=var_95,
                max_sector_weight=max_sector_weight,
                total_positions=len(portfolio_positions),
                correlation_risk=correlation_risk
            )

            logger.info(f"Portfolio risk analysis: Beta={portfolio_beta:.2f}, "
                       f"Vol={portfolio_vol:.1%}, Positions={len(portfolio_positions)}")

            return risk_metrics

        except Exception as e:
            logger.error(f"Error analyzing portfolio risk: {e}")
            return self._create_empty_risk_metrics()

    def get_sector_allocation_analysis(self, positions: List[Dict]) -> List[SectorAllocation]:
        """Analyze current sector allocation vs targets."""
        sector_allocations = defaultdict(lambda: {"symbols": [], "value": 0})
        total_value = sum(pos.get("market_value", 0) for pos in positions)

        if total_value <= 0:
            return []

        # Group positions by sector
        for pos in positions:
            symbol = pos.get("symbol", "")
            market_value = pos.get("market_value", 0)

            if not symbol or market_value <= 0:
                continue

            sector = self._get_sector_for_symbol(symbol)
            sector_allocations[sector]["symbols"].append(symbol)
            sector_allocations[sector]["value"] += market_value

        # Create SectorAllocation objects
        allocations = []
        for sector, data in sector_allocations.items():
            current_weight = data["value"] / total_value
            target_weight = self.target_sector_weights.get(sector, 0.05)  # 5% default
            over_under = current_weight - target_weight

            allocations.append(SectorAllocation(
                sector=sector,
                symbols=data["symbols"],
                total_value=data["value"],
                weight=current_weight,
                target_weight=target_weight,
                over_under=over_under
            ))

        # Sort by current weight (largest first)
        allocations.sort(key=lambda x: x.weight, reverse=True)

        return allocations

    def check_position_risk_limits(self, symbol: str, proposed_allocation: float,
                                 current_positions: List[Dict]) -> Dict[str, Any]:
        """
        Check if a proposed position violates risk limits.
        Returns risk analysis and recommendations.
        """
        total_portfolio_value = sum(pos.get("market_value", 0) for pos in current_positions)
        if total_portfolio_value <= 0:
            total_portfolio_value = proposed_allocation * 10  # Estimate

        proposed_weight = proposed_allocation / total_portfolio_value
        sector = self._get_sector_for_symbol(symbol)

        risk_check = {
            "symbol": symbol,
            "proposed_allocation": proposed_allocation,
            "proposed_weight": proposed_weight,
            "sector": sector,
            "violations": [],
            "warnings": [],
            "approved": True,
            "recommended_allocation": proposed_allocation
        }

        # Check position size limit
        if proposed_weight > self.max_position_weight:
            risk_check["violations"].append(
                f"Position weight {proposed_weight:.1%} exceeds limit {self.max_position_weight:.1%}")
            risk_check["approved"] = False
            risk_check["recommended_allocation"] = total_portfolio_value * self.max_position_weight

        # Check sector concentration
        sector_allocations = self.get_sector_allocation_analysis(current_positions)
        current_sector_weight = 0
        for alloc in sector_allocations:
            if alloc.sector == sector:
                current_sector_weight = alloc.weight
                break

        new_sector_weight = current_sector_weight + proposed_weight
        if new_sector_weight > self.max_sector_weight:
            risk_check["violations"].append(
                f"Sector weight would be {new_sector_weight:.1%}, exceeds limit {self.max_sector_weight:.1%}")
            risk_check["approved"] = False

            # Recommend reduced allocation to stay within sector limit
            max_additional_sector_weight = self.max_sector_weight - current_sector_weight
            risk_check["recommended_allocation"] = max(0, max_additional_sector_weight * total_portfolio_value)

        # Check for excessive correlation
        existing_symbols = [pos.get("symbol") for pos in current_positions]
        high_correlation_symbols = self._find_highly_correlated_symbols(symbol, existing_symbols)

        if high_correlation_symbols:
            risk_check["warnings"].append(
                f"High correlation with existing positions: {', '.join(high_correlation_symbols[:3])}")

        # Check portfolio concentration
        if len(current_positions) < 3 and proposed_weight > 0.15:  # More than 15% in small portfolio
            risk_check["warnings"].append("Portfolio has few positions - consider smaller allocation")

        return risk_check

    def generate_rebalancing_recommendations(self, current_positions: List[Dict]) -> List[Dict]:
        """Generate recommendations to rebalance portfolio for better risk profile."""
        recommendations = []

        if not current_positions:
            return recommendations

        # Analyze current allocation
        sector_allocations = self.get_sector_allocation_analysis(current_positions)
        risk_metrics = self.analyze_portfolio_risk(current_positions)

        # Sector rebalancing recommendations
        for allocation in sector_allocations:
            if abs(allocation.over_under) > 0.05:  # >5% deviation from target
                if allocation.over_under > 0:  # Overweight
                    recommendations.append({
                        "type": "reduce_sector",
                        "sector": allocation.sector,
                        "current_weight": allocation.weight,
                        "target_weight": allocation.target_weight,
                        "action": f"Reduce {allocation.sector} by {allocation.over_under:.1%}",
                        "priority": "high" if allocation.over_under > 0.10 else "medium",
                        "affected_symbols": allocation.symbols
                    })
                else:  # Underweight
                    recommendations.append({
                        "type": "increase_sector",
                        "sector": allocation.sector,
                        "current_weight": allocation.weight,
                        "target_weight": allocation.target_weight,
                        "action": f"Increase {allocation.sector} by {abs(allocation.over_under):.1%}",
                        "priority": "medium",
                        "suggested_allocation": abs(allocation.over_under)
                    })

        # High concentration recommendations
        if risk_metrics.concentration_risk > 0.3:  # High concentration
            # Find largest positions
            positions_by_weight = sorted(current_positions,
                                       key=lambda x: x.get("market_value", 0), reverse=True)

            for pos in positions_by_weight[:2]:  # Top 2 positions
                weight = pos.get("market_value", 0) / sum(p.get("market_value", 0) for p in current_positions)
                if weight > 0.15:  # >15% in single position
                    recommendations.append({
                        "type": "reduce_position",
                        "symbol": pos.get("symbol"),
                        "current_weight": weight,
                        "action": f"Reduce {pos.get('symbol')} position (currently {weight:.1%})",
                        "priority": "high" if weight > 0.20 else "medium"
                    })

        # High beta recommendations
        if risk_metrics.portfolio_beta > self.max_portfolio_beta:
            recommendations.append({
                "type": "reduce_beta",
                "current_beta": risk_metrics.portfolio_beta,
                "target_beta": self.max_portfolio_beta,
                "action": f"Reduce portfolio beta from {risk_metrics.portfolio_beta:.2f}",
                "priority": "medium",
                "suggestion": "Add defensive stocks or reduce high-beta positions"
            })

        # Diversification recommendations
        if risk_metrics.total_positions < 5:
            recommendations.append({
                "type": "increase_diversification",
                "current_positions": risk_metrics.total_positions,
                "target_positions": self.target_positions,
                "action": "Add more positions for better diversification",
                "priority": "high" if risk_metrics.total_positions < 3 else "medium"
            })

        logger.info(f"Generated {len(recommendations)} rebalancing recommendations")
        return recommendations

    def _get_position_metrics(self, symbol: str) -> Tuple[str, Optional[float], Optional[float]]:
        """Get sector, beta, and volatility for a symbol with multiple fallback sources."""
        try:
            if symbol in self.market_data_cache:
                cached_data, timestamp = self.market_data_cache[symbol]
                if datetime.now() - timestamp < timedelta(hours=6):  # 6-hour cache
                    return cached_data

            # Try primary yfinance if available
            if True:  # yfinance available
                try:
                    ticker = yf.Ticker(symbol)
                    info = ticker.info

                    sector = info.get("sector", "Unknown")
                    beta = info.get("beta")

                    # Calculate volatility from price history
                    volatility = None
                    try:
                        hist = ticker.history(period="3mo")
                        if len(hist) > 20:
                            if True:  # numpy always available
                                returns = hist['Close'].pct_change().dropna()
                                volatility = returns.std() * (252 ** 0.5)  # Annualized
                    except:
                        pass

                    result = (sector, beta, volatility)
                    self.market_data_cache[symbol] = (result, datetime.now())
                    return result
                except Exception as e:
                    logger.debug(f"yfinance failed for {symbol}: {e}")

            # Fallback 1: Try Alpaca if available
            if AlpacaClient:
                try:
                    # Use Alpaca's asset endpoint for basic info
                    sector, beta = self._get_alpaca_metrics(symbol)
                    if sector != "Unknown":
                        result = (sector, beta, None)  # No volatility from Alpaca
                        self.market_data_cache[symbol] = (result, datetime.now())
                        return result
                except Exception as e:
                    logger.debug(f"Alpaca fallback failed for {symbol}: {e}")

            # Fallback 2: Use static sector mappings for common stocks
            sector = self._get_sector_from_static_mapping(symbol)
            beta = self._estimate_beta_from_sector(sector)
            volatility = self._estimate_volatility_from_sector(sector)

            result = (sector, beta, volatility)
            self.market_data_cache[symbol] = (result, datetime.now())
            return result

        except Exception as e:
            logger.debug(f"All fallbacks failed for {symbol}: {e}")
            return ("Unknown", None, None)

    def _get_alpaca_metrics(self, symbol: str) -> Tuple[str, Optional[float]]:
        """Get basic metrics from Alpaca API."""
        # This would integrate with Alpaca's asset API if available
        # For now, return defaults
        return ("Unknown", None)

    def _get_sector_from_static_mapping(self, symbol: str) -> str:
        """Get sector from static mapping of common stocks."""
        sector_mapping = {
            # Technology
            'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Technology', 'GOOG': 'Technology',
            'AMZN': 'Technology', 'META': 'Technology', 'TSLA': 'Technology', 'NVDA': 'Technology',
            'NFLX': 'Technology', 'ADBE': 'Technology', 'CRM': 'Technology', 'ORCL': 'Technology',

            # Financial
            'JPM': 'Financial Services', 'BAC': 'Financial Services', 'WFC': 'Financial Services',
            'GS': 'Financial Services', 'MS': 'Financial Services', 'C': 'Financial Services',
            'V': 'Financial Services', 'MA': 'Financial Services', 'AXP': 'Financial Services',

            # Healthcare
            'JNJ': 'Healthcare', 'PFE': 'Healthcare', 'UNH': 'Healthcare', 'ABBV': 'Healthcare',
            'MRK': 'Healthcare', 'LLY': 'Healthcare', 'TMO': 'Healthcare', 'DHR': 'Healthcare',

            # Consumer
            'PG': 'Consumer Defensive', 'KO': 'Consumer Defensive', 'PEP': 'Consumer Defensive',
            'WMT': 'Consumer Defensive', 'HD': 'Consumer Cyclical', 'MCD': 'Consumer Cyclical',
            'NKE': 'Consumer Cyclical', 'SBUX': 'Consumer Cyclical',

            # Industrial
            'BA': 'Industrials', 'CAT': 'Industrials', 'GE': 'Industrials', 'MMM': 'Industrials',

            # Energy
            'XOM': 'Energy', 'CVX': 'Energy', 'COP': 'Energy', 'SLB': 'Energy',

            # ETFs
            'SPY': 'ETF', 'QQQ': 'ETF', 'IWM': 'ETF', 'VTI': 'ETF', 'VOO': 'ETF',
        }

        return sector_mapping.get(symbol.upper(), "Unknown")

    def _estimate_beta_from_sector(self, sector: str) -> Optional[float]:
        """Estimate beta based on sector averages."""
        sector_beta_map = {
            'Technology': 1.2,
            'Financial Services': 1.1,
            'Healthcare': 0.9,
            'Consumer Defensive': 0.7,
            'Consumer Cyclical': 1.0,
            'Industrials': 1.1,
            'Energy': 1.3,
            'ETF': 1.0,
        }
        return sector_beta_map.get(sector)

    def _estimate_volatility_from_sector(self, sector: str) -> Optional[float]:
        """Estimate volatility based on sector averages."""
        sector_volatility_map = {
            'Technology': 0.35,
            'Financial Services': 0.30,
            'Healthcare': 0.25,
            'Consumer Defensive': 0.20,
            'Consumer Cyclical': 0.28,
            'Industrials': 0.30,
            'Energy': 0.40,
            'ETF': 0.18,
        }
        return sector_volatility_map.get(sector)

    def _get_sector_for_symbol(self, symbol: str) -> str:
        """Get sector for a symbol."""
        sector, _, _ = self._get_position_metrics(symbol)
        return sector

    def _calculate_portfolio_beta(self, positions: List[PortfolioPosition]) -> float:
        """Calculate weighted portfolio beta."""
        weighted_beta = 0
        total_weight = 0

        for pos in positions:
            if pos.beta is not None and pos.weight > 0:
                weighted_beta += pos.beta * pos.weight
                total_weight += pos.weight

        return weighted_beta / total_weight if total_weight > 0 else 1.0

    def _calculate_portfolio_volatility(self, positions: List[PortfolioPosition]) -> float:
        """Calculate portfolio volatility (simplified)."""
        weighted_vol = 0
        total_weight = 0

        for pos in positions:
            if pos.volatility is not None and pos.weight > 0:
                weighted_vol += pos.volatility * pos.weight
                total_weight += pos.weight

        return weighted_vol / total_weight if total_weight > 0 else 0.20  # Default 20%

    def _calculate_diversification_ratio(self, positions: List[PortfolioPosition]) -> float:
        """Calculate diversification ratio (simplified)."""
        if len(positions) <= 1:
            return 0.0

        # Simple approximation: 1 - (1 / number_of_positions)
        # Real calculation would need correlation matrix
        return 1 - (1 / len(positions))

    def _calculate_concentration_risk(self, positions: List[PortfolioPosition]) -> float:
        """Calculate concentration risk using Herfindahl index."""
        if not positions:
            return 0.0

        # Herfindahl index: sum of squared weights
        hhi = sum(pos.weight ** 2 for pos in positions)

        # Normalize to 0-1 scale (1 = maximum concentration)
        return hhi

    def _calculate_var_95(self, positions: List[PortfolioPosition], total_value: float) -> float:
        """Calculate 95% Value at Risk (simplified)."""
        portfolio_vol = self._calculate_portfolio_volatility(positions)

        # Assume normal distribution, 95% VaR
        # In reality, would use Monte Carlo or historical simulation
        daily_vol = portfolio_vol / (252 ** 0.5)
        var_95 = total_value * daily_vol * 1.65  # 95% confidence

        return var_95

    def _get_max_sector_weight(self, positions: List[PortfolioPosition]) -> float:
        """Get the weight of the largest sector allocation."""
        sector_weights = defaultdict(float)

        for pos in positions:
            sector_weights[pos.sector] += pos.weight

        return max(sector_weights.values()) if sector_weights else 0.0

    def _calculate_correlation_risk(self, positions: List[PortfolioPosition]) -> float:
        """Calculate average correlation risk (simplified)."""
        if len(positions) < 2:
            return 0.0

        # Simplified: assume technology stocks are highly correlated
        tech_weight = sum(pos.weight for pos in positions if pos.sector == "Technology")

        # High tech concentration = high correlation risk
        return min(tech_weight * 2, 1.0)

    def _find_highly_correlated_symbols(self, symbol: str, existing_symbols: List[str]) -> List[str]:
        """Find symbols in portfolio that are highly correlated with the proposed symbol."""
        highly_correlated = []

        try:
            # Simplified correlation check based on sector
            symbol_sector = self._get_sector_for_symbol(symbol)

            for existing_symbol in existing_symbols:
                existing_sector = self._get_sector_for_symbol(existing_symbol)

                # Same sector = potentially high correlation
                if symbol_sector == existing_sector and symbol_sector in ["Technology", "Energy"]:
                    highly_correlated.append(existing_symbol)

        except Exception as e:
            logger.debug(f"Error finding correlations: {e}")

        return highly_correlated

    def _create_empty_risk_metrics(self) -> RiskMetrics:
        """Create empty risk metrics for error cases."""
        return RiskMetrics(
            portfolio_beta=1.0,
            portfolio_volatility=0.20,
            diversification_ratio=0.0,
            concentration_risk=1.0,
            var_95=0.0,
            max_sector_weight=1.0,
            total_positions=0,
            correlation_risk=0.0
        )

    def get_optimal_position_size(self, symbol: str, base_allocation: float,
                                current_positions: List[Dict]) -> Dict[str, Any]:
        """
        Calculate optimal position size considering risk constraints.
        Uses Kelly Criterion concepts with risk overlays.
        """
        try:
            # Get position risk check
            risk_check = self.check_position_risk_limits(symbol, base_allocation, current_positions)

            if not risk_check["approved"]:
                return {
                    "symbol": symbol,
                    "optimal_allocation": risk_check["recommended_allocation"],
                    "base_allocation": base_allocation,
                    "adjustment_reason": "Risk limit violations",
                    "risk_violations": risk_check["violations"]
                }

            # Risk-adjusted sizing
            sector, beta, volatility = self._get_position_metrics(symbol)

            # Adjust for beta (reduce allocation for high beta stocks)
            beta_adjustment = 1.0
            if beta and beta > 1.5:
                beta_adjustment = 0.8  # 20% reduction for high beta
            elif beta and beta < 0.7:
                beta_adjustment = 1.1  # 10% increase for low beta

            # Adjust for volatility
            vol_adjustment = 1.0
            if volatility and volatility > 0.4:  # >40% volatility
                vol_adjustment = 0.7  # 30% reduction for high volatility
            elif volatility and volatility < 0.15:  # <15% volatility
                vol_adjustment = 1.1  # 10% increase for low volatility

            # Portfolio concentration adjustment
            num_positions = len(current_positions)
            concentration_adjustment = 1.0
            if num_positions < 3:
                concentration_adjustment = 0.8  # Be more conservative with few positions
            elif num_positions > 10:
                concentration_adjustment = 0.9  # Slightly reduce for many positions

            # Calculate final allocation
            optimal_allocation = (base_allocation * beta_adjustment *
                                vol_adjustment * concentration_adjustment)

            # Final safety check
            optimal_allocation = min(optimal_allocation, risk_check["recommended_allocation"])

            return {
                "symbol": symbol,
                "optimal_allocation": optimal_allocation,
                "base_allocation": base_allocation,
                "adjustments": {
                    "beta_adjustment": beta_adjustment,
                    "volatility_adjustment": vol_adjustment,
                    "concentration_adjustment": concentration_adjustment
                },
                "risk_metrics": {
                    "beta": beta,
                    "volatility": volatility,
                    "sector": sector
                }
            }

        except Exception as e:
            logger.error(f"Error calculating optimal position size for {symbol}: {e}")
            return {
                "symbol": symbol,
                "optimal_allocation": base_allocation * 0.5,  # Conservative fallback
                "base_allocation": base_allocation,
                "adjustment_reason": f"Error in calculation: {e}"
            }


def analyze_portfolio_risk(positions: List[Dict]) -> RiskMetrics:
    """Main entry point for portfolio risk analysis."""
    risk_manager = PortfolioRiskManager()
    return risk_manager.analyze_portfolio_risk(positions)


def check_position_risk(symbol: str, allocation: float, current_positions: List[Dict]) -> Dict:
    """Main entry point for position risk checking."""
    risk_manager = PortfolioRiskManager()
    return risk_manager.check_position_risk_limits(symbol, allocation, current_positions)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with sample portfolio
    sample_positions = [
        {"symbol": "AAPL", "quantity": 100, "market_value": 15000},
        {"symbol": "MSFT", "quantity": 50, "market_value": 12000},
        {"symbol": "TSLA", "quantity": 30, "market_value": 8000},
        {"symbol": "JNJ", "quantity": 60, "market_value": 10000},
        {"symbol": "XOM", "quantity": 200, "market_value": 5000}
    ]

    risk_manager = PortfolioRiskManager()

    # Test risk analysis
    risk_metrics = risk_manager.analyze_portfolio_risk(sample_positions)
    logger.info("\n=== Portfolio Risk Analysis ===")
    logger.info("Portfolio Beta: {risk_metrics.portfolio_beta:.2f}")
    logger.info("Portfolio Volatility: {risk_metrics.portfolio_volatility:.1%}")
    logger.info("Concentration Risk: {risk_metrics.concentration_risk:.2f}")
    logger.info("Max Sector Weight: {risk_metrics.max_sector_weight:.1%}")
    logger.info("Total Positions: {risk_metrics.total_positions}")

    # Test sector allocation
    sector_allocations = risk_manager.get_sector_allocation_analysis(sample_positions)
    logger.info("\n=== Sector Allocation ===")
    for allocation in sector_allocations:
        logger.info("{allocation.sector:20s}: {allocation.weight:6.1%} "
              f"(target: {allocation.target_weight:6.1%}, "
              f"diff: {allocation.over_under:+6.1%})")

    # Test position risk check
    risk_check = risk_manager.check_position_risk_limits("GOOGL", 8000, sample_positions)
    logger.info("\n=== Position Risk Check (GOOGL, $8000) ===")
    logger.info("Approved: {risk_check['approved']}")
    logger.info("Recommended Allocation: ${risk_check['recommended_allocation']:.0f}")
    if risk_check["violations"]:
        logger.info("Violations: {risk_check['violations']}")
    if risk_check["warnings"]:
        logger.info("Warnings: {risk_check['warnings']}")

    # Test rebalancing recommendations
    recommendations = risk_manager.generate_rebalancing_recommendations(sample_positions)
    logger.info("\n=== Rebalancing Recommendations ===")
    for rec in recommendations:
        logger.info("{rec['type']:20s}: {rec['action']} (Priority: {rec.get('priority', 'N/A')})")