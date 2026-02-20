"""
Conviction Manager V2 - Maximum Upside Capture

SEND IT MODE: No arbitrary profit targets. Only exit on thesis invalidation.

Philosophy:
- If thesis is right, it can 100x
- If thesis is wrong, cut at max pain
- NO MIDDLE GROUND - binary outcomes only
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path


class ConvictionV2:
    """
    Single high-conviction position with asymmetric upside.
    
    NO PROFIT TARGET. Only thesis invalidation triggers exit.
    """
    
    def __init__(
        self,
        symbol: str,
        thesis: str,
        catalyst: str,
        entry_price: float,
        max_pain_price: float,
        catalyst_deadline: str,
        structure_support: float,
        max_position_pct: float = 1.0  # 100% default
    ):
        self.symbol = symbol
        self.thesis = thesis
        self.catalyst = catalyst
        self.entry_price = entry_price
        self.max_pain_price = max_pain_price
        self.catalyst_deadline = datetime.fromisoformat(catalyst_deadline)
        self.structure_support = structure_support
        self.max_position_pct = max_position_pct
        
        self.set_date = datetime.now()
        self.active = True
        self.exit_reason = None
        self.exit_price = None
        
        # Catalyst tracking
        self.catalyst_events = []
        self.thesis_strength = 100  # Out of 100
    
    def check_exit_triggers(
        self,
        current_price: float,
        news_events: List[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if we should exit.
        
        Returns: (should_exit, reason)
        
        ONLY exit if:
        1. Price < max_pain (thesis dead)
        2. Price < structure_support (momentum dead)
        3. Deadline passed with no catalyst
        4. Thesis explicitly invalidated (news)
        """
        # Hard floor: max pain
        if current_price < self.max_pain_price:
            return True, f"MAX PAIN BREACHED: ${current_price:.2f} < ${self.max_pain_price:.2f} - thesis dead"
        
        # Structure support: momentum broken
        if current_price < self.structure_support:
            return True, f"STRUCTURE BROKEN: ${current_price:.2f} < support ${self.structure_support:.2f}"
        
        # Time decay: deadline passed
        if datetime.now() > self.catalyst_deadline:
            # Check if catalyst actually happened
            if not self._catalyst_confirmed():
                return True, f"DEADLINE EXPIRED: {self.catalyst_deadline.date()} passed, no catalyst"
        
        # News-based invalidation
        if news_events:
            for event in news_events:
                if self._is_thesis_killer(event):
                    return True, f"THESIS INVALIDATED: {event}"
        
        # Otherwise: HOLD
        return False, None
    
    def _catalyst_confirmed(self) -> bool:
        """Check if catalyst event occurred."""
        # TODO: Integrate with news API
        # For now, manual tracking via catalyst_events
        confirmation_keywords = ['acquisition', 'buyout', 'merger', 'announced']
        
        for event in self.catalyst_events:
            if any(kw in event.lower() for kw in confirmation_keywords):
                return True
        
        return False
    
    def _is_thesis_killer(self, event: str) -> bool:
        """Check if news event kills thesis."""
        killer_patterns = [
            'ryan cohen resigns',
            'ryan cohen exits',
            'acquisition rejected',
            'merger terminated',
            'sec investigation',
            'bankruptcy'
        ]
        
        event_lower = event.lower()
        return any(pattern in event_lower for pattern in killer_patterns)
    
    def record_catalyst_event(self, event: str, impact: int):
        """
        Record catalyst-related news.
        
        impact: -100 to +100 (how much this helps/hurts thesis)
        """
        self.catalyst_events.append({
            'timestamp': datetime.now().isoformat(),
            'event': event,
            'impact': impact
        })
        
        # Adjust thesis strength
        self.thesis_strength = max(0, min(100, self.thesis_strength + impact))
    
    def get_action(
        self,
        current_price: float,
        current_position_size: float,
        available_cash: float
    ) -> Dict:
        """
        Determine action: HOLD, ADD, or EXIT.
        
        Strategy:
        - Price dips 10%+ from entry → ADD (DCA)
        - Exit triggers hit → EXIT
        - Otherwise → HOLD
        """
        should_exit, reason = self.check_exit_triggers(current_price)
        
        if should_exit:
            return {
                'action': 'EXIT',
                'reason': reason,
                'size': current_position_size,
                'urgency': 'IMMEDIATE'
            }
        
        # Check if we should add (dip buying)
        dip_pct = (current_price / self.entry_price) - 1.0
        
        if dip_pct < -0.10 and current_position_size < self.max_position_pct:
            # Buy the dip
            add_size = min(
                available_cash * 0.25,  # 25% of available cash
                self.max_position_pct - current_position_size
            )
            
            return {
                'action': 'ADD',
                'reason': f"BUY DIP: {dip_pct:.1%} below entry",
                'size': add_size,
                'urgency': 'NORMAL'
            }
        
        # Otherwise hold
        unrealized_pnl = (current_price / self.entry_price) - 1.0
        
        return {
            'action': 'HOLD',
            'reason': f"Thesis intact, P/L {unrealized_pnl:+.1%}",
            'size': current_position_size,
            'urgency': 'NONE'
        }
    
    def get_status(self, current_price: float) -> Dict:
        """Get conviction position status."""
        unrealized_pnl = (current_price / self.entry_price) - 1.0
        distance_to_pain = (current_price / self.max_pain_price) - 1.0
        days_until_deadline = (self.catalyst_deadline - datetime.now()).days
        
        return {
            'symbol': self.symbol,
            'thesis': self.thesis,
            'entry': self.entry_price,
            'current': current_price,
            'max_pain': self.max_pain_price,
            'unrealized_pnl': unrealized_pnl,
            'distance_to_pain': distance_to_pain,
            'days_remaining': days_until_deadline,
            'thesis_strength': self.thesis_strength,
            'active': self.active,
            'catalyst_events': len(self.catalyst_events)
        }


class ConvictionManagerV2:
    """
    Manages multiple conviction positions.
    
    SEND IT MODE:
    - 1-3 conviction positions max (not 10)
    - 100% of capital in convictions
    - No diversification hedging
    - Exit only on thesis break
    """
    
    def __init__(self, state_file: str = "state/convictions_v2.json"):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.convictions: Dict[str, ConvictionV2] = {}
        self.max_concurrent = 3  # Max 3 positions
        
        self._load_state()
    
    def add_conviction(
        self,
        symbol: str,
        thesis: str,
        catalyst: str,
        entry_price: float,
        max_pain_price: float,
        catalyst_deadline: str,
        structure_support: float,
        max_position_pct: float = 1.0
    ):
        """Add new conviction position."""
        if len(self.convictions) >= self.max_concurrent:
            raise ValueError(f"Max {self.max_concurrent} convictions allowed")
        
        conviction = ConvictionV2(
            symbol=symbol,
            thesis=thesis,
            catalyst=catalyst,
            entry_price=entry_price,
            max_pain_price=max_pain_price,
            catalyst_deadline=catalyst_deadline,
            structure_support=structure_support,
            max_position_pct=max_position_pct
        )
        
        self.convictions[symbol] = conviction
        self._save_state()
        
        print(f"✅ CONVICTION SET: {symbol}")
        print(f"   Thesis: {thesis}")
        print(f"   Entry: ${entry_price:.2f}")
        print(f"   Max Pain: ${max_pain_price:.2f}")
        print(f"   Deadline: {catalyst_deadline}")
        print(f"   Max Position: {max_position_pct:.0%}")
    
    def update_all(
        self,
        current_prices: Dict[str, float],
        current_positions: Dict[str, float],
        available_cash: float
    ) -> List[Dict]:
        """
        Update all convictions and get actions.
        
        Returns list of actions to execute.
        """
        actions = []
        
        for symbol, conviction in self.convictions.items():
            if not conviction.active:
                continue
            
            current_price = current_prices.get(symbol, 0)
            if current_price == 0:
                continue
            
            position_size = current_positions.get(symbol, 0)
            
            action = conviction.get_action(
                current_price,
                position_size,
                available_cash
            )
            
            if action['action'] != 'HOLD':
                actions.append({
                    'symbol': symbol,
                    **action
                })
            
            # If exiting, mark inactive
            if action['action'] == 'EXIT':
                conviction.active = False
                conviction.exit_reason = action['reason']
                conviction.exit_price = current_price
        
        self._save_state()
        return actions
    
    def _load_state(self):
        """Load convictions from disk."""
        if not self.state_file.exists():
            return
        
        with open(self.state_file, 'r') as f:
            data = json.load(f)
        
        # TODO: Deserialize ConvictionV2 objects
        # For now, skip
    
    def _save_state(self):
        """Save convictions to disk."""
        data = {
            symbol: {
                'thesis': c.thesis,
                'entry_price': c.entry_price,
                'max_pain': c.max_pain_price,
                'active': c.active,
                'exit_reason': c.exit_reason
            }
            for symbol, c in self.convictions.items()
        }
        
        with open(self.state_file, 'w') as f:
            json.dump(data, f, indent=2)


if __name__ == '__main__':
    # Test
    cm = ConvictionManagerV2()
    
    # Set GME conviction with NO profit target
    cm.add_conviction(
        symbol='GME',
        thesis="Acquisition by AAPL or MSFT for gaming/metaverse play",
        catalyst="Ryan Cohen positioning + blockchain infrastructure",
        entry_price=24.89,
        max_pain_price=10.0,  # Below this = thesis dead
        catalyst_deadline='2026-10-31T23:59:59',
        structure_support=15.0,  # If breaks below $15, momentum dead
        max_position_pct=1.0  # 100% of capital
    )
    
    # Test scenarios
    print("\n--- Scenario 1: Price at $50 ---")
    actions = cm.update_all(
        current_prices={'GME': 50.0},
        current_positions={'GME': 0.7},
        available_cash=100
    )
    print(f"Actions: {actions}")  # Should be HOLD
    
    print("\n--- Scenario 2: Price at $500 ---")
    actions = cm.update_all(
        current_prices={'GME': 500.0},
        current_positions={'GME': 0.7},
        available_cash=100
    )
    print(f"Actions: {actions}")  # Should STILL be HOLD (no target!)
    
    print("\n--- Scenario 3: Price at $9 (max pain) ---")
    actions = cm.update_all(
        current_prices={'GME': 9.0},
        current_positions={'GME': 0.7},
        available_cash=100
    )
    print(f"Actions: {actions}")  # Should be EXIT
