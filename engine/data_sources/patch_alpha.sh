#!/bin/bash
# Patch alpha_engine.py to include alt data boost
set -e

cd ~/shared/stockbot/strategy_v2

echo "Backing up alpha_engine.py..."
cp alpha_engine.py alpha_engine.py.bak_altdata

echo "Applying patch..."

# Check if already patched
if grep -q "_get_alt_data_boost" alpha_engine.py; then
    echo "Already patched!"
    exit 0
fi

# Create the helper method in a temp file
cat > /tmp/helper_method.txt << 'EOF'

    def _get_alt_data_boost(self, symbol: str) -> float:
        """Get signal boost from alternative data sources."""
        try:
            import sys
            import os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'data_sources'))
            from alt_data_aggregator import AltDataAggregator
            
            aggregator = AltDataAggregator(data_dir=os.path.join(os.path.dirname(__file__), 'data', 'alt_data'))
            signals = aggregator.get_signals_for_ticker(symbol)
            
            if not signals:
                return 0.0
            
            composite = signals['composite_score']
            confidence = signals['confidence']
            
            if composite > 70:
                boost = min(20.0, (composite - 70) * 0.667 * confidence)
            elif composite < 30:
                boost = max(-20.0, (composite - 30) * 0.667 * confidence)
            else:
                boost = 0.0
            
            return boost
        except Exception:
            return 0.0

EOF

# Insert helper method before score_opportunity
line_num=$(grep -n "def score_opportunity" alpha_engine.py | cut -d: -f1)
if [ -z "$line_num" ]; then
    echo "ERROR: Could not find score_opportunity method"
    exit 1
fi

# Insert at line before score_opportunity
insert_line=$((line_num - 1))
sed -i "${insert_line}r /tmp/helper_method.txt" alpha_engine.py

# Now add the boost calculation
# Find the line with "# Determine dominant strategy" and insert before it
boost_line=$(grep -n "# Determine dominant strategy" alpha_engine.py | cut -d: -f1)
if [ -z "$boost_line" ]; then
    echo "ERROR: Could not find boost insertion point"
    exit 1
fi

# Create boost code
cat > /tmp/boost_code.txt << 'EOF'

        # Add alternative data boost
        alt_data_boost = self._get_alt_data_boost(symbol)
        weighted_score += alt_data_boost
        weighted_score = max(0, min(100, weighted_score))  # Clamp to 0-100
        
EOF

# Insert boost code
boost_insert=$((boost_line - 1))
sed -i "${boost_insert}r /tmp/boost_code.txt" alpha_engine.py

echo "Patch applied successfully!"
echo "Backup saved to alpha_engine.py.bak_altdata"

# Cleanup
rm /tmp/helper_method.txt /tmp/boost_code.txt
