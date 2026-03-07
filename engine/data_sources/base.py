#!/usr/bin/env python3
"""
Base scraper class for web data sources.
Provides common functionality for API calls, retries, logging, and error handling.
"""

import requests
import time
import logging
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class BaseScraper(ABC):
    """Base class for web scrapers with common functionality."""

    def __init__(self, name: str, base_url: Optional[str] = None):
        self.name = name
        self.base_url = base_url
        self.session = requests.Session()
        self.logger = logging.getLogger(f"{__name__}.{name}")

    def _make_request(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """Make HTTP request with retry logic and error handling."""
        max_retries = 3
        backoff = 1

        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"{self.name} API error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    self.logger.error(f"{self.name} API failed after {max_retries} attempts")
                    return None
        return None

    def _get_json(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Get JSON data from URL with error handling."""
        response = self._make_request(url, **kwargs)
        if response:
            try:
                return response.json()
            except ValueError as e:
                self.logger.error(f"{self.name} JSON parse error: {e}")
        return None

    def _get_text(self, url: str, **kwargs) -> Optional[str]:
        """Get text data from URL with error handling."""
        response = self._make_request(url, **kwargs)
        return response.text if response else None

    @abstractmethod
    def fetch_data(self, *args, **kwargs):
        """Abstract method for data fetching - implement in subclasses."""
        pass

    def close(self):
        """Clean up resources."""
        self.session.close()