#!/usr/bin/env python3
"""
Clean Main Wrapper - Strategy V2
Runs clean orchestrator on 30-minute cycles during market hours
"""
import asyncio
import logging
import schedule
import time
from datetime import datetime
from pathlib import Path
import sys

# Add to path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from orchestrator_clean import CleanOrchestrator

# Setup logging
log_dir = BASE_DIR / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "trading.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("main_wrapper")


def is_market_hours():
    """Quick check if we're in market hours (9:30 AM - 4:00 PM ET Mon-Fri)"""
    now = datetime.now()
    
    # Check if weekend
    if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False
    
    # Check if market hours
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
        logger.info(f"Starting cycle at {datetime.now()}")
        logger.info("=" * 60)
        
        if not is_market_hours():
            logger.info("Outside market hours - skipping cycle")
            return
        
        # Run orchestrator
        orchestrator = CleanOrchestrator()
        asyncio.run(orchestrator.run_cycle())
        
        logger.info("Cycle complete")
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        raise
    except Exception as e:
        logger.error(f"Cycle failed: {e}", exc_info=True)


def main():
    """Main loop - runs orchestrator every 30 minutes"""
    logger.info("=" * 80)
    logger.info("CLEAN BOT - STARTING")
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
        logger.info("BOT STOPPED")
        logger.info("=" * 80)
        sys.exit(0)


if __name__ == '__main__':
    main()
