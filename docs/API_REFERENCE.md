# Referencia de la API (216 rutas)

Inventario completo de endpoints, agrupado por blueprint. Extraído del código
el 2026-06-09 con `scripts/gen_api_inventory.py` — regenerar tras agregar rutas.

**Convenciones de esta página**

- Todos los endpoints requieren JWT (`Authorization: Bearer ...`) salvo que se
  indique lo contrario (`login`, `refresh`, `/health`).
- **Auth** indica el gate adicional al JWT:
  - *admin* = rol `admin` o `super_admin` (verificado dentro del endpoint con
    `require_admin()`/`is_admin()`).
  - *inv-lectura* = `_require_inventario`: roles `inventario`,
    `solicitante_material`, `coordinador`, `admin`, `super_admin`.
  - *inv-admin* = `_require_inventario_admin`: roles `inventario`, `admin`,
    `super_admin`.
  - *login* = `_require_login`: cualquier usuario autenticado.
  - *RL* = tiene `@limiter.limit` propio además del default global.
- Respuestas de error estándar: 401 (sin/expirado JWT), 403 (rol), 404, 422
  (validación), 429 (rate-limit), 419 (CSRF en flujos con cookie), 500 JSON.

---

## Salud

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` y `/health` | Liveness probe, `{"status":"ok"}`. Sin auth. |
| GET | `/api/v1/health` | Health del módulo inventario. Sin auth. |

---

## `api_auth` — `/api/auth`

Autenticación, sesión y 2FA. Detalle de flujos en `SEGURIDAD.md`.

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/login` | RL | Valida credenciales. Si el user tiene 2FA devuelve reto; si no, access token + cookie `skilled_rt` |
| POST | `/verify-2fa` | RL | Segundo paso: código TOTP o backup code (anti-replay 90 s en Redis) |
| POST | `/refresh` | cookie + `X-Requested-With`, RL | Rota el refresh token y emite nuevo access token |
| POST | `/logout` | cookie + `X-Requested-With` | Revoca el refresh token y limpia la cookie |
| GET | `/me` | RL | Perfil propio (rol, nombre, foto, 2FA habilitado) |
| GET | `/me/activity` | RL | Últimas acciones del PROPIO usuario en el audit log |
| GET | `/users` | — | Directorio de usuarios (oculta `role`/`totp_enabled`/`last_seen` a no-admin) |
| GET | `/users/<id>` | — | Detalle de un usuario para el directorio |
| GET | `/users/<id>/foto` | — | Foto de perfil servida con JWT |
| POST | `/profile` | RL | Actualiza perfil propio (multipart, soporta foto) |
| DELETE | `/profile/foto` | RL | Elimina la foto de perfil propia |
| POST | `/change-password/<id>` | RL | Cambia password propia (exige `current_password` y TOTP si hay 2FA); invalida JWTs vía `password_version` |
| GET | `/sessions` | — | Sesiones activas propias (refresh tokens vivos) |
| DELETE | `/sessions/<id>` | RL | Revoca una sesión propia |
| DELETE | `/sessions/all` | RL | Pánico: revoca TODAS las sesiones propias y fuerza logout de WebSockets |
| POST | `/setup-2fa` | RL | Paso 1: genera secret TOTP (reauth con password) |
| POST | `/confirm-2fa` | RL | Paso 2: valida el código y persiste el secret (cifrado Fernet) |
| POST | `/disable-2fa` | RL | Desactiva 2FA propio (password + código) |
| GET | `/backup-codes` | — | Cuántos códigos de respaldo quedan (no los revela) |
| POST | `/backup-codes` | RL | Genera 10 códigos, mostrados UNA sola vez |
| DELETE | `/backup-codes` | RL | Revoca todos los códigos de respaldo |

---

## `api_trabajadores` — `/api/trabajadores`

Empleados. Coordinador solo accede a trabajadores de sus proyectos
(`_authorized`); whitelist de campos editables por rol en el PUT.

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/` | — | Listado paginado. Filtros `q`, `estado=activos\|bajas\|todos` (bajas solo admin), orden `sort`/`dir` (ver `ORDENAMIENTO_LISTADOS.md`) |
| GET | `/ficha-tecnica` | — | Listado con info médica y contacto de emergencia |
| GET | `/<id>` | — | Detalle completo (campos filtrados por rol) |
| POST | `/` | admin, RL | Alta de trabajador |
| PUT/POST | `/<id>` | — | Edición con whitelist por rol; campos prohibidos se ignoran y regresan en `warnings` |
| DELETE | `/<id>` | admin | Baja lógica (`activo=False`, `fecha_baja`) |
| POST | `/<id>/reactivar` | admin | Revierte la baja |
| POST | `/bulk` | admin | Acción masiva sobre una selección |
| GET | `/<id>/timeline` | — | Eventos cronológicos consolidados del trabajador |
| GET | `/<id>/notas` | — | Notas internas, desc por fecha (ver `NOTAS_TRABAJADOR.md`) |
| POST | `/<id>/notas` | — | Crea nota `{texto}`; emite `nota:changed` |
| DELETE | `/<id>/notas/<nota_id>` | autor o admin | Elimina nota |
| GET | `/credenciales-lista` | — | Credenciales de planta de todos los trabajadores |
| POST | `/<id>/credenciales` | — | Guarda credenciales de planta del trabajador |
| POST | `/<id>/foto` | — | Sube foto de perfil (valida magic bytes, convierte a WebP) |
| GET | `/<id>/foto` | — | Foto a tamaño completo |
| GET | `/<id>/foto/thumb` | — | Miniatura |
| POST | `/<id>/documentos` | RL | Sube documento (PDF/JPG/PNG/HEIC ≤20 MB, magic bytes) |
| GET | `/documentos/<doc_id>` | — | Descarga documento (X-Accel-Redirect en prod) |
| DELETE | `/documentos/<doc_id>` | — | Elimina documento |
| GET | `/plantilla-importar` | — | Excel plantilla para carga masiva |
| POST | `/importar` | RL | Importa empleados desde Excel |
| GET | `/<id>/exportar` | — | Excel de un empleado |
| POST | `/bulk-exportar` | — | Excel de la selección recibida |
| GET | `/exportar-todos` | — | Excel de toda la plantilla |

---

## `api_proyectos` — `/api/proyectos`

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/` | — | Listado con filtros `q` y `estado=activos\|inactivos\|todos` |
| GET | `/mios` | — | Proyectos del usuario (coordinador: solo los suyos) |
| GET | `/meta` | — | Datos auxiliares para el modal de alta/edición |
| GET | `/<id>` | — | Detalle de un proyecto |
| POST | `/` | admin | Crea proyecto |
| PUT | `/<id>` | admin | Actualiza proyecto (coordinador, participantes, activo) |

---

## `api_horas` — `/api/horas`

Reportes semanales de horas por proyecto. Flujo: `BORRADOR → CERRADO`
(el cierre genera la prenómina y notifica `REPORTE_CERRADO`).

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/reportes` | — | Listado de reportes (coordinador: solo sus proyectos) |
| GET | `/proyectos-disponibles` | — | Proyectos activos para abrir reporte; respeta ownership |
| POST | `/reportes` | — | Abre reporte semanal |
| GET | `/reportes/<id>` | — | Detalle con registros diarios |
| POST | `/reportes/<id>/cerrar` | — | Cierra el reporte (emite `reporte:estado_cambio`) |
| POST | `/reportes/<id>/registros` | — | Crea registro diario de horas |
| POST | `/reportes/<id>/registros/bulk` | — | Upsert de varios registros en una transacción (`client_record_id` idempotente) |
| PUT | `/registros/<id>` | — | Edita registro diario |
| DELETE | `/registros/<id>` | — | Elimina registro diario |
| GET | `/movil/resumen` | — | Pantalla móvil del coordinador: proyectos + reportes BORRADOR |
| POST | `/qr-check` | RL | Checada entrada/salida por QR del trabajador |
| GET | `/qr/trabajadores` | admin | Trabajadores activos con su QR actual |
| POST | `/qr/generar/<trabajador_id>` | admin, RL | Genera/regenera el QR de un trabajador |
| GET | `/qr/imagen/<qr_code>` | — | PNG del QR (requiere conocer el código) |
| GET | `/qr/imagen/<trabajador_id>` | admin | PNG del QR por id de trabajador |
| POST | `/rfid/asociar` | RL | Asocia tarjeta RFID a un trabajador |
| GET | `/rfid/trabajadores-reporte/<reporte_id>` | — | Trabajadores del reporte con su `rfid_uid` (kiosko) |

---

## `api_prenomina` — `/api/prenomina`

Cálculo semanal a partir de los reportes de horas. Flujo:
preview → guardar → editar (descuentos/depósitos/viáticos/festivos) → cerrar.

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/semanas` | — | Semanas con prenómina y su estado |
| GET | `/semanas/<fecha>/preview` | — | Cálculo en vivo sin persistir |
| POST | `/semanas/<fecha>/guardar` | — | Persiste la prenómina de la semana |
| GET | `/semanas/<fecha>/editar` | — | Detalle para el editor |
| POST | `/semanas/<fecha>/cerrar` | — | Cierra la semana (aplica abonos de préstamos, notifica `PRENOMINA_CERRADA`) |
| POST | `/descuentos` | — | Agrega descuento granular (valida tipo enum, concepto 1-250, monto ≤ $999,999.99, fecha no futura) |
| DELETE | `/descuentos/<id>` | — | Elimina descuento |
| POST | `/depositos` | — | Agrega depósito extra |
| DELETE | `/depositos/<id>` | — | Elimina depósito |
| PATCH | `/viaticos` | — | Ajusta viáticos de un registro |
| PATCH | `/festivos` | — | Marca/desmarca día festivo |
| GET | `/semanas/<fecha>/imprimir` | — | PDF consolidado de la semana |
| GET | `/semanas/<fecha>/trabajadores/<id>/imprimir` | — | PDF de recibo individual |
| GET | `/semanas/<fecha>/excel` | — | Excel con el formato del consolidado |
| POST | `/semanas/<fecha>/trabajadores/<id>/correo` | — | Envía recibo por email a un trabajador |
| POST | `/semanas/<fecha>/correo/bulk` | — | Envía recibos a un subconjunto |
| POST | `/semanas/<fecha>/correo` | — | Envía recibos a todos los de la semana |

---

## `api_prestamos` — `/api/prestamos`

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/` | — | Listado paginado. Filtros `q`, `estado=ACTIVO\|LIQUIDADO`, orden `sort`/`dir` |
| GET | `/trabajadores-disponibles` | — | Trabajadores elegibles para nuevo préstamo |
| GET | `/<id>` | — | Detalle con abonos |
| POST | `/` | — | Crea préstamo (monto, plazo, descuento semanal) |
| PUT | `/<id>` | — | Edita préstamo |
| POST | `/<id>/abonar` | — | Abono manual (emite `abono:new`) |
| POST | `/<id>/liquidar` | — | Liquida el saldo restante |
| GET | `/trabajadores/<id>/excel` | — | Excel con todos los préstamos del trabajador y sus abonos |

---

## `api_ajustes` — `/api/ajustes` (ajuste Inbursa)

Recuperación de depósitos adelantados, por periodos mensuales. Ver
`MANUAL_USO_PRENOMINA_PRESTAMOS_INBURSA.md` para el flujo funcional.

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/periodos` | — | Lista periodos |
| POST | `/periodos` | — | Crea periodo (nombre, rango de fechas, metas por trabajador) |
| GET | `/periodos/<id>` | — | Detalle con descuentos |
| POST | `/periodos/<id>/cerrar` | — | Cierra el periodo |
| GET | `/periodos/<id>/excel` | — | Excel del periodo (dos hojas) |
| GET | `/trabajadores-disponibles` | — | Trabajadores con monto de ajuste configurado |
| POST | `/periodos/<id>/descuentos` | — | Agrega descuento a un trabajador del periodo |
| DELETE | `/descuentos/<id>` | — | Elimina un descuento |
| POST | `/descuentos/bulk-delete` | — | Elimina varios descuentos en una transacción |

---

## `api_proyecto_total` — `/api/proyecto-total`

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/` | — | Totales acumulados de nómina por proyecto |
| GET | `/<proyecto_id>/excel` | RL | Excel del acumulado del proyecto |

---

## `api_historico` — `/api/historico`

Semanas de prenómina ya cerradas (solo lectura + export).

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/` | — | Semanas cerradas |
| GET | `/<fecha>` | — | Detalle de la semana |
| GET | `/<fecha>/proyecto/<id>/pdf` | RL | PDF del recibo por proyecto |
| GET | `/<fecha>/excel` | RL | Excel de la semana |

---

## `api_users` — `/api/users`

Administración de cuentas del sistema. Solo `super_admin` crea/elimina admins;
cambiar la password de un admin NO resetea su 2FA.

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/` | admin | Lista usuarios |
| POST | `/` | admin, RL | Crea usuario (`api_transactional`) |
| PUT | `/<id>` | admin, RL | Edita perfil de otro usuario |
| DELETE | `/<id>` | admin | Elimina usuario (cierra sus WebSockets) |
| POST | `/<id>/foto` | admin, RL | Sube/reemplaza foto de perfil del usuario |
| POST | `/<id>/password` | admin, RL | Resetea password de otro usuario |
| DELETE | `/<id>/sessions` | admin, RL | Forzar logout en todos los dispositivos del usuario |

---

## `api_dashboard`, `api_bitacora`, `api_metricas`, `api_notificaciones`, `api_search`

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/api/dashboard` | admin | KPIs generales (filtra PII) |
| GET | `/api/dashboard/alertas` | admin | Alertas operativas |
| GET | `/api/bitacora` | admin | Audit log paginado con filtros |
| GET | `/api/bitacora/<id>` | admin | Detalle de una entrada |
| GET | `/api/metricas` | — | Métricas generales del sistema |
| GET | `/api/notificaciones/resumen` | — | No-leídas + recientes; purga leídas >30 días |
| POST | `/api/notificaciones/<id>/leer` | — | Marca una como leída |
| POST | `/api/notificaciones/marcar_todas` | — | Marca todas como leídas |
| GET | `/api/v1/buscar` | login, RL | Búsqueda global (trabajadores, proyectos, productos…) |

---

## `inventario_api` — `/api/v1` (materiales)

### Productos

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/productos/` | inv-lectura | Catálogo paginado con filtros |
| GET | `/productos/by-codigo/<codigo>` | inv-lectura | Lookup por código (scanner móvil) |
| GET | `/productos/bajo-minimo/` | inv-lectura | En o bajo el mínimo, con consumo promedio y días restantes |
| POST | `/productos/` | inv-admin | Alta de producto |
| PUT | `/productos/<id>` | inv-admin | Edición |
| DELETE | `/productos/<id>` | inv-admin | Baja lógica |
| GET | `/productos/<id>/stocks` | inv-admin | Desglose de stock por almacén |
| GET | `/productos/<id>/disponibilidad` | inv-admin | Stock real / reservado / disponible |
| GET | `/productos/<id>/kardex` | inv-admin | Historial cronológico con saldo corrido |
| GET | `/productos/plantilla-importar` | inv-lectura | Excel plantilla de carga masiva |
| POST | `/productos/importar` | inv-admin, RL | Importación masiva desde Excel |

### Almacenes y estantes

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/almacenes/` | inv-lectura | Lista almacenes |
| POST / PUT / DELETE | `/almacenes/…` | inv-admin | CRUD de almacenes |
| GET | `/almacenes/<qr>/validar` | inv-lectura | Valida QR de almacén (PWA) |
| GET | `/almacenes/<id>/estantes` | inv-lectura | Estantes del almacén |
| GET | `/estantes/` | inv-lectura | Lista estantes |
| POST / PUT / DELETE | `/estantes/…` | inv-admin | CRUD de estantes |
| GET | `/estantes/<qr>/validar` | inv-lectura | Valida QR de estante |
| GET | `/estantes/<qr>/inventario` | inv-lectura | Productos asignados al estante (por QR) |
| GET | `/estantes/<id>/productos` | inv-lectura | Productos asignados (por id, UI admin) |
| PUT | `/estantes/<id>/productos` | inv-admin | Reemplaza la asignación producto↔estante |
| GET | `/estantes/<id>/qr-image` | inv-lectura | PNG del QR del estante |

### Movimientos

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/movimientos/` | inv-admin | Historial con filtros |
| POST | `/movimientos/` | inv-admin, RL | ENTRADA/SALIDA/AJUSTE/TRASPASO; altera `StockPorAlmacen` con `with_for_update` (anti over-selling) |
| POST | `/movimientos/rapido` | inv-admin, RL | Atajo PWA: resuelve producto por código/QR |

### Solicitudes de material

Flujo: `PENDIENTE → APROBADA/RECHAZADA → ENTREGADA` (con reserva de stock al
aprobar y entregas parciales).

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/solicitudes/` | login, RL | Crea solicitud (carrito del solicitante) |
| GET | `/solicitudes/` | login | Lista (solicitante solo ve las suyas) |
| PATCH | `/solicitudes/<id>/estado` | inv-admin | Cambia estatus aplicando reservas de stock |
| PATCH | `/solicitudes/<id>/detalles/<det_id>` | inv-admin | Edita `cantidad_aprobada` de una línea APROBADA |
| POST | `/solicitudes/<id>/entregar` | inv-admin, RL | Entrega total o parcial |
| GET | `/solicitudes/<id>/pdf` | login | PDF de la solicitud (solicitante solo la propia) |
| POST | `/solicitudes/preview-pdf` | login, RL | PDF del carrito actual SIN guardar |

### Tomas físicas de inventario

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/tomas/` | inv-admin | Inicia toma de un almacén (snapshot de `StockPorAlmacen`) |
| GET | `/tomas/` y `/tomas/<id>` | inv-admin | Listado / detalle |
| PATCH | `/tomas/<id>/detalles/<det_id>` | inv-admin | Captura `cantidad_fisica` de una línea |
| PATCH | `/tomas/<id>/detalles/por-codigo` | inv-admin | Captura por código (PWA scanner) |
| POST | `/tomas/<id>/cerrar` | inv-admin | Cierra generando AJUSTES por cada diferencia |
| POST | `/tomas/<id>/cancelar` | inv-admin | Cancela sin aplicar ajustes |
| GET | `/tomas/<id>/pdf` | inv-admin | Acta con diferencias y firmas |

### Catálogo, etiquetas y reportes

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/proyectos/` | login | Proyectos para asociar a solicitudes |
| GET | `/categorias/` | inv-lectura | Unión de categorías de productos + config |
| GET | `/categorias-config/` | login | Metadatos visuales por categoría |
| PUT / DELETE | `/categorias-config/<nombre>` | inv-admin | Upsert / borra config visual |
| POST | `/etiquetas/pdf` | inv-lectura, RL | PDF de etiquetas Avery (5160/5163) con barcode o QR |
| POST | `/ordenes-compra/express/sugerencia` | inv-admin, RL | Sugerencia de compra agrupada por proveedor |
| POST | `/ordenes-compra/express/pdf` | inv-admin, RL | PDF de la orden de compra express |
| GET | `/reportes/inventario-actual.xlsx` | inv-admin, RL | Stock actual de productos activos |
| GET | `/reportes/movimientos.xlsx` | inv-admin, RL | Movimientos con filtros |
| GET | `/reportes/kardex.xlsx` | inv-admin, RL | Kardex de un producto |
| GET | `/reportes/consumo-proyecto.xlsx` | inv-admin, RL | Consumo agrupado por proyecto |
| GET | `/reportes/solicitudes.xlsx` | inv-admin, RL | Solicitudes del periodo con totales |

---

## `herramientas_api` — `/api/v1` (herramientas)

Catálogo (tipo) → unidades físicas rastreables (serie/QR) → ciclo de vida:
asignación, mantenimiento, incidencia, baja.

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/herramientas/` y `/herramientas/<id>` | inv-lectura | Catálogo de tipos |
| POST / PUT / DELETE | `/herramientas/…` | inv-admin | CRUD del catálogo (delete = baja lógica) |
| GET | `/herramientas/clasificaciones` | inv-lectura | Clasificaciones existentes |
| GET | `/herramientas/stats` | inv-lectura | Resumen para el dashboard |
| GET | `/herramientas-categorias/` | login | Metadatos visuales de clasificaciones |
| PUT | `/herramientas-categorias/<nombre>` | inv-admin | Upsert de metadatos |
| GET | `/herramientas-unidades/` y `…/<id>` | login | Unidades físicas (filtros por estado/almacén/asignación) |
| POST / PUT | `/herramientas-unidades/…` | inv-admin | Alta / edición de unidad |
| GET | `/herramientas-unidades/<id>/eventos` | login | Bitácora funcional de la unidad (`EventoHerramienta`) |
| GET | `/herramientas-unidades/<id>/qr-image` | inv-lectura | PNG del QR de la unidad |
| GET | `/herramientas-unidades/<qr>/validar` | login | Valida QR (PWA) |
| POST | `/herramientas-unidades/<id>/fotos` | inv-lectura, RL | Sube foto de la unidad |
| GET | `/herramientas-unidades/<id>/media/<media_id>` | login | Sirve archivo de media |
| POST | `/asignaciones-herramienta/` | inv-admin, RL | Entrega unidad a trabajador (estado → ASIGNADA) |
| GET | `/asignaciones-herramienta/` | login | Lista asignaciones |
| PATCH | `/asignaciones-herramienta/<id>/devolver` | inv-admin, RL | Registra devolución con condición |
| POST | `/mantenimientos-herramienta/` | inv-admin, RL | Abre mantenimiento (unidad → EN_MANTENIMIENTO) |
| GET | `/mantenimientos-herramienta/` | login | Lista mantenimientos |
| PATCH | `/mantenimientos-herramienta/<id>/cerrar` | inv-admin, RL | Cierra con costo y estado final de la unidad |
| POST | `/incidencias-herramienta/` | login, RL | Reporta incidencia (daño, pérdida…) |
| GET | `/incidencias-herramienta/` | login | Lista incidencias |
| PATCH | `/incidencias-herramienta/<id>/atender` | inv-admin, RL | Atiende/resuelve incidencia |
| POST | `/solicitudes-baja-herramienta/` | login, RL | Solicita baja de una unidad |
| GET | `/solicitudes-baja-herramienta/` | login | Lista solicitudes de baja |
| PATCH | `/solicitudes-baja-herramienta/<id>/autorizar` | inv-admin, RL | Autoriza la baja |
| PATCH | `/solicitudes-baja-herramienta/<id>/rechazar` | inv-admin, RL | Rechaza la baja |
| POST | `/solicitudes-baja-herramienta/<id>/ejecutar` | inv-admin, RL | Ejecuta la baja autorizada |
| POST | `/herramientas-unidades/<id>/dar-baja` | inv-admin, RL | Atajo: crea solicitud APROBADA y la ejecuta de inmediato |

---

## Eventos Socket.IO relacionados

Resumen de los pushes que acompañan a estos endpoints (detalle en
`ARQUITECTURA.md` y `WEBSOCKETS_Y_DEPLOY.md`):

| Evento | Dispara | Audiencia |
|---|---|---|
| `notif:new` | Insert de `Notificacion` | `user:{id}` destinatario |
| `bitacora:new` | Insert de `AuditLog` | roles admin |
| `abono:new` | Insert de `AbonoPrestamo` (manual o por prenómina) | roles admin |
| `reporte:estado_cambio` | Cambio de estado de `ReporteSemanal` | sala `reporte:{id}` |
| `reporte:registros_cambio` | Cambios en `RegistroDiarioHoras` | sala `reporte:{id}` |
| `nota:changed` | POST/DELETE en `/trabajadores/<id>/notas` | roles admin + coordinador |
