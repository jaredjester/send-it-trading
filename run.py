#!/usr/bin/env python3
"""Entry point. Usage: python run.py [scan|exits|portfolio|cycle]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    from orchestrator import main
    main()
