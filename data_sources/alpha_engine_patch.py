#!/usr/bin/env python3
"""
Alpha Engine Integration Patch
Adds alternative data signals to alpha_engine.py scoring.

Usage:
    1. Deploy all alt data source files to Pi
    2. Run: python3 alpha_engine_patch.py
    3. Restart stockbot service
"""

import os
import sys

PATCH_MARKER = "# ALT_DATA_INTEGRATION"

INTEGRATION_CODE = '''
    # ALT_DATA_INTEGRATION - Alternative Data Signals
    def _get_alt_data_boost(self, symbol):
        """
        Get signal boost from alternative data sources.
        Returns: score adjustment (-20 to +20 points)
        """
        try:
            sys.path.insert(0, os.path.dirname(__file__) + '/data_sources')
            from alt_data_aggregator import AltDataAggregator
            
            aggregator = AltDataAggregator()
            signals = aggregator.get_signals_for_ticker(symbol)
            
            if not signals:
                return 0
            
            # Use composite score (0-100) to adjust alpha score
            # High alt data score (>70) = +20 points
            # Low alt data score (<30) = -20 points
            # Neutral (40-60) = 0 points
            
            composite = signals['composite_score']
            confidence = signals['confidence']
            
            if composite > 70:
                boost = min(20, (composite - 70) * confidence)
            elif composite < 30:
                boost = max(-20, (composite - 30) * confidence)
            else:
                boost = 0
            
            return boost
            
        except Exception as e:
            # Alt data optional - don't break if it fails
            return 0
'''

SCORE_SYMBOL_PATCH = '''
        # Add alternative data boost (after all other factors)
        alt_boost = self._get_alt_data_boost(symbol)
        score += alt_boost
        
        if alt_boost != 0:
            factors['alt_data_boost'] = alt_boost
'''

def patch_alpha_engine(alpha_engine_path='../alpha_engine.py'):
    """
    Patch alpha_engine.py to include alt data signals.
    """
    if not os.path.exists(alpha_engine_path):
        print(f"⚠️  alpha_engine.py not found at {alpha_engine_path}")
        return False
    
    print(f"🔧 Patching {alpha_engine_path}...")
    
    with open(alpha_engine_path, 'r') as f:
        content = f.read()
    
    # Check if already patched
    if PATCH_MARKER in content:
        print("✅ Already patched. Skipping.")
        return True
    
    # Find the class definition
    if 'class AlphaEngine:' not in content:
        print("⚠️  Could not find AlphaEngine class. Manual patching required.")
        return False
    
    # Add the new method after class definition
    # Find a good insertion point (after __init__ method)
    insert_pos = content.find('    def score_symbol(self, symbol')
    
    if insert_pos == -1:
        print("⚠️  Could not find score_symbol method. Manual patching required.")
        return False
    
    # Insert the helper method before score_symbol
    patched = content[:insert_pos] + INTEGRATION_CODE + '\n' + content[insert_pos:]
    
    # Now find where to add the boost in score_symbol
    # Look for the return statement
    score_method_end = patched.find('return {', insert_pos + len(INTEGRATION_CODE))
    
    if score_method_end == -1:
        print("⚠️  Could not find return statement in score_symbol. Manual patching required.")
        return False
    
    # Insert boost calculation before return
    patched = patched[:score_method_end] + SCORE_SYMBOL_PATCH + '\n        ' + patched[score_method_end:]
    
    # Create backup
    backup_path = alpha_engine_path + '.bak'
    with open(backup_path, 'w') as f:
        f.write(content)
    
    print(f"📝 Backup saved to {backup_path}")
    
    # Write patched version
    with open(alpha_engine_path, 'w') as f:
        f.write(patched)
    
    print("✅ Patching complete!")
    print("\nNext steps:")
    print("  1. Deploy data_sources/ folder to Pi")
    print("  2. Install dependencies: pip3 install -r data_sources/requirements.txt")
    print("  3. Run daily alt data scan (add to cron)")
    print("  4. Restart stockbot: sudo systemctl restart mybot")
    
    return True

if __name__ == '__main__':
    success = patch_alpha_engine()
    sys.exit(0 if success else 1)
