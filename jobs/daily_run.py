"""Daily run job: compute, archive, email. Weekdays 5:00 AM PT.

Belt and braces on the weekday rule: Scheduler is cron'd Mon-Fri, and this
job refuses to email on Sat/Sun regardless of how it was triggered.
"""

import json
import os
import sys
from datetime import datetime

from core.email_render import render_email
from core.mailer import send_html
from core.profile import ET
from core.run_builder import build_run
from core import store

RECIPIENT = os.environ.get("VANTAGE_EMAIL_TO", "pedram@patexia.com")


def main() -> int:
    force = "--force-email" in sys.argv
    now_et = datetime.now(ET)
    is_weekend = now_et.weekday() >= 5  # 5=Sat, 6=Sun

    run = build_run(persist=True)

    email_result = {"skipped": True, "reason": "weekend - no email Sat/Sun"}
    if not is_weekend or force:
        subject, html = render_email(run)
        email_result = send_html(RECIPIENT, subject, html)
        if run.get("run_id"):
            store.db().collection("runs").document(run["run_id"]).set(
                {"email": email_result, "email_subject": subject,
                 "email_html": html}, merge=True)

    print(json.dumps({
        "run_id": run.get("run_id"),
        "date": run.get("date"),
        "weekday": now_et.strftime("%a"),
        "verdict": run.get("verdict"),
        "errors": run.get("errors"),
        "email": email_result,
    }))
    if run.get("es") is None and run.get("spy") is None:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
