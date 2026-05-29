"""Live smoke test for DevinClient — creates a real session, reads it back, terminates it.

Run from cognition_remediation/:
    python scripts/test_devin_live.py

Uses credentials from .env. Session is terminated immediately after creation
so cost is minimal (typically < $0.01).
"""

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "..", "pypackages"))

from app.devin_client import DevinClient, DevinAPIError


def main() -> None:
    api_key = os.environ.get("DEVIN_API_KEY")
    org_id = os.environ.get("DEVIN_ORG_ID")
    if not api_key or not org_id:
        print("ERROR: DEVIN_API_KEY and DEVIN_ORG_ID must be set in .env")
        sys.exit(1)

    client = DevinClient(api_key=api_key, org_id=org_id)

    print("1. Creating session...")
    session_id = client.create_session(
        prompt="Print the string 'hello world' to stdout and exit.",
        repo_url="https://github.com/hchang19/superset",
        issue_id=0,
    )
    print(f"   session_id: {session_id}")

    print("2. Fetching session status...")
    resp = client.get_session(session_id)
    print(f"   status:      {resp.status}")
    print(f"   cost_usd:    {resp.cost_usd}")
    print(f"   session_url: {resp.session_url}")
    print(f"   pr_url:      {resp.pr_url}")
    print(f"   output:      {resp.output!r}")

    print("3. Terminating session...")
    try:
        client.terminate_session(session_id)
        print("   terminated.")
    except DevinAPIError as e:
        print(f"   terminate failed (may already be terminal): {e}")

    print("4. Fetching final status after termination...")
    resp = client.get_session(session_id)
    print(f"   status: {resp.status}")

    print("\nSummary:")
    print(json.dumps({
        "session_id": resp.session_id,
        "status": resp.status,
        "cost_usd": resp.cost_usd,
        "session_url": resp.session_url,
    }, indent=2))


if __name__ == "__main__":
    main()
