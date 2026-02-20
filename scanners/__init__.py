"""
High-ROI Opportunity Scanners

These scanners find explosive daily setups:
- Gap Scanner: Morning gap-ups (10-30% potential)
- Catalyst Scanner: Volume spikes + news (15-30% potential)
"""
from .morning_gap_scanner import GapScanner, run_morning_scan
from .catalyst_scanner import CatalystScanner, run_catalyst_scan

__all__ = [
    'GapScanner',
    'CatalystScanner',
    'run_morning_scan',
    'run_catalyst_scan'
]
