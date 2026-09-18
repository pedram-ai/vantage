"""Daily run job: compute + archive the run. Executed as a Cloud Run Job
weekdays at 5:00 AM PT by Cloud Scheduler."""

import json
import sys

from core.run_builder import build_run


def main() -> int:
    run = build_run(persist=True)
    print(json.dumps({
        "run_id": run.get("run_id"),
        "date": run.get("date"),
        "verdict": run.get("verdict"),
        "errors": run.get("errors"),
    }))
    # Fail the job (for alerting) only if BOTH sources failed.
    if run.get("es") is None and run.get("spy") is None:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
