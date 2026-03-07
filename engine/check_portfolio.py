#!/usr/bin/env python3
"""
Quick Portfolio Health Check
Run this script anytime to get instant portfolio status
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path
import sys
import logging

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import alpaca_env
alpaca_env.bootstrap()

from core.config import load_config
from portfolio_optimizer import PortfolioOptimizer
from execution_gate import ExecutionGate

logger = logging.getLogger(__name__)
logging.basicConfig(level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper()))


def get_alpaca_positions():
    """Fetch current positions from Alpaca API."""
    config = load_config()
    acct = config.get("account", {})
    api_key = acct.get("alpaca_api_key") or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_LIVE_KEY")
    api_secret = acct.get("alpaca_secret_key") or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret
    }
    
    # Get account info
    base_url = acct.get("alpaca_base_url", "https://paper-api.alpaca.markets")
    account_url = f"{base_url}/v2/account"
    account_resp = requests.get(account_url, headers=headers, timeout=10)
    account = account_resp.json()
    
    # Get positions
    positions_url = f"{base_url}/v2/positions"
    positions_resp = requests.get(positions_url, headers=headers, timeout=10)
    positions_data = positions_resp.json()
    
    portfolio_value = float(account['portfolio_value'])
    cash = float(account['cash'])
    
    # Format positions for optimizer
    positions = []
    for pos in positions_data:
        positions.append({
            "symbol": pos['symbol'],
            "qty": float(pos['qty']),
            "market_value": float(pos['market_value']),
            "cost_basis": float(pos['cost_basis']),
            "unrealized_pl_pct": float(pos['unrealized_plpc']),
            "sector": pos.get('asset_class', 'unknown'),
            "entry_date": pos.get('created_at', datetime.utcnow().isoformat()),
            "avg_daily_volume": 0  # Would need separate API call
        })
    
    return {
        "positions": positions,
        "portfolio_value": portfolio_value,
        "cash": cash,
        "starting_value": portfolio_value  # Assume no change today for quick check
    }


def main():
    logger.info("="*70)
    logger.info("🏦 PI HEDGE FUND - PORTFOLIO HEALTH CHECK")
    logger.info(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*70)
    
    try:
        # Fetch live data from Alpaca
        logger.info("\n📡 Fetching portfolio data from Alpaca...")
        data = get_alpaca_positions()
        
        positions = data['positions']
        portfolio_value = data['portfolio_value']
        cash = data['cash']
        
        logger.info(f"✓ Loaded {len(positions)} positions")
        
    except Exception as e:
        logger.warning(f"⚠️  Could not fetch live data: {e}")
        logger.info("   Using demo data instead...")
        
        # Fallback to demo data
        from datetime import timedelta
        positions = [
            {
                "symbol": "GME",
                "qty": 10,
                "market_value": 293.00,
                "cost_basis": 350.00,
                "unrealized_pl_pct": -0.163,
                "sector": "Consumer Cyclical",
                "entry_date": (datetime.utcnow() - timedelta(days=45)).isoformat(),
                "avg_daily_volume": 5000000
            }
        ]
        portfolio_value = 366.00
        cash = 33.00
    
    # Initialize optimizer and gate
    optimizer = PortfolioOptimizer()
    gate = ExecutionGate()
    
    # Generate report
    logger.info("\n🔍 Analyzing portfolio...")
    report = optimizer.generate_portfolio_report(positions, portfolio_value)
    gate_status = gate.get_gate_status()
    
    # Display results
    logger.info(f"\n{'='*70}")
    logger.info("💰 PORTFOLIO SUMMARY")
    logger.info('='*70)
    
    logger.info(f"\nTotal Value:  ${portfolio_value:,.2f}")
    logger.info(f"Cash:         ${cash:,.2f} ({cash/portfolio_value*100:.1f}%)")
    logger.info(f"Invested:     ${portfolio_value - cash:,.2f} ({(portfolio_value-cash)/portfolio_value*100:.1f}%)")
    logger.info(f"Positions:    {len(positions)}")
    
    logger.info(f"\n{'='*70}")
    logger.info("📊 HEALTH SCORE")
    logger.info('='*70)
    
    score = report['summary']['health_score']
    
    if score >= 80:
        grade = "🟢 EXCELLENT"
    elif score >= 70:
        grade = "🟡 GOOD"
    elif score >= 60:
        grade = "🟠 FAIR"
    else:
        grade = "🔴 POOR"
    
    logger.info(f"\nOverall Health: {score:.1f}/100 {grade}")
    
    logger.info(f"\n{'='*70}")
    logger.info("📋 POSITIONS")
    logger.info('='*70)
    
    for pos in sorted(positions, key=lambda p: p['market_value'], reverse=True):
        pct = pos['market_value'] / portfolio_value * 100
        pl_str = f"{pos['unrealized_pl_pct']*100:+.1f}%"
        
        status = "✓" if pct <= 20 else "⚠️"
        logger.info(f"\n{status} {pos['symbol']:<6} ${pos['market_value']:>8,.2f} ({pct:>5.1f}%) | P/L: {pl_str:>7}")
    
    if report['checks']['rebalancing']:
        logger.info(f"\n{'='*70}")
        logger.info("⚠️  REBALANCING NEEDED")
        logger.info('='*70)
        
        for action in report['checks']['rebalancing']:
            logger.info(f"\n• {action['action'].upper()}: {action['symbol']}")
            logger.info(f"  Reason: {action['reason']}")
            if 'trim_amount' in action:
                logger.info(f"  Amount: ${action['trim_amount']:.2f}")
            if 'amount' in action:
                logger.info(f"  Amount: ${action['amount']:.2f}")
    
    if report['checks']['zombies']:
        logger.info(f"\n{'='*70}")
        logger.info("💀 ZOMBIE POSITIONS")
        logger.info('='*70)
        
        for zombie in report['checks']['zombies']:
            logger.info(f"\n• {zombie['symbol']}: {zombie['reason']}")
            logger.info(f"  Value: ${zombie['market_value']:.2f}")
            logger.info(f"  Action: {zombie['action'].upper()}")
    
    if report['checks']['tax_loss_harvest']:
        logger.info(f"\n{'='*70}")
        logger.info("💸 TAX LOSS HARVEST OPPORTUNITIES")
        logger.info('='*70)
        
        for harvest in report['checks']['tax_loss_harvest']:
            logger.info(f"\n• {harvest['symbol']}: {harvest['reason']}")
            logger.info(f"  Loss: ${abs(harvest['loss_amount']):.2f}")
            logger.info(f"  Priority: {harvest['priority'].upper()}")
    
    if report['checks']['correlation']:
        logger.info(f"\n{'='*70}")
        logger.info("🔗 CORRELATION WARNINGS")
        logger.info('='*70)
        
        for warning in report['checks']['correlation']:
            logger.info(f"\n• {warning['symbol_a']} <-> {warning['symbol_b']}")
            logger.info(f"  Correlation: {warning['correlation']:.2f}")
            logger.info(f"  {warning['recommendation']}")
    
    if 'portfolio_return' in report['checks']['benchmark']:
        bench = report['checks']['benchmark']
        
        logger.info(f"\n{'='*70}")
        logger.info("📈 BENCHMARK COMPARISON")
        logger.info('='*70)
        
        logger.info(f"\nPortfolio Return:  {bench['portfolio_return']*100:+.2f}%")
        logger.info(f"SPY Return:        {bench['spy_return']*100:+.2f}%")
        logger.info(f"Outperformance:    {bench['outperformance']*100:+.2f}%")
        
        if bench.get('risk_adjustment'):
            adj = bench['risk_adjustment']
            logger.info(f"\nRisk Adjustment: {adj['action'].upper()}")
            logger.info(f"Reason: {adj['reason']}")
    
    logger.info(f"\n{'='*70}")
    logger.info("🚦 EXECUTION GATE STATUS")
    logger.info('='*70)
    
    status_icon = "🟢 OPEN" if gate_status['gates_open'] else "🔴 CLOSED"
    logger.info(f"\nGate Status:        {status_icon}")
    logger.info(f"RL Mode:            {gate_status['rl_mode']}")
    logger.info(f"RL Multiplier:      {gate_status['rl_confidence_multiplier']:.2f}x")
    logger.info(f"Trades Today:       {gate_status['trades_today']}")
    logger.info(f"Consecutive Losses: {gate_status['circuit_breakers']['consecutive_losses']}")
    
    logger.info(f"\n{'='*70}")
    logger.info("💡 TOP RECOMMENDATIONS")
    logger.info('='*70)
    
    recommendations = []
    
    # Check for concentration
    for pos in positions:
        pct = pos['market_value'] / portfolio_value * 100
        if pct > 25:
            recommendations.append(
                f"🔴 URGENT: Trim {pos['symbol']} from {pct:.1f}% to 20% "
                f"(sell ${pos['market_value'] - portfolio_value*0.20:.2f})"
            )
    
    # Check cash reserve
    cash_pct = cash / portfolio_value
    if cash_pct < 0.10:
        needed = portfolio_value * 0.10 - cash
        recommendations.append(
            f"🟠 Raise cash reserve from {cash_pct*100:.1f}% to 10% "
            f"(liquidate ${needed:.2f})"
        )
    
    # Check position count
    if len(positions) < 5:
        recommendations.append(
            "🟡 Add more positions for diversification (target: 10-15)"
        )
    elif len(positions) > 20:
        recommendations.append(
            "🟡 Too many positions - consider consolidating best performers"
        )
    
    # Check zombies
    if report['checks']['zombies']:
        recommendations.append(
            f"🔴 Liquidate {len(report['checks']['zombies'])} zombie position(s)"
        )
    
    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            logger.info(f"\n{i}. {rec}")
    else:
        logger.info("\n✅ Portfolio looks healthy! No immediate actions needed.")
    
    logger.info(f"\n{'='*70}")
    logger.info(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*70}\n")


if __name__ == "__main__":
    main()
