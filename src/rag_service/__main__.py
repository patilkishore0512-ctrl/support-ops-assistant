"""
CLI for the RAG service.

    python -m rag_service ingest                     # build the index
    python -m rag_service ask "How do I enable CORS?"
    python -m rag_service serve --port 8000          # start the API
"""

from __future__ import annotations

import argparse
import json

from rag_service.config import get_settings
from rag_service.guardrails import configure_logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rag_service")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="parse, chunk, embed and index the knowledge base")
    ing.add_argument("--if-missing", action="store_true", help="skip if an index already exists")
    a = sub.add_parser("ask", help="ask one question")
    a.add_argument("question")
    a.add_argument("--json", action="store_true", help="print the full response as JSON")
    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    configure_logging()
    settings = get_settings()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("rag_service.api:app", host=args.host, port=args.port)
        return 0

    from rag_service.service import Assistant

    if args.cmd == "ingest":
        if args.if_missing and (settings.index_dir / "info.json").exists():
            print(f"index already exists at {settings.index_dir}; skipping")
            return 0
        print(json.dumps(Assistant(settings).ingest(), indent=2))
        return 0

    ans = Assistant(settings).ask(args.question)
    if args.json:
        print(json.dumps(ans, default=lambda o: o.__dict__, indent=2))
    else:
        print(f"[{ans.route}] confidence={ans.confidence} abstained={ans.abstained}\n\n{ans.answer}\n")
        for i, c in enumerate(ans.citations, start=1):
            print(f"  [{i}] {c.source} :: {c.section}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
