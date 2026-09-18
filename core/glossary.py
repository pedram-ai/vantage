"""Hover definitions for the UI. Short on screen, full text on hover."""

GLOSSARY = {
    "POC": "Point of Control - the price with the most traded volume in the session. "
           "Tic Toc's highest-weight reference.",
    "VAH": "Value Area High - top of the 70% volume value area. Resistance in a "
           "downtrend, breakout shelf in an uptrend.",
    "VAL": "Value Area Low - bottom of the 70% volume value area. Support in an uptrend.",
    "VWAP": "Volume Weighted Average Price - typical price x volume / volume. First "
            "intraday reference tested in the cash session.",
    "RTH": "Regular Trading Hours - the cash session, 09:30-16:00 ET. Both authors "
           "profile this session.",
    "Overnight": "Globex session, 18:00 ET previous day to 09:30 ET.",
    "Open": "Session opening print. Ranked above close and high/low as a reference.",
    "High": "Session high.",
    "Low": "Session low.",
    "Close": "Session closing print.",
    "HVN": "High Volume Node - a local volume peak at least 1.5x its neighbours; "
           "price tends to stall there.",
    "Session": "The time window profiled: RTH (09:30-16:00 ET) or overnight (18:00-09:30 ET).",
    "TICK": "NYSE TICK. +/-1000 extreme, +/-700 normal trending day, +/-400 dead zone "
            "(Tic Toc does not trade it).",
    "TRIN": "NYSE TRIN (Arms index). Under 1 healthy tape, over 1 weak; 3+ is an "
            "extreme that often marks a short-term bottom.",
    "CALL ZONE": "Price area where Pedram's edge is long - buy calls on the stated trigger.",
    "PUT ZONE": "Price area where the edge is short - buy puts on the stated trigger.",
    "NO TRADE": "Price is between the zones; no edge. Wait for a band.",
    "Open type": "Tic Toc's open taxonomy - where price opens relative to the prior "
                 "value area and POC decides the day type.",
    "Band": "A cluster of levels within 10 points, weighted by how many independent "
            "sources agree. Profile sources count 1.5x author levels.",
    "Trigger": "What price must do at the band before the trade is valid.",
    "Stop": "Level that invalidates the trade.",
    "Target": "First and second profit objectives. Short-dated options decay fast - "
              "take the first target.",
    "Smashlevel": "Smashelito's daily pivot.",
    "FUT": "Final Upside Target (Smashelito). Do not chase longs above it.",
    "FDT": "Final Downside Target (Smashelito). Do not chase shorts below it.",
    "UT1": "Smashelito upside target 1.",
    "UT2": "Smashelito upside target 2.",
    "DT1": "Smashelito downside target 1.",
    "DT2": "Smashelito downside target 2.",
    "Contract": "Active front-month ES contract. Rolls ~8 days before the 3rd Friday "
                "of Mar/Jun/Sep/Dec. History is never back-adjusted.",
}
