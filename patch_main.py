"""
Patch main.py to add the orchestrated strategy alongside existing logic.

This adds a single new scheduled task that runs every 30 minutes:
  run_orchestrated_cycle()

It does NOT remove the existing analyze_and_notify() — both run in parallel.
The orchestrator handles its own risk gating, so having both is safe.

Run this script on the Pi to apply the patch.
"""

import re
import os
import shutil
from datetime import datetime

MAIN_PY = os.path.expanduser("~/shared/stockbot/main.py")
BACKUP_DIR = os.path.expanduser("~/shared/stockbot/backups")


def patch():
    # Read current main.py
    with open(MAIN_PY) as f:
        content = f.read()

    # Check if already patched
    if "run_orchestrated_cycle" in content:
        print("Already patched! Skipping.")
        return True

    # Backup
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_name = f"main.py.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    shutil.copy2(MAIN_PY, os.path.join(BACKUP_DIR, backup_name))
    print(f"Backed up to {backup_name}")

    # 1. Add import at top (after existing imports)
    import_line = (
        "\n# Strategy V2 — Orchestrated multi-factor + RL\n"
        "import sys as _sys\n"
        "_sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'strategy_v2'))\n"
        "try:\n"
        "    from orchestrator import run_orchestrated_cycle\n"
        "    _HAS_ORCHESTRATOR = True\n"
        "except ImportError as _e:\n"
        "    logging.warning(f'Orchestrator not available: {_e}')\n"
        "    _HAS_ORCHESTRATOR = False\n"
    )

    # Find a good insertion point (after the last from/import block near the top)
    # Insert after "from send import send_email" or similar
    insert_after = "from send import send_email"
    if insert_after in content:
        content = content.replace(insert_after, insert_after + import_line)
    else:
        # Fallback: insert after load_dotenv()
        content = content.replace("load_dotenv()", "load_dotenv()" + import_line)

    # 2. Add scheduled task in async_schedule()
    # Find the options strategy schedule line and add after it
    schedule_line = (
        "\n    # Strategy V2: Orchestrated cycle every 30 minutes\n"
        "    if _HAS_ORCHESTRATOR:\n"
        "        schedule.every(30).minutes.do(\n"
        "            lambda: asyncio.create_task(run_orchestrated_cycle())\n"
        "        )\n"
        "        logger.info('Strategy V2 orchestrator scheduled (30 min cycle)')\n"
    )

    # Insert before the "try: while True:" block in async_schedule
    target = "    schedule.every(15).minutes.do(lambda: asyncio.create_task(task_manager.run_options_strategy()))"
    if target in content:
        content = content.replace(target, target + schedule_line)
    else:
        # Fallback: find any schedule line and append
        print("WARNING: Could not find exact insertion point, trying fallback")
        # Find the last schedule.every line
        lines = content.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            if "schedule.every" in lines[i] and "do(" in lines[i]:
                lines.insert(i + 1, schedule_line)
                break
        content = "\n".join(lines)

    # Write patched file
    with open(MAIN_PY, "w") as f:
        f.write(content)

    print("✓ main.py patched successfully!")
    print("  - Added orchestrator import")
    print("  - Added 30-minute orchestrated cycle")
    print("  - Restart stockbot to activate: sudo systemctl restart mybot")
    return True


if __name__ == "__main__":
    patch()
