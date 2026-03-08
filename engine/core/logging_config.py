"""
Centralized Logging Configuration
"""

import logging
import os
from pathlib import Path


def setup_logging(level: str = None, log_file: str = None):
    """Setup logging for the application."""
    if level is None:
        level = os.getenv('LOG_LEVEL', 'INFO').upper()
    
    handlers = [logging.StreamHandler()]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=getattr(logging, level),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)