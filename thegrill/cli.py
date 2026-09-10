"""CLI mínima: crear la base de datos y lanzar la cadena."""
import argparse
from datetime import date

from thegrill import db
from thegrill.orchestrator.chain import DbCheckpoints, build_default_chain


def main(argv=None):
    p = argparse.ArgumentParser(prog="thegrill")
    p.add_argument("--db", default="sqlite:///thegrill.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="crea las tablas")
    r = sub.add_parser("run-chain", help="ejecuta la cadena diaria (reanudable)")
    r.add_argument("--date", default=date.today().isoformat())
    args = p.parse_args(argv)

    db.init_engine(args.db)
    if args.cmd == "init-db":
        db.create_all()
        print("tablas creadas en", args.db)
    elif args.cmd == "run-chain":
        db.create_all()
        chain = build_default_chain(DbCheckpoints(db.session_scope), handlers={})
        report = chain.run(date.fromisoformat(args.date))
        for name, res in report.results.items():
            print(f"{name:28s} {res.state.value:16s} {res.detail}")
        if report.skipped:
            print("saltados (día de la semana):", ", ".join(report.skipped))


if __name__ == "__main__":
    main()
