#!/usr/bin/env python3
"""
Main Wrapper for Strategy V2 Orchestrator
Runs the orchestrator on a schedule (every 30 minutes during market hours)
"""
import os
import sys
import time
import logging
import asyncio
import schedule
from datetime import datetime
from pathlib import Path

# Add strategy_v2 to path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# Import orchestrator
try:
    from orchestrator import run_orchestrated_cycle
    print("✓ Orchestrator imported successfully")
except ImportError as e:
    print(f"✗ Failed to import orchestrator: {e}")
    sys.exit(1)

# Configure logging
log_dir = BASE_DIR / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "orchestrator.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("main_wrapper")


def is_market_hours():
    """Check if it's currently market hours (9:30 AM - 4:00 PM ET Mon-Fri)"""
    now = datetime.now()
    
    # Check if weekend
    if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False
    
    # Check if market hours (9:30 AM - 4:00 PM)
    hour = now.hour
    minute = now.minute
    
    if hour < 9 or hour >= 16:
        return False
    if hour == 9 and minute < 30:
        return False
    
    return True


def run_cycle_safe():
    """Run orchestrator cycle with error handling"""
    try:
        logger.info("=" * 60)
        logger.info("Starting orchestrator cycle")
        logger.info("=" * 60)
        
        if not is_market_hours():
            logger.info("Market closed - skipping cycle")
            return
        
        # Run orchestrator
        asyncio.run(run_orchestrated_cycle())
        
        logger.info("Cycle complete")
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        raise
    except Exception as e:
        logger.error(f"Orchestrator cycle failed: {e}", exc_info=True)


def main():
    """Main loop - runs orchestrator every 30 minutes"""
    logger.info("=" * 80)
    logger.info("STRATEGY V2 ORCHESTRATOR - STARTING")
    logger.info("=" * 80)
    logger.info(f"Base directory: {BASE_DIR}")
    logger.info(f"Log directory: {log_dir}")
    logger.info("")
    
    # Schedule orchestrator to run every 30 minutes
    schedule.every(30).minutes.do(run_cycle_safe)
    
    # Run immediately on startup
    logger.info("Running initial cycle...")
    run_cycle_safe()
    
    # Main loop
    logger.info("")
    logger.info("Entering main loop (30-minute cycles)")
    logger.info("Press Ctrl+C to stop")
    logger.info("")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
            
    except KeyboardInterrupt:
        logger.info("")
        logger.info("=" * 80)
        logger.info("ORCHESTRATOR STOPPED")
        logger.info("=" * 80)
        sys.exit(0)


if __name__ == '__main__':
    main()
