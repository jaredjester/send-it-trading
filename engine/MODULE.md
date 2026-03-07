# Engine Module

The core trading engine that orchestrates all trading activities.

## Structure

- `main_wrapper_simple.py`: Simplified main wrapper
- `orchestrator_simple.py`: Trading orchestration logic
- `core/`: Core components (Alpaca client, config, sizing, etc.)
- `data_sources/`: External data aggregators
- `evaluation/`: Backtesting and performance evaluation
- `evolution/`: Parameter optimization and RL training
- `scanners/`: Market scanning for opportunities
- `state/`: Persistent state management

## Key Features

- Multi-timeframe analysis
- Risk-adjusted position sizing
- Real-time portfolio management
- Automated trade execution
- Performance tracking and optimization

## Running the Engine

```bash
python engine/orchestrator_simple.py
```