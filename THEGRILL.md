# THE GRILL — Sistema de Gestión de Cocina & Carne

Programa que reemplaza la cadena nocturna "Daily Morning Chain v2" (scripts
Python sueltos + workbooks Excel) por una aplicación con base de datos como
fuente única de verdad. Especificación de negocio: Albano, 2026-09-10.

## Decisión de plataforma

**Backend Python + base de datos + panel web (PWA).** No un programa de
escritorio Windows ni una app nativa de tienda.

| Necesidad del negocio | Web + backend | Programa Windows | App nativa |
|---|---|---|---|
| Cadena nocturna automática (scheduler 00:07) | Sí, corre en servidor | Necesita el PC encendido | No aplica |
| Registro a pie de cámara (tablet/móvil) | Sí | No | Sí, con doble coste |
| Varios usuarios a la vez (Ahmed, Yuan, Ram, carnicero) | Sí | Un puesto | Sí |
| Trazabilidad HACCP no editable, hora de servidor | Sí | Archivo local editable | Sí |
| Director lo consulta desde fuera | Sí | No | Sí |
| Impresión de etiquetas FEFO en térmica | Servicio local (reutiliza `win32print` del POS) | Directo | Difícil |
| Coste de mantenimiento | Un solo código | Un solo código | Dos plataformas + tiendas |

El código del POS existente (`programa.py`, tkinter + SQLite + win32print)
sigue funcionando tal cual. Su parte de impresión sirve de base para el
servicio de etiquetas.

## Estructura

```
thegrill/
  config.py            FX 1.550 IQD/USD, límites HACCP, ventana 09-20h, supresiones, flags 8 %/15 %
  db.py                SQLAlchemy; SQLite por defecto, PostgreSQL cambiando la URL
  models.py            17 tablas de §5 + checkpoints, audit_log, sent_messages
  rules.py             Reglas §8 como funciones puras (etiqueta manda, TG completo, 3 estados, ...)
  engine/
    fefo.py            Consumo por caducidad, valoración de merma/producción, alertas
    stock.py           Motor v4: ledger, re-anclaje por conteo, floors con base física, genealogía por serial
    roster.py          Fichaje con desfase de medianoche sin falsas alarmas
    cost.py            Landed $/kg, coste por corte, food cost proxy y real
  messaging/guards.py  Ventana horaria, supresiones, anti-duplicado, mutex de sesión, outbox
  orchestrator/chain.py Pasos en orden, checkpoint por paso, idempotente, reintentos 30/60/120 s
  importers/           Fase 1 pendiente: POS PDF, facturas, hojas manuscritas, CSV fichaje
  reports/             Fase 4 pendiente: parte de carne, Daily Report PDF, Morning Brief
  cli.py               `init-db` y `run-chain --date`
tests/                 37 tests que fijan cada regla de negocio
```

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q
python -m thegrill.cli init-db
python -m thegrill.cli run-chain --date 2026-09-10
```

Un paso sin handler queda `BLOCKED`, nunca `DONE`: la cadena no puede
"pasar" un paso que no ha hecho.

## Reglas codificadas (§8) y dónde viven

| Regla | Función | Test |
|---|---|---|
| 1 Etiqueta física manda | `rules.resolve_from_label` | `test_label_wins_over_sheet_and_flags` |
| 2 TG con seriales + piezas y peso/pieza | `rules.validate_tg`, tabla `despiece_primals` (única fuente) | `test_tg_requires_serials_and_cut_detail` |
| 3 Nunca CUT por inferencia | `rules.can_mark_cut`, `stock.drain_primals` (solo por serial) | `test_drain_primals_by_serial_only` |
| 4 Conteo semanal completo | `rules.weekly_count_is_complete/stale` | `test_weekly_count_complete_and_stale` |
| 5 FEFO, coste nunca en blanco | `fefo.consume` lanza `NoCostBasis` | `test_never_blank_cost` |
| 6 Pescado fuera de entrada carne | `rules.is_meat_entry` | `test_fish_excluded_from_meat_entry` |
| 7 Ventana 09:00–20:00 | `guards.Gate` (self-chat, fichaje nocturno y pedidos <08:00 exentos) | `test_outside_window_queues_then_flushes_once` |
| 8 Supresiones Fadi / Olivier | `config.SUPPRESSIONS` | `test_suppressions_drop_not_queue` |
| 9 Mutex sesión WhatsApp | `guards.SessionMutex` | `test_mutex_single_session` |
| 10 Anti-duplicado | `guards.AntiDup` + tabla `sent_messages` | idem ventana |
| 11 Idempotencia + checkpoints | `chain.Chain`, tabla `chain_checkpoints` | `test_sequential_idempotent_and_weekday_steps` |
| 12 Tres estados | `models.SourceStatus`, `rules.classify_source` | `test_three_states_never_collapse` |
| 13 Reconciliación por SKU | `stock.rebuild` (READJUST re-ancla), `stock.mass_balance` | `test_rebuild_in_out_and_readjust_reanchors` |
| 14 Reintentos | `chain.TransientError`, backoff 30/60/120 | `test_transient_retry_with_backoff...` |

## Roadmap

1. **Fase 1 (esta entrega, parte A):** modelo de datos, reglas, motores base, orquestador, guardas. Pendiente parte B: importadores y migración de los .xlsx actuales.
2. Motor de stock v4 conectado a la BD real y mapeo primal→corte completo.
3. Ventas POS, FEFO maestro, coste, roster sobre datos reales.
4. Informes: parte, Daily Report PDF (weasyprint), Morning Brief.
5. Mensajería WhatsApp/Telegram con las guardas ya construidas.
6. Scheduler 00:07 + panel web.
