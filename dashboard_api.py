#!/usr/bin/env python3
"""
Live Web Dashboard API
Serves real-time portfolio data, convictions, and logs via HTTP
"""
from flask import Flask, render_template, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import sys
from pathlib import Path
from datetime import datetime
import json
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

app = Flask(__name__)
CORS(app)

# Paths
STRATEGY_DIR = Path(__file__).parent
UNIFIED_DIR  = Path('/home/jonathangan/shared/unified')
sys.path.insert(0, str(STRATEGY_DIR))

from core.alpaca_client import AlpacaClient

# Initialize clients
try:
    # Use LIVE API (not paper trading)
    alpaca_client = AlpacaClient(base_url="https://api.alpaca.markets")
    ALPACA_AVAILABLE = True
except Exception as e:
    print(f"⚠️  Alpaca client unavailable: {e}")
    ALPACA_AVAILABLE = False



def _decode_option_symbol(symbol):
    """Decode OCC option symbol e.g. LWLG260320C00005000 -> dict with ticker/direction/strike/expiry."""
    try:
        for i in range(len(symbol) - 1, 5, -1):
            if symbol[i] in ('C', 'P') and symbol[i-6:i].isdigit():
                ticker    = symbol[:i-6]
                date_str  = symbol[i-6:i]
                direction = 'CALL' if symbol[i] == 'C' else 'PUT'
                strike    = int(symbol[i+1:]) / 1000.0
                expiry    = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:]}"
                return {
                    'ticker':    ticker,
                    'expiry':    expiry,
                    'direction': direction,
                    'strike':    strike,
                    'label':     f"{ticker} ${strike:.2f} {direction} exp {expiry[5:]}",
                }
    except Exception:
        pass
    return None

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('live_dashboard.html')


@app.route('/api/health')
def api_health():
    """Health check"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'alpaca_connected': ALPACA_AVAILABLE
    })


@app.route('/api/portfolio')
def api_portfolio():
    """Full portfolio state"""
    if not ALPACA_AVAILABLE:
        return jsonify({'error': 'Alpaca client unavailable'}), 503
    
    try:
        account = alpaca_client.get_account()
        positions = alpaca_client.get_positions()
        
        # Calculate total P/L (positions are dicts, not objects)
        total_pl = sum(float(p.get('unrealized_pl', 0)) for p in positions)
        portfolio_value = float(account.get('portfolio_value', 1))
        total_pl_pct = (total_pl / portfolio_value) * 100 if portfolio_value > 0 else 0
        
        def _enrich(p):
            sym = p.get('symbol', '')
            base = {
                'symbol':          sym,
                'qty':             float(p.get('qty', 0)),
                'market_value':    float(p.get('market_value', 0)),
                'unrealized_pl':   float(p.get('unrealized_pl', 0)),
                'unrealized_plpc': float(p.get('unrealized_plpc', 0)),
                'current_price':   float(p.get('current_price', 0)),
                'avg_entry_price': float(p.get('avg_entry_price', 0)),
                'cost_basis':      float(p.get('cost_basis', 0)),
            }
            opt = _decode_option_symbol(sym)
            if opt:
                base['_option']   = True
                base['direction'] = opt['direction']
                base['strike']    = opt['strike']
                base['expiry']    = opt['expiry']
                base['label']     = opt['label']
            else:
                base['_option'] = False
            return base

        options_positions = [p for p in positions if _decode_option_symbol(p.get('symbol', ''))]
        equity_positions  = [p for p in positions if not _decode_option_symbol(p.get('symbol', ''))]
        options_pl = sum(float(p.get('unrealized_pl', 0)) for p in options_positions)
        equity_pl  = sum(float(p.get('unrealized_pl', 0)) for p in equity_positions)

        return jsonify({
            'portfolio_value':      portfolio_value,
            'cash':                 float(account.get('cash', 0)),
            'buying_power':         float(account.get('buying_power', 0)),
            'options_buying_power': float(account.get('options_buying_power', 0)),
            'equity':               float(account.get('equity', 0)),
            'total_pl':             total_pl,
            'total_pl_pct':         total_pl_pct,
            'options_pl':           options_pl,
            'equity_pl':            equity_pl,
            'options_count':        len(options_positions),
            'equity_count':         len(equity_positions),
            'positions':            [_enrich(p) for p in positions],
            'position_count':       len(positions),
            'timestamp':            datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/convictions')
def api_convictions():
    """Active conviction positions"""
    try:
        weights_file = UNIFIED_DIR / 'state/rl_weights.json'
        if weights_file.exists():
            with open(weights_file, 'r') as f:
                data = json.load(f)
            return jsonify({'rl_weights': data, 'convictions': {}})
        return jsonify({'rl_weights': {}, 'convictions': {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
def api_logs():
    """Merged logs from all bots — options_v1, unified, orchestrator"""
    LOG_SOURCES = [
        ('OPTIONS_V1', Path('/home/jonathangan/shared/options_v1/data/trading.log')),
        ('UNIFIED',    UNIFIED_DIR / 'logs/trading.log'),
        ('STOCKBOT',   Path('/home/jonathangan/shared/stockbot/strategy_v2/logs/trading.log')),
    ]
    all_entries = []
    try:
        for label, log_file in LOG_SOURCES:
            if not log_file.exists():
                continue
            lines = log_file.read_text(errors='replace').split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                all_entries.append({'source': label, 'line': line, 'raw': line})

        # Sort by timestamp prefix if present, otherwise preserve order
        def sort_key(e):
            line = e['line']
            # Try to extract timestamp from common log formats
            # Format: 2026-03-05 06:25:02,145 or 2026-03-05 06:25:02
            try:
                ts = line[:23].replace(',', '.')
                from datetime import datetime
                return datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')
            except Exception:
                return ''

        try:
            all_entries.sort(key=sort_key)
        except Exception:
            pass

        # Return last 200 lines across all sources, formatted with source tag
        recent = all_entries[-200:]
        formatted = [f"[{e['source']}] {e['line']}" for e in recent]
        return jsonify({'logs': formatted, 'total': len(all_entries)})
    except Exception as e:
        return jsonify({'error': str(e), 'logs': []})


@app.route('/api/status')
def api_status():
    """Status for all running bots"""
    import subprocess
    services = {
        'options_v1_bot': '/home/jonathangan/shared/options_v1/data/trading.log',
        'unified_bot':    str(UNIFIED_DIR / 'logs/trading.log'),
        'mybot':          '/home/jonathangan/shared/stockbot/strategy_v2/logs/trading.log',
    }
    statuses = {}
    most_recent_activity = None
    try:
        for svc, log_path in services.items():
            try:
                result = subprocess.run(['systemctl', 'is-active', svc],
                                        capture_output=True, text=True)
                running = result.stdout.strip() == 'active'
            except Exception:
                running = False
            log_file = Path(log_path)
            if log_file.exists():
                age_secs = datetime.now().timestamp() - log_file.stat().st_mtime
                age_min  = int(age_secs / 60)
                last_line = ''
                try:
                    lines = log_file.read_text(errors='replace').strip().split('\n')
                    last_line = lines[-1][:120] if lines else ''
                except Exception:
                    pass
            else:
                age_min   = -1
                last_line = 'No log file'
            statuses[svc] = {
                'running':      running,
                'log_age_min':  age_min,
                'last_line':    last_line,
                'activity':     'Active' if age_min < 5 else (f'{age_min}m ago' if age_min >= 0 else 'No log'),
            }
            if age_min >= 0 and (most_recent_activity is None or age_min < most_recent_activity):
                most_recent_activity = age_min

        # Strategy V2 scan stats from battle plan
        v2_scan = {}
        try:
            import json as _jsc
            from pathlib import Path as _PSC
            bp = _PSC('/home/jonathangan/shared/stockbot/strategy_v2/state/market_open_plan.json')
            lc = _PSC('/home/jonathangan/shared/stockbot/strategy_v2/evaluation/live_config.json')
            tb = _PSC('/home/jonathangan/shared/stockbot/strategy_v2/evaluation/threshold_bandit.json')
            if bp.exists():
                bpd = _jsc.loads(bp.read_text())
                v2_scan['candidates'] = len(bpd.get('candidates', []))
                v2_scan['built_at']   = bpd.get('built_at', '')[:16]
                top = sorted(bpd.get('candidates', []), key=lambda x: x.get('score', 0), reverse=True)[:3]
                v2_scan['top_picks']  = [{'symbol': c.get('symbol'), 'score': round(c.get('score', 0), 1)} for c in top]
            if lc.exists():
                lcd = _jsc.loads(lc.read_text())
                v2_scan['threshold'] = lcd.get('min_score_threshold')
                v2_scan['regime']    = lcd.get('rl_threshold_regime')
        except Exception as _se:
            v2_scan['error'] = str(_se)

        return jsonify({
            'service_running':  any(s['running'] for s in statuses.values()),
            'services':         statuses,
            'log_age_minutes':  most_recent_activity or -1,
            'last_activity':    'Active' if (most_recent_activity or 999) < 5 else f'{most_recent_activity}m ago',
            'v2_scan':          v2_scan,
            'timestamp':        datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/decisions/recent')
def api_recent_decisions():
    """Recent decision log entries"""
    try:
        memory_file = UNIFIED_DIR / 'state/trade_memory.jsonl'
        if not memory_file.exists():
            return jsonify({'decisions': []})
        trades = []
        with open(memory_file, 'r') as f:
            lines = f.readlines()
            for line in lines[-10:]:
                if line.strip():
                    try:
                        t = json.loads(line)
                        trades.append({
                            'timestamp': t.get('timestamp', ''),
                            'symbol': t.get('symbol', ''),
                            'action': f"{t.get('strategy','')} {t.get('kind','')} x{t.get('contracts',0)}",
                            'outcome': t.get('outcome', 'open'),
                            'pnl': t.get('pnl'),
                            'entry_price': t.get('entry_price'),
                            'ev': t.get('ev_at_entry'),
                        })
                    except:
                        pass
        return jsonify({'decisions': trades})
    except Exception as e:
        return jsonify({'error': str(e), 'decisions': []})



@app.route('/api/intel')
def api_intel():
    import json as _j
    from pathlib import Path as _P
    out = {'vix': {}, 'market_proxy': None, 'symbols': {}, 'rl': {}}
    try:
        p = _P('/home/jonathangan/shared/options_v1/data/news_intel.json')
        if p.exists():
            d = _j.loads(p.read_text())
            out['vix'] = d.get('vix', {})
            out['market_proxy'] = d.get('market_proxy')
            out['symbols'] = d.get('symbols', {})
    except Exception as e:
        out['intel_error'] = str(e)
    try:
        r = _P('/home/jonathangan/shared/options_v1/data/rl_weights.json')
        if r.exists():
            rl = _j.loads(r.read_text())
            def g(k): return rl.get(k, {}).get('DCVX', 0)
            out['rl'] = {
                'n_trades':    g('n_trades'),
                'win_rate':    g('win_rate') or 0.5,
                'kelly_scale': g('kelly_scale') or 1.0,
                'total_pnl':   g('total_pnl'),
                'ev_threshold':g('ev_threshold'),
            }
    except Exception as e:
        out['rl_error'] = str(e)
    return jsonify(out)




@app.route('/api/logs/options')
def api_logs_options():
    """Options V1 bot logs only."""
    from pathlib import Path as _P
    log_file = _P('/home/jonathangan/shared/options_v1/data/trading.log')
    lines = []
    if log_file.exists():
        raw = log_file.read_text(errors='replace').splitlines()
        lines = [l.strip() for l in raw if l.strip()]
    return jsonify({'logs': lines[-300:], 'total': len(lines)})


@app.route('/api/signals')
def api_signals():
    """Merged signal intelligence: news + insider + CA + RL IC for all symbols."""
    import json as _j
    from pathlib import Path as _P

    out = {'symbols': {}, 'rl_ic': {}, 'scanned_at': None}

    # News intel
    news_data = {}
    try:
        p = _P('/home/jonathangan/shared/options_v1/data/news_intel.json')
        if p.exists():
            d = _j.loads(p.read_text())
            news_data = d.get('symbols', {})
            out['scanned_at'] = d.get('scanned_at')
    except Exception as e:
        out['news_error'] = str(e)

    # Insider intel
    insider_data = {}
    try:
        p = _P('/home/jonathangan/shared/options_v1/data/insider_intel.json')
        if p.exists():
            insider_data = _j.loads(p.read_text()).get('data', {})
    except Exception as e:
        out['insider_error'] = str(e)

    # CA sentiment cache
    ca_data = {}
    try:
        p = _P('/home/jonathangan/shared/options_v1/data/sentiment_cache.json')
        if p.exists():
            ca_data = _j.loads(p.read_text())
    except Exception:
        pass

    # RL signal IC
    try:
        p = _P('/home/jonathangan/shared/options_v1/data/rl_weights.json')
        if p.exists():
            rl = _j.loads(p.read_text())
            out['rl_ic'] = rl.get('signal_ic', {})
            out['ic_obs_counts'] = {k: len(v) for k, v in rl.get('ic_obs', {}).items()}
    except Exception:
        pass

    # Merge per symbol
    all_syms = set(list(news_data.keys()) + list(insider_data.keys()))
    for sym in sorted(all_syms):
        ni = news_data.get(sym, {})
        ii = insider_data.get(sym, {})
        ca = ca_data.get(sym, {})

        out['symbols'][sym] = {
            'news': {
                'score':      ni.get('news', {}).get('score', 0.0),
                'label':      ni.get('news', {}).get('label', 'neutral'),
                'confidence': ni.get('news', {}).get('confidence', 0.0),
                'n':          ni.get('news', {}).get('n', 0),
                'articles':   ni.get('news', {}).get('articles', []),
            },
            'insider': {
                'score':      ii.get('score', 0.0),
                'label':      ii.get('label', 'neutral'),
                'confidence': ii.get('confidence', 0.0),
                'buys':       ii.get('buys', 0),
                'sells':      ii.get('sells', 0),
                'net_shares': ii.get('net_shares', 0),
                'transactions': ii.get('transactions', []),
            },
            'ca': {
                'score':      ca.get('score', 0.0) if isinstance(ca, dict) else 0.0,
                'label':      ca.get('label', 'neutral') if isinstance(ca, dict) else 'neutral',
                'events':     ca.get('events', []) if isinstance(ca, dict) else [],
            },
        }

    # Polymarket intel + calibration
    try:
        from pathlib import Path as _PP
        import json as _jj, sys as _sys
        pm_path = _PP('/home/jonathangan/shared/options_v1/data/polymarket_intel.json')
        if pm_path.exists():
            pd = _jj.loads(pm_path.read_text())
            # Calibration from ledger
            calib = {}
            try:
                _sys.path.insert(0, '/home/jonathangan/shared/options_v1')
                from options_v1 import polymarket_scanner as _pms
                calib = _pms.get_calibration_summary()
            except Exception:
                ledger = _PP('/home/jonathangan/shared/options_v1/data/poly_ledger.jsonl')
                if ledger.exists():
                    entries = [_jj.loads(l) for l in ledger.read_text().strip().split('\n') if l.strip()]
                    resolved = [e for e in entries if e.get('ic_alignment') is not None]
                    if resolved:
                        aligns = [e['ic_alignment'] for e in resolved]
                        calib = {
                            'ic':              round(sum(aligns)/len(aligns), 4),
                            'n_trades':        len(resolved),
                            'directional_acc': round(sum(1 for a in aligns if a>0)/len(aligns), 4),
                            'avg_brier':       None,
                        }
            out['polymarket'] = {
                'macro':          pd.get('macro', [])[:8],
                'by_symbol':      {s: v[:2] for s, v in pd.get('by_symbol', {}).items()},
                'scanned_at':     pd.get('scanned_at'),
                'relevant':       pd.get('relevant', 0),
                'calibration':    calib,
            }
    except Exception as e:
        out['poly_error'] = str(e)

    return jsonify(out)



@app.route('/api/trades')
def api_trades():
    trades = []
    from pathlib import Path as _TMP2
    _trade_files = [
        _TMP2('/home/jonathangan/shared/options_v1/data/trade_memory.jsonl'),
        _TMP2('/home/jonathangan/shared/stockbot/strategy_v2/state/options_plans.jsonl'),
    ]
    for _TM in _trade_files:
      if _TM.exists():
        for line in _TM.read_text(errors='replace').splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
                # Support both options_v1 format and strategy_v2 options_plans format
                sym      = t.get('symbol', '')
                kind     = t.get('kind') or t.get('direction', '')
                strike   = t.get('strike') or t.get('strike_price') or 0
                entry_ts = t.get('timestamp') or t.get('entry_ts', '')
                outcome  = t.get('outcome') or t.get('status', 'open')
                pnl_raw  = t.get('pnl') or t.get('actual_pnl')
                t['display'] = {
                    'symbol':    sym,
                    'occ':       t.get('occ_symbol', ''),
                    'kind':      kind.upper() if kind else '—',
                    'strike':    strike,
                    'contracts': t.get('contracts', 1),
                    'entry':     round(t.get('entry_price', 0), 2),
                    'exit':      round(t.get('exit_price', 0), 2) if t.get('exit_price') else None,
                    'pnl':       round(pnl_raw, 2) if pnl_raw is not None else None,
                    'ev':        round(t.get('ev_at_entry', 0) or t.get('alpha_score', 0), 2),
                    'outcome':   outcome,
                    'strategy':  t.get('strategy', 'options_v2'),
                    'signal':    t.get('signal_type', ''),
                    'thesis':    (t.get('entry_thesis') or t.get('thesis') or '')[:80],
                    'ts':        entry_ts[:16],
                }
                trades.append(t)
            except Exception:
                pass
    # most recent first
    trades.reverse()
    return jsonify({'trades': trades[:50], 'total': len(trades)})


@app.route('/api/plans')
def api_plans():
    """Return all trade plans with full detail."""
    import json as _j
    plans = []
    for pf in [
        Path('/home/jonathangan/shared/options_v1/data/trade_plans.jsonl'),
        Path('/home/jonathangan/shared/stockbot/strategy_v2/state/options_plans.jsonl'),
    ]:
        if pf.exists():
            for line in pf.read_text(errors='replace').strip().split('\n'):
                if line.strip():
                    try:
                        plans.append(_j.loads(line))
                    except Exception:
                        pass

    open_p = [p for p in plans if p.get('status') == 'open']
    closed  = [p for p in plans if p.get('status') != 'open']

    closed_total = len(closed)
    targets_hit  = sum(1 for p in closed if p.get('status') == 'target_hit')
    stops_hit    = sum(1 for p in closed if p.get('status') == 'stop_hit')
    switched     = sum(1 for p in closed if p.get('status') in ('switched', 'oc_switch'))
    pnls         = [p.get('actual_pnl', 0) for p in closed if p.get('actual_pnl') is not None]
    avg_rr       = sum(pnls) / max(1, len(pnls)) if pnls else 0.0

    from datetime import datetime as _dtx, timezone as _tzx
    def _make_display(p):
        ep = p.get('entry_price', 0) or 0
        tp = p.get('target_price', 0) or 0
        sp = p.get('stop_price', 0) or 0
        status = p.get('status', 'open')
        try:
            dl = _dtx.fromisoformat(p.get('target_date','').replace('Z','+00:00'))
            days_left = (dl - _dtx.now(_tzx.utc)).days
        except Exception:
            days_left = None
        actual_pnl = p.get('actual_pnl')
        return {
            'symbol':       p.get('symbol', '?'),
            'direction':    p.get('direction', '?').upper(),
            'entry':        f"${ep:.2f}",
            'target':       f"${tp:.2f}",
            'stop':         f"${sp:.2f}",
            'rr':           f"{p.get('risk_reward', 0):.1f}:1",
            'deadline':     p.get('target_date', '?')[:10],
            'days_left':    days_left,
            'thesis':       (p.get('entry_thesis', '') or '')[:100],
            'max_loss':     f"${abs(p.get('max_loss_dollars', 0) or 0):.0f}",
            'target_gain':  f"${(p.get('target_gain_dollars', 0) or 0):.0f}",
            'status':       status,
            'actual_pnl':   round(actual_pnl, 2) if actual_pnl is not None else None,
            'oc_checks':    p.get('oc_checks', 0),
            'oc_ev_hold':   p.get('oc_last_ev_hold'),
            'oc_ev_new':    p.get('oc_last_ev_new'),
            'exit_reason':  p.get('exit_reason', ''),
            'strategy':     p.get('strategy', 'DCVX'),
            'occ_symbol':   p.get('occ_symbol', ''),
            'contracts':    p.get('contracts', 1),
            'ev':           round(p.get('ev_at_entry', 0) or 0, 2),
        }

    for p in open_p + closed:
        p['display'] = _make_display(p)

    return jsonify({
        'plans': open_p + closed[-10:],
        'summary': {
            'open':             len(open_p),
            'closed':           closed_total,
            'target_hit_rate':  f"{targets_hit}/{closed_total}",
            'stop_rate':        f"{stops_hit}/{closed_total}",
            'switched':         switched,
            'avg_pnl':          f"${avg_rr:.2f}",
        }
    })


@app.route('/api/stream')
def api_stream():
    """Server-Sent Events — pushes live data to dashboard with no client polling."""
    from flask import stream_with_context as _swc
    import json as _j, time as _t
    from pathlib import Path as _P

    OPTIONS_LOG  = _P('/home/jonathangan/shared/options_v1/data/trading.log')
    INTEL_FILE   = _P('/home/jonathangan/shared/options_v1/data/news_intel.json')
    INSIDER_FILE = _P('/home/jonathangan/shared/options_v1/data/insider_intel.json')
    RL_FILE      = _P('/home/jonathangan/shared/options_v1/data/rl_weights.json')
    SVC_NAME     = 'options_v1_bot'
    MYBOT_LOG    = _P('/home/jonathangan/shared/stockbot/strategy_v2/logs/trading.log')

    def _event(name, data):
        return f"event: {name}\ndata: {_j.dumps(data)}\n\n"

    def _ping():
        return f": keepalive\n\n"

    def _portfolio():
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetPortfolioHistoryRequest
            import os
            key    = os.environ.get('ALPACA_API_LIVE_KEY') or os.environ.get('ALPACA_API_KEY_ID','')
            secret = os.environ.get('ALPACA_API_SECRET')  or os.environ.get('APCA_API_SECRET_KEY','')
            tc = TradingClient(key, secret, paper=False)
            acct = tc.get_account()
            positions = tc.get_all_positions()
            pos_list = []
            total_pl = 0.0
            for p in positions:
                upl = float(p.unrealized_pl or 0)
                total_pl += upl
                sym    = p.symbol
                entry  = {
                    'symbol':          sym,
                    'qty':             float(p.qty),
                    'market_value':    float(p.market_value or 0),
                    'avg_entry_price': float(p.avg_entry_price or 0),
                    'current_price':   float(p.current_price or 0),
                    'unrealized_pl':   upl,
                    'unrealized_plpc': float(p.unrealized_plpc or 0),
                }
                opt = _decode_option_symbol(sym)
                if opt:
                    entry['_option']   = True
                    entry['direction'] = opt['direction']
                    entry['strike']    = opt['strike']
                    entry['expiry']    = opt['expiry']
                    entry['label']     = opt['label']
                else:
                    entry['_option'] = False
                pos_list.append(entry)
            equity = float(acct.equity or 0)
            cash   = float(acct.cash   or 0)
            port   = equity   # equity already includes cash
            opt_pos    = [p for p in pos_list if p.get('_option')]
            eq_pos     = [p for p in pos_list if not p.get('_option')]
            options_pl = sum(p['unrealized_pl'] for p in opt_pos)
            equity_pl  = sum(p['unrealized_pl'] for p in eq_pos)
            return {
                'portfolio_value':      port,
                'equity':               equity,
                'cash':                 cash,
                'buying_power':         float(acct.buying_power or 0),
                'options_buying_power': float(acct.options_buying_power or 0),
                'total_pl':             total_pl,
                'total_pl_pct':         (total_pl / port * 100) if port > 0 else 0,
                'options_pl':           options_pl,
                'equity_pl':            equity_pl,
                'options_count':        len(opt_pos),
                'equity_count':         len(eq_pos),
                'position_count':       len(pos_list),
                'positions':            pos_list,
                'timestamp':            _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime()),
            }
        except Exception as e:
            return {'error': str(e)}

    def _status():
        try:
            import subprocess
            r = subprocess.run(
                ['systemctl', 'is-active', SVC_NAME],
                capture_output=True, text=True, timeout=3
            )
            running = r.stdout.strip() == 'active'
            activity = '—'
            if OPTIONS_LOG.exists():
                lines = OPTIONS_LOG.read_text(errors='replace').splitlines()
                for line in reversed(lines[-50:]):
                    stripped = line.strip()
                    if not stripped or '[' not in stripped:
                        continue
                    # Extract message after log level: '2026-... [INFO] main: message'
                    parts = stripped.split('] ', 1)
                    msg = parts[-1] if len(parts) > 1 else stripped
                    # Skip pure separator lines (===, ---, etc.)
                    body = msg.split(': ', 1)[-1] if ': ' in msg else msg
                    if body and not all(c in '=- ' for c in body):
                        activity = msg[:100]
                        break
            # mybot (Strategy V2) status
            mybot_running  = False
            mybot_activity = 'No log'
            try:
                r2 = subprocess.run(['systemctl', 'is-active', 'mybot'],
                                    capture_output=True, text=True, timeout=3)
                mybot_running = r2.stdout.strip() == 'active'
            except Exception:
                pass
            if MYBOT_LOG.exists():
                mb_lines = MYBOT_LOG.read_text(errors='replace').splitlines()
                for line in reversed(mb_lines[-30:]):
                    stripped = line.strip()
                    if stripped and 'INFO' in stripped and '===' not in stripped and '---' not in stripped:
                        mybot_activity = stripped[-120:]
                        break

            return {
                'services': {
                    SVC_NAME: {
                        'running':  running,
                        'activity': activity,
                    },
                    'mybot': {
                        'running':  mybot_running,
                        'activity': mybot_activity,
                    },
                },
                'service_running': running or mybot_running,
                'mybot_running':   mybot_running,
                'mybot_activity':  mybot_activity,
            }
        except Exception as e:
            return {'error': str(e), 'service_running': False}

    def _intel():
        try:
            if INTEL_FILE.exists():
                d = _j.loads(INTEL_FILE.read_text())
                rl_ic = {}
                ic_obs = {}
                if RL_FILE.exists():
                    rl = _j.loads(RL_FILE.read_text())
                    rl_ic  = rl.get('signal_ic', {})
                    ic_obs = {k: len(v) for k, v in rl.get('ic_obs', {}).items()}
                d['rl_ic']       = rl_ic
                d['ic_obs_counts'] = ic_obs
                # RL state
                if RL_FILE.exists():
                    rl = _j.loads(RL_FILE.read_text())
                    def g(k): return rl.get(k, {}).get('DCVX', 0)
                    d['rl'] = {
                        'n_trades':    g('n_trades'),
                        'win_rate':    g('win_rate') or 0.5,
                        'kelly_scale': g('kelly_scale') or 1.0,
                        'total_pnl':   g('total_pnl'),
                    }
                return d
        except Exception as e:
            return {'error': str(e)}
        return {}

    def _signals():
        try:
            news_data    = {}
            insider_data = {}
            rl_ic        = {}
            ic_obs       = {}
            if INTEL_FILE.exists():
                nd = _j.loads(INTEL_FILE.read_text())
                news_data = nd.get('symbols', {})
            if INSIDER_FILE.exists():
                insider_data = _j.loads(INSIDER_FILE.read_text()).get('data', {})
            if RL_FILE.exists():
                rl = _j.loads(RL_FILE.read_text())
                rl_ic  = rl.get('signal_ic', {})
                ic_obs = {k: len(v) for k, v in rl.get('ic_obs', {}).items()}
            syms = {}
            for sym in sorted(set(list(news_data.keys()) + list(insider_data.keys()))):
                ni = news_data.get(sym, {})
                ii = insider_data.get(sym, {})
                syms[sym] = {
                    'news': {
                        'score':      ni.get('news', {}).get('score', 0.0),
                        'label':      ni.get('news', {}).get('label', 'neutral'),
                        'confidence': ni.get('news', {}).get('confidence', 0.0),
                        'n':          ni.get('news', {}).get('n', 0),
                        'articles':   ni.get('news', {}).get('articles', []),
                    },
                    'insider': {
                        'score':        ii.get('score', 0.0),
                        'label':        ii.get('label', 'neutral'),
                        'buys':         ii.get('buys', 0),
                        'sells':        ii.get('sells', 0),
                        'net_shares':   ii.get('net_shares', 0),
                        'transactions': ii.get('transactions', []),
                    },
                }
            return {'symbols': syms, 'rl_ic': rl_ic, 'ic_obs_counts': ic_obs}
        except Exception as e:
            return {'error': str(e)}

    def _plans():
        try:
            import json as _jj
            plans = []
            for pf in [
                _P('/home/jonathangan/shared/options_v1/data/trade_plans.jsonl'),
                _P('/home/jonathangan/shared/stockbot/strategy_v2/state/options_plans.jsonl'),
            ]:
                if not pf.exists():
                    continue
                for line in pf.read_text(errors='replace').splitlines():
                    if line.strip():
                        try:
                            plans.append(_jj.loads(line))
                        except Exception:
                            pass
            open_p = [p for p in plans if p.get('status') == 'open']
            closed = [p for p in plans if p.get('status') != 'open']
            return {
                'plans': open_p + closed[-5:],
                'summary': {
                    'open': len(open_p),
                    'closed': len(closed),
                    'targets_hit': sum(1 for p in closed if p.get('status') == 'target_hit'),
                    'stops_hit': sum(1 for p in closed if p.get('status') == 'stop_hit'),
                }
            }
        except Exception as e:
            return {'plans': [], 'summary': {}, 'error': str(e)}

    def generate():
        last_portfolio   = 0.0
        last_status      = 0.0
        last_intel_mtime = 0.0
        last_sig_mtime   = 0.0
        last_rl_mtime    = 0.0
        last_ping        = 0.0
        last_plans_mtime = 0.0
        log_pos          = 0

        # Seed log position to end of file (only stream new lines)
        if OPTIONS_LOG.exists():
            log_pos = OPTIONS_LOG.stat().st_size

        # Initial full push
        yield _event('portfolio', _portfolio())
        yield _event('status',    _status())
        yield _event('intel',     _intel())
        yield _event('signals',   _signals())
        yield _event('plans',     _plans())
        # Initial last 120 log lines
        if OPTIONS_LOG.exists():
            raw   = OPTIONS_LOG.read_text(errors='replace').splitlines()
            lines = [l.strip() for l in raw[-120:] if l.strip()]
            yield _event('log_init', {'lines': lines})

        while True:
            now = _t.time()

            # ── Log: stream new lines as they appear ──────────
            try:
                if OPTIONS_LOG.exists():
                    size = OPTIONS_LOG.stat().st_size
                    if size > log_pos:
                        with open(OPTIONS_LOG, errors='replace') as fh:
                            fh.seek(log_pos)
                            chunk = fh.read()
                        log_pos = size
                        new_lines = [l.strip() for l in chunk.splitlines() if l.strip()]
                        if new_lines:
                            yield _event('log_lines', {'lines': new_lines})
            except Exception:
                pass

            # ── Portfolio: every 5s ────────────────────────────
            if now - last_portfolio >= 5:
                yield _event('portfolio', _portfolio())
                last_portfolio = now

            # RL threshold bandit — push every 30s
            if now - last_portfolio >= 30:
                try:
                    import json as _jt
                    from pathlib import Path as _PT
                    bfile = _PT('/home/jonathangan/shared/stockbot/strategy_v2/evaluation/threshold_bandit.json')
                    lfile = _PT('/home/jonathangan/shared/stockbot/strategy_v2/evaluation/live_config.json')
                    rl_data = {}
                    if bfile.exists():
                        rl_data['state'] = _jt.loads(bfile.read_text())
                    if lfile.exists():
                        lc = _jt.loads(lfile.read_text())
                        rl_data['active_threshold'] = lc.get('min_score_threshold')
                        rl_data['active_regime']    = lc.get('rl_threshold_regime')
                    yield _event('rl_threshold', rl_data)
                except Exception:
                    pass

            # ── Status: every 15s ─────────────────────────────
            if now - last_status >= 15:
                yield _event('status', _status())
                last_status = now

            # ── Intel: when news_intel.json changes ───────────
            try:
                if INTEL_FILE.exists():
                    mt = INTEL_FILE.stat().st_mtime
                    if mt != last_intel_mtime:
                        last_intel_mtime = mt
                        yield _event('intel', _intel())
            except Exception:
                pass

            # ── Signals: when insider or rl changes ───────────
            try:
                sig_mtime = 0.0
                for fp in (INSIDER_FILE, RL_FILE):
                    if fp.exists():
                        sig_mtime = max(sig_mtime, fp.stat().st_mtime)
                if sig_mtime != last_sig_mtime and sig_mtime > 0:
                    last_sig_mtime = sig_mtime
                    yield _event('signals', _signals())
            except Exception:
                pass

            # ── Plans: emit when either plans file changes ────
            try:
                v1_f = _P('/home/jonathangan/shared/options_v1/data/trade_plans.jsonl')
                v2_f = _P('/home/jonathangan/shared/stockbot/strategy_v2/state/options_plans.jsonl')
                latest_mtime = 0.0
                for pf in [v1_f, v2_f]:
                    if pf.exists():
                        latest_mtime = max(latest_mtime, pf.stat().st_mtime)
                if latest_mtime > 0 and latest_mtime != last_plans_mtime:
                    last_plans_mtime = latest_mtime
                    yield _event('plans', _plans())
            except Exception:
                pass

            # ── Keepalive ping every 25s ──────────────────────
            if now - last_ping >= 25:
                yield _ping()
                last_ping = now

            _t.sleep(0.8)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':    'no-cache',
            'X-Accel-Buffering':'no',
            'Connection':       'keep-alive',
        }
    )


PLANS_FILE    = Path('/home/jonathangan/shared/options_v1/data/trade_plans.jsonl')
V2_PLANS_FILE = Path('/home/jonathangan/shared/stockbot/strategy_v2/state/options_plans.jsonl')

def _plans_data():
    """Load plans for API/SSE - merges options_v1 and strategy_v2 plans."""
    from datetime import datetime as _dtx, timezone as _tzx
    plans = []
    for pf in [PLANS_FILE, V2_PLANS_FILE]:
        if not pf.exists():
            continue
        for raw_line in pf.read_text(errors='replace').splitlines():
            if not raw_line.strip():
                continue
            try:
                p = json.loads(raw_line)
                ep = p.get('entry_price', 0) or 0
                tp = p.get('target_price', 0) or 0
                sp = p.get('stop_price', 0) or 0
                try:
                    dl = _dtx.fromisoformat(p.get('target_date', '').replace('Z', '+00:00'))
                    days_left = max(0, (dl - _dtx.now(_tzx.utc)).days)
                except Exception:
                    days_left = '?'
                thesis_text = p.get('entry_thesis') or p.get('thesis') or ''
                p['display'] = {
                    'symbol':      p.get('symbol', '?'),
                    'occ_symbol':  p.get('occ_symbol', p.get('symbol', '?')),
                    'direction':   p.get('direction', '?').upper(),
                    'contracts':   p.get('contracts', 1),
                    'entry':       '$%.2f' % ep,
                    'target':      '$%.2f' % tp,
                    'stop':        '$%.2f' % sp,
                    'rr':          '%.1f:1' % p.get('risk_reward', 0),
                    'deadline':    p.get('target_date', '?')[:10],
                    'days_left':   days_left,
                    'thesis':      thesis_text[:120],
                    'max_loss':    '$%.0f' % abs(p.get('max_loss_dollars', 0)),
                    'target_gain': '$%.0f' % p.get('target_gain_dollars', 0),
                    'oc_checks':   p.get('oc_checks', 0),
                    'oc_ev_hold':  p.get('oc_last_ev_hold'),
                    'oc_ev_new':   p.get('oc_last_ev_new'),
                    'status':      p.get('status', 'open'),
                    'actual_pnl':  p.get('actual_pnl'),
                    'strategy':    p.get('strategy', 'options_v2'),
                    'signal_type': p.get('signal_type', ''),
                    'alpha_score': p.get('alpha_score', p.get('ev_at_entry', 0)),
                }
                plans.append(p)
            except Exception:
                pass

    open_p  = [p for p in plans if p.get('status') == 'open']
    closed  = [p for p in plans if p.get('status') != 'open']
    targets_hit = sum(1 for p in closed if p.get('status') == 'target_hit')
    stops_hit   = sum(1 for p in closed if p.get('status') == 'stop_hit')
    switched    = sum(1 for p in closed if p.get('status') in ('switched', 'oc_switch'))
    total_pnl   = sum(p.get('actual_pnl', 0) or 0 for p in closed)

    return {
        'plans': open_p + closed[-10:],
        'summary': {
            'open':        len(open_p),
            'closed':      len(closed),
            'targets_hit': targets_hit,
            'stops_hit':   stops_hit,
            'switched':    switched,
            'total_pnl':   round(total_pnl, 2),
            'target_rate': '%d/%d' % (targets_hit, max(1, len(closed))),
        }
    }


@app.route('/api/rl_threshold')
def api_rl_threshold():
    """ThresholdLearner bandit state — learned optimal score thresholds per regime."""
    from pathlib import Path as _P
    import json as _j
    bandit_file = _P('/home/jonathangan/shared/stockbot/strategy_v2/evaluation/threshold_bandit.json')
    live_cfg    = _P('/home/jonathangan/shared/stockbot/strategy_v2/evaluation/live_config.json')
    out = {'regimes': {}, 'active_threshold': None, 'active_regime': None}
    try:
        if bandit_file.exists():
            state = _j.loads(bandit_file.read_text())
            for regime, buckets in state.items():
                out['regimes'][regime] = {}
                for threshold, data in buckets.items():
                    a, b   = data.get('alpha', 1), data.get('beta', 1)
                    trades = data.get('trades', 0)
                    out['regimes'][regime][threshold] = {
                        'win_rate':  round(a / (a + b), 3),
                        'trades':    trades,
                        'total_pnl': round(data.get('total_pnl', 0), 4),
                        'alpha':     a,
                        'beta':      b,
                        'last_selected': data.get('last_selected'),
                    }
        if live_cfg.exists():
            cfg = _j.loads(live_cfg.read_text())
            out['active_threshold'] = cfg.get('min_score_threshold')
            out['active_regime']    = cfg.get('rl_threshold_regime')
            out['threshold_updated_at'] = cfg.get('rl_threshold_updated_at')
    except Exception as e:
        out['error'] = str(e)
    return jsonify(out)

@app.route('/api/gex')
def api_gex():
    """Return latest GEX profiles for all scanned symbols."""
    gex_file = Path('/home/jonathangan/shared/options_v1/data/gex_cache.json')
    if not gex_file.exists():
        return jsonify({'gex': {}, 'summary': 'No GEX data yet'})
    try:
        data = json.loads(gex_file.read_text())
        squeeze_active = [s for s, d in data.items() if d.get('squeeze_active')]
        regimes = {}
        for s, d in data.items():
            r = d.get('regime', 'neutral')
            regimes[r] = regimes.get(r, 0) + 1
        return jsonify({
            'gex': data,
            'summary': {
                'squeeze_active': squeeze_active,
                'regimes': regimes,
                'total_symbols': len(data),
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)})


if __name__ == '__main__':
    print("=" * 60)
    print("SEND IT TRADING DASHBOARD")
    print("=" * 60)
    print(f"Starting on http://0.0.0.0:5555")
    print(f"Strategy dir: {STRATEGY_DIR}")
    print(f"Alpaca connected: {ALPACA_AVAILABLE}")
    print("=" * 60)

    app.run(host='0.0.0.0', port=5555, debug=False, threaded=True)
