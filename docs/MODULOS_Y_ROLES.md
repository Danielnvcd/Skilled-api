# Módulos y roles

Qué cubre el sistema, quién puede entrar a cada parte y cómo llegan las notificaciones. Los 216 endpoints, uno por uno, están en [`API_REFERENCE.md`](./API_REFERENCE.md).

---

## Qué cubre

- **Empleados**: alta, baja, edición; campos laborales, personales, médicos y financieros con whitelist por rol. Notas internas tipo "chatter" en la ficha (ver [`NOTAS_TRABAJADOR.md`](./NOTAS_TRABAJADOR.md)).
- **Nómina**: prenómina, descuentos, depósitos extra, préstamos con abonos y el ajuste Inbursa por periodo.
- **Horas**: reportes semanales, registros diarios, ausencias, saldo de vacaciones y captura por RFID/QR.
- **Proyectos M:N**: un trabajador puede estar en varios proyectos; expediente y credenciales derivan la relación SOLO de proyectos activos (ver [`PROYECTOS_DERIVACION_M2M.md`](./PROYECTOS_DERIVACION_M2M.md)).
- **Inventario**: productos, almacenes y estantes con QR; movimientos con lock anti-concurrencia; solicitudes con flujo `PENDIENTE → APROBADA/RECHAZADA/ENTREGADA` y entrega parcial; tomas físicas con ajustes automáticos; etiquetas Avery y órdenes de compra express.
- **Herramientas**: catálogo + unidades físicas rastreables (serie/QR); asignaciones a trabajadores, mantenimientos, incidencias y flujo de baja con autorización.
- **Panel de sistemas**: estado de la infraestructura, tráfico y percentiles, sesiones activas, bloqueos de cuenta, eventos de seguridad y auditoría de archivos huérfanos en R2.
- **Listados ordenables**: trabajadores, préstamos y proyectos aceptan `?sort=<campo>&dir=asc|desc` con whitelist de columnas (ver [`ORDENAMIENTO_LISTADOS.md`](./ORDENAMIENTO_LISTADOS.md)).
- **Carga masiva**: import/export de plantillas `.xlsx` para empleados y productos.
- **Reportes**: PDF (recibos, constancias, solicitudes, tomas, OC) y Excel (totales por proyecto, histórico), saneados contra fórmula-injection.
- **Realtime**: notificaciones in-app vía Socket.IO, con expiración automática de las leídas a los 30 días.

---

## Inventario y herramientas

Backend bajo `/api/v1/`: paquetes `app/routes/inventario_api/` (materiales) y `app/routes/herramientas_api/` (herramientas). Frontend en `plantilla-frontend/src/pages/inventario/`.

- **Productos**: CRUD + `GET /productos/bajo-minimo/`, `GET /productos/plantilla-importar`, `POST /productos/importar`.
- **Almacenes y estantes**: CRUD + validación por QR (`GET /almacenes/<qr>/validar`) y generación del PNG (`GET /estantes/<id>/qr-image`).
- **Movimientos**: `POST /movimientos/` toma la fila con `with_for_update` para evitar over-selling concurrente; rate-limit 20/min por IP.
- **Solicitudes**: `PATCH /solicitudes/<id>/estado` mueve el flujo PENDIENTE → APROBADA / RECHAZADA / ENTREGADA, con soporte de entrega parcial.
- **Categorías**: `categorias_config` guarda los metadatos visuales (imagen, etc.) aunque la categoría todavía no tenga productos.
- **Herramientas**: unidades con serie y QR, asignación a trabajador, mantenimientos, incidencias y baja con autorización.

---

## Roles

| Rol | Acceso |
|---|---|
| `super_admin` | Todo. Único que administra otros admins. |
| `admin` | Todo excepto crear/eliminar otros admins. |
| `sistemas` | Panel de TI (`/api/sistemas`): infraestructura, sesiones, bloqueos, eventos de seguridad. Exige 2FA activo. |
| `inventario` | Módulo de inventario completo, sin administración de usuarios. |
| `coordinador` | Solo `/horas` y campos médicos/de contacto de los trabajadores de sus proyectos. |
| `solicitante_material` | Solo `/inventario/mis-pedidos`. |
| `user` | Rol por defecto al crear una cuenta; sin acceso a los módulos administrativos. |

---

## Notificaciones

Panel en el sidebar para `admin` y `super_admin`. El push va por Socket.IO (evento `notif:new`, emitido post-commit por un hook ORM) y el polling queda como respaldo.

| Tipo | Origen |
|---|---|
| `REPORTE_CERRADO` | Al cerrar un reporte de horas |
| `PRENOMINA_CERRADA` | Al aprobar/cerrar una prenómina |
| `ACTUALIZACION` | Entradas del `CHANGELOG` de `app/routes/api_notificaciones/_core.py` (hoy vacío) |

Las leídas se eliminan a los 30 días en cada `GET /resumen`, sin cron externo; el plazo se cambia en `DIAS_EXPIRACION`. Las tablas `notificaciones`, `totp_backup_codes` y `trabajador_notas` se autocrean al arrancar (`inspect().has_table()`), así que no necesitan `flask db upgrade`.
