# Bot Module

This module contains the core trading bot logic, including:

- `main.py`: Main entry point for the trading bot
- `train_synthetic.py`: Synthetic data training for RL models
- `config/`: Configuration files and base settings
- `options_v1/`: Options trading strategies and components

## Key Components

- **Strategies**: Various trading strategies (momentum, mean reversion, etc.)
- **Risk Management**: Position sizing, stop losses, Greeks calculations
- **Data Sources**: Integration with external data feeds
- **Execution**: Order placement and management

## Usage

Run the bot with:
```bash
python bot/main.py
```