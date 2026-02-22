#!/usr/bin/env python3
"""
Live Web Dashboard API
Serves real-time portfolio data, convictions, and logs via HTTP
"""
from flask import Flask, render_template, jsonify, send_from_directory
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
sys.path.insert(0, str(STRATEGY_DIR))

from core.alpaca_client import AlpacaClient

# Initialize clients
try:
    alpaca_client = AlpacaClient()
    ALPACA_AVAILABLE = True
except Exception as e:
    print(f"⚠️  Alpaca client unavailable: {e}")
    ALPACA_AVAILABLE = False


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
        
        # Calculate total P/L
        total_pl = sum(float(p.unrealized_pl) for p in positions)
        total_pl_pct = (total_pl / float(account.portfolio_value)) * 100 if float(account.portfolio_value) > 0 else 0
        
        return jsonify({
            'portfolio_value': float(account.portfolio_value),
            'cash': float(account.cash),
            'buying_power': float(account.buying_power),
            'equity': float(account.equity),
            'total_pl': total_pl,
            'total_pl_pct': total_pl_pct,
            'positions': [
                {
                    'symbol': p.symbol,
                    'qty': float(p.qty),
                    'market_value': float(p.market_value),
                    'unrealized_pl': float(p.unrealized_pl),
                    'unrealized_plpc': float(p.unrealized_plpc),
                    'current_price': float(p.current_price),
                    'avg_entry_price': float(p.avg_entry_price),
                    'cost_basis': float(p.cost_basis)
                }
                for p in positions
            ],
            'position_count': len(positions),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/convictions')
def api_convictions():
    """Active conviction positions"""
    try:
        convictions_file = STRATEGY_DIR / 'state/convictions.json'
        if convictions_file.exists():
            with open(convictions_file, 'r') as f:
                data = json.load(f)
                return jsonify(data)
        return jsonify({'convictions': {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
def api_logs():
    """Recent orchestrator logs"""
    try:
        log_file = STRATEGY_DIR / 'logs/orchestrator.log'
        if log_file.exists():
            lines = log_file.read_text().split('\n')
            # Get last 100 lines, filter out empty
            recent_lines = [line for line in lines[-100:] if line.strip()]
            return jsonify({'logs': recent_lines})
        return jsonify({'logs': ['No logs yet']})
    except Exception as e:
        return jsonify({'error': str(e), 'logs': []})


@app.route('/api/status')
def api_status():
    """Bot status and stats"""
    try:
        # Check if bot is running
        import subprocess
        result = subprocess.run(['systemctl', 'is-active', 'mybot'], 
                              capture_output=True, text=True)
        service_active = result.stdout.strip() == 'active'
        
        # Check log file age
        log_file = STRATEGY_DIR / 'logs/orchestrator.log'
        if log_file.exists():
            log_age_seconds = (datetime.now().timestamp() - log_file.stat().st_mtime)
            log_age_minutes = int(log_age_seconds / 60)
        else:
            log_age_minutes = -1
        
        return jsonify({
            'service_running': service_active,
            'log_age_minutes': log_age_minutes,
            'last_activity': 'Active' if log_age_minutes < 5 else f'{log_age_minutes} min ago',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/decisions/recent')
def api_recent_decisions():
    """Recent decision log entries"""
    try:
        decisions_dir = STRATEGY_DIR / 'logs/decisions'
        if not decisions_dir.exists():
            return jsonify({'decisions': []})
        
        # Get today's decision log
        today = datetime.now().strftime('%Y-%m-%d')
        log_file = decisions_dir / f'{today}.jsonl'
        
        if not log_file.exists():
            return jsonify({'decisions': []})
        
        # Read last 10 decisions
        decisions = []
        with open(log_file, 'r') as f:
            lines = f.readlines()
            for line in lines[-10:]:
                if line.strip():
                    try:
                        decisions.append(json.loads(line))
                    except:
                        pass
        
        return jsonify({'decisions': decisions})
    except Exception as e:
        return jsonify({'error': str(e), 'decisions': []})


if __name__ == '__main__':
    print("=" * 60)
    print("🎯 SEND IT TRADING DASHBOARD")
    print("=" * 60)
    print(f"Starting on http://0.0.0.0:5555")
    print(f"Strategy dir: {STRATEGY_DIR}")
    print(f"Alpaca connected: {ALPACA_AVAILABLE}")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=5555, debug=False, threaded=True)
