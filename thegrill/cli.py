"""CLI mínima: crear la base de datos y lanzar la cadena."""
import argparse
from datetime import date

from thegrill import db
from thegrill.orchestrator.chain import DbCheckpoints, build_default_chain


def _lan_addresses() -> list[str]:
    """La dirección de esta máquina en la wifi, para abrirla desde el móvil."""
    import socket
    salida = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.168.1.1", 1))       # no manda nada: solo mira la ruta
            salida.append(probe.getsockname()[0])
    except OSError:
        pass
    return [ip for ip in salida if ip and not ip.startswith("127.")]


def _open_when_ready(puerto: int, espera: float = 40.0) -> None:
    """Abre el navegador cuando la demo esté sirviendo de verdad, no antes.

    Montar la demo la primera vez lleva un minuto largo, y durante ese rato el
    navegador solo sabe decir «no se puede acceder a este sitio web». Quien lo
    ve no tiene manera de saber si aquello está trabajando o se ha roto. Así
    que la pantalla la abre el programa, y la abre cuando hay algo que enseñar.
    """
    import socket
    import threading
    import time
    import webbrowser

    def esperar():
        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            with socket.socket() as prueba:
                prueba.settimeout(0.5)
                if prueba.connect_ex(("127.0.0.1", puerto)) == 0:
                    print(f"  Abriendo http://127.0.0.1:{puerto} …")
                    try:
                        webbrowser.open(f"http://127.0.0.1:{puerto}/")
                    except Exception:
                        pass          # sin navegador —un servidor, Docker—: da igual
                    return
            time.sleep(0.4)

    threading.Thread(target=esperar, daemon=True).start()


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
    d = sub.add_parser("demo", help="monta una demo con un mes de trabajo dentro y la sirve")
    d.add_argument("--puerto", type=int, default=8001)
    d.add_argument("--host", default="0.0.0.0",
                   help="0.0.0.0 para poder abrirla desde el móvil de la misma wifi")
    d.add_argument("--dias", type=int, default=30, help="cuánto trabajo de mentira")
    d.add_argument("--reiniciar", action="store_true", help="borra lo que hubiera y la rehace")
    d.add_argument("--solo-montar", action="store_true", help="la monta y no la sirve")
    d.add_argument("--no-abrir", action="store_true",
                   help="no abre el navegador solo cuando esté lista")
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
        from thegrill.meat import privacy, security
        with db.session_scope() as session:
            print(f"borradas {privacy.purge(session, days=args.dias)} solicitudes")
            # Y los números de envío de hace un mes: ningún teléfono guarda
            # tanto, así que recordarlos ya no evita nada.
            print(f"borrados {security.forget_old_submissions(session)} números de envío")
    elif args.cmd == "demo":
        import os

        from thegrill import bench
        from thegrill.models import Restaurant

        # La demo va por http, también desde el móvil: sin esto la cookie de
        # sesión no se guarda y no se puede entrar.
        os.environ.setdefault("GRILL_INSECURE_COOKIE", "1")
        fichero = args.db.replace("sqlite:///", "")
        if args.reiniciar and fichero and os.path.exists(fichero):
            os.remove(fichero)
        db.create_all()
        with db.session_scope() as session:
            hay = session.query(Restaurant).filter(Restaurant.platform.isnot(True)).count()
            cuentas = (bench.demo(session, days=args.dias) if not hay
                       else bench.demo_accounts(session))
        print()
        print("  CONTROL DE CARNES · demo con un mes de trabajo dentro")
        print("  " + "-" * 66)
        for cuenta in cuentas:
            print(f"  {cuenta.who[:34]:34s} {cuenta.email:26s} {cuenta.password}")
            print(f"  {'':34s} {cuenta.sees}")
        print("  " + "-" * 66)
        print(f"  En este ordenador:  http://127.0.0.1:{args.puerto}")
        for ip in _lan_addresses():
            print(f"  Desde el móvil:     http://{ip}:{args.puerto}   (misma wifi)")
        print("  Para parar: Ctrl+C. Para empezar de cero: --reiniciar")
        print()
        if args.solo_montar:
            return 0
        import uvicorn

        from thegrill.meat.app import create_app
        if not args.no_abrir:
            _open_when_ready(args.puerto)
        uvicorn.run(create_app(args.db), host=args.host, port=args.puerto)
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
