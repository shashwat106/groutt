"""Run automations from the command line (or Windows Task Scheduler / cron).

    uv run python -m automations.run            # run whatever is due now
    uv run python -m automations.run sync       # run one job now
    uv run python -m automations.run loop       # keep running (what the Console does in the background)
"""
import sys
import time

from automations.jobs import JOBS, run, run_due
from db.session import init_db


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    init_db()
    if argv and argv[0] in JOBS:
        r = run(argv[0], "manual")
        print(f"[{argv[0]}] {'ok' if r.ok else 'FAILED'}: {r.summary}")
    elif argv and argv[0] == "loop":
        while True:
            for j in run_due():
                print(f"ran {j}")
            time.sleep(20)
    else:
        print("ran:", run_due() or "nothing due")


if __name__ == "__main__":
    main(sys.argv[1:])
