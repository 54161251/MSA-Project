"""Command line entry points (work the same on Windows, macOS and Linux).

    python -m msa.cli seed [--reset] [--approve-all]   build the demo database
    python -m msa.cli api                              start the API on :8000
    python -m msa.cli ui                               start the Streamlit UI on :8501
    python -m msa.cli daily                            run the daily check once
    python -m msa.cli mcp                              run the safety tools MCP server (stdio)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="msa")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--reset", action="store_true", help="drop all tables first")
    s.add_argument("--approve-all", action="store_true",
                   help="seed staff approves every agent draft so the demo starts published")
    a = sub.add_parser("api")
    a.add_argument("--port", type=int, default=8000)
    sub.add_parser("ui")
    sub.add_parser("daily")
    sub.add_parser("mcp")
    args = p.parse_args(argv)

    if args.cmd == "seed":
        from msa.core import seed

        done = seed.run(reset=args.reset, approve_all=args.approve_all)
        print("Seeded:", ", ".join(done))
    elif args.cmd == "api":
        import uvicorn

        uvicorn.run("msa.api.main:app", host="127.0.0.1", port=args.port)
    elif args.cmd == "ui":
        home = Path(__file__).parent / "ui" / "Home.py"
        if not home.exists():
            print("The UI (src/msa/ui) isn't merged yet.")
            return 1
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(home)])
    elif args.cmd == "daily":
        from msa.core.db import init_db
        from msa.core.scheduler import run_daily

        init_db()
        print(json.dumps(run_daily(), indent=2, default=str))
    elif args.cmd == "mcp":
        try:
            from msa.safety_tools.server import main as mcp_main
        except ImportError:
            print("The safety tools (src/msa/safety_tools) aren't merged yet.")
            return 1
        mcp_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
