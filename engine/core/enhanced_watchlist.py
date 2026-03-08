"""
Enhanced Watchlist Manager with Kelly Allocation and Worker Specialization
"""
import logging
import time
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
from dataclasses import dataclass
import json

from .dynamic_config import cfg
from .contrarian_research import get_contrarian_research_boost, assess_consensus_risk_adjustment
from ..sector_map import get_sector, get_symbols_in_sector

logger = logging.getLogger(__name__)


@dataclass
class OpportunityCandidate:
    symbol: str
    category: str  # high_conviction, momentum_plays, etc.
    expected_return: float
    volatility: float
    kelly_fraction: float
    composite_score: float
    sector: str
    source: str  # scanner, insider, catalyst, etc.
    contrarian_boost: float  # Boost from contrarian research patterns
    consensus_risk: float    # Risk from over-consensus (0=low, 1=high)
    metadata: Dict[str, Any]


class EnhancedWatchlistManager:
    """
    Enhanced watchlist management with Kelly allocation and worker specialization.

    Features:
    - Tiered watchlist (core holdings + dynamic opportunities)
    - Worker-specific symbol filtering
    - Kelly-weighted position sizing
    - Portfolio context awareness
    - Sector concentration limits
    """

    def __init__(self, worker_id: str = "balanced"):
        self.worker_id = worker_id
        self.worker_config = cfg("worker_specialization", {}).get(worker_id, {})
        self.kelly_config = cfg("kelly", {})

        # Cache for performance
        self._opportunity_cache = {}
        self._cache_timestamp = 0
        self._cache_ttl = 300  # 5 minutes

        logger.info(f"Enhanced watchlist initialized for worker: {worker_id}")

    def get_core_holdings(self) -> List[str]:
        """Get core holdings that should never be sold."""
        return cfg("core_holdings", [])

    def get_specialized_universe(self, current_positions: List[Dict] = None) -> List[str]:
        """
        Get worker-specific symbol universe based on specialization config.

        Args:
            current_positions: List of current position dicts from Alpaca

        Returns:
            List of symbols optimized for this worker's strategy
        """
        if current_positions is None:
            current_positions = []

        # Start with core holdings if this worker should hold them
        universe = []
        focus_categories = self.worker_config.get("focus_categories", [])

        if "core_holdings" in focus_categories:
            universe.extend(self.get_core_holdings())

        # Add category-specific opportunities
        opportunities = self._get_categorized_opportunities()

        for category in focus_categories:
            if category in opportunities:
                category_symbols = [opp.symbol for opp in opportunities[category]]
                universe.extend(category_symbols)

        # Apply worker-specific filters
        universe = self._apply_worker_filters(universe)

        # Apply portfolio context filters
        universe = self._apply_portfolio_context_filters(universe, current_positions)

        # Remove duplicates and limit size
        universe = list(dict.fromkeys(universe))[:25]  # Max 25 symbols per worker

        logger.info(f"[{self.worker_id}] Universe: {len(universe)} symbols")
        return universe

    def calculate_kelly_allocation(self, opportunities: List[OpportunityCandidate],
                                 portfolio_value: float) -> Dict[str, Dict]:
        """
        Calculate Kelly-weighted position allocations.

        Args:
            opportunities: List of scored opportunities
            portfolio_value: Total portfolio value

        Returns:
            Dict mapping symbol to allocation info
        """
        allocations = {}

        # Kelly parameters
        fractional_kelly = self.kelly_config.get("fractional_kelly", 0.35)
        max_position_pct = self.kelly_config.get("max_position_pct", 0.15)
        min_kelly = self.kelly_config.get("min_kelly_threshold", 0.001)

        # Calculate raw Kelly fractions
        total_kelly = 0
        valid_opportunities = []

        for opp in opportunities:
            if opp.kelly_fraction >= min_kelly:
                kelly_adj = opp.kelly_fraction * fractional_kelly
                kelly_adj = min(kelly_adj, max_position_pct)  # Cap individual positions
                valid_opportunities.append((opp, kelly_adj))
                total_kelly += kelly_adj

        # Normalize if total exceeds 100%
        if total_kelly > 1.0:
            scale_factor = 0.95 / total_kelly  # Leave 5% cash buffer
            valid_opportunities = [(opp, kelly * scale_factor)
                                 for opp, kelly in valid_opportunities]
            total_kelly = 0.95

        # Calculate dollar allocations with contrarian research adjustments
        for opp, kelly_fraction in valid_opportunities:
            base_allocation = kelly_fraction * portfolio_value

            # Apply consensus risk adjustment (reduce size if over-consensus)
            consensus_risk_adjusted = assess_consensus_risk_adjustment(
                opp.symbol, opp.sector, base_allocation
            )

            # Apply contrarian research boost
            contrarian_multiplier = 1.0 + (opp.contrarian_boost * 0.5)  # Max 50% boost
            final_allocation = consensus_risk_adjusted * contrarian_multiplier

            # Log significant adjustments
            if abs(final_allocation - base_allocation) / base_allocation > 0.1:
                logger.info(f"[{self.worker_id}] {opp.symbol} allocation adjustment: "
                           f"${base_allocation:.0f} → ${final_allocation:.0f} "
                           f"(contrarian: +{opp.contrarian_boost:.2f}, consensus risk applied)")

            allocations[opp.symbol] = {
                "target_value": final_allocation,
                "kelly_fraction": kelly_fraction,
                "expected_return": opp.expected_return,
                "volatility": opp.volatility,
                "composite_score": opp.composite_score,
                "category": opp.category,
                "sector": opp.sector,
                "contrarian_boost": opp.contrarian_boost,
                "consensus_risk_adjustment": final_allocation / base_allocation if base_allocation > 0 else 1.0,
                "sizing_reason": "kelly_contrarian_enhanced"
            }

        logger.info(f"[{self.worker_id}] Kelly allocations: {len(allocations)} positions, "
                   f"total allocation: {total_kelly:.1%}")

        return allocations

    def should_rebalance_position(self, symbol: str, current_value: float,
                                target_value: float, unrealized_pnl: float) -> bool:
        """
        Determine if a position should be rebalanced based on opportunity cost.
        """
        if symbol in self.get_core_holdings():
            return False  # Never rebalance core holdings

        # Only rebalance profitable positions
        if unrealized_pnl <= 0:
            return False

        # Check if current allocation is significantly off target
        if target_value == 0:
            return True  # Position should be closed

        allocation_drift = abs(current_value - target_value) / target_value
        if allocation_drift > 0.25:  # 25% drift threshold
            return True

        # Check for better opportunities
        rebalance_threshold = self.kelly_config.get("rebalance_threshold", 0.15)
        current_opportunities = self._get_categorized_opportunities()

        # This is a simplified check - in practice, you'd compare opportunity scores
        return False  # Placeholder for now

    def _get_categorized_opportunities(self) -> Dict[str, List[OpportunityCandidate]]:
        """Get opportunities organized by category with caching."""
        now = time.time()
        if now - self._cache_timestamp < self._cache_ttl and self._opportunity_cache:
            return self._opportunity_cache

        opportunities = {
            "high_conviction": [],
            "momentum_plays": [],
            "mean_reversion": [],
            "event_driven": [],
            "sector_rotation": [],
        }

        # This would be populated by scanner results, insider data, etc.
        # For now, we'll use a simplified approach based on existing scanners
        try:
            # Import scanner functions - these may not exist yet
            from ..scanners.finviz_scanner import get_momentum_candidates
            momentum_candidates = get_momentum_candidates()
            for symbol in momentum_candidates[:10]:
                symbol_sector = get_sector(symbol)
                contrarian_boost = get_contrarian_research_boost(symbol, symbol_sector)
                consensus_risk = 0.0  # Will be calculated during position sizing

                # Enhance composite score with contrarian research
                base_score = 65
                enhanced_score = base_score + (contrarian_boost * 100)  # Convert to score points

                opportunities["momentum_plays"].append(
                    OpportunityCandidate(
                        symbol=symbol,
                        category="momentum_plays",
                        expected_return=0.15,  # Placeholder
                        volatility=0.35,       # Placeholder
                        kelly_fraction=0.08,   # Placeholder
                        composite_score=enhanced_score,
                        sector=symbol_sector,
                        source="finviz",
                        contrarian_boost=contrarian_boost,
                        consensus_risk=consensus_risk,
                        metadata={"base_score": base_score, "contrarian_enhancement": contrarian_boost}
                    )
                )
        except Exception as e:
            logger.debug(f"Could not load momentum candidates: {e}")

        # Use fallback symbols if no scanner data
        if not any(opportunities.values()):
            fallback_symbols = cfg("watchlist", [])
            for symbol in fallback_symbols:
                symbol_sector = get_sector(symbol)
                contrarian_boost = get_contrarian_research_boost(symbol, symbol_sector)
                consensus_risk = 0.0  # Will be calculated during position sizing

                # Enhance composite score with contrarian research
                base_score = 55
                enhanced_score = base_score + (contrarian_boost * 100)

                opportunities["high_conviction"].append(
                    OpportunityCandidate(
                        symbol=symbol,
                        category="high_conviction",
                        expected_return=0.12,
                        volatility=0.25,
                        kelly_fraction=0.05,
                        composite_score=enhanced_score,
                        sector=symbol_sector,
                        source="fallback",
                        contrarian_boost=contrarian_boost,
                        consensus_risk=consensus_risk,
                        metadata={"base_score": base_score, "contrarian_enhancement": contrarian_boost}
                    )
                )

        self._opportunity_cache = opportunities
        self._cache_timestamp = now

        return opportunities

    def _apply_worker_filters(self, symbols: List[str]) -> List[str]:
        """Apply worker-specific filtering based on volatility, sectors, etc."""
        if not self.worker_config:
            return symbols

        filtered = []
        min_vol = self.worker_config.get("min_volatility", 0.0)
        max_vol = self.worker_config.get("max_volatility", 5.0)
        include_meme = self.worker_config.get("include_meme_stocks", False)
        sector_prefs = self.worker_config.get("sector_preferences", [])

        for symbol in symbols:
            # Sector filter
            if sector_prefs:
                symbol_sector = get_sector(symbol)
                if symbol_sector not in sector_prefs and symbol_sector != "other":
                    continue

            # Meme stock filter
            if not include_meme and get_sector(symbol) == "meme":
                continue

            # Volatility filter would require market data - skip for now
            # In practice, you'd check historical volatility here

            filtered.append(symbol)

        return filtered

    def _apply_portfolio_context_filters(self, symbols: List[str],
                                       current_positions: List[Dict]) -> List[str]:
        """Filter symbols to avoid over-concentration and conflicts."""
        if not current_positions:
            return symbols

        # Get current sector exposure
        sector_exposure = {}
        total_portfolio_value = sum(float(pos.get("market_value", 0))
                                  for pos in current_positions)

        for pos in current_positions:
            symbol = pos.get("symbol", "")
            if symbol:
                sector = get_sector(symbol)
                market_value = float(pos.get("market_value", 0))
                sector_exposure[sector] = sector_exposure.get(sector, 0) + market_value

        # Calculate sector concentrations
        sector_pcts = {sector: value / max(total_portfolio_value, 1)
                      for sector, value in sector_exposure.items()}

        # Filter out symbols from over-concentrated sectors (>25% of portfolio)
        filtered = []
        for symbol in symbols:
            symbol_sector = get_sector(symbol)
            if sector_pcts.get(symbol_sector, 0) < 0.25:  # Max 25% per sector
                filtered.append(symbol)
            else:
                logger.debug(f"Filtering {symbol} - sector {symbol_sector} "
                           f"over-concentrated at {sector_pcts[symbol_sector]:.1%}")

        return filtered

    def get_rebalance_recommendations(self, current_positions: List[Dict],
                                    portfolio_value: float) -> Dict[str, Any]:
        """
        Get comprehensive rebalancing recommendations.

        Returns:
            Dict with 'sell_orders', 'buy_orders', and 'hold_positions'
        """
        opportunities = self._get_categorized_opportunities()
        all_opportunities = []

        # Flatten opportunities for allocation calculation
        for category_opps in opportunities.values():
            all_opportunities.extend(category_opps)

        # Filter by worker specialization
        focus_categories = self.worker_config.get("focus_categories", [])
        if focus_categories:
            all_opportunities = [opp for opp in all_opportunities
                               if opp.category in focus_categories]

        # Calculate target allocations
        target_allocations = self.calculate_kelly_allocation(all_opportunities, portfolio_value)

        # Generate orders
        sell_orders = []
        buy_orders = []
        hold_positions = []

        # Current positions analysis
        current_symbols = {pos.get("symbol"): pos for pos in current_positions}

        # Determine sells
        for symbol, pos in current_symbols.items():
            if symbol in self.get_core_holdings():
                hold_positions.append(symbol)
                continue

            current_value = float(pos.get("market_value", 0))
            unrealized_pnl = float(pos.get("unrealized_pl", 0))
            target_value = target_allocations.get(symbol, {}).get("target_value", 0)

            if self.should_rebalance_position(symbol, current_value, target_value, unrealized_pnl):
                if unrealized_pnl > 0 and target_value == 0:  # Profitable position to close
                    sell_orders.append({
                        "symbol": symbol,
                        "market_value": current_value,
                        "qty": float(pos.get("qty", 0)),
                        "pnl": unrealized_pnl,
                        "reason": "rebalance_exit"
                    })

        # Determine buys
        for symbol, allocation in target_allocations.items():
            if symbol not in current_symbols:
                buy_orders.append({
                    "symbol": symbol,
                    "target_market_value": allocation["target_value"],
                    "sizing_reason": allocation["sizing_reason"],
                    "category": allocation["category"],
                    "expected_return": allocation["expected_return"]
                })

        return {
            "sell_orders": sell_orders,
            "buy_orders": buy_orders,
            "hold_positions": hold_positions,
            "target_allocations": target_allocations
        }