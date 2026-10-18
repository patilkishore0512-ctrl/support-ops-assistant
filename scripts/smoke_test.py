"""
Smoke test against a running service (used by CI after `docker run`).

    python scripts/smoke_test.py http://localhost:8000
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def wait_for_health(timeout_s: int = 300) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            return call("GET", "/health")
        except Exception:
            time.sleep(3)
    raise SystemExit("service did not become healthy")


def main() -> int:
    health = wait_for_health()
    print("health:", json.dumps(health, indent=2))
    assert health["index_loaded"], "index not loaded"
    assert health["ticket_store_exists"], "ticket store missing"

    checks = [
        ("How do I configure CORSMiddleware to allow my frontend origin?", "docs", False),
        ("What is the average resolution time per product?", "tickets", False),
        ("What is the capital of France?", "docs", True),
    ]
    failed = 0
    for question, route, abstain in checks:
        r = call("POST", "/ask", {"question": question})
        ok = r["route"] == route and r["abstained"] == abstain and (abstain or r["citations"])
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} [{r['route']}] abstained={r['abstained']} "
              f"confidence={r['confidence']} latency={r['latency_ms']}ms :: {question}")
        print("   ", r["answer"][:200].replace("\n", " "))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
