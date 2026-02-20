#!/usr/bin/env python3
"""
Quick Portfolio Health Check
Run this script anytime to get instant portfolio status
"""

import json
import requests
from datetime import datetime
from portfolio_optimizer import PortfolioOptimizer
from execution_gate import ExecutionGate


def get_alpaca_positions():
    """Fetch current positions from Alpaca API."""
    with open('master_config.json', 'r') as f:
        config = json.load(f)
    
    headers = {
        "APCA-API-KEY-ID": config['account']['alpaca_api_key'],
        "APCA-API-SECRET-KEY": config['account']['alpaca_secret_key']
    }
    
    # Get account info
    account_url = f"{config['account']['alpaca_base_url']}/v2/account"
    account_resp = requests.get(account_url, headers=headers)
    account = account_resp.json()
    
    # Get positions
    positions_url = f"{config['account']['alpaca_base_url']}/v2/positions"
    positions_resp = requests.get(positions_url, headers=headers)
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
    print("="*70)
    print("🏦 PI HEDGE FUND - PORTFOLIO HEALTH CHECK")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    try:
        # Fetch live data from Alpaca
        print("\n📡 Fetching portfolio data from Alpaca...")
        data = get_alpaca_positions()
        
        positions = data['positions']
        portfolio_value = data['portfolio_value']
        cash = data['cash']
        
        print(f"✓ Loaded {len(positions)} positions")
        
    except Exception as e:
        print(f"⚠️  Could not fetch live data: {e}")
        print("   Using demo data instead...")
        
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
    print("\n🔍 Analyzing portfolio...")
    report = optimizer.generate_portfolio_report(positions, portfolio_value)
    gate_status = gate.get_gate_status()
    
    # Display results
    print(f"\n{'='*70}")
    print("💰 PORTFOLIO SUMMARY")
    print('='*70)
    
    print(f"\nTotal Value:  ${portfolio_value:,.2f}")
    print(f"Cash:         ${cash:,.2f} ({cash/portfolio_value*100:.1f}%)")
    print(f"Invested:     ${portfolio_value - cash:,.2f} ({(portfolio_value-cash)/portfolio_value*100:.1f}%)")
    print(f"Positions:    {len(positions)}")
    
    print(f"\n{'='*70}")
    print("📊 HEALTH SCORE")
    print('='*70)
    
    score = report['summary']['health_score']
    
    if score >= 80:
        grade = "🟢 EXCELLENT"
    elif score >= 70:
        grade = "🟡 GOOD"
    elif score >= 60:
        grade = "🟠 FAIR"
    else:
        grade = "🔴 POOR"
    
    print(f"\nOverall Health: {score:.1f}/100 {grade}")
    
    print(f"\n{'='*70}")
    print("📋 POSITIONS")
    print('='*70)
    
    for pos in sorted(positions, key=lambda p: p['market_value'], reverse=True):
        pct = pos['market_value'] / portfolio_value * 100
        pl_str = f"{pos['unrealized_pl_pct']*100:+.1f}%"
        
        status = "✓" if pct <= 20 else "⚠️"
        print(f"\n{status} {pos['symbol']:<6} ${pos['market_value']:>8,.2f} ({pct:>5.1f}%) | P/L: {pl_str:>7}")
    
    if report['checks']['rebalancing']:
        print(f"\n{'='*70}")
        print("⚠️  REBALANCING NEEDED")
        print('='*70)
        
        for action in report['checks']['rebalancing']:
            print(f"\n• {action['action'].upper()}: {action['symbol']}")
            print(f"  Reason: {action['reason']}")
            if 'trim_amount' in action:
                print(f"  Amount: ${action['trim_amount']:.2f}")
            if 'amount' in action:
                print(f"  Amount: ${action['amount']:.2f}")
    
    if report['checks']['zombies']:
        print(f"\n{'='*70}")
        print("💀 ZOMBIE POSITIONS")
        print('='*70)
        
        for zombie in report['checks']['zombies']:
            print(f"\n• {zombie['symbol']}: {zombie['reason']}")
            print(f"  Value: ${zombie['market_value']:.2f}")
            print(f"  Action: {zombie['action'].upper()}")
    
    if report['checks']['tax_loss_harvest']:
        print(f"\n{'='*70}")
        print("💸 TAX LOSS HARVEST OPPORTUNITIES")
        print('='*70)
        
        for harvest in report['checks']['tax_loss_harvest']:
            print(f"\n• {harvest['symbol']}: {harvest['reason']}")
            print(f"  Loss: ${abs(harvest['loss_amount']):.2f}")
            print(f"  Priority: {harvest['priority'].upper()}")
    
    if report['checks']['correlation']:
        print(f"\n{'='*70}")
        print("🔗 CORRELATION WARNINGS")
        print('='*70)
        
        for warning in report['checks']['correlation']:
            print(f"\n• {warning['symbol_a']} <-> {warning['symbol_b']}")
            print(f"  Correlation: {warning['correlation']:.2f}")
            print(f"  {warning['recommendation']}")
    
    if 'portfolio_return' in report['checks']['benchmark']:
        bench = report['checks']['benchmark']
        
        print(f"\n{'='*70}")
        print("📈 BENCHMARK COMPARISON")
        print('='*70)
        
        print(f"\nPortfolio Return:  {bench['portfolio_return']*100:+.2f}%")
        print(f"SPY Return:        {bench['spy_return']*100:+.2f}%")
        print(f"Outperformance:    {bench['outperformance']*100:+.2f}%")
        
        if bench.get('risk_adjustment'):
            adj = bench['risk_adjustment']
            print(f"\nRisk Adjustment: {adj['action'].upper()}")
            print(f"Reason: {adj['reason']}")
    
    print(f"\n{'='*70}")
    print("🚦 EXECUTION GATE STATUS")
    print('='*70)
    
    status_icon = "🟢 OPEN" if gate_status['gates_open'] else "🔴 CLOSED"
    print(f"\nGate Status:        {status_icon}")
    print(f"RL Mode:            {gate_status['rl_mode']}")
    print(f"RL Multiplier:      {gate_status['rl_confidence_multiplier']:.2f}x")
    print(f"Trades Today:       {gate_status['trades_today']}")
    print(f"Consecutive Losses: {gate_status['circuit_breakers']['consecutive_losses']}")
    
    print(f"\n{'='*70}")
    print("💡 TOP RECOMMENDATIONS")
    print('='*70)
    
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
            print(f"\n{i}. {rec}")
    else:
        print("\n✅ Portfolio looks healthy! No immediate actions needed.")
    
    print(f"\n{'='*70}")
    print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
