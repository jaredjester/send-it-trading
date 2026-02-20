#!/usr/bin/env python3
"""
Execution Gate - RL-Gated Trade Execution
Production-ready execution control for Pi Hedge Fund

Filters alpha signals through confidence thresholds, RL recommendations,
and circuit breakers before allowing trade execution.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np


class ExecutionGate:
    """
    Execution gating system that:
    - Filters signals by confidence threshold
    - Integrates Q-learner recommendations
    - Implements circuit breakers
    - Manages position sizing based on confidence
    """
    
    def __init__(self, config_path: str = "master_config.json"):
        """Initialize execution gate with configuration."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        self.gate_cfg = self.config['execution_gate']
        self.risk_cfg = self.config['risk']
        self.portfolio_cfg = self.config['portfolio']
        
        # Initialize logging first
        logging.basicConfig(
            level=self.config['logging']['level'],
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Circuit breaker state
        self.circuit_breaker_state = {
            "daily_drawdown_triggered": False,
            "consecutive_losses": 0,
            "vix_spike_triggered": False,
            "last_reset_date": datetime.utcnow().date().isoformat(),
            "trades_today": []
        }
        
        # Load RL state if available
        self.rl_state = self._load_rl_state()
    
    def _load_rl_state(self) -> Optional[Dict]:
        """Load Q-learner state from file."""
        rl_path = self.gate_cfg['rl_state_path']
        
        if not os.path.exists(rl_path):
            self.logger.warning(f"RL state file not found: {rl_path}")
            return None
        
        try:
            with open(rl_path, 'r') as f:
                rl_state = json.load(f)
                self.logger.info(f"Loaded RL state: {rl_state.get('current_mode', 'unknown')}")
                return rl_state
        except Exception as e:
            self.logger.error(f"Failed to load RL state: {e}")
            return None
    
    def _reset_daily_state(self):
        """Reset daily circuit breaker state at start of new trading day."""
        today = datetime.utcnow().date().isoformat()
        
        if self.circuit_breaker_state['last_reset_date'] != today:
            self.logger.info("Resetting daily circuit breaker state")
            self.circuit_breaker_state = {
                "daily_drawdown_triggered": False,
                "consecutive_losses": 0,
                "vix_spike_triggered": False,
                "last_reset_date": today,
                "trades_today": []
            }
    
    def check_circuit_breakers(self, portfolio_value: float, 
                              starting_value: float,
                              vix_proxy: Optional[float] = None) -> Dict:
        """
        Check all circuit breaker conditions.
        
        Args:
            portfolio_value: Current portfolio value
            starting_value: Portfolio value at start of day
            vix_proxy: Optional VIX proxy value (e.g., SPY implied volatility)
            
        Returns:
            Dict with circuit breaker status and messages
        """
        self._reset_daily_state()
        
        status = {
            "all_clear": True,
            "triggers": [],
            "restrictions": []
        }
        
        # Check daily drawdown
        if starting_value > 0:
            daily_drawdown = (starting_value - portfolio_value) / starting_value
            
            if daily_drawdown > self.risk_cfg['max_daily_drawdown_pct']:
                self.circuit_breaker_state['daily_drawdown_triggered'] = True
                status['all_clear'] = False
                status['triggers'].append({
                    "type": "daily_drawdown",
                    "message": f"Portfolio down {daily_drawdown*100:.1f}% today",
                    "threshold": f"{self.risk_cfg['max_daily_drawdown_pct']*100:.1f}%",
                    "restriction": "No new buys for rest of day"
                })
                status['restrictions'].append("halt_new_buys")
        
        # Check consecutive losses
        if self.circuit_breaker_state['consecutive_losses'] >= self.risk_cfg['max_consecutive_losses']:
            status['all_clear'] = False
            status['triggers'].append({
                "type": "consecutive_losses",
                "message": f"{self.circuit_breaker_state['consecutive_losses']} consecutive losses",
                "threshold": f"{self.risk_cfg['max_consecutive_losses']}",
                "restriction": "No new buys until tomorrow"
            })
            status['restrictions'].append("halt_new_buys")
        
        # Check VIX spike
        if vix_proxy is not None:
            # Would need historical VIX proxy to calculate spike
            # For now, just check absolute level
            if vix_proxy > 30:  # High volatility threshold
                self.circuit_breaker_state['vix_spike_triggered'] = True
                status['all_clear'] = False
                status['triggers'].append({
                    "type": "high_volatility",
                    "message": f"VIX proxy at {vix_proxy:.1f}",
                    "threshold": "30",
                    "restriction": "Reduce all position sizes by 50%"
                })
                status['restrictions'].append("reduce_position_sizes")
        
        return status
    
    def record_trade_result(self, symbol: str, result: str):
        """
        Record trade result to track consecutive losses.
        
        Args:
            symbol: Stock symbol
            result: "win" or "loss"
        """
        self._reset_daily_state()
        
        trade_record = {
            "symbol": symbol,
            "result": result,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        self.circuit_breaker_state['trades_today'].append(trade_record)
        
        if result == "loss":
            self.circuit_breaker_state['consecutive_losses'] += 1
            self.logger.warning(f"Consecutive losses: {self.circuit_breaker_state['consecutive_losses']}")
        else:
            self.circuit_breaker_state['consecutive_losses'] = 0
    
    def get_rl_recommendation(self) -> Dict:
        """
        Get current RL recommendation from Q-learner state.
        
        Returns:
            Dict with RL mode and confidence adjustment
        """
        if self.rl_state is None:
            return {
                "mode": "neutral",
                "confidence_multiplier": 1.0,
                "message": "No RL state available"
            }
        
        mode = self.rl_state.get('current_mode', 'neutral')
        
        # Map RL mode to confidence adjustments
        mode_multipliers = {
            "aggressive_buy": 1.2,
            "moderate_buy": 1.1,
            "neutral": 1.0,
            "defensive": 0.7,
            "risk_off": 0.5
        }
        
        multiplier = mode_multipliers.get(mode, 1.0)
        
        return {
            "mode": mode,
            "confidence_multiplier": multiplier,
            "message": f"RL recommends {mode} mode"
        }
    
    def calculate_position_size(self, 
                               base_size: float,
                               confidence: float,
                               portfolio_value: float,
                               circuit_breaker_status: Dict) -> Dict:
        """
        Calculate position size based on confidence and constraints.
        
        Args:
            base_size: Base position size in dollars
            confidence: Signal confidence (0-1)
            portfolio_value: Total portfolio value
            circuit_breaker_status: Current circuit breaker status
            
        Returns:
            Dict with adjusted position size and explanation
        """
        original_size = base_size
        multiplier = 1.0
        adjustments = []
        
        # Confidence-based sizing
        if confidence < self.gate_cfg['min_confidence']:
            return {
                "position_size": 0,
                "original_size": original_size,
                "multiplier": 0,
                "adjustments": ["Signal rejected - confidence below minimum threshold"],
                "approved": False
            }
        
        elif confidence < self.gate_cfg['medium_confidence']:
            multiplier *= 0.5
            adjustments.append(f"Low confidence ({confidence:.2f}) - reduced size by 50%")
        
        elif confidence >= self.gate_cfg['very_high_confidence']:
            multiplier *= 1.25
            adjustments.append(f"Very high confidence ({confidence:.2f}) - increased size by 25%")
        
        elif confidence >= self.gate_cfg['high_confidence']:
            adjustments.append(f"High confidence ({confidence:.2f}) - full position size")
        
        # RL adjustment
        rl_rec = self.get_rl_recommendation()
        rl_multiplier = rl_rec['confidence_multiplier']
        
        if rl_multiplier != 1.0:
            multiplier *= rl_multiplier
            adjustments.append(f"RL mode '{rl_rec['mode']}' - adjusted by {rl_multiplier:.1f}x")
        
        # Circuit breaker adjustments
        if "reduce_position_sizes" in circuit_breaker_status.get('restrictions', []):
            multiplier *= 0.5
            adjustments.append("High volatility - reduced size by 50%")
        
        if "halt_new_buys" in circuit_breaker_status.get('restrictions', []):
            return {
                "position_size": 0,
                "original_size": original_size,
                "multiplier": 0,
                "adjustments": adjustments + ["Circuit breaker triggered - no new positions"],
                "approved": False
            }
        
        # Calculate final size
        final_size = base_size * multiplier
        
        # Enforce maximum position size
        max_position = portfolio_value * self.portfolio_cfg['max_position_pct']
        if final_size > max_position:
            final_size = max_position
            adjustments.append(f"Capped at {self.portfolio_cfg['max_position_pct']*100:.0f}% of portfolio")
        
        return {
            "position_size": final_size,
            "original_size": original_size,
            "multiplier": multiplier,
            "adjustments": adjustments,
            "approved": final_size > 0
        }
    
    def evaluate_signal(self,
                       alpha_score: Dict,
                       portfolio_value: float,
                       starting_value: float,
                       current_positions: int,
                       vix_proxy: Optional[float] = None) -> Dict:
        """
        Master evaluation function combining all gating logic.
        
        Args:
            alpha_score: Alpha engine output dict with score, confidence, etc.
            portfolio_value: Current portfolio value
            starting_value: Portfolio value at start of day
            current_positions: Number of current positions
            vix_proxy: Optional VIX proxy value
            
        Returns:
            Dict with final execution recommendation:
            {
                "approved": bool,
                "position_size": float,
                "confidence": float,
                "alpha_score": float,
                "rl_mode": str,
                "circuit_breakers": {...},
                "adjustments": [...],
                "final_recommendation": str
            }
        """
        # Reload RL state (may have been updated by Q-learner)
        self.rl_state = self._load_rl_state()
        
        # Check circuit breakers
        circuit_breaker_status = self.check_circuit_breakers(
            portfolio_value, starting_value, vix_proxy
        )
        
        # Get RL recommendation
        rl_rec = self.get_rl_recommendation()
        
        # Adjust confidence based on RL
        adjusted_confidence = alpha_score['confidence']
        
        # RL override logic
        if rl_rec['mode'] == 'risk_off' and alpha_score['suggested_action'] in ['buy', 'strong_buy']:
            return {
                "approved": False,
                "position_size": 0,
                "confidence": adjusted_confidence,
                "alpha_score": alpha_score['score'],
                "rl_mode": rl_rec['mode'],
                "circuit_breakers": circuit_breaker_status,
                "adjustments": ["RL override: risk_off mode blocks all buys"],
                "final_recommendation": "REJECT"
            }
        
        if rl_rec['mode'] == 'defensive' and alpha_score['suggested_action'] == 'buy':
            adjusted_confidence *= 0.7
        
        if rl_rec['mode'] == 'aggressive_buy' and alpha_score['suggested_action'] in ['buy', 'strong_buy']:
            adjusted_confidence = min(adjusted_confidence * 1.2, 1.0)
        
        # Blend alpha and RL weights
        rl_weight = self.gate_cfg['rl_weight']
        alpha_weight = self.gate_cfg['alpha_weight']
        
        # Effective confidence combines both
        effective_confidence = (
            adjusted_confidence * alpha_weight +
            rl_rec['confidence_multiplier'] * rl_weight
        ) / (alpha_weight + rl_weight)
        
        # Check position limits
        if current_positions >= self.portfolio_cfg['max_positions']:
            return {
                "approved": False,
                "position_size": 0,
                "confidence": effective_confidence,
                "alpha_score": alpha_score['score'],
                "rl_mode": rl_rec['mode'],
                "circuit_breakers": circuit_breaker_status,
                "adjustments": [f"Position limit reached ({current_positions}/{self.portfolio_cfg['max_positions']})"],
                "final_recommendation": "REJECT"
            }
        
        # Calculate position size
        base_size = portfolio_value * 0.05  # 5% base position
        sizing = self.calculate_position_size(
            base_size,
            effective_confidence,
            portfolio_value,
            circuit_breaker_status
        )
        
        if not sizing['approved']:
            return {
                "approved": False,
                "position_size": 0,
                "confidence": effective_confidence,
                "alpha_score": alpha_score['score'],
                "rl_mode": rl_rec['mode'],
                "circuit_breakers": circuit_breaker_status,
                "adjustments": sizing['adjustments'],
                "final_recommendation": "REJECT"
            }
        
        # Final recommendation
        if effective_confidence >= self.gate_cfg['high_confidence']:
            recommendation = "STRONG_APPROVE"
        elif effective_confidence >= self.gate_cfg['medium_confidence']:
            recommendation = "APPROVE"
        else:
            recommendation = "WEAK_APPROVE"
        
        return {
            "approved": True,
            "position_size": sizing['position_size'],
            "confidence": effective_confidence,
            "alpha_score": alpha_score['score'],
            "rl_mode": rl_rec['mode'],
            "circuit_breakers": circuit_breaker_status,
            "adjustments": sizing['adjustments'],
            "final_recommendation": recommendation,
            "symbol": alpha_score.get('symbol', 'UNKNOWN'),
            "strategy": alpha_score.get('strategy', 'unknown'),
            "entry_price": alpha_score.get('entry_price', 0),
            "stop_loss": alpha_score.get('stop_loss', 0),
            "take_profit": alpha_score.get('take_profit', 0)
        }
    
    def get_gate_status(self) -> Dict:
        """
        Get current gate status for monitoring.
        
        Returns:
            Dict with current gate state
        """
        self._reset_daily_state()
        
        rl_rec = self.get_rl_recommendation()
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "rl_mode": rl_rec['mode'],
            "rl_confidence_multiplier": rl_rec['confidence_multiplier'],
            "circuit_breakers": {
                "daily_drawdown": self.circuit_breaker_state['daily_drawdown_triggered'],
                "consecutive_losses": self.circuit_breaker_state['consecutive_losses'],
                "vix_spike": self.circuit_breaker_state['vix_spike_triggered']
            },
            "trades_today": len(self.circuit_breaker_state['trades_today']),
            "gates_open": not any([
                self.circuit_breaker_state['daily_drawdown_triggered'],
                self.circuit_breaker_state['consecutive_losses'] >= self.risk_cfg['max_consecutive_losses']
            ])
        }


if __name__ == "__main__":
    # Test the execution gate
    gate = ExecutionGate()
    
    # Sample alpha score
    test_alpha_score = {
        "symbol": "AAPL",
        "score": 75,
        "confidence": 0.82,
        "strategy": "momentum",
        "suggested_action": "buy",
        "entry_price": 150.0,
        "stop_loss": 138.0,
        "take_profit": 165.0,
        "target_hold_days": 10
    }
    
    portfolio_value = 366
    starting_value = 366
    current_positions = 5
    
    print("="*60)
    print("EXECUTION GATE EVALUATION")
    print("="*60)
    
    # Get gate status
    status = gate.get_gate_status()
    print(f"\nGate Status:")
    print(f"  RL Mode: {status['rl_mode']}")
    print(f"  RL Multiplier: {status['rl_confidence_multiplier']:.2f}x")
    print(f"  Gates Open: {status['gates_open']}")
    print(f"  Consecutive Losses: {status['circuit_breakers']['consecutive_losses']}")
    print(f"  Trades Today: {status['trades_today']}")
    
    # Evaluate signal
    print(f"\n{'='*60}")
    print(f"Evaluating Signal: {test_alpha_score['symbol']}")
    print(f"  Alpha Score: {test_alpha_score['score']:.1f}")
    print(f"  Confidence: {test_alpha_score['confidence']:.2f}")
    print(f"  Strategy: {test_alpha_score['strategy']}")
    print(f"{'='*60}")
    
    result = gate.evaluate_signal(
        test_alpha_score,
        portfolio_value,
        starting_value,
        current_positions
    )
    
    print(f"\nDecision: {result['final_recommendation']}")
    print(f"Approved: {result['approved']}")
    print(f"Position Size: ${result['position_size']:.2f}")
    print(f"Effective Confidence: {result['confidence']:.2f}")
    
    print(f"\nAdjustments:")
    for adj in result['adjustments']:
        print(f"  - {adj}")
    
    print(f"\nCircuit Breakers:")
    if result['circuit_breakers']['all_clear']:
        print("  All clear ✓")
    else:
        for trigger in result['circuit_breakers']['triggers']:
            print(f"  ⚠ {trigger['type']}: {trigger['message']}")
    
    # Test with consecutive losses
    print(f"\n{'='*60}")
    print("Testing Circuit Breaker: 3 Consecutive Losses")
    print(f"{'='*60}")
    
    for i in range(3):
        gate.record_trade_result("TEST", "loss")
    
    result2 = gate.evaluate_signal(
        test_alpha_score,
        portfolio_value,
        starting_value,
        current_positions
    )
    
    print(f"\nDecision: {result2['final_recommendation']}")
    print(f"Approved: {result2['approved']}")
    print(f"Reason: {result2['adjustments']}")
