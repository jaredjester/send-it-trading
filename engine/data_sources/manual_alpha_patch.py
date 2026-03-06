#!/usr/bin/env python3
"""
Manual Alpha Engine Patch for Alt Data Integration
Adds alternative data boost to score_opportunity method.
"""

import os

# The helper method to add (insert before score_opportunity)
HELPER_METHOD = '''
    def _get_alt_data_boost(self, symbol: str) -> float:
        """
        Get signal boost from alternative data sources.
        Returns: score adjustment (-20 to +20 points)
        """
        try:
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'data_sources'))
            from alt_data_aggregator import AltDataAggregator
            
            aggregator = AltDataAggregator(data_dir=os.path.join(os.path.dirname(__file__), 'data', 'alt_data'))
            signals = aggregator.get_signals_for_ticker(symbol)
            
            if not signals:
                return 0.0
            
            # Use composite score (0-100) to adjust alpha score
            composite = signals['composite_score']
            confidence = signals['confidence']
            
            # High alt data score (>70) = +20 points max
            # Low alt data score (<30) = -20 points max
            # Neutral (40-60) = 0 points
            if composite > 70:
                boost = min(20.0, (composite - 70) * 0.667 * confidence)
            elif composite < 30:
                boost = max(-20.0, (composite - 30) * 0.667 * confidence)
            else:
                boost = 0.0
            
            return boost
            
        except Exception:
            # Alt data optional - don't break if it fails
            return 0.0

'''

# The modification to score_opportunity (after weighted_score calculation)
SCORE_MODIFICATION = '''
        # Add alternative data boost
        alt_data_boost = self._get_alt_data_boost(symbol)
        weighted_score += alt_data_boost
        weighted_score = max(0, min(100, weighted_score))  # Clamp to 0-100
        
'''

def patch_alpha_engine_manually():
    """Apply manual patch to alpha_engine.py on Pi"""
    import subprocess
    
    print("🔧 Manually patching alpha_engine.py on Pi...")
    
    # SSH command to patch
    patch_script = f'''
cd ~/shared/stockbot/strategy_v2

# Backup original
cp alpha_engine.py alpha_engine.py.bak_altdata

# Create patched version
python3 << 'PYTHON_EOF'
with open('alpha_engine.py', 'r') as f:
    content = f.read()

# Check if already patched
if '_get_alt_data_boost' in content:
    print("✅ Already patched!")
    exit(0)

# Find insertion point for helper method (before score_opportunity)
insert_point = content.find('    def score_opportunity(')
if insert_point == -1:
    print("⚠️  Could not find score_opportunity method")
    exit(1)

# Insert helper method
helper_method = """
{HELPER_METHOD}"""

content = content[:insert_point] + helper_method + content[insert_point:]

# Find where to add the boost (after weighted_score calculation)
boost_insert = content.find('        # Determine dominant strategy')
if boost_insert == -1:
    print("⚠️  Could not find weighted_score section")
    exit(1)

# Insert boost calculation
boost_code = """
{SCORE_MODIFICATION}"""

content = content[:boost_insert] + boost_code + content[boost_insert:]

# Write patched version
with open('alpha_engine.py', 'w') as f:
    f.write(content)

print("✅ Patch applied successfully!")
PYTHON_EOF
'''
    
    cmd = f"sshpass -p 'Notraspberry123!' ssh -o StrictHostKeyChecking=no jonathangan@192.168.12.44 \"{patch_script}\""
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    print(result.stdout)
    if result.stderr:
        print("Errors:", result.stderr)
    
    if result.returncode == 0:
        print("\n✅ Alpha engine successfully patched!")
        print("\nNext step: Restart stockbot")
        print("  ssh jonathangan@192.168.12.44")
        print("  sudo systemctl restart mybot")
        return True
    else:
        print("\n⚠️  Patch failed. Manual editing required.")
        return False

if __name__ == '__main__':
    patch_alpha_engine_manually()
