# Plan: partir archivos grandes en sub-paquetes

Estado: **5 de 6 paquetes aplicados** (2026-06-08). Solo queda `api_auth`,
diferido por su criticidad (todos los `api_*` importan `jwt_required` de ahí).

| # | Paquete | Estado | Rutas |
|---|---|---|---|
| 1 | `app/routes/herramientas_api/` | ✅ aplicado | 33 |
| 2 | `app/routes/api_horas/` | ✅ aplicado | 17 |
| 3 | `app/routes/api_prenomina/` | ✅ aplicado | 17 |
| 4 | `app/routes/api_trabajadores/` | ✅ aplicado | 22 |
| 5 | `app/routes/api_ajustes/` | ✅ aplicado | 9 |
| 6 | `app/routes/api_auth/` | ⏳ pendiente | 21 |

Validación post-refactor: `create_app()` arranca limpio y el total de rutas
de la app se mantiene en 215. Cada paquete pasó diff IDÉNTICO contra su
snapshot pre-refactor (mismos `endpoint name` + método HTTP + URL).

Replica el patrón ya hecho en `app/routes/inventario_api/`:

```
app/routes/inventario_api/
├── __init__.py     # re-exporta bp + helpers públicos
├── _core.py        # blueprint + decoradores + schemas + serializers + helpers
├── productos.py    # endpoints CRUD de productos
├── almacenes.py    # endpoints almacenes + estantes
├── movimientos.py  # ENTRADA / SALIDA / AJUSTE / TRASPASO
├── solicitudes.py  # ciclo de solicitudes + PDF
├── catalogo.py     # proyectos + categorías + importación Excel
├── reportes.py     # exports Excel
├── etiquetas.py    # etiquetas Avery + órdenes de compra express
└── tomas.py        # tomas físicas de inventario
```

El **contrato externo no cambia**: el blueprint `bp` se sigue registrando como
antes (`app/__init__.py` no necesita modificación), y las URLs/métodos/payloads
quedan idénticos. Lo único que cambia es **dónde vive el código** internamente.

---

## Por qué importa

Archivos > 1000 líneas que justificaron el refactor:

| Archivo original | Líneas | Endpoints | Estado |
|---|---|---|---|
| `app/routes/api_trabajadores.py` | 2059 | 22 | ✅ partido |
| `app/routes/api_auth.py` | 1745 | 21 | ⏳ pendiente |
| `app/routes/herramientas_api.py` | 1603 | 33 | ✅ partido |
| `app/routes/api_prenomina.py` | 1175 | 17 | ✅ partido |
| `app/routes/api_horas.py` | 1079 | 17 | ✅ partido |
| `app/routes/api_ajustes.py` | 510 | 9 | ✅ partido |

Problemas concretos que esto provoca:

1. **Cualquier cambio toca un archivo enorme** → conflictos al hacer merge,
   diffs ilegibles, search/replace que matchea casualmente en otra sección.
2. **Imports cíclicos invisibles**: como todo vive en un módulo, no se
   detectan dependencias hasta que el archivo es inmanejable.
3. **`grep`/IDE lentos** y peor "jump to definition" — la herramienta tiene
   que abrir el archivo entero.
4. **Onboarding**: un nuevo dev tarda más en mapear "dónde está X" cuando
   el archivo mezcla helpers, schemas, serializers y 30 endpoints.

---

## Reglas comunes para todos los splits

1. **`_core.py`** (en cada paquete) tiene: el `Blueprint(...)`, decoradores
   de auth, schemas Marshmallow/pydantic, serializers (`_to_dict`),
   helpers internos compartidos. **Nunca rutas** (excepto `/health` trivial
   si aplica).
2. **`__init__.py`** importa todos los sub-módulos para que sus `@bp.route(...)`
   se registren. Re-exporta `bp` + helpers públicos usados desde fuera del
   paquete (los que otros `api_*` ya importan vía `from app.routes.X import Y`).
3. **No tocar URLs, métodos, payloads, respuestas, códigos HTTP, auth.**
   Solo se mueve código entre archivos. El test es: misma cantidad de rutas
   antes y después, idéntico endpoint name.
4. **Verificación obligatoria por paso**: `from app import create_app; create_app()`
   + conteo de rutas por blueprint debe coincidir 1:1.
5. **Un paquete a la vez**, commit independiente por paquete.

---

## Splits propuestos

### 1. `api_trabajadores.py` (2059 → 8 archivos) — ✅ APLICADO

22 endpoints agrupados por dominio funcional. Líneas finales por archivo:

```
app/routes/api_trabajadores/
├── __init__.py        39    re-exporta bp + procesar_excel_trabajadores
├── _core.py          308    bp, _authorized, _row_summary, _full_detail,
│                            _ADMIN_ONLY_FIELDS, _COORD_ALLOWED_FIELDS,
│                            _apply_payload, _replace_credenciales, _save_foto
├── crud.py           391    listar, ficha-tecnica, obtener, crear,
│                            actualizar, dar_baja, reactivar, bulk_accion
├── timeline.py       238    /<id>/timeline (horas + ausencias + ajustes + préstamos)
├── credenciales.py   128    credenciales-lista, POST /<id>/credenciales
├── multimedia.py     209    foto (GET/POST), thumb, documentos (POST/GET/DELETE)
├── importar.py       598    plantilla-importar, procesar_excel_trabajadores, importar
└── exportar.py       306    exportar_uno, bulk_exportar, exportar_todos,
                             _build_export_styles, _HEADERS_EXPORT, _row_values
```

**Riesgo**: medio (resultó manejable). El módulo es grande y entre helpers
hay dependencias (p. ej. `_full_detail` usa `_mask_pii` que viene del legacy
`trabajadores.py`). Cuando se eliminen las rutas legacy (pendiente), esos
helpers deberán moverse a `_core.py` o `app/utils/`.

**Símbolos externos** que otros archivos importan: solo
`procesar_excel_trabajadores` (usado por `app/routes/trabajadores.py` legacy).
Re-exportado en `__init__.py`.

---

### 2. `api_auth.py` (1745 → ~7 archivos) — ⏳ PENDIENTE

21 endpoints + ~60 helpers (encode/decode JWT, cookies, 2FA, backup codes,
notificaciones de device, etc).

```
app/routes/api_auth/
├── __init__.py        # re-exporta bp + jwt_required + helpers públicos
├── _core.py           # bp, constantes (_RT_COOKIE, _MAX_*_LEN, _JWT_ISS/AUD,
│                      # ACCESS_TOKEN_LIFETIME_MINUTES, etc.),
│                      # _no_store_on_auth_responses
├── tokens.py          # _encode_access_token, _encode_pre_2fa_token,
│                      # _revoke_jti, _is_jti_revoked, _set_rt_cookie,
│                      # _clear_rt_cookie, _hash_token, _rt_cookie_samesite
├── jwt_required.py    # decorador jwt_required (lo importa todo el resto del API)
├── login.py           # /login, /verify-2fa, /refresh, /logout
├── twofa.py           # /setup-2fa, /confirm-2fa, /disable-2fa,
│                      # /backup-codes (GET/POST/DELETE),
│                      # _hash_backup_code, _format_backup_code,
│                      # _generate_backup_codes, _try_consume_backup_code,
│                      # _count_active_backup_codes,
│                      # _BACKUP_CODE_ALPHABET, _BACKUP_CODES_COUNT
├── sessions.py        # /sessions (GET), /sessions/<id> (DELETE),
│                      # /sessions/all (DELETE),
│                      # _device_fingerprint, _is_known_device,
│                      # _notify_new_device_login
└── perfil.py          # /me, /me/activity, /profile, /profile/foto,
                       # /change-password, /users (lista admin-light),
                       # /users/<id>, /users/<id>/foto
```

**Riesgo**: **ALTO**. Es el archivo más sensible — todos los `api_*` importan
`jwt_required` de aquí. Cualquier import circular rompe el arranque.

**Símbolos externos** que otros archivos importan:
- `jwt_required` (TODOS los `api_*` la usan)
- `_DUMMY_PW_HASH, _check_lockout, _clear_login_failures, _format_ttl,
   _hash_token, _LOGIN_FAILS_WINDOW, _LOGIN_FAILS_THRESHOLD,
   _LOCKOUT_DURATIONS, _LOCKOUT_LEVEL_TTL, _register_login_failure`
  (importados desde `app/routes/auth.py` legacy).

**Mitigación**:
- `jwt_required.py` solo importa de `_core.py` y `tokens.py` (NO de los
  módulos de endpoints). Así rompe el ciclo.
- Tras el split, **antes** de borrar `auth.py` legacy, mover los helpers
  `_check_lockout` etc. a `app/utils/auth.py` (o a `api_auth/_core.py`).

**Orden sugerido**: hacer ESTE split **al final**, después de los demás —
así si algo se rompe, no arrastra a toda la app. (Los otros 5 ya están hechos;
este es el siguiente paso natural.)

---

### 3. `herramientas_api.py` (1603 → 9 archivos) — ✅ APLICADO

33 endpoints, agrupados por sub-recurso (`/herramientas/*`,
`/herramientas-unidades/*`, `/asignaciones-herramienta/*`, etc.).
Líneas finales por archivo:

```
app/routes/herramientas_api/
├── __init__.py        35    re-exporta bp
├── _core.py          402    bp, schemas Marshmallow (14), serializers (8),
│                            helpers de QR/fotos/eventos
├── catalogo.py       249    /herramientas/* CRUD del catálogo de tipos,
│                            /herramientas/clasificaciones,
│                            /herramientas/stats,
│                            /herramientas-categorias/*
├── unidades.py       276    /herramientas-unidades/* (CRUD, eventos, QR, validar)
├── asignaciones.py   177    /asignaciones-herramienta/* (asignar, listar, devolver)
├── mantenimientos.py 167    /mantenimientos-herramienta/* (abrir, listar, cerrar)
├── incidencias.py    122    /incidencias-herramienta/* (reportar, listar, atender)
├── bajas.py          293    /solicitudes-baja-herramienta/* (solicitar, autorizar,
│                            rechazar, ejecutar, dar-baja directo)
└── multimedia.py     100    /herramientas-unidades/<uid>/fotos,
                             /herramientas-unidades/<uid>/media/<media_id>
```

**Riesgo**: bajo (confirmado). Los endpoints son funcionalmente independientes
— pocos helpers compartidos.

**Símbolos externos**: ninguno (no se importa nada de `herramientas_api` desde
otros archivos). Solo `bp` se expone en `__init__.py`.

---

### 4. `api_prenomina.py` (1175 → 5 archivos) — ✅ APLICADO

17 endpoints, agrupados por flujo: vista semanal, edición/cierre,
descuentos/depósitos, impresión/correo/excel. Líneas finales:

```
app/routes/api_prenomina/
├── __init__.py        30    re-exporta bp
├── _core.py          161    bp, _parse_fecha, _reportes_de_semana,
│                            _trabajador_min, _num, _prenomina_dict,
│                            _build_recibos_data, _render_recibos_pdf
├── semanas.py        381    /semanas (listar), /semanas/<f>/preview,
│                            /semanas/<f>/guardar, /semanas/<f>/editar,
│                            /semanas/<f>/cerrar
├── ajustes.py        292    /descuentos (POST), /descuentos/<id> (DELETE),
│                            /depositos (POST), /depositos/<id> (DELETE),
│                            /viaticos (PATCH), /festivos (PATCH)
└── envio.py          401    /semanas/<f>/imprimir (PDF),
                             /semanas/<f>/trabajadores/<t>/imprimir (PDF),
                             /semanas/<f>/trabajadores/<t>/correo,
                             /semanas/<f>/correo (bulk),
                             /semanas/<f>/correo/bulk,
                             /semanas/<f>/excel
```

**Riesgo**: bajo-medio (confirmado). La lógica de cálculo
(`calcular_preview_prenomina`) viene del legacy `prenomina.py`. Cuando se
elimine ese archivo, esa función hay que moverla a `_core.py` o a
`app/utils/payroll.py`.

**Nota**: `_EMAIL_RE` (que aparecía en el plan original) terminó en
`importar.py` de `api_trabajadores` — no en este paquete. En `api_prenomina`
no se usaba.

---

### 5. `api_horas.py` (1079 → 6 archivos) — ✅ APLICADO

17 endpoints, mezclan flujo web/escritorio + flujo móvil (RFID/QR).
Líneas finales:

```
app/routes/api_horas/
├── __init__.py        31    re-exporta bp + _puede_acceder_proyecto
├── _core.py          112    bp, DIAS_SEMANA, INCIDENCIAS,
│                            _is_coordinador, _puede_acceder_proyecto,
│                            _hora_a_str, _parse_time, _reporte_row,
│                            _registro_dict, _trabajador_row, _semana_fechas
├── reportes.py       218    /reportes (GET, POST), /proyectos-disponibles,
│                            /reportes/<id> (GET), /reportes/<id>/cerrar
├── registros.py      377    /reportes/<id>/registros (POST),
│                            /reportes/<id>/registros/bulk,
│                            /registros/<id> (PUT, DELETE),
│                            _validar_y_aplicar_registro
├── movil.py          188    /movil/resumen, /qr-check
└── rfid_qr.py        258    /qr/trabajadores, /qr/generar/<t>,
                             /qr/imagen/<qr>, /qr/imagen/<int:t>,
                             /rfid/asociar, /rfid/trabajadores-reporte/<r>,
                             _normalizar_uid
```

**Riesgo**: bajo (confirmado). Los flujos están bien separados.

**Símbolos externos**: `_puede_acceder_proyecto` re-exportado en `__init__.py`
porque `app/realtime.py` lo importa para filtrar emits a coordinadores con
scope sobre el proyecto.

---

### 6. `api_ajustes.py` (510 → 4 archivos) — ✅ APLICADO

9 endpoints. Aunque estaba en el límite, se partió por consistencia con el
resto. Líneas finales:

```
app/routes/api_ajustes/
├── __init__.py        22    re-exporta bp
├── _core.py           39    bp, _num, _periodo_row
├── periodos.py       287    /periodos (listar, crear, detalle, cerrar),
│                            /periodos/<id>/excel
└── descuentos.py     205    /periodos/<id>/descuentos (POST),
                             /descuentos/<id> (DELETE),
                             /descuentos/bulk-delete (POST),
                             /trabajadores-disponibles
```

---

## Orden de ejecución (histórico)

| # | Paquete | Estado | Razón |
|---|---|---|---|
| 1 | `herramientas_api/` | ✅ hecho | Aislado, sin dependencias externas. Buen ensayo. |
| 2 | `api_horas/` | ✅ hecho | Bajo riesgo, flujos bien separados. |
| 3 | `api_prenomina/` | ✅ hecho | Helpers del legacy `prenomina.py` se quedan importables. |
| 4 | `api_trabajadores/` | ✅ hecho | Más grande pero ya bien comprendido. |
| 5 | `api_ajustes/` | ✅ hecho | Opcional / cosmético — se hizo por consistencia. |
| 6 | `api_auth/` | ⏳ pendiente | **Último**. Si se rompe, arrastra toda la app. |

Cada paso es **independiente y reversible**:

```bash
git checkout -b refactor/split-herramientas-api
# ... aplicar split
git commit -m "refactor: partir herramientas_api.py en sub-paquete por dominio"
# verificar y mergear; si algo sale mal, revert
```

---

## Smoke test estándar (por paso)

```python
from app import create_app
app = create_app()

# 1. App arranca
print('app ok')

# 2. Mismo blueprint, misma cantidad de rutas
import collections
rules_por_bp = collections.Counter(r.endpoint.split('.')[0] for r in app.url_map.iter_rules())
print(rules_por_bp)
# Comparar contra valores conocidos antes del refactor.

# 3. URLs idénticas
urls = sorted(str(r) for r in app.url_map.iter_rules() if r.endpoint.startswith('<paquete>.'))
# Diffear contra snapshot pre-refactor.

# 4. Endpoints (nombre) idénticos
eps = sorted(r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith('<paquete>.'))
# Diffear contra snapshot pre-refactor.
```

---

## Lo que NO hace este refactor

- No cambia URLs, métodos HTTP, payloads, respuestas, códigos de estado, auth.
- No toca BD, migraciones, FKs, índices.
- No toca CORS/CSP/Talisman/Socket.IO.
- No agrega features ni cambia comportamiento — solo mueve código.
- No elimina las rutas legacy (eso es un refactor separado — ver
  `docs/REFACTOR_ELIMINAR_LEGACY.md` cuando se planifique).

---

## Pendiente cruzado

El refactor "eliminar rutas legacy" depende en parte de éste:
- Algunos helpers reusados de los módulos legacy (`_mask_pii`, `_parse_date`,
  `calcular_preview_prenomina`, etc.) deberán moverse a `_core.py` del
  sub-paquete correspondiente **o** a `app/utils/`.
- Eso facilita borrar después los archivos legacy sin romper imports.

---

## Resultado del refactor (2026-06-08)

5 archivos monolíticos → 5 paquetes con 32 módulos:

| Paquete | Líneas pre | Archivos post | Rutas |
|---|---|---|---|
| `herramientas_api/` | 1603 | 9 | 33 |
| `api_horas/` | 1079 | 6 | 17 |
| `api_prenomina/` | 1175 | 5 | 17 |
| `api_trabajadores/` | 2059 | 8 | 22 |
| `api_ajustes/` | 510 | 4 | 9 |
| **Total partido** | **6426** | **32** | **98** |

`api_auth` (1745 líneas, 21 rutas) sigue como archivo único.

Total de rutas de la app sin cambios: **215** antes y después.
