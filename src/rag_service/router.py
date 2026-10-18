"""
Question router: knowledge base ("docs") or ticket data ("tickets").

A rule-based router is used on purpose:
  - deterministic and testable (the golden set checks routing),
  - no LLM call, so no added latency or cost on every question,
  - the two domains barely overlap: ticket questions talk about tickets, backlog,
    resolution time, agents and the support products; the docs are about FastAPI.
If neither rule matches, the question goes to the docs, where weak evidence produces
"I don't know" rather than a wrong SQL answer.
"""

from __future__ import annotations

import re

TICKET_ID_RE = re.compile(r"\bTKT-\d{6}\b", re.I)
TICKET_TERMS_RE = re.compile(
    r"\b(tickets?|backlog|resolution times?|time to resolve|support queue|sla|escalat\w*|"
    r"unassigned|agents? (resolved|handled|closed))\b",
    re.I,
)
PRODUCT_RE = re.compile(r"\b(billing|mobile app|web portal|integrations)\b", re.I)
METRIC_RE = re.compile(r"\b(how many|number of|count|average|avg|median|per week|per product|trend|total)\b", re.I)


def route(question: str) -> str:
    if TICKET_ID_RE.search(question) or TICKET_TERMS_RE.search(question):
        return "tickets"
    if PRODUCT_RE.search(question) and METRIC_RE.search(question):
        return "tickets"
    return "docs"
