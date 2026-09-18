"""Gates the deploy on the email rules that actually broke rendering for
Pedram (handoff §5.1), and on the weekday send rule.

These assert on OUTPUT, not on "it rendered" — a Gmail-unsafe email compiles
and builds perfectly fine.

Usage: python -m tests.test_email_guard
"""

from __future__ import annotations

import re
import sys
from datetime import date

from core.email_render import render_email

FORBIDDEN = {
    "<img": "images do not render for him",
    "<svg": "SVG is stripped",
    "<script": "scripts are stripped",
    "cid:": "inline images do not render",
    "&#9664;": "Gmail turns it into an emoji",
    "&#9654;": "Gmail turns it into an emoji",
}

# A SPY/SPX PRICE, strike or expiry (§5.1). Generic prose like
# "open-interest strikes" carries no number and is allowed.
SPY_NUM = re.compile(r"\b(SPY|SPX)\b[^.<]{0,20}?\d", re.I)

FAKE_RUN = {
    "date": date(2026, 9, 18).isoformat(),
    "generated_at_pt": "2026-09-18 05:00 AM PT",
    "errors": [],
    "es": {
        "contract": "ESZ26", "price": 7700.25,
        "events": [{"kind": "quarterly_opex", "text": "Quarterly opex: pinned midday "
                                                      "around large open-interest strikes."}],
        "author_levels": [{"author": "Smashelito", "kind": "fut", "price": 7775}],
        "sessions": {"rth": [{"label": "RTH 2026-09-17", "open": 7713.5, "high": 7716.25,
                              "low": 7680.5, "close": 7707.25, "poc": 7705, "vah": 7711,
                              "val": 7691, "vwap": 7700.67}],
                     "overnight": None, "combined": None},
        "action_map": {
            "verdict": "NO TRADE - wait for a zone", "zone_state": "none",
            "open_type": {"code": "A", "text": "Open inside prior value."},
            "distances": {"put_zone_above": {"pts": 75.0, "range": [7775, 7800]},
                          "call_zone_below": {"pts": 31.0, "range": [7634, 7669]}},
            "notes": ["overnight high 7739 rejected here"],
            "zones": {
                "fade": {"band": {"lo": 7775, "hi": 7800, "sources": ["smashelito"]},
                         "trigger": "tag 7775-7800, reject", "internals": "TICK fails above +700",
                         "targets": [7714, 7705], "stop": 7805, "preferred": True},
                "breakout": {"above": 7800, "trigger": "hold > 7800, 15 min",
                             "internals": "TICK > 0", "targets": [7825, 7855],
                             "stop": 7775, "cap": 7855, "low_odds": True},
                "dip": {"band": {"lo": 7634, "hi": 7669, "sources": ["profile"]},
                        "trigger": "dip into 7634-7669, hold", "internals": "TRIN < 1",
                        "targets": [7689, 7711], "stop": 7615, "preferred": True},
                "breakdown": {"below": 7620, "trigger": "hold < 7620, 15 min",
                              "internals": "TRIN > 1", "targets": [7589, 7570],
                              "stop": 7669, "cap": 7540, "low_odds": True},
            },
        },
    },
    "spy": {"price": 761.69, "sessions": {"rth": [{"label": "SPY RTH", "open": 761.31,
            "high": 762.0, "low": 757.97, "close": 761.69, "poc": 759, "vah": 761,
            "val": 759, "vwap": 760.1}], "overnight": None, "combined": None}},
}


def check(html: str) -> list[str]:
    fails = []
    for tok, why in FORBIDDEN.items():
        if tok.lower() in html.lower():
            fails.append(f"contains {tok!r} - {why}")
    # `background:` shorthand is stripped by Gmail; background-color is fine.
    if re.search(r"background\s*:", html):
        fails.append("uses the `background` shorthand - Gmail strips it")
    m = SPY_NUM.search(html)
    if m:
        fails.append(f"leaks a SPY/SPX number: {m.group(0)!r}")
    if "max-width:720px" not in html.replace(" ", ""):
        fails.append("missing the 720px max width")
    return fails


def main() -> int:
    subject, html = render_email(FAKE_RUN)
    fails = check(html)

    # The verdict and both distances must survive - the 10-second read.
    for must in ("NO TRADE", "75.0 pts above", "31.0 pts below", "ESZ26"):
        if must not in html:
            fails.append(f"missing {must!r} from the headline block")
    if not subject.startswith("ES Action Map - Fri Sep 18, 2026"):
        fails.append(f"bad subject: {subject}")

    # Mutation proof: a guard that cannot fail is decoration.
    poisoned = html.replace("</div>", '<img src="x.png"></div>', 1)
    if not check(poisoned):
        fails.append("MUTATION PROOF FAILED - guard does not catch an injected <img>")
    poisoned2 = html + "<p>SPY 761.69</p>"
    if not check(poisoned2):
        fails.append("MUTATION PROOF FAILED - guard does not catch a SPY price")

    for f in fails:
        print("FAIL:", f)
    print("RESULT:", "PASS" if not fails else "FAIL")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
