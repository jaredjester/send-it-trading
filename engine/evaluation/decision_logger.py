"""
Decision logging scratchpad for orchestrator.

Append-only JSONL log of every decision the bot makes.
"""
import json
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class DecisionLogger:
    """
    Logs every orchestrator cycle decision to disk.
    
    Enables post-mortem analysis: "WTF did the bot do at 9:47 AM?"
    """
    
    def __init__(self, log_dir: str = "logs/decisions"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # One file per day
        today = datetime.now().strftime('%Y-%m-%d')
        self.current_log = self.log_dir / f"decisions_{today}.jsonl"
    
    def log_cycle(
        self,
        cycle_number: int,
        portfolio_state: Dict,
        signals_scored: List[Dict],
        rl_recommendation: Dict,
        conviction_positions: List[Dict],
        decisions: List[Dict],
        execution_results: List[Dict],
        regime: str,
        risk_checks: Dict,
        errors: Optional[List[str]] = None
    ):
        """
        Log complete orchestrator cycle.
        
        Example entry:
        {
          "timestamp": "2026-02-19T09:30:15Z",
          "cycle": 47,
          "portfolio": {
            "value": 372.15,
            "cash": 57.28,
            "positions": 14,
            "concentration": 0.69
          },
          "signals": [
            {
              "symbol": "SPY",
              "alpha_score": 45,
              "alt_data_boost": 8,
              "final_score": 53,
              "confidence": 0.42
            }
          ],
          "rl": {
            "action": "hold",
            "confidence": 0.15,
            "episodes": 3
          },
          "convictions": [
            {
              "symbol": "GME",
              "score": 76,
              "phase": "HOLDING",
              "target": 45,
              "current": 23.6,
              "pnl_pct": -5.2
            }
          ],
          "decisions": [
            {
              "action": "HOLD",
              "symbol": "GME",
              "reason": "conviction protection - thesis intact"
            },
            {
              "action": "SKIP",
              "symbol": "SPY",
              "reason": "confidence 0.42 < threshold 0.55"
            }
          ],
          "execution": [],
          "regime": "low_volatility",
          "risk": {
            "daily_loss_ok": true,
            "drawdown_ok": true,
            "concentration_warning": true
          },
          "errors": []
        }
        """
        entry = {
            'timestamp': datetime.now().isoformat(),
            'cycle': cycle_number,
            'portfolio': portfolio_state,
            'signals': signals_scored,
            'rl': rl_recommendation,
            'convictions': conviction_positions,
            'decisions': decisions,
            'execution': execution_results,
            'regime': regime,
            'risk': risk_checks,
            'errors': errors or []
        }
        
        # Append to today's log
        with open(self.current_log, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def get_recent_decisions(self, hours: int = 24) -> List[Dict]:
        """Load recent decision history."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        decisions = []
        
        # Check today's file
        if self.current_log.exists():
            with open(self.current_log, 'r') as f:
                for line in f:
                    entry = json.loads(line)
                    entry_time = datetime.fromisoformat(entry['timestamp'])
                    
                    if entry_time >= cutoff:
                        decisions.append(entry)
        
        # Check yesterday's file if needed
        if cutoff.date() < datetime.now().date():
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            yesterday_log = self.log_dir / f"decisions_{yesterday}.jsonl"
            
            if yesterday_log.exists():
                with open(yesterday_log, 'r') as f:
                    for line in f:
                        entry = json.loads(line)
                        entry_time = datetime.fromisoformat(entry['timestamp'])
                        
                        if entry_time >= cutoff:
                            decisions.append(entry)
        
        return sorted(decisions, key=lambda x: x['timestamp'])
    
    def analyze_decision_pattern(self, symbol: str, days: int = 7) -> Dict:
        """
        Analyze bot's decision history for a specific symbol.
        
        Returns patterns like:
        - How many times did we consider it?
        - How many times did we trade it?
        - What was the average alpha score?
        - Did RL override signal?
        """
        decisions = self.get_recent_decisions(hours=days * 24)
        
        symbol_decisions = []
        for entry in decisions:
            for sig in entry['signals']:
                if sig.get('symbol') == symbol:
                    symbol_decisions.append({
                        'timestamp': entry['timestamp'],
                        'alpha_score': sig.get('alpha_score', 0),
                        'final_score': sig.get('final_score', 0),
                        'confidence': sig.get('confidence', 0),
                        'action': next((d['action'] for d in entry['decisions'] if d.get('symbol') == symbol), 'SKIP')
                    })
        
        if not symbol_decisions:
            return {
                'symbol': symbol,
                'times_considered': 0,
                'avg_alpha_score': 0,
                'times_traded': 0
            }
        
        return {
            'symbol': symbol,
            'times_considered': len(symbol_decisions),
            'avg_alpha_score': np.mean([d['alpha_score'] for d in symbol_decisions]),
            'avg_confidence': np.mean([d['confidence'] for d in symbol_decisions]),
            'times_traded': sum(1 for d in symbol_decisions if d['action'] in ['BUY', 'SELL']),
            'times_skipped': sum(1 for d in symbol_decisions if d['action'] == 'SKIP'),
            'recent_actions': [d['action'] for d in symbol_decisions[-10:]]
        }
    
    def find_errors(self, hours: int = 24) -> List[Dict]:
        """Find all cycles that had errors."""
        decisions = self.get_recent_decisions(hours=hours)
        
        return [
            {
                'timestamp': d['timestamp'],
                'cycle': d['cycle'],
                'errors': d['errors']
            }
            for d in decisions
            if d['errors']
        ]
    
    def export_for_analysis(self, days: int = 30, output_path: Optional[str] = None):
        """Export decision log as analyzable JSON."""
        decisions = self.get_recent_decisions(hours=days * 24)
        
        if output_path is None:
            output_path = f"logs/decisions_export_{datetime.now().strftime('%Y%m%d')}.json"
        
        with open(output_path, 'w') as f:
            json.dump(decisions, f, indent=2)
        
        print(f"Exported {len(decisions)} decisions to {output_path}")
        return output_path


if __name__ == '__main__':
    # Test logger
    import numpy as np
    from datetime import timedelta
    
    logger = DecisionLogger()
    
    # Log a mock cycle
    logger.log_cycle(
        cycle_number=47,
        portfolio_state={
            'value': 372.15,
            'cash': 57.28,
            'positions': 14,
            'concentration': 0.69
        },
        signals_scored=[
            {
                'symbol': 'SPY',
                'alpha_score': 45,
                'alt_data_boost': 8,
                'final_score': 53,
                'confidence': 0.42
            },
            {
                'symbol': 'GME',
                'conviction_score': 76,
                'phase': 'HOLDING'
            }
        ],
        rl_recommendation={
            'action': 'hold',
            'confidence': 0.15,
            'episodes': 3
        },
        conviction_positions=[
            {
                'symbol': 'GME',
                'score': 76,
                'phase': 'HOLDING',
                'target': 45,
                'current': 23.6,
                'pnl_pct': -5.2
            }
        ],
        decisions=[
            {
                'action': 'HOLD',
                'symbol': 'GME',
                'reason': 'conviction protection'
            },
            {
                'action': 'SKIP',
                'symbol': 'SPY',
                'reason': 'confidence < threshold'
            }
        ],
        execution_results=[],
        regime='low_volatility',
        risk_checks={
            'daily_loss_ok': True,
            'drawdown_ok': True,
            'concentration_warning': True
        }
    )
    
    print(f"Logged to {logger.current_log}")
    
    # Analyze
    recent = logger.get_recent_decisions(hours=1)
    print(f"Found {len(recent)} recent decisions")
