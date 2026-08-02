"""Manual entrypoints:  python -m atlas.cli <command>

  run rera                one RERA registry run
  run magicbricks         one Bangalore portal run (needs APIFY_TOKEN)
  run magicbricks_mysore  one Mysore portal run (needs APIFY_TOKEN)
  daily                   the full daily sequence (rera -> portals -> sweep+tag)
  sweep-and-tag           staleness sweep + legal tagging pass
  health                  per-source health summary
  gate                    Phase-1 gate: consecutive clean ingestion days
  plan                    capital plan: cash bar + countdown
  score                   Deal Score pass (--dry-run / --explain <listing_id>)
  top                     ranked listings with their factor decomposition
  reparse                 replay raw_payloads through the current parser
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
    sub.add_parser("plan", help="capital plan: what you need, and how long")
    gate_p = sub.add_parser("gate", help="Phase-1 gate: consecutive clean days")
    gate_p.add_argument("--days", type=int, default=None,
                        help="clean days required (default 7)")

    score_p = sub.add_parser("score", help="compute Deal Scores")
    score_p.add_argument("--dry-run", action="store_true",
                         help="show the score distribution; write nothing")
    score_p.add_argument("--explain", type=int, metavar="LISTING_ID",
                         help="full factor decomposition for one listing")
    top_p = sub.add_parser("top", help="ranked listings with evidence")
    top_p.add_argument("--limit", type=int, default=10)
    top_p.add_argument("--city", default=None)
    top_p.add_argument("--all", action="store_true",
                       help="include listings you cannot fund today")
    reparse_p = sub.add_parser("reparse",
                               help="replay raw_payloads through the parser")
    reparse_p.add_argument("--source", default=None,
                           help="registry source name (default: all)")
    reparse_p.add_argument("--dry-run", action="store_true")
    digest_p = sub.add_parser("digest", help="build and send the daily briefing")
    digest_p.add_argument("--dry-run", action="store_true",
                          help="render to stdout; store nothing, send nothing")
    digest_p.add_argument("--force", action="store_true",
                          help="re-send even if today's digest already went out")
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
    elif args.command == "plan":
        from atlas.db import get_engine, make_session_factory
        from atlas.plan import build_plan, format_plan
        with make_session_factory(get_engine())() as session:
            print(format_plan(build_plan(session)))
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
    elif args.command == "score":
        from atlas.db import get_engine, make_session_factory
        from atlas.scoring.report import format_explain, format_score_run
        from atlas.scoring.engine import score_listings
        with make_session_factory(get_engine())() as session:
            if args.explain:
                result = score_listings(session, listing_ids=[args.explain],
                                        dry_run=True)
                print(format_explain(session, result, args.explain))
            else:
                result = score_listings(session, dry_run=args.dry_run)
                print(format_score_run(result))
    elif args.command == "top":
        from atlas.db import get_engine, make_session_factory
        from atlas.scoring.engine import latest_scores
        from atlas.scoring.report import format_top
        with make_session_factory(get_engine())() as session:
            rows = latest_scores(session, limit=args.limit, city=args.city,
                                 reachable_only=not args.all)
            print(format_top(rows, reachable_only=not args.all))
    elif args.command == "reparse":
        from atlas.db import get_engine, make_session_factory
        from atlas.ingest.reparse import reparse
        with make_session_factory(get_engine())() as session:
            for result in reparse(session, source=args.source,
                                  dry_run=args.dry_run):
                print(f"{result.source:<24} payloads={result.payloads} "
                      f"parsed={result.parsed} updated={result.listings_updated} "
                      f"posted_at_filled={result.posted_at_filled} "
                      f"unmatched={result.unmatched}"
                      f"{'  (dry run)' if args.dry_run else ''}")
    elif args.command == "digest":
        from atlas.db import get_engine, make_session_factory
        from atlas.report import send_digest
        with make_session_factory(get_engine())() as session:
            sent, text = send_digest(session, dry_run=args.dry_run,
                                     force=args.force)
            print(text)
            print()
            print("[sent]" if sent else
                  "[not sent - dry run]" if args.dry_run else
                  "[not sent - already delivered today, or delivery unconfigured]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
