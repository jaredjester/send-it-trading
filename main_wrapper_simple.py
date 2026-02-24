#!/usr/bin/env python3
"""
Main Wrapper - Simple Orchestrator
30-minute trading cycles
"""
import asyncio
import logging
from pathlib import Path
import sys
import time

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from orchestrator_simple import SimpleOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / 'logs/trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('main_wrapper')


async def run_continuous():
    """Run trading bot continuously"""
    logger.info("=" * 80)
    logger.info("TRADING BOT - STARTING")
    logger.info("=" * 80)
    logger.info(f"Base directory: {BASE_DIR}")
    logger.info(f"Cycle interval: 30 minutes")
    logger.info("")
    
    orchestrator = SimpleOrchestrator()
    
    cycle_count = 0
    
    while True:
        cycle_count += 1
        
        try:
            logger.info(f"[Cycle #{cycle_count}]")
            await orchestrator.run_cycle()
            
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        
        except Exception as e:
            logger.error(f"Cycle #{cycle_count} failed: {e}", exc_info=True)
        
        # Sleep 30 minutes
        logger.info(f"Sleeping 30 minutes...")
        logger.info("")
        await asyncio.sleep(30 * 60)


def main():
    """Entry point"""
    try:
        asyncio.run(run_continuous())
    except KeyboardInterrupt:
        logger.info("Graceful shutdown")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
