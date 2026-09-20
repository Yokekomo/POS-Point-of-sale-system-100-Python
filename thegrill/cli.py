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
    r.add_argument("--restaurant", type=int, default=1, help="id del restaurante")
    w = sub.add_parser("serve", help="arranca la plataforma web")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    m = sub.add_parser("serve-carne", help="arranca la edición de control de carnes")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8001)
    args = p.parse_args(argv)

    db.init_engine(args.db)
    if args.cmd == "init-db":
        db.create_all()
        print("tablas creadas en", args.db)
    elif args.cmd == "run-chain":
        db.create_all()
        chain = build_default_chain(DbCheckpoints(db.session_scope, args.restaurant), handlers={})
        report = chain.run(date.fromisoformat(args.date))
        for name, res in report.results.items():
            print(f"{name:28s} {res.state.value:16s} {res.detail}")
        if report.skipped:
            print("saltados (día de la semana):", ", ".join(report.skipped))
    elif args.cmd == "serve":
        import uvicorn

        from thegrill.web.app import create_app
        uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    elif args.cmd == "serve-carne":
        import uvicorn

        from thegrill.meat.app import create_app
        uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
