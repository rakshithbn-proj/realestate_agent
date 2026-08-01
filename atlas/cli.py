"""Manual entrypoints:  python -m atlas.cli <command>

  run rera                one RERA registry run
  run magicbricks         one Bangalore portal run (needs APIFY_TOKEN)
  run magicbricks_mysore  one Mysore portal run (needs APIFY_TOKEN)
  daily                   the full daily sequence (rera -> portals -> sweep+tag)
  sweep-and-tag           staleness sweep + legal tagging pass
  health                  per-source health summary
  gate                    Phase-1 gate: consecutive clean ingestion days
"""
import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    from atlas.ingest.registry import SOURCES

    parser = argparse.ArgumentParser(prog="atlas")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run one source now")
    # Derived from the registry so a new SourceSpec is runnable immediately —
    # a hardcoded list silently strands newly-added sources (e.g. mysore).
    run_p.add_argument("source", choices=["rera", *SOURCES])
    sub.add_parser("daily", help="full daily sequence (rera, portals, sweep+tag)")
    sub.add_parser("sweep-and-tag", help="staleness sweep + legal tagging")
    sub.add_parser("health", help="per-source health summary")
    gate_p = sub.add_parser("gate", help="Phase-1 gate: consecutive clean days")
    gate_p.add_argument("--days", type=int, default=None,
                        help="clean days required (default 7)")
    args = parser.parse_args(argv)

    from atlas import jobs

    if args.command == "run":
        if args.source == "rera":
            jobs.ingest_rera()
        else:
            jobs.ingest_portal(args.source)
    elif args.command == "daily":
        # Non-zero exit when a step hard-failed, so Task Scheduler / cron
        # surfaces the bad day instead of reporting success.
        return 1 if jobs.run_daily() else 0
    elif args.command == "sweep-and-tag":
        jobs.sweep_and_tag()
    elif args.command == "health":
        from atlas.db import get_engine, make_session_factory
        from atlas.health import source_health_dicts
        with make_session_factory(get_engine())() as session:
            print(json.dumps(source_health_dicts(session), indent=2))
    elif args.command == "gate":
        from atlas.db import get_engine, make_session_factory
        from atlas.gate import REQUIRED_CLEAN_DAYS, gate_status
        required = args.days or REQUIRED_CLEAN_DAYS
        with make_session_factory(get_engine())() as session:
            status = gate_status(session, required_days=required)
        for day in status.days:
            if not day.sources:
                continue
            mark = "PENDING" if day.pending else ("CLEAN" if day.clean else "DIRTY")
            detail = "  ".join(f"{k}={v}" for k, v in sorted(day.sources.items()))
            print(f"{day.day}  {mark:<8} {detail}")
        print(f"\nStreak: {status.streak}/{status.required_days} clean days "
              f"-> Phase-1 gate {'MET' if status.met else 'NOT MET'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
