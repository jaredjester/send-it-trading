"""
Base Scanner Class
"""

from abc import ABC, abstractmethod
from typing import List, Dict


class BaseScanner(ABC):
    """Base class for all scanners."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def scan(self, symbols: List[str]) -> Dict[str, List[Dict]]:
        """Scan for opportunities in the given symbols."""
        pass