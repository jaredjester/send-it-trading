"""
Unified Opportunity Finder

Combines all scanners and ranks opportunities by score.
Returns top 3-5 daily plays for execution.
"""
from typing import List, Dict
from datetime import datetime
import logging

from .morning_gap_scanner import GapScanner
from .catalyst_scanner import CatalystScanner

logger = logging.getLogger(__name__)


class OpportunityFinder:
    """Finds and ranks all trading opportunities."""
    
    def __init__(self):
        self.gap_scanner = GapScanner()
        self.catalyst_scanner = CatalystScanner()
    
    def find_all_opportunities(self) -> List[Dict]:
        """
        Scan all opportunity types and rank them.
        
        Returns:
            List of opportunities sorted by score (best first)
        """
        all_opps = []
        
        # Morning gaps
        try:
            gaps = self.gap_scanner.scan_gaps(min_gap_pct=5.0)
            for gap in gaps:
                all_opps.append({
                    **gap,
                    'opportunity_type': 'GAP',
                    'entry_time': 'MARKET_OPEN',
                    'hold_duration': 'INTRADAY'
                })
        except Exception as e:
            logger.error(f"Gap scan failed: {e}")
        
        # Catalyst plays
        try:
            catalysts = self.catalyst_scanner.scan_catalysts(min_volume_ratio=3.0)
            for cat in catalysts:
                all_opps.append({
                    **cat,
                    'opportunity_type': 'CATALYST',
                    'entry_time': 'IMMEDIATE',
                    'hold_duration': 'HOURS'
                })
        except Exception as e:
            logger.error(f"Catalyst scan failed: {e}")
        
        # Sort by score (descending)
        all_opps.sort(key=lambda x: x['score'], reverse=True)
        
        return all_opps
    
    def get_top_opportunities(self, limit: int = 5) -> List[Dict]:
        """Get top N opportunities for execution."""
        all_opps = self.find_all_opportunities()
        return all_opps[:limit]
    
    def get_immediate_plays(self) -> List[Dict]:
        """Get plays that should be entered immediately."""
        all_opps = self.find_all_opportunities()
        return [opp for opp in all_opps if opp['entry_time'] == 'IMMEDIATE']
    
    def get_market_open_plays(self) -> List[Dict]:
        """Get plays for market open (9:35 AM)."""
        all_opps = self.find_all_opportunities()
        return [opp for opp in all_opps if opp['entry_time'] == 'MARKET_OPEN']


def run_unified_scan():
    """Run all scanners and return ranked opportunities."""
    finder = OpportunityFinder()
    
    print("🎯 UNIFIED OPPORTUNITY SCAN")
    print("=" * 80)
    print()
    
    opportunities = finder.get_top_opportunities(limit=10)
    
    if not opportunities:
        print("No high-quality opportunities found")
        return []
    
    print(f"Found {len(opportunities)} opportunities (showing top 10):\n")
    
    for i, opp in enumerate(opportunities, 1):
        symbol = opp['symbol']
        opp_type = opp['opportunity_type']
        score = opp['score']
        
        print(f"{i}. {symbol} - {opp_type} (Score: {score:.0f}/100)")
        
        if opp_type == 'GAP':
            print(f"   Gap: {opp['gap_pct']:+.1f}% | Volume: {opp['volume_ratio']:.1f}x")
            print(f"   Price: ${opp['current_price']:.2f} | News: {opp['news_score']:.0f}/100")
            print(f"   Entry: 9:35 AM | Hold: Intraday")
        
        elif opp_type == 'CATALYST':
            print(f"   Catalyst: {opp['catalyst_type']} ({opp['catalyst_age_hours']:.1f}h ago)")
            print(f"   Volume: {opp['volume_ratio']:.1f}x | Change: {opp['change_pct']:+.1f}%")
            print(f"   Price: ${opp['price']:.2f} | VWAP: {'Above' if opp['above_vwap'] else 'Below'}")
            print(f"   Entry: Immediate | Hold: Hours")
        
        print()
    
    # Execution recommendations
    print("\n📋 EXECUTION PLAN:")
    print("-" * 80)
    
    immediate = finder.get_immediate_plays()
    if immediate:
        print(f"\n⚡ IMMEDIATE (Execute Now):")
        for opp in immediate[:3]:
            print(f"  • {opp['symbol']} - {opp['opportunity_type']} (Score: {opp['score']:.0f})")
    
    market_open = finder.get_market_open_plays()
    if market_open:
        print(f"\n🔔 MARKET OPEN (9:35 AM):")
        for opp in market_open[:3]:
            print(f"  • {opp['symbol']} - {opp['opportunity_type']} (Score: {opp['score']:.0f})")
    
    print()
    
    return opportunities


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    
    opps = run_unified_scan()
    
    if opps:
        print(f"\n✅ Best opportunity: {opps[0]['symbol']} ({opps[0]['opportunity_type']})")
        print(f"   Score: {opps[0]['score']:.0f}/100")
        print(f"   Action: {opps[0]['entry_time']}")
