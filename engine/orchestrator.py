#!/usr/bin/env python3
"""
Orchestrator — Strategy V2.2 (Dynamic Config + RL Episodes)

All parameters are loaded from evaluation/live_config.json via dynamic_config.cfg().
The overnight optimizer continuously tunes trading params.
The RL episode bridge learns from realized P&L and adjusts aggression daily.

No hardcoded constants. Everything is dynamic.
"""
import asyncio
import logging
import json
import os
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
REPO_DIR = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from core.alpaca_client import AlpacaClient
from core.dynamic_config import cfg, cfg_set
# conviction_manager removed — pure systematic alpha (no conviction plays)

# Scanner signals loaded natively via _load_scanner_signals() / _scanner_boost()

from alpha_engine import AlphaEngine
from core.monte_carlo import MonteCarloSimulator
try:
    from rl.online_learner import OnlineLearner as _OL
    _online_learner = _OL()
except Exception as _e:
    import logging as _log
    _log.getLogger('orchestrator').warning('OnlineLearner unavailable: %s', _e)
    _online_learner = None
from rl.episode_bridge import EpisodeBridge
from core.options_trader import OptionsTrader
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "logs/trading.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("orchestrator")


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    """Trading orchestrator v2.2 — fully dynamic config + RL episode learning."""

    def __init__(self):
        logger.info("=" * 60)
        logger.info("Initializing Orchestrator v2.2 (Dynamic + RL)")
        logger.info("=" * 60)

        self.alpaca = AlpacaClient(base_url="https://api.alpaca.markets")

        try:
            self.alpha_engine = AlphaEngine()
            logger.info("✓ Alpha Engine loaded")
        except Exception as e:
            logger.error(f"Alpha Engine failed to load: {e}")
            self.alpha_engine = None

        # MonteCarloSimulator requires historical returns — instantiated lazily per trade
        self.monte_carlo = None
        logger.info("✓ Monte Carlo ready (lazy-init per trade)")

        try:
            self.rl_bridge = EpisodeBridge()
            logger.info("✓ RL Episode Bridge loaded")
        except Exception as e:
            logger.error(f"RL Bridge failed to load: {e}")
            self.rl_bridge = None

        try:
            self.options_trader = OptionsTrader(
                api_key=self.alpaca.api_key,
                api_secret=self.alpaca.api_secret,
            )
            logger.info("✓ OptionsTrader loaded (options-first execution)")
        except Exception as e:
            logger.error(f"OptionsTrader failed to load: {e}")
            self.options_trader = None

        self._online_learner = _online_learner

        # Enhanced watchlist with worker specialization
        worker_id = os.getenv("WORKER_ID", "balanced")
        try:
            from core.enhanced_watchlist import EnhancedWatchlistManager
            self.enhanced_watchlist = EnhancedWatchlistManager(worker_id)
            logger.info(f"✓ Enhanced Watchlist Manager loaded (worker: {worker_id})")
        except Exception as e:
            logger.warning(f"Enhanced Watchlist unavailable: {e}")
            self.enhanced_watchlist = None

        # IC state cache
        self._ic_cache = {}
        self._load_ic_state()

        self._episode_started_today: str = ""
        self._cycle_count = 0

        logger.info(
            f"Config: threshold={cfg('min_score_threshold')} "
            f"max_pos={cfg('max_position_pct'):.0%} "
            f"zombie={cfg('zombie_loss_threshold'):.0%} "
            f"rl_action={cfg('rl_action')} "
            f"trade_mult={cfg('rl_trade_multiplier')} "
            f"size_mult={cfg('rl_size_multiplier')}"
        )
        logger.info("=" * 60)


    def _load_ic_state(self):
        """Load IC metrics for signal kill enforcement."""
        try:
            ic_path = BASE_DIR / "evaluation/alpha_metrics.json"
            if ic_path.exists():
                with open(ic_path) as f:
                    data = json.load(f)
                signals = data.get("signals", {})
                for sig_name, sig_data in signals.items():
                    self._ic_cache[sig_name] = sig_data.get("last_30_ic", 0.0)
                logger.info(f"IC state loaded: {self._ic_cache}")
            else:
                logger.info("No IC state yet — all signals active")
        except Exception as e:
            logger.warning(f"IC load failed: {e}")

    def _signal_has_edge(self, signal_name: str) -> bool:
        """Return False if signal IC is below kill threshold."""
        ic = self._ic_cache.get(signal_name, None)
        if ic is None:
            return True
        kill = cfg("ic_kill_threshold")
        if ic < kill:
            logger.debug(f"IC KILL: {signal_name} IC={ic:.3f} < {kill}")
            return False
        return True

    def _ic_size_multiplier(self, signal_name: str) -> float:
        """Return size multiplier based on signal IC quality."""
        ic = self._ic_cache.get(signal_name, 0.08)
        strong = cfg("ic_strong_threshold")
        if ic >= strong:
            return 1.15
        elif ic >= 0.08:
            return 1.0
        else:
            return 0.70

    async def is_market_open(self) -> bool:
        try:
            import requests
            r = requests.get(
                "https://api.alpaca.markets/v2/clock",
                headers=self._auth_headers(),
                timeout=10,
            )
            return r.json().get("is_open", False)
        except Exception as e:
            logger.error(f"Market check failed: {e}")
            return False

    def _auth_headers(self):
        return {
            "APCA-API-KEY-ID": self.alpaca.api_key,
            "APCA-API-SECRET-KEY": self.alpaca.api_secret,
        }

    async def get_portfolio_state(self):
        try:
            account = self.alpaca.get_account()
            positions = self.alpaca.get_positions()
            return {
                "portfolio_value": float(account.get("portfolio_value", 0)),
                "cash": float(account.get("cash", 0)),
                "positions": positions,
                "position_count": len(positions),
            }
        except Exception as e:
            logger.error(f"Portfolio fetch failed: {e}")
            return None

    def _submit_order(self, symbol, qty=None, notional=None, side="buy"):
        import requests

        payload = {
            "symbol": symbol,
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        if notional:
            payload["notional"] = round(notional, 2)
        elif qty:
            payload["qty"] = qty

        r = requests.post(
            "https://api.alpaca.markets/v2/orders",
            json=payload,
            headers=self._auth_headers(),
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    async def execute_sell(self, symbol, qty, exit_price=0.0):
        try:
            self._submit_order(symbol=symbol, qty=str(qty), side="sell")
            logger.info(f"✓ SOLD {symbol} x{qty}")
            if self._online_learner:
                try:
                    self._online_learner.record_exit(symbol, exit_price=exit_price, outcome='sell')
                except Exception as e:
                    logger.debug("online_learner.record_exit failed: %s", e)
            return True
        except Exception as e:
            logger.error(f"Sell failed {symbol}: {e}")
            return False

    async def execute_buy(self, symbol, notional, signal_type='', score=0.0):
        if not symbol or not isinstance(symbol, str) or not symbol.isalpha():
            raise ValueError(f"Invalid symbol: {symbol}")
        if not isinstance(notional, (int, float)) or notional <= 0:
            raise ValueError(f"Invalid notional: {notional}")
        if not isinstance(score, (int, float)) or not (0 <= score <= 100):
            raise ValueError(f"Invalid score: {score}")
        # OPTIONS FIRST — try options before stock
        if self.options_trader:
            try:
                result = self.options_trader.execute_options_buy(
                    symbol=symbol, signal_type=signal_type,
                    budget=notional, score=score,
                )
                if result["traded"]:
                    logger.info(
                        "OPTIONS BUY: %s | %s | $%.2f (signal=%s score=%.1f)" % (
                            result["contract"], result["direction"].upper(),
                            result["notional"], signal_type, score
                        )
                    )
                    return True
                else:
                    logger.info("  Options skip %s: %s -> stock fallback" % (symbol, result["reason"]))
            except Exception as _oe:
                logger.warning("  Options error %s: %s -> stock fallback" % (symbol, _oe))
        # STOCK FALLBACK
        try:
            self._submit_order(symbol=symbol, notional=notional, side="buy")
            logger.info("✓ BOUGHT %s $%.2f (signal=%s)" % (symbol, notional, signal_type))
            if self._online_learner:
                try:
                    self._online_learner.record_entry(symbol, notional, score=score, signals={})
                except Exception as e:
                    logger.debug("online_learner.record_entry failed: %s", e)
            return True
        except Exception as e:
            logger.error(f"Buy failed {symbol}: {e}")
            return False

    async def clean_zombies(self, portfolio):
        """Exit zombie positions. All thresholds from live_config.json."""
        zombie_thresh = cfg("zombie_loss_threshold")
        min_val = cfg("min_position_value")
        untradeable = set(cfg("untradeable_symbols") or [])

        zombies = []
        for pos in portfolio["positions"]:
            symbol = pos.get("symbol", "")
            loss_pct = float(pos.get("unrealized_plpc", 0))
            value = float(pos.get("market_value", 0))
            qty = float(pos.get("qty", 0))

            is_zombie = loss_pct < zombie_thresh or value < min_val
            is_conviction = False  # conviction plays removed
            is_untradeable = symbol in untradeable

            if is_zombie and not is_conviction and not is_untradeable:
                zombies.append({"symbol": symbol, "qty": qty, "loss": loss_pct, "value": value})
            elif is_untradeable and is_zombie:
                logger.info(f"  Skipping untradeable zombie {symbol}")

        if zombies:
            logger.info(f"Cleaning {len(zombies)} zombies (threshold {zombie_thresh:.0%}):")
            for z in zombies:
                logger.info(f"  {z['symbol']}: {z['loss']:.1%} / ${z['value']:.2f}")
                await self.execute_sell(z["symbol"], z["qty"])
                # Notify RL bridge — zombie exit = realized loss
                if self.rl_bridge:
                    self.rl_bridge.on_trade_closed(z["symbol"], z["loss"], portfolio)
        else:
            logger.info("No zombies")

        return len(zombies)

    async def scan_opportunities(self):
        opportunities = []
        try:
            sys.path.insert(0, str(BASE_DIR / "scanners"))
            try:
                from morning_gap_scanner import run_morning_scan
                gaps = run_morning_scan()
                if gaps:
                    logger.info(f"Gap scanner: {len(gaps)} opportunities")
                    opportunities.extend(gaps)
            except Exception as e:
                logger.debug(f"Gap scanner: {e}")
            try:
                from catalyst_scanner import run_catalyst_scan
                catalysts = run_catalyst_scan()
                if catalysts:
                    logger.info(f"Catalyst scanner: {len(catalysts)} opportunities")
                    opportunities.extend(catalysts)
            except Exception as e:
                logger.debug(f"Catalyst scanner: {e}")
            # Finviz dynamic screener — finds niche/momentum/oversold candidates
            try:
                from finviz_scanner import run_finviz_scan
                finviz_hits = run_finviz_scan()
                if finviz_hits:
                    logger.info(f"Finviz scanner: {len(finviz_hits)} candidates")
                    opportunities.extend(finviz_hits)
            except Exception as e:
                logger.debug(f"Finviz scanner: {e}")
        except Exception as e:
            logger.warning(f"Scanner import failed: {e}")

        # Fallback: if ALL scanners found nothing, use enhanced watchlist
        if not opportunities:
            if self.enhanced_watchlist:
                try:
                    # Get current positions for context-aware filtering
                    current_positions = []
                    try:
                        portfolio = await self.get_portfolio_state()
                        current_positions = portfolio.get("positions", [])
                    except Exception:
                        pass

                    specialized_symbols = self.enhanced_watchlist.get_specialized_universe(current_positions)
                    logger.warning(f"⚠️  All scanners returned 0 hits — enhanced watchlist fallback ({len(specialized_symbols)} symbols)")

                    for sym in specialized_symbols:
                        opportunities.append({
                            "symbol": sym,
                            "score": 60,
                            "type": "enhanced_watchlist_fallback",
                        })
                except Exception as e:
                    logger.warning(f"Enhanced watchlist fallback failed: {e}")
                    # Final fallback to basic watchlist
                    watchlist = cfg("watchlist")
                    logger.warning(f"⚠️  Using basic watchlist fallback ({len(watchlist)} symbols)")
                    for sym in watchlist:
                        opportunities.append({
                            "symbol": sym,
                            "score": 60,
                            "type": "basic_watchlist_fallback",
                        })
            else:
                watchlist = cfg("watchlist")
                logger.warning(f"⚠️  All scanners returned 0 hits — basic watchlist fallback ({len(watchlist)} symbols)")
                for sym in watchlist:
                    opportunities.append({
                        "symbol": sym,
                        "score": 60,
                        "type": "basic_watchlist_fallback",
                    })

        return opportunities

    def _load_scanner_signals(self) -> dict:
        """Load scanner signals from engine/scanner_signals.json (written by cron scanners)."""
        try:
            path = BASE_DIR / "scanner_signals.json"
            if not path.exists():
                return {}
            data = json.loads(path.read_text())
            # Build {symbol: play_data} dict preserving all_plays order (rank matters for boost)
            return {p["symbol"]: p for p in data.get("all_plays", []) if p.get("symbol")}
        except Exception as exc:
            logger.debug("scanner_signals load failed: %s", exc)
            return {}

    def _scanner_boost(self, symbol: str) -> int:
        """Score boost based on pre-ranked scanner signal position."""
        signals = getattr(self, "_scanner_signals", {})
        if symbol not in signals:
            return 0
        rank = list(signals.keys()).index(symbol)
        if rank < 3:
            return 15
        elif rank < 10:
            return 10
        return 5

    def _vwap_boost(self, symbol: str) -> int:
        """Score boost based on VWAP signal strength."""
        try:
            from indicators.vwap_calculator import VWAPCalculator
            calculator = VWAPCalculator()
            vwap_data = calculator.calculate_vwap(symbol)

            if vwap_data:
                # Use the built-in signal strength
                signal_strength = vwap_data.signal_strength

                # Convert to boost points (0-12 points based on signal strength)
                boost = int(signal_strength * 12)

                # Additional boost for high volume
                if vwap_data.volume_ratio and vwap_data.volume_ratio > 1.5:
                    boost += 3

                # Extra boost for strong signals
                if abs(vwap_data.vwap_deviation) > 0.02:  # 2% deviation
                    boost += 2

                return min(boost, 15)  # Cap at 15 points
        except Exception as e:
            logger.debug(f"VWAP boost calculation failed for {symbol}: {e}")

        return 0

    async def score_opportunity(self, opp):
        """Score with IC-killed signals filtered out.

        FIXED: alpha_engine uses score_opportunity() not score_symbol().
        score_opportunity() returns a dict — extract numeric 'score' field.
        Falls back to opp['score'] (set by scanners) if alpha engine fails.
        """
        symbol = opp.get("symbol")

        if self.alpha_engine:
            try:
                # ── Bot intel enrichment (news/insider/GEX from send-it-bot) ──
                _sentiment_score = None
                _intel_boost = 0
                try:
                    import json as _ji, os as _osi
                    _dd = Path(_osi.getenv('DATA_DIR', str(REPO_DIR / 'data')))
                    _nf = _dd / 'news_intel.json'
                    if _nf.exists():
                        _sn = _ji.loads(_nf.read_text()).get('symbols', {}).get(symbol, {}).get('news', {})
                        if _sn:
                            _rs = float(_sn.get('score', 0.5))
                            _sentiment_score = _rs
                            if _rs > 0.65:   _intel_boost += 12
                            elif _rs < 0.35: _intel_boost -= 8
                    _if = _dd / 'insider_intel.json'
                    if _if.exists():
                        _is = float(_ji.loads(_if.read_text()).get('data', {}).get(symbol, {}).get('score', 0))
                        if _is > 0.5:    _intel_boost += 10
                        elif _is < -0.3: _intel_boost -= 6
                    _gf = _dd / 'gex_cache.json'
                    if _gf.exists():
                        _gd = _ji.loads(_gf.read_text()).get(symbol, {})
                        # GEX squeeze
                        _sq = float(_gd.get('squeeze_score', 0))
                        if _gd.get('squeeze_active') or _sq > 65:  _intel_boost += 15
                        elif _sq > 40:                              _intel_boost += 8
                        elif _gd.get('regime') == 'positive_gamma': _intel_boost += 5
                        # VEX: positive vanna = dealers buy on IV drops = bullish
                        _vex = float(_gd.get('vex_bn', 0))
                        if _vex > 1.0:   _intel_boost += 8
                        elif _vex < -1.0: _intel_boost -= 5
                        # Dealer pressure composite (GEX+VEX+CEX combined)
                        _dp = float(_gd.get('dealer_pressure', 0))
                        if _dp > 20:    _intel_boost += 6
                        elif _dp < -10: _intel_boost -= 4
                    _pf = _dd / 'polymarket_intel.json'
                    if _pf.exists():
                        for _m in _ji.loads(_pf.read_text()).get('by_symbol', {}).get(symbol, [])[:2]:
                            if _m.get('bullish_signal'): _intel_boost += 5
                except Exception as e:
                    logger.debug("Bot intel enrichment failed for %s: %s", symbol, e)
                # ── alpha_engine.score_opportunity returns a dict, NOT a float ─
                result = self.alpha_engine.score_opportunity(symbol, sentiment_score=_sentiment_score)
                if isinstance(result, dict):
                    raw_score = float(result.get("score", opp.get("score", 50)))
                    raw_score = min(raw_score + _intel_boost, 100)  # bot intel boost
                else:
                    raw_score = float(result)

                # Apply IC kill — if primary signals have no edge, penalize score
                active_signals = [s for s in ["rsi", "macd", "volume", "sentiment", "adx"]
                                  if self._signal_has_edge(s)]
                if len(active_signals) < 2:
                    logger.warning(f"Only {len(active_signals)} signals have IC edge — capping at 60")
                    raw_score = min(raw_score, 60)

                # Scanner/Finviz boost: if the screener already found a high-confidence
                # signal, give a small nudge on top of alpha engine score
                screener_type = opp.get("type", "")
                # Dynamic boost: starts from prior knowledge, shifts to learned data
                if screener_type == "finviz_preearnings":  # PEAD pre-announcement edge
                    raw_score = min(raw_score + 15, 100)
                elif screener_type == "finviz_postearnings":
                    raw_score = min(raw_score + 12, 100)
                elif screener_type == "finviz_insider":
                    raw_score = min(raw_score + 10, 100)
                elif screener_type in ("finviz_breakout", "finviz_momentum", "gap"):
                    raw_score = min(raw_score + 8, 100)

                # Scanner signal boost (from cron-written scanner_signals.json)
                boost = self._scanner_boost(symbol)
                if boost:
                    raw_score = min(raw_score + boost, 100)
                    logger.info(f"  {symbol} scanner boost +{boost} → {raw_score:.0f}")

                # VWAP signal boost
                vwap_boost = self._vwap_boost(symbol)
                if vwap_boost:
                    raw_score = min(raw_score + vwap_boost, 100)
                    logger.info(f"  {symbol} VWAP boost +{vwap_boost} → {raw_score:.0f}")

                raw_score = max(0.0, raw_score)  # clamp: never return negative scores
                logger.info(f"  {symbol} alpha score={raw_score:.1f} (type={screener_type})")
                return raw_score

            except Exception as e:
                logger.debug(f"Alpha scoring failed for {symbol}: {e}")

        # Alpha engine unavailable — use scanner's pre-set score
        fallback = opp.get("score", 50)
        logger.debug(f"  {symbol} fallback score={fallback}")
        return fallback

    async def calculate_size(self, portfolio, score, signal_name="composite", symbol=None):
        """
        Dynamic position sizing: all params from live_config.json.
        Now enhanced with Kelly criterion when kelly_position_sizing is enabled.
        """
        max_pos = cfg("max_position_pct")
        haircut = cfg("live_sharpe_haircut")
        rl_size = cfg("rl_size_multiplier")
        threshold = cfg("min_score_threshold")

        # Enhanced Kelly-based sizing if enabled and symbol provided
        if cfg("kelly_position_sizing") and symbol:
            try:
                from core.pricing import enhanced_signal_sizing
                from core.contrarian_research import get_contrarian_research_boost, assess_consensus_risk_adjustment
                from ..sector_map import get_sector

                # Estimate volatility (placeholder - would use historical data in production)
                estimated_volatility = 0.35  # Default assumption

                # Get Kelly-enhanced sizing
                kelly_result = enhanced_signal_sizing(
                    signal_score=score,
                    volatility=estimated_volatility,
                    portfolio_value=portfolio["portfolio_value"]
                )

                base_kelly_size = kelly_result["position_size"]

                # Apply contrarian research enhancements if enabled
                if cfg("contrarian.enabled"):
                    symbol_sector = get_sector(symbol)

                    # Get contrarian research boost
                    contrarian_boost = get_contrarian_research_boost(symbol, symbol_sector)

                    # Apply consensus risk adjustment
                    consensus_adjusted = assess_consensus_risk_adjustment(
                        symbol, symbol_sector, base_kelly_size
                    )

                    # Apply contrarian boost
                    contrarian_multiplier = 1.0 + (contrarian_boost * 0.5)  # Max 50% boost
                    contrarian_enhanced_size = consensus_adjusted * contrarian_multiplier

                    # Log significant contrarian adjustments
                    if abs(contrarian_enhanced_size - base_kelly_size) / base_kelly_size > 0.1:
                        logger.info(f"  {symbol} contrarian adjustment: "
                                   f"${base_kelly_size:.0f} → ${contrarian_enhanced_size:.0f} "
                                   f"(boost: +{contrarian_boost:.2%})")

                    base_kelly_size = contrarian_enhanced_size

                # Apply RL and IC multipliers to final result
                ic_mult = self._ic_size_multiplier(signal_name)
                final_size = base_kelly_size * haircut * rl_size * ic_mult

                logger.debug(
                    f"  Kelly Size: base=${kelly_result['position_size']:.0f} "
                    f"× contrarian × haircut={haircut} × rl={rl_size:.1f} × IC={ic_mult:.2f} "
                    f"→ ${final_size:.0f} (Kelly+Contrarian method)"
                )

                return min(final_size, portfolio["portfolio_value"] * max_pos)

            except Exception as e:
                logger.debug(f"Kelly sizing failed, using traditional: {e}")

        # Traditional sizing method (fallback)
        score_range = max(score - threshold, 0)
        base_pct   = cfg("min_position_pct")
        scale      = cfg("position_scale_factor")
        raw_pct    = base_pct + score_range / 100 * scale
        raw_pct    = max(base_pct, min(raw_pct, max_pos))

        # Sharpe haircut + RL size multiplier
        adjusted_pct = raw_pct * haircut * rl_size

        # IC quality multiplier
        ic_mult = self._ic_size_multiplier(signal_name)
        final_pct = adjusted_pct * ic_mult

        # Hard ceiling at max_pos regardless of multipliers
        final_pct = max(cfg("position_floor_pct"), min(final_pct, max_pos))
        notional = portfolio["portfolio_value"] * final_pct

        logger.debug(
            f"  Size: raw={raw_pct:.1%} × haircut={haircut} × rl={rl_size:.1f} "
            f"× IC={ic_mult:.2f} → {final_pct:.1%} (${notional:.2f})"
        )
        return notional

    async def get_kelly_rebalance_recommendations(self, portfolio):
        """Get Kelly-based rebalancing recommendations from enhanced watchlist."""
        if not self.enhanced_watchlist:
            return None

        try:
            current_positions = portfolio.get("positions", [])
            portfolio_value = portfolio.get("portfolio_value", 0)

            if portfolio_value <= 0:
                return None

            recommendations = self.enhanced_watchlist.get_rebalance_recommendations(
                current_positions, portfolio_value
            )

            logger.info(f"Kelly rebalance: {len(recommendations['sell_orders'])} sells, "
                       f"{len(recommendations['buy_orders'])} buys, "
                       f"{len(recommendations['hold_positions'])} holds")

            return recommendations

        except Exception as e:
            logger.warning(f"Kelly rebalance failed: {e}")
            return None

    async def check_risk_limits(self, portfolio, symbol, notional):
        max_pos = cfg("max_position_pct")
        min_cash = cfg("min_cash_reserve")
        max_exposure = cfg("max_total_exposure")

        available_cash = portfolio["cash"] - min_cash
        if notional > available_cash:
            logger.warning(f"  ❌ {symbol}: insufficient cash (${available_cash:.2f} avail, reserve=${min_cash})")
            return False

        for pos in portfolio["positions"]:
            if pos.get("symbol") == symbol:
                existing_pct = float(pos.get("market_value", 0)) / portfolio["portfolio_value"]
                if existing_pct >= max_pos:
                    logger.warning(f"  ❌ {symbol}: already at {existing_pct:.1%} (max={max_pos:.0%})")
                    return False

        total_long = sum(float(p.get("market_value", 0)) for p in portfolio["positions"])
        exposure = (total_long + notional) / portfolio["portfolio_value"]
        if exposure >= max_exposure:
            logger.warning(f"  ❌ Exposure {exposure:.1%} >= limit {max_exposure:.0%}")
            return False

        return True

    async def execute_opportunities(self, portfolio, opportunities):
        if not opportunities:
            logger.info("No opportunities")
            return

        threshold = cfg("min_score_threshold")
        base_max_trades = int(cfg("max_trades_per_cycle"))
        trade_mult = float(cfg("rl_trade_multiplier"))
        max_trades = max(0, round(base_max_trades * trade_mult))
        min_notional = cfg("min_trade_notional")

        if max_trades == 0:
            logger.info(f"RL action={cfg('rl_action')} → trade_mult=0 → no new buys this cycle")
            return

        scored = []
        for opp in opportunities:
            score = await self.score_opportunity(opp)
            if score >= threshold:
                scored.append((score, opp))

        scored.sort(key=lambda x: x[0], reverse=True)
        logger.info(
            f"{len(scored)} above threshold={threshold} | "
            f"max_trades={max_trades} (base={base_max_trades} × rl={trade_mult:.1f}) | "
            f"rl_action={cfg('rl_action')}"
        )

        executed = 0
        for score, opp in scored[:max_trades]:
            symbol = opp.get("symbol")
            logger.info(f"  → {symbol}: score={score:.1f}")

            notional = await self.calculate_size(portfolio, score, symbol=symbol)

            if notional < min_notional:
                logger.info(f"    ❌ Too small: ${notional:.2f} (min=${min_notional})")
                continue

            if not await self.check_risk_limits(portfolio, symbol, notional):
                continue

            sig_type = opp.get("type", "")
            success = await self.execute_buy(symbol, notional, signal_type=sig_type, score=score)
            if success and hasattr(self, "_cycle_buys"):
                self._cycle_buys.append((symbol, notional))
            if success:
                executed += 1
                portfolio["cash"] -= notional
                if self.rl_bridge:
                    self.rl_bridge.on_trade(symbol, score, notional, portfolio)

        if executed:
            logger.info(f"✓ Executed {executed} trades this cycle")
        else:
            logger.info("No trades executed (nothing passed all filters)")

    async def log_portfolio_summary(self, portfolio):
        """Log clean portfolio summary each cycle."""
        positions = portfolio["positions"]
        total_pl = sum(float(p.get("unrealized_pl", 0)) for p in positions)
        logger.info(
            f"Portfolio: ${portfolio['portfolio_value']:.2f} | "
            f"Cash: ${portfolio['cash']:.2f} | "
            f"Positions: {portfolio['position_count']} | "
            f"Unrealized P&L: ${total_pl:.2f}"
        )

    async def get_next_market_open(self) -> str:
        """Return ISO timestamp of next market open from Alpaca clock."""
        try:
            import requests
            r = requests.get(
                "https://api.alpaca.markets/v2/clock",
                headers=self._auth_headers(),
                timeout=10,
            )
            return r.json().get("next_open", "")
        except Exception:
            return ""

    def _load_battle_plan(self) -> list:
        """Load pre-scored candidates from overnight research."""
        plan_path = BASE_DIR / "state" / "market_open_plan.json"
        try:
            if plan_path.exists():
                with open(plan_path) as f:
                    data = json.load(f)
                candidates = data.get("candidates", [])
                built_at = data.get("built_at", "?")
                if candidates:
                    logger.info(f"Battle plan loaded: {len(candidates)} pre-scored candidates (built {built_at})")
                    for c in candidates[:5]:
                        logger.info(
                            f"  Plan: {c['symbol']:8s} score={c.get('score', '?'):>5} | {c.get('reason', c.get('type', ''))}"
                        )
                return candidates
        except Exception as e:
            logger.debug(f"Battle plan load failed: {e}")
        return []

    def _clear_battle_plan(self):
        """Remove battle plan after it's been consumed at market open."""
        plan_path = BASE_DIR / "state" / "market_open_plan.json"
        try:
            if plan_path.exists():
                plan_path.unlink()
                logger.info("Battle plan consumed and cleared")
        except Exception as e:
            logger.debug("Battle plan clear failed: %s", e)

    async def run_after_hours_cycle(self):
        """
        After-hours alpha research cycle — runs every 30 min while market is closed.

        Does NOT execute orders. Instead:
          1. Runs all scanners (Finviz works 24/7; gap/catalyst use after-hours data)
          2. Scores each candidate via alpha engine (bar data available 24/7)
          3. Writes top candidates to state/market_open_plan.json
          4. Plan is loaded and prioritized at next market open

        This means the bot hits the ground running at 9:30 AM every day.
        """
        logger.info("Market CLOSED — running after-hours alpha research")

        next_open = await self.get_next_market_open()
        if next_open:
            logger.info(f"Next market open: {next_open}")

        self._load_ic_state()

        # Scan for candidates (finviz insider/screener works after hours)
        opportunities = await self.scan_opportunities()
        if not opportunities:
            logger.info("After-hours scan: no candidates found")
            return

        # Score each candidate
        scored = []
        for opp in opportunities:
            sym = opp.get("symbol", "")
            if not sym or sym in set(cfg("untradeable_symbols") or []):
                continue
            try:
                score = await self.score_opportunity(opp)
                opp = opp.copy()
                opp["score"] = round(score, 1)
                scored.append(opp)
            except Exception as e:
                logger.debug(f"  After-hours score failed {sym}: {e}")

        # Sort and cap
        scored.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = scored[:12]

        # Show what we're planning
        logger.info(f"After-hours research: {len(top)} candidates scored above threshold")
        for c in top:
            marker = "★" if c.get("score", 0) >= cfg("min_score_threshold") else " "
            logger.info(
                f"  {marker} {c['symbol']:8s} score={c.get('score', 0):>5.1f} "
                f"type={c.get('type','?'):20s} | {c.get('reason', '')[:60]}"
            )

        executable = [c for c in top if c.get("score", 0) >= cfg("min_score_threshold")]
        logger.info(f"  → {len(executable)} meet threshold ({cfg('min_score_threshold')}) — queued for market open")

        # Write battle plan
        (BASE_DIR / "state").mkdir(exist_ok=True)
        plan = {
            "built_at": __import__("datetime").datetime.now().isoformat(),
            "next_market_open": next_open,
            "total_candidates": len(scored),
            "executable_count": len(executable),
            "candidates": top,
        }
        plan_path = BASE_DIR / "state" / "market_open_plan.json"
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2)

        logger.info(f"Battle plan written → {plan_path}")
        logger.info("=" * 80)

        # Populate cycle stats for Telegram report
        self._cycle_is_afterhours   = True
        self._cycle_total_candidates = len(scored)
        self._cycle_top = [
            (c.get("score", 0), c.get("symbol", ""), c.get("type") or c.get("sig_type") or "unknown")
            for c in top
            if c.get("score", 0) >= cfg("min_score_threshold") * 0.5  # require at least half-threshold
        ]
        # Fetch portfolio for report (non-blocking best-effort)
        try:
            p = await self.get_portfolio_state()
            if p:
                self._last_portfolio = p
        except Exception as e:
            logger.debug("After-hours portfolio fetch failed: %s", e)

    async def run_cycle(self):
        logger.info("")
        logger.info("=" * 80)
        logger.info("CYCLE START")
        logger.info("=" * 80)

        # Per-cycle stat tracking (read by main_wrapper for Telegram report)
        self._cycle_buys         = []   # [(symbol, notional)]
        self._cycle_sells        = []   # [(symbol, qty, reason)]
        self._cycle_top          = []   # [(score, symbol, sig_type)]
        self._cycle_total_candidates = 0
        self._cycle_is_afterhours    = False

        is_open = await self.is_market_open()
        if not is_open:
            await self.run_after_hours_cycle()
            return

        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"Market OPEN | rl_action={cfg('rl_action')} trade_mult={cfg('rl_trade_multiplier')} size_mult={cfg('rl_size_multiplier')}")

        # Load and merge overnight battle plan
        plan_candidates = self._load_battle_plan()
        self._clear_battle_plan()

        self._scanner_signals = self._load_scanner_signals()
        if self._scanner_signals:
            logger.info(f"Scanner signals active: {list(self._scanner_signals.keys())[:5]}")

        portfolio = await self.get_portfolio_state()
        if not portfolio:
            logger.error("Failed to get portfolio state")
            return
        self._last_portfolio = portfolio  # for cycle report

        await self.log_portfolio_summary(portfolio)
        self._load_ic_state()

        # ── RL: start episode on first open cycle of the day ──────────────────
        if self.rl_bridge and self._episode_started_today != today:
            self.rl_bridge.on_market_open(portfolio)
            self._episode_started_today = today

        # 1. Clean zombies
        await self.clean_zombies(portfolio)

        # 1b. Options position management (stop/profit/expiry)
        if self.options_trader:
            try:
                closed = self.options_trader.manage_options_positions()
                if closed:
                    logger.info(f"Options managed: closed {len(closed)} contracts: {closed}")
            except Exception as _oe:
                logger.warning(f"Options management error: {_oe}")

        # 1c. Kelly-based portfolio rebalancing (if enabled)
        if cfg("kelly_position_sizing"):
            try:
                rebalance_recs = await self.get_kelly_rebalance_recommendations(portfolio)
                if rebalance_recs and (rebalance_recs.get("sell_orders") or rebalance_recs.get("buy_orders")):
                    logger.info("Kelly rebalancing recommendations available - applying selectively")
                    # For now, log recommendations. Full implementation would execute them.
                    for sell_rec in rebalance_recs.get("sell_orders", []):
                        logger.info(f"  Kelly sell recommendation: {sell_rec['symbol']} (${sell_rec['market_value']:.0f}, {sell_rec['reason']})")
                    for buy_rec in rebalance_recs.get("buy_orders", [])[:3]:  # Limit to top 3
                        logger.info(f"  Kelly buy recommendation: {buy_rec['symbol']} (${buy_rec['target_market_value']:.0f}, {buy_rec['category']})")
            except Exception as e:
                logger.warning(f"Kelly rebalancing failed: {e}")

        # 2. Convictions removed — pure systematic alpha

        # 3. Scan + merge battle plan, execute
        fresh_opps = await self.scan_opportunities()
        fresh_syms = {o.get("symbol") for o in fresh_opps}
        plan_extras = [p for p in plan_candidates if p.get("symbol") not in fresh_syms]
        if plan_extras:
            logger.info(f"Adding {len(plan_extras)} overnight candidates to live scan")
        opportunities = fresh_opps + plan_extras

        # Track top scored candidates for cycle report
        if hasattr(self, "_cycle_top"):
            from collections import defaultdict
            _scored_this_cycle = []
            for opp in opportunities:
                sym   = opp.get("symbol", "")
                score = opp.get("score", 0)
                stype = opp.get("sig_type") or opp.get("type") or "unknown"
                if sym and score > 0:
                    _scored_this_cycle.append((score, sym, stype))
            self._cycle_top = sorted(_scored_this_cycle, reverse=True)[:10]
            self._cycle_total_candidates = len(opportunities)
        await self.execute_opportunities(portfolio, opportunities)

        # ── RL: check if market just closed (last cycle of the day) ───────────
        # We check again after execution — if market is now closed, end episode
        is_still_open = await self.is_market_open()
        if not is_still_open and self.rl_bridge and self.rl_bridge.current_episode_id:
            fresh_portfolio = await self.get_portfolio_state()
            if fresh_portfolio:
                self.rl_bridge.on_market_close(fresh_portfolio)
                logger.info("Episode ended — RL Q-update triggered")

        self._cycle_count = getattr(self, "_cycle_count", 0) + 1
        logger.info("=" * 80)
        logger.info("CYCLE COMPLETE")
        logger.info("=" * 80)


async def main():
    orchestrator = Orchestrator()
    try:
        await orchestrator.run_cycle()
    except Exception as e:
        logger.error(f"Cycle failed: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
