#!/bin/bash
# Deploy scanners to Pi

echo "🚀 Deploying High-ROI Scanners to Pi..."

# Copy scanners directory to Pi
sshpass -p 'Notraspberry123!' scp -r -o StrictHostKeyChecking=no \
  /Users/jon/.openclaw/workspace/strategy-v2/scanners \
  jonathangan@192.168.12.44:~/shared/stockbot/strategy_v2/

echo "✅ Scanners deployed to ~/shared/stockbot/strategy_v2/scanners/"

# Test on Pi
echo ""
echo "Testing scanners on Pi..."
sshpass -p 'Notraspberry123!' ssh -o StrictHostKeyChecking=no jonathangan@192.168.12.44 \
  "cd ~/shared/stockbot/strategy_v2/scanners && python3 test_scanners.py"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Next: Integrate into orchestrator to execute opportunities"
