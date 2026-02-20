"""
Deploy SEND IT mode to Pi bot.

Converts GME conviction from conservative (exit at $45) to maximum upside capture (exit only on thesis break).
"""
import json
import sys
from pathlib import Path
from datetime import datetime


def update_gme_conviction_send_it():
    """Update GME conviction to SEND IT mode."""
    
    conviction_file = Path.home() / "shared/stockbot/strategy_v2/state/convictions.json"
    
    if not conviction_file.exists():
        print(f"❌ Conviction file not found: {conviction_file}")
        return False
    
    # Load current convictions
    with open(conviction_file, 'r') as f:
        convictions = json.load(f)
    
    if 'GME' not in convictions:
        print("❌ GME conviction not found")
        return False
    
    gme = convictions['GME']
    
    print("Current GME Conviction:")
    print(f"  Entry: ${gme['entry_price']:.2f}")
    print(f"  Target: ${gme.get('target_price', 'None')}")
    print(f"  Max Position: {gme.get('max_position_pct', 0.45):.0%}")
    print(f"  Max Pain: ${gme['max_pain_price']:.2f}")
    print()
    
    # Backup
    backup_file = conviction_file.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(backup_file, 'w') as f:
        json.dump(convictions, f, indent=2)
    print(f"✅ Backup saved: {backup_file}")
    
    # Update to SEND IT mode
    gme_send_it = {
        **gme,
        'target_price': None,  # REMOVE profit target
        'exit_on_target': False,
        'hold_until_thesis_breaks': True,
        'max_position_pct': 1.0,  # 100% position
        'send_it_mode': True,
        'updated_at': datetime.now().isoformat(),
        'update_reason': 'SEND IT - no arbitrary targets, exit only on thesis invalidation',
        
        # Exit triggers (only these)
        'exit_triggers': [
            'price_below_max_pain_10',
            'price_below_support_15',
            'deadline_oct_2026_no_catalyst',
            'ryan_cohen_exits',
            'acquisition_rejected'
        ]
    }
    
    convictions['GME'] = gme_send_it
    
    # Save updated
    with open(conviction_file, 'w') as f:
        json.dump(convictions, f, indent=2)
    
    print("\n✅ GME CONVICTION UPDATED TO SEND IT MODE")
    print("\nNew Settings:")
    print(f"  Target: NONE (let it run to $1,000+)")
    print(f"  Max Position: 100%")
    print(f"  Exit triggers:")
    print(f"    - Price < $10 (max pain)")
    print(f"    - Price < $15 (support broken)")
    print(f"    - Oct 2026, no catalyst")
    print(f"    - Ryan Cohen exits")
    print(f"    - Acquisition rejected")
    print()
    print("⚠️ NEXT STEPS:")
    print("  1. Review updated conviction file")
    print("  2. Restart orchestrator: sudo systemctl restart mybot")
    print("  3. Monitor decision logs for correct behavior")
    print("  4. DO NOT manually take profits")
    print()
    print(f"📁 Backup: {backup_file}")
    
    return True


def show_send_it_comparison():
    """Show before/after comparison."""
    print("=" * 60)
    print("SEND IT MODE DEPLOYMENT")
    print("=" * 60)
    print()
    print("BEFORE (Conservative):")
    print("  ❌ Exit at $45 target (+80%)")
    print("  ❌ Max 45% position")
    print("  ❌ Miss $1,000 upside")
    print()
    print("AFTER (Send It):")
    print("  ✅ NO profit target")
    print("  ✅ 100% position")
    print("  ✅ Hold to $1,000+ until thesis breaks")
    print()
    print("Exit ONLY if:")
    print("  1. Price < $10 (thesis dead)")
    print("  2. Price < $15 (momentum dead)")
    print("  3. Oct 2026 passes, no news")
    print("  4. Acquisition rejected")
    print("  5. Ryan Cohen exits")
    print()
    print("Path:")
    print("  $390 → $39K (100x if GME $24→$2,400)")
    print("  $39K → $1.95M (50x next setup)")
    print("  $1.95M → $3.9M (2x cleanup)")
    print()
    print("Time: 18-36 months, not 30 years")
    print("=" * 60)
    print()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Deploy SEND IT mode')
    parser.add_argument('--confirm', action='store_true', help='Actually deploy (otherwise dry run)')
    parser.add_argument('--show', action='store_true', help='Show comparison only')
    
    args = parser.parse_args()
    
    if args.show:
        show_send_it_comparison()
        sys.exit(0)
    
    show_send_it_comparison()
    
    if not args.confirm:
        print("⚠️ DRY RUN MODE")
        print()
        print("This would update GME conviction to SEND IT mode.")
        print("To actually deploy, run:")
        print("  python3 deploy_send_it_mode.py --confirm")
        print()
        sys.exit(0)
    
    print("🚀 DEPLOYING SEND IT MODE...")
    print()
    
    success = update_gme_conviction_send_it()
    
    if success:
        print()
        print("✅ DEPLOYMENT COMPLETE")
        print()
        print("GME is now in SEND IT mode.")
        print("Hold until thesis breaks.")
        print("Let it run to $1,000+.")
        sys.exit(0)
    else:
        print()
        print("❌ DEPLOYMENT FAILED")
        sys.exit(1)
