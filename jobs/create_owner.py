"""Bootstrap the first owner account.

    python -m jobs.create_owner pedram@patexia.com

Prints a single-use setup link. The operator never chooses or sees a password —
the user sets their own through that link.
"""
import sys
from core import auth


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    email = sys.argv[1]
    existing = auth.get_user(email)
    if existing:
        token = auth.new_setup_token(email)
        print(f"{email} already exists — issued a fresh setup link.")
    else:
        token = auth.create_user(email, role="owner")["setup_token"]
        print(f"Created owner {email}.")
    base = "https://vantage-424459368059.us-central1.run.app"
    print(f"\nSetup link (single use, 48h):\n  {base}/setup/{token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
