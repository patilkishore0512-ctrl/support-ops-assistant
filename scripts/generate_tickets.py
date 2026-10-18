"""
Synthetic support-ticket generator.

Writes three daily ticket files plus two reference files (customers, agents)
into data/. The files contain deliberate quality problems so the pipeline has
something real to clean, check and quarantine.

Deliberate problems (and roughly how often they appear):
  - exact duplicate rows                      ~3% of rows per file
  - inconsistent casing / whitespace          status, priority, product, ids
  - mixed date formats                        5 formats, mixed per row
  - unparseable dates                         ~0.5%
  - updated_at earlier than created_at        ~0.5%
  - nulls in key columns                      ticket_id, customer_id, priority, product
  - invalid categorical values                status "WIP", priority "P0", product "Legacy CRM"
  - unknown agent / customer ids              referential failures
  - updates of earlier tickets in later files status changes on day 2 and day 3
  - stale (out-of-order) versions             day 3 re-sends an older version of some tickets
  - schema drift                              day 3 adds a "channel" column and changes header casing
  - PII in resolution_text                    emails and phone numbers (used later for log masking)

Usage:
    python scripts/generate_tickets.py --out data --seed 42
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

FILE_DATES = [datetime(2026, 10, 1), datetime(2026, 10, 2), datetime(2026, 10, 3)]
NEW_TICKETS_PER_DAY = [2000, 1200, 1200]
UPDATES_PER_DAY = [0, 450, 450]
STALE_RESENDS_DAY3 = 60
DUPLICATE_RATE = 0.03

N_CUSTOMERS = 800
N_AGENTS = 40

STATUSES = ["open", "pending", "in_progress", "resolved", "closed"]
PRIORITIES = ["low", "medium", "high", "urgent"]
PRODUCTS = ["Billing", "Mobile App", "Web Portal", "API", "Integrations"]
CHANNELS = ["email", "chat", "phone", "web"]

STATUS_VARIANTS = {
    "open": ["open", "Open", "OPEN", " open"],
    "pending": ["pending", "Pending", "PENDING"],
    "in_progress": ["in_progress", "In Progress", "in-progress", "IN_PROGRESS"],
    "resolved": ["resolved", "Resolved", "RESOLVED", "resolved "],
    "closed": ["closed", "Closed", "CLOSED"],
}
PRIORITY_VARIANTS = {
    "low": ["low", "Low", "LOW"],
    "medium": ["medium", "Medium", "MEDIUM", " medium"],
    "high": ["high", "High", "HIGH"],
    "urgent": ["urgent", "Urgent", "URGENT"],
}
PRODUCT_VARIANTS = {
    "Billing": ["Billing", "billing", "BILLING", " Billing "],
    "Mobile App": ["Mobile App", "mobile app", "MOBILE_APP", "mobile-app"],
    "Web Portal": ["Web Portal", "web portal", "web_portal", "WEB PORTAL"],
    "API": ["API", "api", " Api"],
    "Integrations": ["Integrations", "integrations", "INTEGRATIONS"],
}

RESOLUTIONS = [
    "Reset the user's password and confirmed login works.",
    "Refund issued for duplicate charge; customer notified.",
    "Cleared app cache; issue resolved after reinstall.",
    "Rotated API key and updated rate-limit tier.",
    "Re-authorised the integration and resynced records.",
    "Known issue fixed in release 4.2; customer upgraded.",
    "Customer confirmed fix by email at {email}.",
    "Called customer on {phone} and walked through the settings.",
    "Updated billing address and regenerated invoice.",
    "Escalated to engineering; patch deployed and verified.",
]


def fmt_date(dt: datetime, rng: random.Random) -> str:
    """Render a datetime in one of five formats, chosen at random."""
    choice = rng.random()
    if choice < 0.50:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    if choice < 0.70:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if choice < 0.85:
        return dt.strftime("%Y/%m/%d %H:%M")
    if choice < 0.95:
        return dt.strftime("%d-%b-%Y %H:%M")
    return str(int(dt.timestamp()))  # epoch seconds


def messy(value: str, variants: dict[str, list[str]], rng: random.Random) -> str:
    return rng.choice(variants[value])


def messy_id(value: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.03:
        return value.lower()
    if r < 0.05:
        return f" {value} "
    return value


class TicketFactory:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.next_id = 1
        self.customers = [f"CUST-{i:04d}" for i in range(1, N_CUSTOMERS + 1)]
        self.agents = [f"AGT-{i:03d}" for i in range(1, N_AGENTS + 1)]
        # current true state of every ticket, keyed by ticket_id
        self.state: dict[str, dict] = {}
        # every version ever emitted, so day 3 can resend stale ones
        self.history: dict[str, list[dict]] = {}

    def _resolution(self) -> str:
        text = self.rng.choice(RESOLUTIONS)
        name = self.rng.choice(["alex", "priya", "sam", "rahul", "meera"])
        return text.format(
            email=f"{name}.{self.rng.randint(10, 99)}@example.com",
            phone=f"+91 98{self.rng.randint(10000000, 99999999)}",
        )

    def new_ticket(self, file_date: datetime) -> dict:
        tid = f"TKT-{self.next_id:06d}"
        self.next_id += 1
        # created within the 5 weeks before the file date
        created = file_date - timedelta(
            days=self.rng.randint(0, 35),
            hours=self.rng.randint(0, 23),
            minutes=self.rng.randint(0, 59),
        )
        updated = min(created + timedelta(hours=self.rng.randint(1, 240)), file_date + timedelta(hours=20))
        status = self.rng.choices(STATUSES, weights=[25, 15, 15, 30, 15])[0]
        t = {
            "ticket_id": tid,
            "customer_id": self.rng.choice(self.customers),
            "created_at": created,
            "updated_at": updated,
            "status": status,
            "priority": self.rng.choices(PRIORITIES, weights=[35, 35, 20, 10])[0],
            "product": self.rng.choice(PRODUCTS),
            "agent_id": None if status == "open" and self.rng.random() < 0.6 else self.rng.choice(self.agents),
            "resolution_text": self._resolution() if status in ("resolved", "closed") else None,
            "channel": self.rng.choice(CHANNELS),
        }
        self._remember(t)
        return dict(t)

    def update_ticket(self, file_date: datetime) -> dict:
        """Move an existing unresolved-or-resolved ticket forward in its lifecycle."""
        tid = self.rng.choice(list(self.state))
        t = dict(self.state[tid])
        nxt = {
            "open": ["pending", "in_progress", "resolved"],
            "pending": ["in_progress", "resolved"],
            "in_progress": ["resolved", "pending"],
            "resolved": ["closed", "in_progress"],  # reopen happens
            "closed": ["closed"],
        }[t["status"]]
        t["status"] = self.rng.choice(nxt)
        t["updated_at"] = max(
            t["updated_at"] + timedelta(hours=1),
            file_date + timedelta(hours=self.rng.randint(0, 20), minutes=self.rng.randint(0, 59)),
        )
        if t["agent_id"] is None:
            t["agent_id"] = self.rng.choice(self.agents)
        if t["status"] in ("resolved", "closed") and not t["resolution_text"]:
            t["resolution_text"] = self._resolution()
        self._remember(t)
        return dict(t)

    def stale_version(self) -> dict | None:
        candidates = [tid for tid, h in self.history.items() if len(h) >= 2]
        if not candidates:
            return None
        return dict(self.history[self.rng.choice(candidates)][0])

    def _remember(self, t: dict) -> None:
        self.state[t["ticket_id"]] = dict(t)
        self.history.setdefault(t["ticket_id"], []).append(dict(t))


def corrupt(row: dict, rng: random.Random, file_date: datetime) -> dict:
    """Render one clean ticket as a messy output row."""
    out = {
        "ticket_id": messy_id(row["ticket_id"], rng),
        "customer_id": messy_id(row["customer_id"], rng),
        "created_at": fmt_date(row["created_at"], rng),
        "updated_at": fmt_date(row["updated_at"], rng),
        "status": messy(row["status"], STATUS_VARIANTS, rng),
        "priority": messy(row["priority"], PRIORITY_VARIANTS, rng),
        "product": messy(row["product"], PRODUCT_VARIANTS, rng),
        "agent_id": messy_id(row["agent_id"], rng) if row["agent_id"] else "",
        "resolution_text": row["resolution_text"] or "",
        "channel": row["channel"],
    }

    r = rng.random()
    if r < 0.004:
        out["ticket_id"] = ""
    elif r < 0.008:
        out["customer_id"] = ""
    elif r < 0.012:
        out["priority"] = ""
    elif r < 0.016:
        out["product"] = ""
    elif r < 0.020:
        out["status"] = "WIP"
    elif r < 0.024:
        out["priority"] = "P0"
    elif r < 0.028:
        out["product"] = "Legacy CRM"
    elif r < 0.033:
        out["created_at"] = "2026-13-45 99:00:00"
    elif r < 0.038:
        # updated before created
        out["updated_at"] = fmt_date(row["created_at"] - timedelta(days=2), rng)
    elif r < 0.042:
        out["agent_id"] = "AGT-999"
    elif r < 0.046:
        out["customer_id"] = "CUST-9999"
    elif r < 0.049:
        out["updated_at"] = fmt_date(file_date + timedelta(days=30), rng)  # future date
    elif r < 0.052:
        out["ticket_id"] = "TICKET#" + row["ticket_id"][-6:]  # bad id format
    return out


def write_file(path: Path, rows: list[dict], header: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(out_dir: str = "data", seed: int = 42) -> None:
    rng = random.Random(seed)
    out = Path(out_dir)
    (out / "incoming").mkdir(parents=True, exist_ok=True)
    (out / "reference").mkdir(parents=True, exist_ok=True)

    factory = TicketFactory(rng)

    with (out / "reference" / "customers.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "customer_name", "region"])
        for c in factory.customers:
            w.writerow([c, f"Customer {c[-4:]}", rng.choice(["APAC", "EMEA", "AMER"])])
    with (out / "reference" / "agents.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent_id", "agent_name", "team"])
        for a in factory.agents:
            w.writerow([a, f"Agent {a[-3:]}", rng.choice(["Tier1", "Tier2", "Billing"])])

    base_header = [
        "ticket_id", "customer_id", "created_at", "updated_at", "status",
        "priority", "product", "agent_id", "resolution_text",
    ]
    total = 0
    for day_idx, file_date in enumerate(FILE_DATES):
        clean_rows = [factory.new_ticket(file_date) for _ in range(NEW_TICKETS_PER_DAY[day_idx])]
        clean_rows += [factory.update_ticket(file_date) for _ in range(UPDATES_PER_DAY[day_idx])]
        if day_idx == 2:
            for _ in range(STALE_RESENDS_DAY3):
                stale = factory.stale_version()
                if stale:
                    clean_rows.append(stale)

        rows = [corrupt(r, rng, file_date) for r in clean_rows]
        rows += [dict(r) for r in rng.sample(rows, int(len(rows) * DUPLICATE_RATE))]
        rng.shuffle(rows)

        if day_idx == 2:
            # schema drift: new column + different header casing
            header = [h.title() for h in base_header] + ["Channel"]
            rows = [{k.title(): v for k, v in r.items()} for r in rows]
        else:
            header = base_header

        path = out / "incoming" / f"tickets_{file_date:%Y-%m-%d}.csv"
        write_file(path, rows, header)
        total += len(rows)
        print(f"wrote {path} ({len(rows)} rows)")
    print(f"total rows: {total}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generate messy synthetic support tickets")
    p.add_argument("--out", default="data")
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    main(a.out, a.seed)
