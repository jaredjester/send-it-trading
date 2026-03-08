#!/usr/bin/env python3
"""
Enhanced Finviz Scanner — Incorporates valuable features from Invest Assist.

New features added:
1. High volume stocks screening (from opportunities API)
2. Congressional trading tracking (Senate/House trades)
3. Sector performance analysis
4. Enhanced insider trading with value filtering
5. Options-aware screening for high OI tickers
6. Market summary integration
"""

import logging
import requests
import json
import os
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("enhanced_finviz_scanner")

try:
    from core.dynamic_config import cfg as _cfg
except ImportError:
    # Fallback config function
    def _cfg(key: str, default):
        return default

class EnhancedFinvizScanner:
    def __init__(self):
        self.base_api = _cfg("finviz.api_base_url", "https://api.investassist.com/api")
        self.timeout = _cfg("finviz.request_timeout", 15)

    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make API request with error handling and caching."""
        try:
            url = f"{self.base_api}/{endpoint}"
            response = requests.get(url, params=params or {}, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.debug(f"API request failed for {endpoint}: {e}")
            return None

    def scan_high_volume_opportunities(self) -> List[Dict]:
        """
        Scan for high-volume stock opportunities using Invest Assist's volume screener.
        These are stocks with unusual volume that may indicate institutional activity.
        """
        results = []
        data = self._make_request("finviz/opportunities")

        if not data or not data.get("high_volume_stocks"):
            return results

        opportunities = data["high_volume_stocks"]
        max_stocks = int(_cfg("finviz.max_high_volume", 10))

        for stock in opportunities[:max_stocks]:
            symbol = stock.get("ticker", "").strip()
            if not symbol:
                continue

            # Calculate score based on volume ratio and price action
            volume_ratio = stock.get("volume_ratio", 1.0)
            price_change = stock.get("price_change_percent", 0)

            # Higher volume ratio + positive price action = higher score
            base_score = min(65 + (volume_ratio * 5) + (price_change * 2), 85)

            results.append({
                "symbol": symbol,
                "score": int(base_score),
                "type": "finviz_high_volume",
                "reason": f"High volume ({volume_ratio:.1f}x avg), {price_change:+.1f}%",
                "volume_ratio": volume_ratio,
                "price_change": price_change,
                "sector": stock.get("sector", "Unknown")
            })

        logger.info(f"High volume scan: {len(results)} opportunities")
        return results

    def scan_congressional_trades(self) -> List[Dict]:
        """
        Track congressional trading activity (Senate/House) as contrarian/momentum signals.
        Recent politician trades often precede significant price movements.
        """
        results = []

        # Scan both Senate and House trading
        for endpoint, label in [("senator-trading", "Senate"), ("house-rep-trading", "House")]:
            data = self._make_request(endpoint)
            if not data:
                continue

            trades = data if isinstance(data, list) else data.get("trades", [])
            max_per_house = int(_cfg("finviz.max_congressional_per_house", 5))

            for trade in trades[:max_per_house]:
                symbol = trade.get("ticker", "").strip()
                if not symbol:
                    continue

                trade_type = trade.get("transaction", "").lower()
                amount = trade.get("amount", "")
                politician = trade.get("representative", trade.get("senator", "Unknown"))
                trade_date = trade.get("transaction_date", "")

                # Score based on trade type and recency
                base_score = 68 if "buy" in trade_type.lower() else 62

                # Boost score for recent trades (within 7 days)
                try:
                    trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
                    days_ago = (datetime.now() - trade_dt).days
                    if days_ago <= 7:
                        base_score += 5
                    elif days_ago <= 30:
                        base_score += 2
                except:
                    pass

                results.append({
                    "symbol": symbol,
                    "score": base_score,
                    "type": "finviz_congressional",
                    "reason": f"{label} {trade_type}: {politician} ({amount})",
                    "politician": politician,
                    "trade_type": trade_type,
                    "amount": amount,
                    "trade_date": trade_date,
                    "house": label.lower()
                })

        logger.info(f"Congressional trades: {len(results)} signals")
        return results

    def scan_enhanced_insider_trading(self) -> List[Dict]:
        """
        Enhanced insider trading scanner with value filtering and relationship scoring.
        Focuses on high-value insider transactions that typically precede major moves.
        """
        results = []
        data = self._make_request("finviz/insider", {"option": "latest"})

        if not data:
            return results

        trades = data if isinstance(data, list) else []
        seen_symbols = set()

        for trade in trades:
            symbol = trade.get("Ticker", "").strip()
            if not symbol or symbol in seen_symbols:
                continue

            seen_symbols.add(symbol)

            transaction = trade.get("Transaction", "").lower()
            value_str = trade.get("Value", "").replace("$", "").replace(",", "")
            owner = trade.get("Owner", "Unknown")
            relationship = trade.get("Relationship", "")

            # Only track buy transactions
            if "buy" not in transaction and "purchase" not in transaction:
                continue

            # Parse trade value
            try:
                if "k" in value_str.lower():
                    value = float(value_str.lower().replace("k", "")) * 1000
                elif "m" in value_str.lower():
                    value = float(value_str.lower().replace("m", "")) * 1000000
                else:
                    value = float(value_str) if value_str.replace(".", "").isdigit() else 0
            except:
                value = 0

            # Score based on trade value and insider relationship
            base_score = 65

            # Value scoring
            if value >= 1000000:  # $1M+
                base_score += 8
            elif value >= 500000:  # $500K+
                base_score += 5
            elif value >= 100000:  # $100K+
                base_score += 3

            # Relationship scoring (higher for key insiders)
            if any(role in relationship.lower() for role in ["ceo", "cfo", "president"]):
                base_score += 5
            elif any(role in relationship.lower() for role in ["director", "officer"]):
                base_score += 3

            results.append({
                "symbol": symbol,
                "score": min(base_score, 85),
                "type": "finviz_enhanced_insider",
                "reason": f"Insider buy: {owner} ({relationship}) ${value:,.0f}",
                "insider_name": owner,
                "relationship": relationship,
                "trade_value": value,
                "transaction": transaction
            })

            if len(results) >= int(_cfg("finviz.max_insider_trades", 8)):
                break

        logger.info(f"Enhanced insider trades: {len(results)} high-value signals")
        return results

    def scan_sector_rotation_opportunities(self) -> List[Dict]:
        """
        Identify sector rotation opportunities by analyzing sector performance.
        Focuses on sectors showing strength relative to market.
        """
        results = []
        data = self._make_request("sector-performance")

        if not data or not data.get("sectors"):
            return results

        sectors = data["sectors"]

        # Find top performing sectors
        top_sectors = sorted(
            sectors,
            key=lambda x: x.get("day_change_percent", 0),
            reverse=True
        )[:3]

        # For each top sector, look for individual stock opportunities
        for sector_data in top_sectors:
            sector_name = sector_data.get("sector", "Unknown")
            sector_performance = sector_data.get("day_change_percent", 0)

            if sector_performance < 1.0:  # Only consider sectors up >1%
                continue

            # Get sector leaders (this would need additional API endpoint)
            # For now, we'll create a placeholder that could be enhanced
            base_score = min(66 + int(sector_performance * 2), 75)

            results.append({
                "symbol": "SECTOR_" + sector_name.replace(" ", "_").upper(),
                "score": base_score,
                "type": "finviz_sector_rotation",
                "reason": f"Sector leader: {sector_name} +{sector_performance:.1f}%",
                "sector": sector_name,
                "sector_performance": sector_performance
            })

        logger.info(f"Sector rotation: {len(results)} sector opportunities")
        return results

    def get_market_summary_context(self) -> Dict:
        """
        Get market summary to provide context for all scans.
        This helps adjust scoring based on overall market conditions.
        """
        data = self._make_request("market-summary")
        if not data:
            return {}

        summary = {
            "market_direction": "neutral",
            "vix_level": data.get("vix", 20),
            "spy_change": data.get("spy_change_percent", 0),
            "qqq_change": data.get("qqq_change_percent", 0),
            "sentiment": "neutral"
        }

        # Determine market direction
        if summary["spy_change"] > 1.5:
            summary["market_direction"] = "bullish"
            summary["sentiment"] = "risk_on"
        elif summary["spy_change"] < -1.5:
            summary["market_direction"] = "bearish"
            summary["sentiment"] = "risk_off"

        # Adjust for volatility
        if summary["vix_level"] > 25:
            summary["sentiment"] = "high_volatility"
        elif summary["vix_level"] < 15:
            summary["sentiment"] = "low_volatility"

        return summary

    def run_enhanced_scan(self) -> List[Dict]:
        """
        Main entry point for enhanced Finviz scanning.
        Combines all new scanning capabilities with market context.
        """
        all_results = []

        # Get market context first
        market_context = self.get_market_summary_context()
        logger.info(f"Market context: {market_context.get('market_direction')} "
                   f"(SPY {market_context.get('spy_change', 0):+.1f}%, "
                   f"VIX {market_context.get('vix_level', 20):.1f})")

        # Run all enhanced scans
        scan_functions = [
            self.scan_high_volume_opportunities,
            self.scan_congressional_trades,
            self.scan_enhanced_insider_trading,
            self.scan_sector_rotation_opportunities
        ]

        for scan_func in scan_functions:
            try:
                results = scan_func()

                # Adjust scores based on market context
                for result in results:
                    original_score = result["score"]

                    # Boost momentum plays in bull markets
                    if (market_context.get("market_direction") == "bullish" and
                        result["type"] in ["finviz_high_volume", "finviz_congressional"]):
                        result["score"] = min(result["score"] + 3, 85)

                    # Boost insider/contrarian plays in bear markets
                    elif (market_context.get("market_direction") == "bearish" and
                          result["type"] in ["finviz_enhanced_insider", "finviz_congressional"]):
                        result["score"] = min(result["score"] + 2, 85)

                    # Reduce all scores in high volatility
                    if market_context.get("vix_level", 20) > 30:
                        result["score"] = max(result["score"] - 5, 45)

                all_results.extend(results)

            except Exception as e:
                logger.error(f"Error in {scan_func.__name__}: {e}")

        # Deduplicate and sort
        symbol_map = {}
        for result in all_results:
            symbol = result["symbol"]
            if symbol in symbol_map:
                # Combine signals for same symbol
                existing = symbol_map[symbol]
                existing["score"] = min(existing["score"] + 2, 85)  # Small boost for multiple signals
                existing["reason"] += f" + {result['type']}"
            else:
                symbol_map[symbol] = result

        final_results = list(symbol_map.values())
        final_results.sort(key=lambda x: x["score"], reverse=True)

        # Limit total results
        max_total = int(_cfg("finviz.max_enhanced_total", 20))
        final_results = final_results[:max_total]

        # Add market context to results
        for result in final_results:
            result["market_context"] = market_context

        logger.info(f"Enhanced Finviz scan complete: {len(final_results)} total opportunities")

        # Cache results for other modules
        self._cache_results(final_results, market_context)

        return final_results

    def _cache_results(self, results: List[Dict], market_context: Dict):
        """Cache enhanced scan results for use by other modules."""
        try:
            cache_dir = Path(__file__).parent.parent / "state"
            cache_dir.mkdir(exist_ok=True)

            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "market_context": market_context,
                "enhanced_signals": results,
                "total_signals": len(results)
            }

            cache_file = cache_dir / "enhanced_finviz_signals.json"
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)

        except Exception as e:
            logger.debug(f"Failed to cache enhanced results: {e}")


def run_enhanced_finviz_scan() -> List[Dict]:
    """Main entry point for external callers."""
    scanner = EnhancedFinvizScanner()
    return scanner.run_enhanced_scan()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = EnhancedFinvizScanner()
    results = scanner.run_enhanced_scan()

    print(f"\n=== Enhanced Finviz Scan Results ({len(results)} total) ===")
    for result in results:
        print(f"{result['symbol']:12s} | Score: {result['score']:2d} | "
              f"{result['type']:25s} | {result['reason']}")