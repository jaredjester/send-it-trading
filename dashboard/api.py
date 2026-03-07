#!/usr/bin/env python3
"""
Send It Trading — Live Dashboard API
Serves real-time portfolio, plans, trades, signals, and RL state via HTTP + SSE.

All paths are relative to BASE_DIR (this file's parent) or configurable via .env.
Clone → configure .env → pip install → python dashboard_api.py
"""
from flask import Flask, render_template, jsonify, Response, stream_with_context
from flask_cors import CORS
import sys
import time
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from shlex import split as shlex_split

# ─── Path Setup ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / '.env')

sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(REPO_DIR / 'engine'))

import alpaca_env
alpaca_env.bootstrap()

# All data paths relative to BASE_DIR, overridable via env
DATA_DIR  = Path(os.getenv('DATA_DIR',  str(REPO_DIR / 'data')))
STATE_DIR = Path(os.getenv('STATE_DIR', str(REPO_DIR / 'state')))
LOG_DIR   = Path(os.getenv('LOG_DIR',   str(REPO_DIR / 'logs')))
EVAL_DIR  = Path(os.getenv('EVAL_DIR',  str(REPO_DIR / 'engine' / 'evaluation')))

# Ensure directories exist
for d in [DATA_DIR, STATE_DIR, LOG_DIR, EVAL_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Configurable
PORT         = int(os.getenv('DASHBOARD_PORT', '5555'))
BOT_SERVICE  = os.getenv('BOT_SERVICE', 'mybot')
PAPER        = os.getenv('ALPACA_PAPER', '').lower() in ('1', 'true', 'yes')
COMPOSE_PROJECT = os.getenv('COMPOSE_PROJECT_NAME', 'send-it-trading')
MONITORED_SERVICES = [s.strip() for s in os.getenv('DASHBOARD_SERVICES', 'send-it-bot,send-it-engine,send-it-dashboard').split(',') if s.strip()]
DEFAULT_LOG_LINES = int(os.getenv('DASHBOARD_LOG_LINES', '60'))

# ─── Data File Paths (all relative) ─────────────────────────────────────────
NEWS_INTEL    = DATA_DIR / 'news_intel.json'
INSIDER_INTEL = DATA_DIR / 'insider_intel.json'
SENTIMENT     = DATA_DIR / 'sentiment_cache.json'
RL_WEIGHTS    = DATA_DIR / 'rl_weights.json'
POLY_INTEL    = DATA_DIR / 'polymarket_intel.json'
POLY_LEDGER   = DATA_DIR / 'poly_ledger.jsonl'
GEX_CACHE     = DATA_DIR / 'gex_cache.json'
TRADE_LOG     = LOG_DIR  / 'trading.log'
PLANS_FILE    = STATE_DIR / 'options_plans.jsonl'
TRADES_FILE   = STATE_DIR / 'trade_memory.jsonl'
BATTLE_PLAN   = STATE_DIR / 'market_open_plan.json'
BANDIT_FILE   = EVAL_DIR / 'threshold_bandit.json'
LIVE_CFG_FILE = EVAL_DIR / 'live_config.json'

# ─── Flask App ───────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'))
CORS(app)


def _run_cmd(cmd, timeout=5):
    """Run shell command safely and return stdout (str)."""
    try:
        if isinstance(cmd, str):
            cmd = shlex_split(cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
        app.logger.warning("Command %s failed (%s): %s", cmd, result.returncode, result.stderr.strip())
    except Exception as exc:
        app.logger.warning("Command %s raised %s", cmd, exc)
    return ""

# ─── Alpaca Client ───────────────────────────────────────────────────────────
from core.alpaca_client import AlpacaClient

try:
    alpaca_client = AlpacaClient(base_url=os.getenv('ALPACA_BASE_URL'))
    ALPACA_AVAILABLE = True
except Exception as e:
    print(f"⚠️  Alpaca client unavailable: {e}")
    ALPACA_AVAILABLE = False


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _read_json(path):
    """Safely read a JSON file, return {} on any error."""
    try:
        if path.exists():
            return json.loads(path.read_text(errors='replace'))
    except Exception as e:
        app.logger.debug("_read_json(%s) failed: %s", path, e)
    return {}


def _read_jsonl(path):
    """Read a JSONL file, return list of dicts."""
    items = []
    try:
        if path.exists():
            for line in path.read_text(errors='replace').splitlines():
                if line.strip():
                    try:
                        items.append(json.loads(line))
                    except Exception as e:
                        app.logger.debug("_read_jsonl line parse failed (%s): %s", path, e)
    except Exception as e:
        app.logger.debug("_read_jsonl(%s) failed: %s", path, e)
    return items


def _decode_option_symbol(symbol):
    """Decode OCC option symbol e.g. LWLG260320C00005000 -> dict."""
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
    except Exception as e:
        app.logger.debug("_decode_option_symbol(%s) failed: %s", symbol, e)
    return None


def _enrich_position(p):
    """Add option decoding + clean fields to a position dict."""
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
        base.update({'_option': True, **opt})
    else:
        base['_option'] = False
    return base


def _service_running(name):
    """Check if a systemd service is active."""
    try:
        r = subprocess.run(['systemctl', 'is-active', name],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == 'active'
    except Exception:
        return False




def _systemd_status(service: str) -> dict:
    props = ["ActiveState", "SubState", "Result", "MainPID", "ExecMainStatus"]
    raw = _run_cmd([
        "systemctl", "show", service, "--no-page",
        "--property=" + ",".join(props)
    ])
    data = {p: None for p in props}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v
    logs = _run_cmd([
        "journalctl", "-u", service, "-n", str(DEFAULT_LOG_LINES), "--no-pager"
    ])
    return {
        "service": service,
        "active_state": data.get("ActiveState"),
        "sub_state": data.get("SubState"),
        "result": data.get("Result"),
        "pid": data.get("MainPID"),
        "last_exit": data.get("ExecMainStatus"),
        "logs": logs.splitlines() if logs else []
    }


def _docker_stats_map() -> dict:
    stats_raw = _run_cmd([
        "docker", "stats", "--no-stream",
        "--format", "{{.Name}}||{{.CPUPerc}}||{{.MemUsage}}||{{.NetIO}}||{{.BlockIO}}"
    ], timeout=8)
    stats = {}
    for line in stats_raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("||")
        if len(parts) >= 5:
            stats[parts[0]] = {
                "cpu": parts[1],
                "memory": parts[2],
                "net_io": parts[3],
                "block_io": parts[4],
            }
    return stats


def _docker_env(name: str) -> dict:
    raw = _run_cmd(["docker", "inspect", "-f", "{{json .Config.Env}}", name])
    if not raw:
        return {}
    try:
        env_list = json.loads(raw)
        env_map = {}
        for item in env_list:
            if "=" in item:
                k, v = item.split("=", 1)
                env_map[k] = v
        return env_map
    except Exception:
        return {}


def _docker_logs(name: str, lines: int = DEFAULT_LOG_LINES) -> list:
    logs = _run_cmd([
        "docker", "logs", name, "--tail", str(lines)
    ], timeout=8)
    return logs.splitlines() if logs else []


def _docker_containers() -> list:
    ps_raw = _run_cmd([
        "docker", "ps", "-a",
        "--format", "{{.Names}}||{{.ID}}||{{.Image}}||{{.Status}}||{{.RunningFor}}"
    ])
    stats = _docker_stats_map()
    containers = []
    for line in ps_raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("||")
        if len(parts) < 5:
            continue
        name, cid, image, status, running_for = parts[:5]
        if COMPOSE_PROJECT and not name.startswith(COMPOSE_PROJECT):
            continue
        env = _docker_env(name)
        stat = stats.get(name, {})
        containers.append({
            "name": name,
            "id": cid,
            "image": image,
            "status": status,
            "uptime": running_for,
            "cpu": stat.get("cpu"),
            "memory": stat.get("memory"),
            "net_io": stat.get("net_io"),
            "block_io": stat.get("block_io"),
            "alpaca_mode": env.get("ALPACA_MODE"),
            "worker_id": env.get("WORKER_ID"),
            "eval_dir": env.get("EVAL_DIR"),
        })
    return containers


def _service_logs(service: str, lines: int = DEFAULT_LOG_LINES) -> list:
    logs = _run_cmd([
        "journalctl", "-u", service, "-n", str(lines), "--no-pager"
    ])
    return logs.splitlines() if logs else []
def _log_last_line(log_path, skip_separators=True):
    """Get last meaningful line from a log file."""
    if not log_path.exists():
        return 'No log file', -1
    try:
        age_min = int((time.time() - log_path.stat().st_mtime) / 60)
        lines = log_path.read_text(errors='replace').splitlines()
        if skip_separators:
            for line in reversed(lines[-50:]):
                s = line.strip()
                if s and not all(c in '=- ' for c in s):
                    return s[:120], age_min
        elif lines:
            return lines[-1].strip()[:120], age_min
        return '', age_min
    except Exception:
        return 'Error reading log', -1


def _build_plan_display(p):
    """Build display dict for a plan (works with both v1 and v2 formats)."""
    ep = p.get('entry_price', 0) or 0
    tp = p.get('target_price', 0) or 0
    sp = p.get('stop_price', 0) or 0
    try:
        td_str = p.get('target_date') or ''
        if td_str:
            dl = datetime.fromisoformat(td_str.replace('Z', '+00:00'))
            days_left = max(0, (dl - datetime.now(timezone.utc)).days)
        else:
            days_left = '?'
    except Exception:
        days_left = '?'
    return {
        'symbol':      p.get('symbol', '?'),
        'occ_symbol':  p.get('occ_symbol', ''),
        'direction':   (p.get('direction') or '?').upper(),
        'contracts':   p.get('contracts', 1),
        'entry':       f'${ep:.2f}',
        'target':      f'${tp:.2f}',
        'stop':        f'${sp:.2f}',
        'rr':          f"{p.get('risk_reward', 0):.1f}:1",
        'deadline':    (p.get('target_date') or '?')[:10],
        'days_left':   days_left,
        'thesis':      (p.get('entry_thesis') or p.get('thesis') or '')[:120],
        'max_loss':    f"${abs(p.get('max_loss_dollars', 0) or 0):.0f}",
        'target_gain': f"${p.get('target_gain_dollars', 0) or 0:.0f}",
        'status':      p.get('status', 'open'),
        'actual_pnl':  p.get('actual_pnl'),
        'exit_reason': p.get('exit_reason', ''),
        'strategy':    p.get('strategy', 'options_v2'),
        'signal_type': p.get('signal_type', ''),
        'alpha_score': p.get('alpha_score', p.get('ev_at_entry', 0)),
        'oc_checks':   p.get('oc_checks', 0),
        'oc_ev_hold':  p.get('oc_last_ev_hold'),
        'oc_ev_new':   p.get('oc_last_ev_new'),
        'ev':          round(p.get('ev_at_entry', 0) or 0, 2),
    }


def _build_trade_display(t):
    """Build display dict for a trade record."""
    sym      = t.get('symbol', '')
    kind     = t.get('kind') or t.get('direction', '')
    entry_ts = t.get('timestamp') or t.get('entry_ts', '')
    outcome  = t.get('outcome') or t.get('status', 'open')
    pnl_raw  = t.get('pnl') if t.get('pnl') is not None else t.get('actual_pnl')
    return {
        'symbol':    sym,
        'occ':       t.get('occ_symbol', ''),
        'kind':      kind.upper() if kind else '—',
        'strike':    t.get('strike') or t.get('strike_price') or 0,
        'contracts': t.get('contracts', 1),
        'entry':     round(t.get('entry_price', 0), 2),
        'exit':      round(t.get('exit_price', 0), 2) if t.get('exit_price') else None,
        'pnl':       round(pnl_raw, 2) if pnl_raw is not None else None,
        'ev':        round(t.get('ev_at_entry', 0) or t.get('alpha_score', 0), 2),
        'outcome':   outcome,
        'strategy':  t.get('strategy', 'options_v2'),
        'signal':    t.get('signal_type', ''),
        'thesis':    (t.get('entry_thesis') or t.get('thesis') or '')[:80],
        'ts':        entry_ts[:16] if entry_ts else '',
    }


# ─── Shared Data Loaders ────────────────────────────────────────────────────
def _load_plans():
    """Load all trade plans with display dicts and summary stats. Deduplicates by plan_id."""
    # Engine plans (strategy_v2 options-first)
    plans = _read_jsonl(PLANS_FILE)
    seen_ids = {p.get('plan_id') for p in plans if p.get('plan_id')}
    # Bot plans (options_v1 DCVX) — skip any already present in engine file
    for p in _read_jsonl(DATA_DIR / 'trade_plans.jsonl'):
        pid = p.get('plan_id')
        if pid and pid in seen_ids:
            continue
        p.setdefault('strategy', 'options_v1_dcvx')
        plans.append(p)
        if pid:
            seen_ids.add(pid)
    for p in plans:
        p['display'] = _build_plan_display(p)

    open_p  = [p for p in plans if p.get('status') == 'open']
    closed  = [p for p in plans if p.get('status') != 'open']
    targets = sum(1 for p in closed if p.get('status') == 'target_hit')
    stops   = sum(1 for p in closed if p.get('status') == 'stop_hit')
    switched = sum(1 for p in closed if p.get('status') in ('switched', 'oc_switch'))
    pnls    = [p.get('actual_pnl', 0) or 0 for p in closed]
    avg_pnl = sum(pnls) / max(1, len(pnls))

    return {
        'plans': open_p + closed[-10:],
        'summary': {
            'open':            len(open_p),
            'closed':          len(closed),
            'target_hit_rate': f"{targets}/{max(1, len(closed))}",
            'stop_rate':       f"{stops}/{max(1, len(closed))}",
            'switched':        switched,
            'avg_pnl':         f"${avg_pnl:.2f}",
        }
    }


def _load_trades():
    """Load trade history from plans file (closed trades + open)."""
    # Plans file doubles as trade log — closed plans = completed trades
    plans = _read_jsonl(PLANS_FILE)
    # Also check dedicated trade memory if it exists
    trades = _read_jsonl(TRADES_FILE)
    # Merge, deduplicate by plan_id/trade_id
    seen_ids = set()
    merged = []
    for t in (trades + plans):
        tid = t.get('plan_id') or t.get('trade_id') or t.get('occ_symbol', '')
        if tid and tid in seen_ids:
            continue
        if tid:
            seen_ids.add(tid)
        t['display'] = _build_trade_display(t)
        merged.append(t)
    # Sort by entry timestamp, newest first
    merged.sort(key=lambda x: x.get('entry_ts') or x.get('timestamp') or '', reverse=True)
    return {'trades': merged[:50], 'total': len(merged)}


def _load_intel():
    """Load market intelligence (news, insider, RL weights, VIX)."""
    out = {'vix': {}, 'market_proxy': None, 'symbols': {}, 'rl': {}}
    news = _read_json(NEWS_INTEL)
    if news:
        out['vix']          = news.get('vix', {})
        out['market_proxy'] = news.get('market_proxy')
        out['symbols']      = news.get('symbols', {})
        out['scanned_at']   = news.get('scanned_at')

    rl = _read_json(RL_WEIGHTS)
    if rl:
        def g(k): return rl.get(k, {}).get('DCVX', 0)
        out['rl'] = {
            'n_trades':     g('n_trades'),
            'win_rate':     g('win_rate') or 0.5,
            'kelly_scale':  g('kelly_scale') or 1.0,
            'total_pnl':    g('total_pnl'),
            'ev_threshold': g('ev_threshold'),
        }
        out['rl_ic']         = rl.get('signal_ic', {})
        out['ic_obs_counts'] = {k: len(v) for k, v in rl.get('ic_obs', {}).items()}
    return out


def _load_signals():
    """Load merged signal intelligence: news + insider + RL IC + polymarket + CA sentiment."""
    news_data    = _read_json(NEWS_INTEL).get('symbols', {})
    insider_data = _read_json(INSIDER_INTEL).get('data', {})
    rl           = _read_json(RL_WEIGHTS)
    rl_ic        = rl.get('signal_ic', {})
    ic_obs       = {k: len(v) for k, v in rl.get('ic_obs', {}).items()}
    ca_data      = _read_json(SENTIMENT)

    syms = {}
    for sym in sorted(set(list(news_data.keys()) + list(insider_data.keys()))):
        ni = news_data.get(sym, {})
        ii = insider_data.get(sym, {})
        entry = {
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
        # CA sentiment (corporate actions / StockTwits)
        if ca_data:
            ca_entry = ca_data.get(sym, {})
            if isinstance(ca_entry, dict):
                entry['ca'] = {
                    'score':  ca_entry.get('score', 0.0),
                    'label':  ca_entry.get('label', 'neutral'),
                    'events': ca_entry.get('events', []),
                }
        syms[sym] = entry

    out = {'symbols': syms, 'rl_ic': rl_ic, 'ic_obs_counts': ic_obs}

    # Polymarket — always include so SSE stream and REST endpoint both have it
    poly = _load_polymarket()
    if poly:
        out['polymarket'] = poly

    return out


def _load_polymarket():
    """Load Polymarket intelligence + calibration."""
    pm = _read_json(POLY_INTEL)
    if not pm:
        return {}
    calib = {}
    entries = _read_jsonl(POLY_LEDGER)
    resolved = [e for e in entries if e.get('ic_alignment') is not None]
    if resolved:
        aligns = [e['ic_alignment'] for e in resolved]
        calib = {
            'ic':              round(sum(aligns) / len(aligns), 4),
            'n_trades':        len(resolved),
            'directional_acc': round(sum(1 for a in aligns if a > 0) / len(aligns), 4),
        }
    return {
        'macro':       pm.get('macro', [])[:8],
        'by_symbol':   {s: v[:2] for s, v in pm.get('by_symbol', {}).items()},
        'scanned_at':  pm.get('scanned_at'),
        'relevant':    pm.get('relevant', 0),
        'calibration': calib,
    }


def _load_portfolio():
    """Load portfolio from Alpaca API."""
    if not ALPACA_AVAILABLE:
        return {'error': 'Alpaca client unavailable'}
    try:
        from alpaca.trading.client import TradingClient
        key    = os.getenv('ALPACA_API_KEY') or os.getenv('ALPACA_API_LIVE_KEY') or os.getenv('ALPACA_API_KEY_ID', '')
        secret = os.getenv('ALPACA_API_SECRET') or os.getenv('APCA_API_SECRET_KEY', '')
        tc = TradingClient(key, secret, paper=PAPER)
        acct = tc.get_account()
        positions = tc.get_all_positions()
        pos_list = []
        for p in positions:
            entry = {
                'symbol':          p.symbol,
                'qty':             float(p.qty),
                'market_value':    float(p.market_value or 0),
                'avg_entry_price': float(p.avg_entry_price or 0),
                'current_price':   float(p.current_price or 0),
                'unrealized_pl':   float(p.unrealized_pl or 0),
                'unrealized_plpc': float(p.unrealized_plpc or 0),
            }
            opt = _decode_option_symbol(p.symbol)
            if opt:
                entry.update({'_option': True, **opt})
            else:
                entry['_option'] = False
            pos_list.append(entry)

        equity = float(acct.equity or 0)
        cash   = float(acct.cash or 0)
        opt_pos = [p for p in pos_list if p.get('_option')]
        eq_pos  = [p for p in pos_list if not p.get('_option')]
        total_pl = sum(p['unrealized_pl'] for p in pos_list)

        return {
            'portfolio_value':      equity,
            'equity':               equity,
            'cash':                 cash,
            'buying_power':         float(acct.buying_power or 0),
            'options_buying_power': float(acct.options_buying_power or 0),
            'total_pl':             total_pl,
            'total_pl_pct':         (total_pl / equity * 100) if equity > 0 else 0,
            'options_pl':           sum(p['unrealized_pl'] for p in opt_pos),
            'equity_pl':            sum(p['unrealized_pl'] for p in eq_pos),
            'options_count':        len(opt_pos),
            'equity_count':         len(eq_pos),
            'position_count':       len(pos_list),
            'positions':            pos_list,
            'timestamp':            datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {'error': str(e)}


def _load_status():
    """Bot service status + scan stats."""
    last_line, age_min = _log_last_line(TRADE_LOG)
    running = _service_running(BOT_SERVICE)

    # V2 scan stats from battle plan
    v2_scan = {}
    bp = _read_json(BATTLE_PLAN)
    if bp:
        candidates = bp.get('candidates', [])
        v2_scan['candidates'] = len(candidates)
        v2_scan['built_at']   = (bp.get('built_at') or '')[:16]
        top = sorted(candidates, key=lambda x: x.get('score', 0), reverse=True)[:3]
        v2_scan['top_picks']  = [{'symbol': c.get('symbol'), 'score': round(c.get('score', 0), 1)} for c in top]

    lc = _read_json(LIVE_CFG_FILE)
    if lc:
        v2_scan['threshold'] = lc.get('min_score_threshold')
        v2_scan['regime']    = lc.get('rl_threshold_regime')

    return {
        'service_running': running,
        'services': {
            BOT_SERVICE: {
                'running':     running,
                'log_age_min': age_min,
                'last_line':   last_line,
                'activity':    'Active' if 0 <= age_min < 5 else (f'{age_min}m ago' if age_min >= 0 else 'No log'),
            }
        },
        'log_age_minutes': age_min,
        'last_activity':   'Active' if 0 <= age_min < 5 else (f'{age_min}m ago' if age_min >= 0 else 'No log'),
        'v2_scan':         v2_scan,
        'timestamp':       datetime.now().isoformat(),
    }


def _load_rl_threshold():
    """ThresholdLearner bandit state."""
    out = {'regimes': {}, 'active_threshold': None, 'active_regime': None}
    state = _read_json(BANDIT_FILE)
    for regime, buckets in state.items():
        out['regimes'][regime] = {}
        for threshold, data in buckets.items():
            a, b = data.get('alpha', 1), data.get('beta', 1)
            out['regimes'][regime][threshold] = {
                'win_rate':      round(a / (a + b), 3),
                'trades':        data.get('trades', 0),
                'total_pnl':     round(data.get('total_pnl', 0), 4),
                'alpha':         a,
                'beta':          b,
                'last_selected': data.get('last_selected'),
            }
    lc = _read_json(LIVE_CFG_FILE)
    if lc:
        out['active_threshold']      = lc.get('min_score_threshold')
        out['active_regime']          = lc.get('rl_threshold_regime')
        out['threshold_updated_at']   = lc.get('rl_threshold_updated_at')
    return out


# ─── REST Endpoints ──────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('live_dashboard.html')


@app.route('/api/health')
def api_health():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'alpaca_connected': ALPACA_AVAILABLE,
        'base_dir': str(BASE_DIR),
        'data_dir': str(DATA_DIR),
    })


@app.route('/api/portfolio')
def api_portfolio():
    data = _load_portfolio()
    if 'error' in data and 'positions' not in data:
        return jsonify(data), 503
    return jsonify(data)


@app.route('/api/status')
def api_status():
    return jsonify(_load_status())


@app.route('/api/logs')
def api_logs():
    """Bot trading logs."""
    all_lines = []
    if TRADE_LOG.exists():
        lines = TRADE_LOG.read_text(errors='replace').splitlines()
        all_lines = [f"[BOT] {l.strip()}" for l in lines if l.strip()]
    return jsonify({'logs': all_lines[-200:], 'total': len(all_lines)})


@app.route('/api/intel')
def api_intel():
    return jsonify(_load_intel())


@app.route('/api/signals')
def api_signals():
    return jsonify(_load_signals())


@app.route('/api/plans')
def api_plans():
    return jsonify(_load_plans())


@app.route('/api/trades')
def api_trades():
    return jsonify(_load_trades())


@app.route('/api/positions')
def api_positions():
    """Dedicated positions endpoint — returns enriched list from Alpaca."""
    if not ALPACA_AVAILABLE:
        return jsonify({'error': 'Alpaca unavailable', 'positions': []})
    try:
        raw = alpaca_client.get_positions()
        positions = [_enrich_position(p) for p in raw]
        options = [p for p in positions if p.get('_option')]
        equities = [p for p in positions if not p.get('_option')]
        options_pl = sum(p.get('unrealized_pl', 0) for p in options)
        equity_pl  = sum(p.get('unrealized_pl', 0) for p in equities)
        return jsonify({
            'positions':    positions,
            'options':      options,
            'equities':     equities,
            'count':        len(positions),
            'options_count': len(options),
            'equity_count': len(equities),
            'options_pl':   round(options_pl, 4),
            'equity_pl':    round(equity_pl, 4),
            'total_pl':     round(options_pl + equity_pl, 4),
            'timestamp':    datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({'error': str(e), 'positions': []})


@app.route('/api/system/services')
def api_system_services():
    data = [_systemd_status(s) for s in MONITORED_SERVICES]
    return jsonify({"services": data})


@app.route('/api/system/containers')
def api_system_containers():
    return jsonify({"containers": _docker_containers()})


@app.route('/api/system/container/<name>/logs')
def api_container_logs(name):
    return jsonify({"name": name, "logs": _docker_logs(name)})


@app.route('/api/system/service/<name>/logs')
def api_service_logs(name):
    return jsonify({"service": name, "logs": _service_logs(name)})


@app.route('/api/rl_threshold')
def api_rl_threshold():
    return jsonify(_load_rl_threshold())


@app.route('/api/gex')
def api_gex():
    data = _read_json(GEX_CACHE)
    if not data:
        return jsonify({'gex': {}, 'summary': 'No GEX data yet'})
    squeeze = [s for s, d in data.items() if d.get('squeeze_active')]
    regimes = {}
    for s, d in data.items():
        r = d.get('regime', 'neutral')
        regimes[r] = regimes.get(r, 0) + 1
    return jsonify({
        'gex': data,
        'summary': {'squeeze_active': squeeze, 'regimes': regimes, 'total_symbols': len(data)}
    })


# ─── SSE Stream ─────────────────────────────────────────────────────────────
@app.route('/api/stream')
def api_stream():
    """Server-Sent Events — pushes live data to the dashboard."""

    def _event(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    def generate():
        last_portfolio    = 0.0
        last_status       = 0.0
        last_intel_mtime  = 0.0
        last_sig_mtime    = 0.0
        last_plans_mtime  = 0.0
        last_ping         = 0.0
        last_rl           = 0.0
        log_pos           = 0

        # Seed log to end of file
        if TRADE_LOG.exists():
            log_pos = TRADE_LOG.stat().st_size

        # Initial full push
        yield _event('portfolio',     _load_portfolio())
        yield _event('status',        _load_status())
        yield _event('intel',         _load_intel())
        yield _event('signals',       _load_signals())
        yield _event('plans',         _load_plans())
        yield _event('rl_threshold',  _load_rl_threshold())

        # Initial log lines
        if TRADE_LOG.exists():
            lines = TRADE_LOG.read_text(errors='replace').splitlines()[-120:]
            yield _event('log_init', {'lines': [l.strip() for l in lines if l.strip()]})

        while True:
            now = time.time()

            # Log: stream new lines
            try:
                if TRADE_LOG.exists():
                    size = TRADE_LOG.stat().st_size
                    if size > log_pos:
                        with open(TRADE_LOG, errors='replace') as fh:
                            fh.seek(log_pos)
                            chunk = fh.read()
                        log_pos = size
                        new_lines = [l.strip() for l in chunk.splitlines() if l.strip()]
                        if new_lines:
                            yield _event('log_lines', {'lines': new_lines})
            except Exception as e:
                app.logger.debug("SSE log_lines read failed: %s", e)

            # Portfolio: every 5s
            if now - last_portfolio >= 5:
                yield _event('portfolio', _load_portfolio())
                last_portfolio = now

            # Status: every 15s
            if now - last_status >= 15:
                yield _event('status', _load_status())
                last_status = now

            # RL threshold: every 30s
            if now - last_rl >= 30:
                yield _event('rl_threshold', _load_rl_threshold())
                last_rl = now

            # Intel: on file change
            try:
                if NEWS_INTEL.exists():
                    mt = NEWS_INTEL.stat().st_mtime
                    if mt != last_intel_mtime:
                        last_intel_mtime = mt
                        yield _event('intel', _load_intel())
            except Exception as e:
                app.logger.debug("SSE intel push failed: %s", e)

            # Signals: on file change
            try:
                sig_mt = 0.0
                for fp in (INSIDER_INTEL, RL_WEIGHTS):
                    if fp.exists():
                        sig_mt = max(sig_mt, fp.stat().st_mtime)
                if sig_mt > 0 and sig_mt != last_sig_mtime:
                    last_sig_mtime = sig_mt
                    yield _event('signals', _load_signals())
            except Exception as e:
                app.logger.debug("SSE signals push failed: %s", e)

            # Plans: on file change
            try:
                if PLANS_FILE.exists():
                    mt = PLANS_FILE.stat().st_mtime
                    if mt != last_plans_mtime:
                        last_plans_mtime = mt
                        yield _event('plans', _load_plans())
            except Exception as e:
                app.logger.debug("SSE plans push failed: %s", e)

            # Keepalive every 25s
            if now - last_ping >= 25:
                yield ": keepalive\n\n"
                last_ping = now

            time.sleep(0.8)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )


# ─── Main ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("  SEND IT TRADING — Dashboard")
    print("=" * 60)
    print(f"  Port:      {PORT}")
    print(f"  Base dir:  {BASE_DIR}")
    print(f"  Data dir:  {DATA_DIR}")
    print(f"  Log dir:   {LOG_DIR}")
    print(f"  State dir: {STATE_DIR}")
    print(f"  Alpaca:    {'✅ connected' if ALPACA_AVAILABLE else '❌ unavailable'}")
    print(f"  Paper:     {PAPER}")
    print(f"  Service:   {BOT_SERVICE}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
