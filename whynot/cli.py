import argparse



def main():
    p = argparse.ArgumentParser(prog="whynot", description="Project WHYNOT: paper-trading research platform")
    sub = p.add_subparsers(dest="command", required=True)
    data = sub.add_parser("data", help="historical data: download, check, and report what is really available")
    data.add_argument("action", choices=["ingest", "check", "report"])
    data.add_argument("--no-minutes", action="store_true", help="skip the 1-minute history (the slow part)")
    data.add_argument("--no-options", action="store_true", help="skip option contracts and bars")
    data.add_argument("--catalog", default="data/whynot.db")
    reg = sub.add_parser("regime", help="the regime detector, walked forward through history")
    reg.add_argument("action", choices=["run", "report"])
    reg.add_argument("--catalog", default="data/whynot.db")
    bt = sub.add_parser("backtest", help="walk the five agents through history")
    bt.add_argument("action", choices=["development", "options-2024"])
    bt.add_argument("--catalog", default="data/whynot.db")
    res = sub.add_parser("research", help="bounded research cycles, champions and the approval queue")
    res.add_argument("action", choices=["cycle1", "status"])
    res.add_argument("--catalog", default="data/whynot.db")
    paper = sub.add_parser("paper", help="paper trading on the Alpaca paper account")
    paper.add_argument("action", choices=["start", "close", "open", "status", "schedule"])
    paper.add_argument("--catalog", default="data/whynot.db")
    dash = sub.add_parser("dash", help="the command center: the city and every dashboard section (read-only)")
    dash.add_argument("--port", type=int, default=8090)
    dash.add_argument("--catalog", default="data/whynot.db")
    ops = sub.add_parser("ops", help="health checks, backups, alerts and the daily report")
    ops.add_argument("action", choices=["health", "backup", "after-session", "test-alert"])
    ops.add_argument("--catalog", default="data/whynot.db")
    args = p.parse_args()

    if args.command == "ops":
        from datetime import datetime
        from .ingest.timeutil import ET
        from .ops import alerts, backup, health
        from .ops.run import _broker, after_session
        if args.action == "health":
            for c in health.checks(args.catalog, _broker(), datetime.now(ET)):
                print(f"{'ok    ' if c['ok'] else 'FAILED'} {c['name']}" + (f": {c['detail']}" if c["detail"] else ""))
        elif args.action == "backup":
            print(backup.backup(args.catalog))
        elif args.action == "after-session":
            after_session(args.catalog)
        else:
            a = alerts.Alerter(args.catalog)
            a.emit("test", f"alerts are working ({datetime.now():%H:%M:%S})")
            print(f"channels: {', '.join(n for n, _ in a.senders)}; sent: {a.flush()}")
        return

    if args.command == "dash":
        import uvicorn
        from .dash.server import create_app
        print(f"\nProject WHYNOT command center: http://127.0.0.1:{args.port}\n")
        uvicorn.run(create_app(args.catalog), host="127.0.0.1", port=args.port, log_level="warning")
        return

    if args.command == "paper":
        from datetime import datetime
        from pathlib import Path
        from .live import run
        print(f"--- whynot paper {args.action} at {datetime.now():%Y-%m-%d %H:%M} ---")
        if args.action == "start":
            print(run.start(args.catalog))
        elif args.action == "close":
            run.close(args.catalog)
            from .ops.run import after_session
            after_session(args.catalog)
        elif args.action == "open":
            run.open_(args.catalog)
            from .ops.run import after_open
            after_open(args.catalog)
        elif args.action == "schedule":
            for label, text in run.schedule_files(Path.cwd()).items():
                target = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
                target.write_text(text)
                print(f"wrote {target}")
        else:
            print(run.status(args.catalog))
        return

    if args.command == "research":
        from pathlib import Path
        from . import walkforward
        from .ingest.catalog import Catalog
        from .researchlab.memory import ResearchDB
        from .sim.agents import AGENTS
        db = ResearchDB(args.catalog)
        if args.action == "cycle1":
            from .researchlab.cycles import run_cycle_1
            text = run_cycle_1(Catalog(args.catalog), walkforward.RegimeStore(args.catalog), db)
            Path("data/reports").mkdir(parents=True, exist_ok=True)
            Path("data/reports/research_cycle_1.md").write_text(text)
            print(text)
            return
        db.seed_champions(AGENTS)
        for a in AGENTS:
            c = db.champion(a)
            print(f"{a:<5} champion {c['version']} ({c['status']})")
        print(f"experiments on record: {db.count_tested()}; waiting for approval: {len(db.approval_queue())}; holdout looks: {len(db.holdout_looks())}")
        for c in db.cycles():
            print(f"cycle {c['id']} [{c['direction']}]: {c['status']}, {c['outcome'] or 'in progress'}")
        return

    if args.command == "backtest":
        from pathlib import Path
        from . import walkforward
        from .ingest.catalog import Catalog
        from .sim.ledger import Ledger
        from .sim.run import development
        cat, store, ledger = Catalog(args.catalog), walkforward.RegimeStore(args.catalog), Ledger(args.catalog)
        Path("data/reports").mkdir(parents=True, exist_ok=True)
        if args.action == "options-2024":
            from .sim.options_run import validation_2024
            text, _ = validation_2024(cat, store, ledger)
            Path("data/reports/options_2024.md").write_text(text)
            print(text)
            return
        text = development(cat, store, ledger)
        Path("data/reports/backtest_development.md").write_text(text)
        print(text)
        return

    if args.command == "regime":
        from pathlib import Path
        from . import walkforward
        from .ingest.catalog import Catalog
        cat, store = Catalog(args.catalog), walkforward.RegimeStore(args.catalog)
        if args.action == "run":
            walkforward.run(cat, store)
        text = walkforward.report(cat, store)
        Path("data/reports").mkdir(parents=True, exist_ok=True)
        Path("data/reports/regime.md").write_text(text)
        print(text)
        return

    if args.command == "data":
        from pathlib import Path
        from .ingest import pipeline
        from .ingest.alpaca import Alpaca
        from .ingest.catalog import Catalog
        cat = Catalog(args.catalog)
        if args.action == "ingest":
            pipeline.ingest_all(cat, Alpaca.from_env(), minutes=not args.no_minutes, options=not args.no_options)
        elif args.action == "check":
            pipeline.validate_all(cat)
        text = pipeline.report(cat)
        Path("data/reports").mkdir(parents=True, exist_ok=True)
        Path("data/reports/data_availability.md").write_text(text)
        print(text)
        return



if __name__ == "__main__":
    main()
