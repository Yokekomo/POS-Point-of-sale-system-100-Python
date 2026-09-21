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
    o = sub.add_parser("crear-dueno", help="da de alta al dueño de la plataforma (solo la primera vez)")
    o.add_argument("--email", required=True)
    o.add_argument("--nombre", required=True)
    o.add_argument("--password", required=True)
    o.add_argument("--idioma", default="es")
    b = sub.add_parser("banco", help="monta casas de mentira, las hace trabajar y busca fallos")
    b.add_argument("--casas", type=int, default=50, help="cuántas, la mitad con varias sedes")
    b.add_argument("--dias", type=int, default=30, help="cuántos días de trabajo")
    b.add_argument("--semilla", type=int, default=1, help="la misma semilla, el mismo mes")
    b.add_argument("--martillo", type=int, default=0,
                   help="operaciones al azar por casa, después del mes")
    b.add_argument("--auditar", action="store_true",
                   help="no monta nada: audita lo que ya hay en esa base")
    g = sub.add_parser("purgar-solicitudes",
                       help="borra las solicitudes viejas que no llegaron a cuenta")
    g.add_argument("--dias", type=int, default=180)
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
    elif args.cmd == "crear-dueno":
        db.create_all()
        from thegrill.meat import billing
        with db.session_scope() as session:
            try:
                owner = billing.bootstrap_owner(session, args.email, args.nombre,
                                                args.password, args.idioma)
            except billing.BillingError as e:
                print(e)
                return 1
            print(f"dueño de la plataforma: {owner.name} <{owner.email}>")
    elif args.cmd == "purgar-solicitudes":
        db.create_all()
        from thegrill.meat import privacy
        with db.session_scope() as session:
            print(f"borradas {privacy.purge(session, days=args.dias)} solicitudes")
    elif args.cmd == "banco":
        db.create_all()
        from thegrill import bench
        from thegrill.models import Restaurant
        with db.session_scope() as session:
            if args.auditar:
                casas = [r.id for r in session.query(Restaurant)
                         .filter(Restaurant.platform.isnot(True))]
                hallazgos = [f for rid in casas for f in bench.audit(session, rid)]
                print(f"{len(casas)} casas auditadas · {len(hallazgos)} hallazgos")
                for finding in hallazgos[:50]:
                    print("  !", finding)
                return 1 if hallazgos else 0
            barrio = bench.population(session, houses=args.casas, days=args.dias,
                                      seed=args.semilla)
            print(f"{len(barrio.houses)} casas ({barrio.multisite} con varias sedes) · "
                  f"{barrio.sales} ventas · {barrio.counts} inventarios cerrados")
            golpes = 0
            if args.martillo:
                for casa in barrio.houses:
                    golpes += len(bench.hammer(session, casa, rounds=args.martillo,
                                               seed=casa.restaurant_id))
                barrio.findings = [f for casa in barrio.houses
                                   for f in bench.audit(session, casa.restaurant_id)]
                print(f"martillo: {golpes} operaciones rechazadas como debían")
            if barrio.errors:
                print(f"el banco no pudo hacer {len(barrio.errors)} cosas:")
                for linea in barrio.errors[:20]:
                    print("  ·", linea)
            print(f"hallazgos: {len(barrio.findings)}")
            for finding in barrio.findings[:50]:
                print("  !", finding)
            return 1 if (barrio.findings or barrio.errors) else 0
    elif args.cmd == "serve-carne":
        import uvicorn

        from thegrill.meat.app import create_app
        uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
