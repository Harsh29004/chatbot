"""
Mint an unlimited owner API key.

Owner keys skip all credit checks (see ``backend.shared.auth.verify_api_key``) and
are meant only for the product owners themselves — there is deliberately no
HTTP endpoint for this, so it can't be triggered remotely. Run it locally
on the machine hosting the database:

    python backend/scripts/create_owner_key.py owner@example.com "Harsh"

The raw key is printed once. Store it somewhere safe (password manager,
not source control) — only its hash is kept in the database.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.shared.api_keys import create_owner_key, init_api_key_tables  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python backend/scripts/create_owner_key.py <email> [name]")
        raise SystemExit(1)

    email = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else ""

    init_api_key_tables()
    result = create_owner_key(owner_email=email, owner_name=name)

    print("Owner key created — copy it now, it will not be shown again:\n")
    print(f"  {result['api_key']}\n")
    print(f"  owner_email: {result['owner_email']}")
    print(f"  key_id:      {result['key_id']}")
    print(f"  role:        {result['role']}")


if __name__ == "__main__":
    main()
