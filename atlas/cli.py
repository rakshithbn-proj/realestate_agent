"""Manual entrypoints:  python -m atlas.cli <command>

  run rera          one RERA registry run
  run magicbricks   one portal run (needs APIFY_TOKEN)
  sweep-and-tag     staleness sweep + legal tagging pass
  health            per-source health summary
"""
import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run one source now")
    run_p.add_argument("source", choices=["rera", "magicbricks"])
    sub.add_parser("sweep-and-tag", help="staleness sweep + legal tagging")
    sub.add_parser("health", help="per-source health summary")
    args = parser.parse_args(argv)

    from atlas import jobs

    if args.command == "run":
        if args.source == "rera":
            jobs.ingest_rera()
        else:
            jobs.ingest_portal(args.source)
    elif args.command == "sweep-and-tag":
        jobs.sweep_and_tag()
    elif args.command == "health":
        from atlas.db import get_engine, make_session_factory
        from atlas.health import source_health_dicts
        with make_session_factory(get_engine())() as session:
            print(json.dumps(source_health_dicts(session), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
