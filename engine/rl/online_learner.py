"""
Online learner — updates signal weights from live trade outcomes.
Called by orchestrator after each buy/sell.
"""
import json, os, logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('online_learner')
BASE_DIR = Path(__file__).parent.parent

class OnlineLearner:
    STATE_FILE = BASE_DIR / 'evaluation' / 'learning_state.json'
    OUTCOMES_LOG = BASE_DIR / 'evaluation' / 'trade_outcomes.jsonl'

    # Bayesian priors: start with slight positive bias (alpha=6, beta=4 = 60% prior win rate)
    DEFAULT_PRIORS = {'rsi': [6, 4], 'momentum': [6, 4], 'volume': [5, 5], 'composite': [6, 4]}

    def __init__(self):
        self.state = self._load()

    def _load(self) -> dict:
        if self.STATE_FILE.exists():
            try:
                return json.loads(self.STATE_FILE.read_text())
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
        return {
            'signal_distributions': dict(self.DEFAULT_PRIORS),
            'pending_trades': {},
            'total_recorded': 0,
            'total_wins': 0,
            'last_updated': None
        }

    def _save(self):
        try:
            tmp = str(self.STATE_FILE) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, str(self.STATE_FILE))
        except Exception as e:
            logger.error(f'Save failed: {e}')

    def record_entry(self, symbol: str, price: float, score: float, signals: dict = None):
        """Record trade entry with signal context."""
        self.state['pending_trades'][symbol] = {
            'entry_price': price,
            'entry_score': score,
            'signals': signals or {},
            'entry_time': datetime.now().isoformat()
        }
        self._save()
        logger.info(f'OnlineLearner: entry recorded {symbol} @ {price:.2f} score={score:.0f}')

    def record_exit(self, symbol: str, exit_price: float, outcome: str = 'sell'):
        """Record trade exit. Update signal weights based on P&L."""
        pending = self.state['pending_trades'].pop(symbol, None)
        if not pending:
            logger.debug(f'No pending entry found for {symbol}')
            return

        entry_price = pending.get('entry_price', 0)
        pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
        won = pnl_pct > 0

        # Update Bayesian beta distributions for each signal
        dists = self.state['signal_distributions']
        signals = pending.get('signals', {})

        for signal_name in ['rsi', 'momentum', 'volume', 'composite']:
            if signal_name not in dists:
                dists[signal_name] = list(self.DEFAULT_PRIORS.get(signal_name, [5, 5]))
            alpha, beta = dists[signal_name]
            # Only update signal if it was active (signal value > threshold)
            sig_val = signals.get(signal_name, 0.5)
            if sig_val > 0.4:  # signal contributed to this trade
                if won:
                    dists[signal_name][0] = alpha + 1  # increment wins (alpha)
                else:
                    dists[signal_name][1] = beta + 1   # increment losses (beta)

        self.state['total_recorded'] = self.state.get('total_recorded', 0) + 1
        if won:
            self.state['total_wins'] = self.state.get('total_wins', 0) + 1
        self.state['last_updated'] = datetime.now().isoformat()
        self._save()

        # Log outcome
        outcome_entry = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl_pct': round(pnl_pct, 4),
            'won': won,
            'outcome': outcome,
            'signals': signals,
            'entry_score': pending.get('entry_score', 0)
        }
        self.OUTCOMES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(self.OUTCOMES_LOG, 'a') as f:
            f.write(json.dumps(outcome_entry) + '\n')

        logger.info(f'OnlineLearner: {symbol} exit P&L={pnl_pct:+.1%} {"WIN" if won else "LOSS"}')

    def get_signal_weights(self) -> dict:
        """Return posterior mean weight for each signal (alpha/(alpha+beta))."""
        weights = {}
        for sig, (alpha, beta) in self.state['signal_distributions'].items():
            weights[sig] = round(alpha / (alpha + beta), 3)
        return weights

    def get_summary(self) -> dict:
        total = self.state.get('total_recorded', 0)
        wins = self.state.get('total_wins', 0)
        return {
            'total_trades': total,
            'win_rate': round(wins / total, 3) if total > 0 else None,
            'signal_weights': self.get_signal_weights(),
            'pending_trades': len(self.state.get('pending_trades', {})),
            'last_updated': self.state.get('last_updated')
        }
