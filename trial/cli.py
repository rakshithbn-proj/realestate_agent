"""Offline CLI — test the pipeline without the server.

  python -m trial.cli ingest-fixture     # feed the saved MagicBricks sample through ingest
  python -m trial.cli run [--source X]   # scrape for real (needs APIFY_TOKEN)
  python -m trial.cli report             # regenerate reports/trial-summary.md
"""
import argparse
import json
import logging

from . import config, db, monitor, topsheet
from .scrape import get_token, ingest_fixture, scrape_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    p = argparse.ArgumentParser(prog="trial")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest-fixture")
    runp = sub.add_parser("run")
    runp.add_argument("--source", choices=list(config.SOURCES), default=None)
    runp.add_argument("--token", default=None)
    sub.add_parser("report")
    sub.add_parser("topsheet")
    sub.add_parser("rera")                      # no token needed
    rp = sub.add_parser("rera-reparse")          # replay an archived run
    rp.add_argument("path")

    args = p.parse_args()
    conn = db.connect()
    try:
        if args.cmd == "ingest-fixture":
            ingest_fixture(conn, "magicbricks",
                           config.BASE_DIR / "fixtures" / "magicbricks_sample.json")
            print(monitor.write_report(conn))
        elif args.cmd == "run":
            token = get_token(args.token)
            sources = [args.source] if args.source else list(config.SOURCES)
            for s in sources:
                scrape_source(conn, s, token)
            print(monitor.write_report(conn))
        elif args.cmd == "report":
            print(monitor.write_report(conn))
            print(json.dumps(monitor.summary(conn)["cost"], indent=2))
        elif args.cmd == "topsheet":
            print(topsheet.write_topsheet(conn))
        elif args.cmd == "rera":
            from .sources import rera
            rera.collect(conn)
            print(monitor.write_report(conn))
        elif args.cmd == "rera-reparse":
            from .sources import rera
            rera.ingest_file(conn, args.path)
            print(monitor.write_report(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
