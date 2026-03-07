#!/usr/bin/env python3
"""
Main Wrapper - Simple Orchestrator
30-minute trading cycles
"""
import asyncio
import datetime
import logging
import os
from pathlib import Path
import sys
import time
import zoneinfo

BASE_DIR = Path(__file__).parent
REPO_DIR = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(REPO_DIR))

import requests as _requests
import alpaca_env
alpaca_env.bootstrap()
from orchestrator_simple import SimpleOrchestrator


if __name__ == '__main__':
    main()
