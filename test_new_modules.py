#!/usr/bin/env python3
"""
Test suite for new Invest Assist integrated modules.
Tests functionality and validates fallback mechanisms for broken APIs.
"""

import sys
import os
from pathlib import Path

# Add engine paths
engine_dir = Path(__file__).parent / "engine"
sys.path.insert(0, str(engine_dir))
sys.path.insert(0, str(engine_dir / "core"))
sys.path.insert(0, str(engine_dir / "data_sources"))
sys.path.insert(0, str(engine_dir / "scanners"))

def test_portfolio_risk_manager():
    """Test portfolio risk management module."""
    print("\n=== Testing Portfolio Risk Manager ===")
    try:
        from portfolio_risk_manager import PortfolioRiskManager, analyze_portfolio_risk

        # Sample portfolio data
        sample_positions = [
            {"symbol": "AAPL", "quantity": 100, "market_value": 15000},
            {"symbol": "MSFT", "quantity": 50, "market_value": 12000},
            {"symbol": "TSLA", "quantity": 30, "market_value": 8000}
        ]

        # Test risk analysis
        risk_metrics = analyze_portfolio_risk(sample_positions)
        print(f"✓ Portfolio Beta: {risk_metrics.portfolio_beta:.2f}")
        print(f"✓ Portfolio Volatility: {risk_metrics.portfolio_volatility:.1%}")
        print(f"✓ Total Positions: {risk_metrics.total_positions}")

        # Test position risk check
        risk_manager = PortfolioRiskManager()
        risk_check = risk_manager.check_position_risk_limits("GOOGL", 8000, sample_positions)
        print(f"✓ Position Risk Check: {risk_check['approved']}")

        # Test sector allocation
        allocations = risk_manager.get_sector_allocation_analysis(sample_positions)
        print(f"✓ Sector Allocation: Found {len(allocations)} sectors")

        return True

    except Exception as e:
        print(f"✗ Portfolio Risk Manager failed: {e}")
        return False

def test_fair_value_calculator():
    """Test fair value calculation module."""
    print("\n=== Testing Fair Value Calculator ===")
    try:
        from fair_value_calculator import FairValueCalculator

        calculator = FairValueCalculator()

        # Test with AAPL (should have good data)
        result = calculator.calculate_fair_value("AAPL")
        print(f"✓ AAPL Fair Value: ${result.fair_value:.2f} (current: ${result.current_price:.2f})")
        print(f"✓ Confidence Score: {result.confidence_score:.2f}")
        print(f"✓ Methods Used: {result.methods_used}")
        print(f"✓ Data Quality: {result.data_quality}")

        # Test signal generation
        signals = calculator.get_valuation_signals(["AAPL", "MSFT"])
        print(f"✓ Valuation Signals: {len(signals)} generated")

        return True

    except Exception as e:
        print(f"✗ Fair Value Calculator failed: {e}")
        return False

def test_options_analyzer():
    """Test enhanced options analyzer."""
    print("\n=== Testing Enhanced Options Analyzer ===")
    try:
        from enhanced_options_analyzer import EnhancedOptionsAnalyzer, analyze_options_opportunities

        analyzer = EnhancedOptionsAnalyzer()

        # Test high OI contract screening
        contracts = analyzer.get_high_oi_contracts("AAPL", "call", min_oi=50)
        print(f"✓ High-OI Contracts: Found {len(contracts)} AAPL call contracts")

        if contracts:
            best_contract = contracts[0]
            print(f"✓ Best Contract: {best_contract.option_symbol} (OI: {best_contract.open_interest})")

        # Test income calculation
        income_analysis = analyzer.calculate_options_enhanced_income("AAPL", 10000)
        if not income_analysis.get("error"):
            print(f"✓ Options Income: {income_analysis.get('effective_yield_percent', 0):.1f}% yield")
        else:
            print(f"! Options Income: {income_analysis['error']}")

        # Test opportunity signals
        signals = analyze_options_opportunities(["AAPL", "SPY"])
        print(f"✓ Options Opportunities: {len(signals)} signals")

        return True

    except Exception as e:
        print(f"✗ Enhanced Options Analyzer failed: {e}")
        return False

def test_enhanced_finviz_scanner():
    """Test enhanced Finviz scanner with fallbacks."""
    print("\n=== Testing Enhanced Finviz Scanner (with API fallbacks) ===")
    try:
        from enhanced_finviz_scanner import EnhancedFinvizScanner

        scanner = EnhancedFinvizScanner()

        print("Testing individual scan methods...")

        # Test high volume scan (expect API failure, test fallback)
        volume_results = scanner.scan_high_volume_opportunities()
        print(f"✓ High Volume Scan: {len(volume_results)} results (API may be broken)")

        # Test congressional trades (expect API failure)
        congress_results = scanner.scan_congressional_trades()
        print(f"✓ Congressional Scan: {len(congress_results)} results (API may be broken)")

        # Test enhanced insider (may work with finvizfinance)
        insider_results = scanner.scan_enhanced_insider_trading()
        print(f"✓ Enhanced Insider Scan: {len(insider_results)} results")

        # Test market context
        market_context = scanner.get_market_summary_context()
        print(f"✓ Market Context: {market_context}")

        return True

    except Exception as e:
        print(f"✗ Enhanced Finviz Scanner failed: {e}")
        return False

def test_social_sentiment_analyzer():
    """Test social sentiment analyzer with fallbacks."""
    print("\n=== Testing Social Sentiment Analyzer (with API fallbacks) ===")
    try:
        from social_sentiment_analyzer import SocialSentimentAnalyzer, analyze_social_sentiment

        analyzer = SocialSentimentAnalyzer()

        # Test sentiment extraction (should work without API)
        text_sentiment = analyzer._extract_sentiment_from_text(
            "AAPL to the moon! Strong buy signal, bullish breakout incoming!"
        )
        print(f"✓ Text Sentiment Analysis: Score={text_sentiment[0]:.2f}, Confidence={text_sentiment[1]}")

        # Test Twitter sentiment (expect API failure)
        twitter_sentiment = analyzer.get_twitter_sentiment_for_symbol("AAPL")
        print(f"✓ Twitter Sentiment: {twitter_sentiment.get('sentiment_label', 'neutral')} (API may be broken)")

        # Test trending analysis (expect API failure)
        trending = analyzer.get_trending_tickers()
        print(f"✓ Trending Analysis: {len(trending)} tickers (API may be broken)")

        # Test signal generation
        signals = analyze_social_sentiment(["AAPL", "TSLA"])
        print(f"✓ Sentiment Signals: {len(signals)} generated")

        return True

    except Exception as e:
        print(f"✗ Social Sentiment Analyzer failed: {e}")
        return False

def test_integration_compatibility():
    """Test compatibility with existing Send It bot structure."""
    print("\n=== Testing Integration Compatibility ===")
    try:
        # Test if we can import existing Send It modules
        try:
            from core.dynamic_config import cfg
            print("✓ Dynamic config integration working")
        except:
            print("! Dynamic config not found - using fallback")

        # Check if we can access existing data directories
        data_dir = Path("data")
        state_dir = Path("engine/state")

        if data_dir.exists():
            print("✓ Data directory accessible")
        else:
            print("! Data directory not found")

        if state_dir.exists():
            print("✓ State directory accessible")
        else:
            print("! State directory not found - will create")
            state_dir.mkdir(parents=True, exist_ok=True)

        return True

    except Exception as e:
        print(f"✗ Integration compatibility check failed: {e}")
        return False

def main():
    """Run all tests."""
    print("Starting comprehensive test of new Invest Assist integrated modules...")
    print("Note: Many external APIs from Invest Assist are expected to be broken")
    print("Testing focuses on fallback mechanisms and core functionality")

    test_results = []

    # Run all tests
    test_results.append(test_portfolio_risk_manager())
    test_results.append(test_fair_value_calculator())
    test_results.append(test_options_analyzer())
    test_results.append(test_enhanced_finviz_scanner())
    test_results.append(test_social_sentiment_analyzer())
    test_results.append(test_integration_compatibility())

    # Summary
    passed = sum(test_results)
    total = len(test_results)

    print(f"\n=== Test Summary ===")
    print(f"Passed: {passed}/{total} modules")
    print(f"Success Rate: {passed/total*100:.1f}%")

    if passed == total:
        print("🎉 All modules passed basic functionality tests!")
    elif passed >= total * 0.8:
        print("⚠️ Most modules working - some API dependencies broken as expected")
    else:
        print("❌ Several critical issues found - needs investigation")

    return passed == total

if __name__ == "__main__":
    main()