# Plataforma de gestión de cocina

Aplicación web para cualquier restaurante. El equipo registra desde el móvil
(temperaturas, mermas, recepciones, producción, limpieza, conteos, con fotos) y
la dirección lo ve todo en un panel con estadísticas, alertas y export para
auditoría.

Incluye además los motores especializados de control de carne desarrollados
para el proyecto The Grill (FEFO, stock por serial de primal, despieces, coste,
fichaje) como módulos opcionales sobre la misma base de datos.

## Por qué web y no un programa de escritorio ni una app de tienda

| Necesidad | Web | Programa Windows | App nativa |
|---|---|---|---|
| Cualquiera entra y mete datos, sin instalar nada | Sí | No | Requiere descarga |
| Registro a pie de cámara desde el móvil | Sí | No | Sí |
| Varios usuarios a la vez | Sí | Un puesto | Sí |
| Hora de servidor no manipulable, registros no editables | Sí | Archivo local editable | Sí |
| La dirección lo consulta desde fuera | Sí | No | Sí |
| Cadena de tareas automática nocturna | Sí, en servidor | El PC ha de estar encendido | No |
| Coste de mantenimiento | Un solo código | Un solo código | Dos plataformas y tiendas |

Se añade a la pantalla de inicio del móvil como una aplicación más. Para
imprimir etiquetas en térmica se usa un pequeño servicio local, apoyado en el
código de impresión del punto de venta que ya existe en este repositorio.

## Los dos roles

**Manager.** Ve el panel de estadísticas, el historial completo de todos,
las fotos de cualquiera, y gestiona alertas, plantillas y equipo. Descarga el
CSV de auditoría. Reparte el código de acceso del restaurante.

**Empleado.** Ve la pantalla de captura y rellena registros con fotos. Consulta
solo lo que él mismo ha enviado. No entra en ninguna pantalla de gestión: cada
ruta del área de manager le responde 403.

Un manager puede ascender a un empleado o desactivar una cuenta, pero no puede
cambiar su propio rol ni desactivarse a sí mismo.

## Cómo se adapta a cualquier restaurante

No hay nada codificado sobre un negocio concreto. Cada restaurante define sus
**plantillas de registro**: nombre, categoría, cuántas veces al día se espera,
si pide foto, y sus campos (texto, número, lista, fecha, sí/no). Un campo
numérico con mínimo y máximo genera alerta automática al salirse; un campo de
fecha avisa cuando la caducidad se acerca, que es la base del control FEFO.

Un restaurante nuevo arranca con siete plantillas listas, que el manager edita
o desactiva: temperatura de refrigeración, temperatura de congelación,
recepción de mercancía, merma, producción, limpieza y conteo de inventario.
Refrigeración y congelación van separadas a propósito, porque cada una tiene su
propio límite legal y un solo campo numérico no puede validar los dos.

## Reglas que el programa no deja saltarse

- Un campo obligatorio vacío rechaza el registro entero. No se guarda a medias.
- Un valor fuera de límites **sí** se guarda, marcado con alerta. Una cámara a
  9 °C tiene que quedar registrada, no rechazada.
- La fecha y la hora las pone el servidor.
- Los registros no se editan ni se borran. Una corrección es un registro nuevo
  que apunta al anterior, y el original queda marcado como corregido.
- Cerrar una alerta exige escribir la acción correctiva, y queda firmado con
  quién y cuándo.
- Ningún restaurante ve datos, fotos ni estadísticas de otro.
- Las contraseñas se guardan con PBKDF2 y sal por usuario. La sesión viaja en
  cookie httponly y en la base solo vive el hash del testigo. Cada formulario
  lleva token CSRF.
- Solo se aceptan imágenes y PDF, hasta 12 MB, y cada archivo guarda su SHA-256.

## Estructura

```
thegrill/
  config.py              FX, límites HACCP, ventana de envío, supresiones, umbrales de precio
  db.py                  SQLAlchemy; SQLite por defecto, PostgreSQL cambiando la URL
  models.py              31 tablas: plataforma, módulos de carne, auditoría
  rules.py               Reglas del negocio de carne como funciones puras
  web/
    auth.py              Contraseñas, sesiones, alta de restaurante, códigos de acceso, roles
    seed.py              Las siete plantillas por defecto
    service.py           Validación, alertas, fotos, estadísticas, export CSV
    app.py               Rutas web de empleado y de manager
    templates/           Doce pantallas, móvil primero, claro y oscuro
  engine/
    fefo.py              Consumo por caducidad y valoración de merma
    stock.py             Motor v4: ledger, re-anclaje por conteo, genealogía por serial
    roster.py            Fichaje con desfase de medianoche
    cost.py              Landed por kg, coste por corte, food cost
  messaging/guards.py    Ventana horaria, supresiones, anti-duplicado, mutex de sesión
  orchestrator/chain.py  Cadena diaria idempotente con checkpoints y reintentos
  importers/             Pendiente: POS PDF, facturas, hojas manuscritas, CSV de fichaje
  reports/               Pendiente: parte de carne, informe diario PDF
  cli.py                 init-db, run-chain, serve
tests/                   93 tests
```

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q
python -m thegrill.cli init-db
python -m thegrill.cli serve --host 0.0.0.0 --port 8000
```

Abre el navegador, pulsa **Dar de alta mi restaurante**, y comparte con tu
equipo el código de ocho caracteres que aparece en el panel. Ellos entran por
**Unirme con un código**.

En producción hay que servir por HTTPS, porque la cookie de sesión se marca
como segura salvo que se defina `GRILL_INSECURE_COOKIE=1`, que es solo para
desarrollo. Las fotos se guardan en la ruta de `GRILL_UPLOAD_DIR`.

## Reglas del módulo de carne y dónde viven

| Regla | Función | Test |
|---|---|---|
| Etiqueta física manda sobre la hoja | `rules.resolve_from_label` | `test_label_wins_over_sheet_and_flags` |
| Cada despiece guarda seriales, piezas y peso por pieza | `rules.validate_tg`, tabla `despiece_primals` | `test_tg_requires_serials_and_cut_detail` |
| Nunca marcar cortado por inferencia | `rules.can_mark_cut`, `stock.drain_primals` | `test_drain_primals_by_serial_only` |
| Conteo semanal completo | `rules.weekly_count_is_complete` | `test_weekly_count_complete_and_stale` |
| FEFO, coste nunca en blanco | `fefo.consume` | `test_never_blank_cost` |
| Pescado fuera del registro de carne | `rules.is_meat_entry` | `test_fish_excluded_from_meat_entry` |
| Ventana de envío y excepciones | `messaging.guards.Gate` | `test_outside_window_queues_then_flushes_once` |
| Supresiones de destinatario | `config.SUPPRESSIONS` | `test_suppressions_drop_not_queue` |
| Una sola sesión de mensajería | `guards.SessionMutex` | `test_mutex_single_session` |
| Idempotencia y checkpoints | `orchestrator.chain.Chain` | `test_sequential_idempotent_and_weekday_steps` |
| Tres estados, nunca colapsar en cero | `models.SourceStatus` | `test_three_states_never_collapse` |
| Reintentos solo en errores transitorios | `chain.TransientError` | `test_transient_retry_with_backoff...` |

## Siguientes pasos

1. Importadores: PDFs del punto de venta, facturas, hojas manuscritas, fichaje.
2. Informe diario en PDF y parte de carne desde la base de datos.
3. Mensajería a WhatsApp y Telegram sobre las guardas ya construidas.
4. Funcionamiento sin cobertura: guardar en el móvil y sincronizar al recuperar señal.
5. Programador nocturno que ejecute la cadena diaria por restaurante.
