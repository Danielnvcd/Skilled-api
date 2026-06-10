# Modelos de base de datos

Todas las tablas del sistema, agrupadas por dominio (un módulo por dominio en
`app/models/`). Extraído del código el 2026-06-09 con
`scripts/gen_models_inventory.py` — regenerar tras cambiar modelos.

Convenciones del proyecto:

- PK `id` autoincremental salvo las tablas de mapping con PK compuesta.
- Timestamps en UTC (`_now_utc` de `models/_base.py`).
- Montos en `Numeric(10,2)`.
- Estados como `String` con valores en MAYÚSCULAS (no enums de BD).
- Migraciones con Alembic (`flask db ...`), excepto tres tablas autocreadas en
  el arranque: `notificaciones`, `totp_backup_codes`, `trabajador_notas`.

---

## auth.py — usuarios y sesión

### `users` (User)
Cuenta del sistema (no confundir con `trabajadores`, los empleados de nómina).
`username` único, `password_hash`, `role` (`super_admin` / `admin` /
`inventario` / `coordinador` / `solicitante_material` / `user`),
`totp_secret` **cifrado con Fernet** (`EncryptedString`), `password_version`
(invalida JWTs al cambiar password), datos de perfil (full_name, area,
position, factory, contact_info, profile_pic), `last_seen` y `trabajador_id`
opcional que vincula la cuenta con un empleado.

### `refresh_tokens` (RefreshToken)
Sesión persistente (cookie httpOnly `skilled_rt`). Guarda solo el
`token_hash` (SHA-256, único), `user_id`, `expires_at` (7 días), `revoked`.
La rotación en `/api/auth/refresh` revoca el anterior y crea uno nuevo.

### `totp_backup_codes` (TwoFactorBackupCode)
Códigos de respaldo 2FA: `code_hash`, `consumed_at` (un solo uso). Autocreada.

### `audit_log` (AuditLog)
Bitácora de seguridad: `user` (username, texto), `action`, `ip`, `created_at`.
Alimentada por `log_action()`; su insert emite `bitacora:new`.

---

## trabajador.py — empleados

### `trabajadores` (Trabajador)
El modelo más ancho (~60 columnas), agrupadas así:

- **Identificación**: `no_empleado` (único), `nombre`, `nombre_apellidos`,
  `foto_perfil`, `qr_code` y `rfid_uid` (únicos, para checadas).
- **Laboral**: tipo_mov, tipo_cont, area, puesto, fecha_ingreso, tipo_jornada,
  descripcion_servicio, inicio, termino_prueba, fecha_baja, `activo`
  (baja lógica), no_proyecto, coord_a_cargo, ubicacion_actual/estado.
- **PII fiscal**: curp, rfc, nss (+ columnas `largo_*` de validación),
  domicilio, fecha_nacimiento, edad, sexo, nacionalidad, estado_civil,
  correo, celular.
- **Médico/emergencia** (editable por coordinador): tipo_sangre, alergias,
  enfermedades_cronicas, contacto_emergencia, parentesco, número, lentes,
  licencia_conducir, estatura.
- **Financiero** (solo admin): sb, sdi, salario_real_pactado_x_sem, hr_extra,
  infonavit, ajuste_inbursa, caja_ahorro, viaticos, pago_dia_festivo,
  pagos_efectivo, folio_mov_idse, tipo_pago, tipo_nomina.

Relaciones: `credenciales` → CredencialPlanta, `documentos` →
DocumentoTrabajador.

### `credenciales_plantas` (CredencialPlanta)
Credencial de acceso a una planta: `planta`, `credencial_id`,
`fecha_caducidad`.

### `documentos_trabajador` (DocumentoTrabajador)
Archivo subido del empleado: `nombre_archivo`, `ruta_archivo`,
`tipo_documento`, vigencia (`fecha_inicio`/`fecha_fin`).

### `trabajador_notas` (NotaTrabajador)
Nota interna ("chatter" de la ficha): `user_id` autor, `texto` (≤2000),
`created_at`. Autocreada. Ver `NOTAS_TRABAJADOR.md`.

---

## proyecto.py

### `proyectos` (Proyecto)
`numero_proyecto` (único), `nombre`, `activo`, `coordinador_id` → users.
Relación M:N `participantes` con trabajadores vía la tabla asociativa
**`proyecto_trabajador`**.

---

## horas.py — captura de horas

### `reportes_semanales` (ReporteSemanal)
Cabecera del reporte de un proyecto en una semana: rango de fechas,
`proyecto_id`, `estado` (`BORRADOR` → `CERRADO`), `creado_por_id`.

### `registros_diarios_horas` (RegistroDiarioHoras)
Una fila por trabajador y día dentro del reporte: `hora_entrada`/`hora_salida`,
`tomo_comida`, `aplica_viaticos` (+ monto manual), `aplica_dia_festivo`,
`incidencia`, `tipo_nomina`, `horas_productivas` (calculada),
`client_record_id` (UUID único para upserts idempotentes desde el kiosko) y
`modificado_en`. Sus cambios emiten `reporte:registros_cambio`.

### `saldo_vacaciones` (SaldoVacaciones)
Resumen anual por trabajador: `dias_totales_asignados`, `dias_disfrutados`.

### `ausencias` (Ausencia)
Ausencia programada: rango de fechas, `tipo_ausencia`, `estado`
(`PROGRAMADA`…), `dias_solicitados`, `motivo`.

---

## prenomina.py — nómina semanal

### `prenominas` (Prenomina)
Una fila por trabajador y semana. Percepciones (`salario_base`,
`pago_horas_extras`, `pago_viaticos`, `pago_festivos`, `depositos_otros`,
`depositos_prestamos`), deducciones (`descuento_infonavit`, `ajuste_inbursa`,
`descuentos_otros`, `descuento_prestamos`, `descuento_incidencias`,
`recuperacion_manual`) y totales (`total_percepciones`, `total_deducciones`,
`total_a_pagar`, recalculados por `recalcular_totales_prenomina`).
`estado` `PENDIENTE` → `CERRADA`. Liga opcional a `reporte_semanal_id`.

### `descuentos_prenomina` (DescuentoPrenomina)
Descuento granular (incidencia, manual, préstamo): `tipo`, `concepto`,
`monto`, `fecha_incidencia`.

### `depositos_extra` (DepositoExtra)
Depósito adicional: `monto`, `concepto`.

---

## prestamos.py

### `prestamos` (Prestamo)
`monto_total`, `plazo_semanas`, `descuento_semanal`, `monto_restante`,
`frecuencia`, `fecha_inicio`, `estado` (`ACTIVO` → `LIQUIDADO`), `activo`.

### `abonos_prestamo` (AbonoPrestamo)
Cada pago a un préstamo: `monto`, `fecha_abono`, `tipo` (`NOMINA` automático
al cerrar prenómina, o abono manual), `registrado_por_id`, `notas`. Su insert
emite `abono:new`.

---

## ajustes.py — ajuste Inbursa

### `ajuste_periodos` (AjustePeriodo)
Periodo mensual de recuperación de depósitos adelantados: nombre, rango de
fechas, `estado` (`ABIERTO`/`CERRADO`).

### `ajuste_trabajadores_periodo` (AjusteTrabajadorPeriodo)
Vincula trabajador↔periodo con su `monto_meta`.

### `ajuste_descuentos` (AjusteDescuento)
Descuento individual: `monto`, `fecha_descuento`, `notas`, `cobrado`.

---

## notificaciones.py

### `notificaciones` (Notificacion)
In-app por usuario: `tipo` (`REPORTE_CERRADO`, `PRENOMINA_CERRADA`,
`ACTUALIZACION`…), `titulo`, `mensaje`, `url`, `leida`, `referencia`
(idempotencia). Autocreada. Las leídas se purgan a los 30 días en cada
`GET /resumen`. Su insert emite `notif:new`.

---

## inventario.py — materiales

### `almacenes` (Almacen) y `estantes` (Estante)
Jerarquía física. Ambos con `qr_code` único (validación por PWA) y `activo`.

### `productos` (Producto)
Catálogo: `codigo` único, `descripcion`, `categoria`, `unidad`,
`stock_actual` / `stock_reservado` / `stock_minimo`, `imagen_url`, proveedor
default, `activo` (baja lógica).

### `stock_por_almacen` (StockPorAlmacen)
PK compuesta (producto, almacén): `cantidad` real en ese almacén. Es la tabla
que los movimientos bloquean con `with_for_update`.

### `producto_estante` (ProductoEstante)
Mapping puro producto↔estante (scanner móvil). PK compuesta.

### `movimientos_inventario` (MovimientoInventario)
`tipo` (`ENTRADA`/`SALIDA`/`AJUSTE`/`TRASPASO`), producto, almacén
origen/destino, `cantidad`, `motivo`, usuario, fecha. Base del kardex.

### `tomas_inventario` (TomaInventario) y `tomas_inventario_detalle`
Conteo físico por almacén: la toma snapshotea el stock al iniciar
(`cantidad_sistema` por línea), se captura `cantidad_fisica` y el cierre
genera AJUSTES por cada diferencia. `estatus` `ABIERTA` → `CERRADA`/`CANCELADA`.

### `notificacion_umbral` (NotificacionUmbral)
Idempotencia diaria de alertas STOCK_BAJO: PK (producto, fecha).

### `categorias_config` (CategoriaConfig)
Metadatos visuales por categoría de producto (imagen), independiente de que
la categoría tenga productos.

---

## solicitudes.py — pedidos de material

### `solicitudes_material` (SolicitudMaterial)
Cabecera del pedido: `solicitante_id`, `proyecto`, `estatus`
(`PENDIENTE` → `APROBADA`/`RECHAZADA` → `ENTREGADA`), fechas.

### `solicitudes_material_detalle` (SolicitudMaterialDetalle)
Línea del pedido: `cantidad_solicitada` / `aprobada` / `entregada` (soporta
entregas parciales), `tipo_item` (`MATERIAL` o `HERRAMIENTA` — una solicitud
puede pedir herramientas con `fecha_uso_inicio`/`fin` y `justificacion`).

---

## herramientas.py — herramientas y su ciclo de vida

### `herramientas` (Herramienta)
Catálogo (el "modelo" abstracto): `sku` único, `descripcion`,
`clasificacion`, marca/modelo, `unidad`, `piezas`, `serializada` (si cada
unidad lleva número de serie), `activo`.

### `herramienta_unidades` (HerramientaUnidad)
Instancia física rastreable: `no_serie` (si serializada), `codigo_interno` y
`qr_code` únicos, `estado` (`DISPONIBLE` / `ASIGNADA` / `EN_MANTENIMIENTO` /
`BAJA`…), ubicación (almacén/estante), `asignado_trabajador_id`, datos de
adquisición (fecha, costo, vida útil) y de baja (fecha, motivo).

### `asignaciones_herramienta` (AsignacionHerramienta)
Entrega de una unidad a un trabajador: fechas de entrega/devolución
(prevista y real), `estado` (`ACTIVA`/`DEVUELTA`), condición a la
entrega/devolución, quién entregó/recibió. Puede ligarse a una
`solicitud_id` de material.

### `mantenimientos_herramienta` (MantenimientoHerramienta)
`tipo`, `motivo`, `proveedor`, fechas, `costo`, `estado`
(`ABIERTO`/`CERRADO`) y `estado_final_unidad` al cerrar.

### `incidencias_herramienta` (IncidenciaHerramienta)
Reporte de daño/pérdida: `tipo`, `descripcion`, `estado`
(`ABIERTA`/`CERRADA`), `resolucion`.

### `solicitudes_baja_herramienta` (SolicitudBajaHerramienta)
Flujo de baja con autorización: `estado` `PENDIENTE` → `APROBADA`/`RECHAZADA`
→ ejecutada; registra quién solicitó/autorizó/ejecutó.

### `eventos_herramienta` (EventoHerramienta)
Bitácora funcional por unidad (distinta del `audit_log` de seguridad):
`tipo_evento`, estado anterior/nuevo, referencia polimórfica
(`referencia_id` + `referencia_tipo`).

### `media_herramienta` (MediaHerramienta)
Fotos/archivos de una unidad; con `evento_id=NULL` y `tipo='FOTO_HERRAMIENTA'`
es la foto principal.

### `herramienta_categorias` (HerramientaCategoria)
Metadatos visuales por clasificación (icono, color, imagen).

---

## Diagrama de relaciones principales

```text
users ─┬─< refresh_tokens / totp_backup_codes / notificaciones
       ├─< trabajador_notas >── trabajadores
       └── proyectos.coordinador_id

trabajadores ─┬─< credenciales_plantas / documentos_trabajador
              ├─< registros_diarios_horas >── reportes_semanales >── proyectos
              ├─< prenominas ─< descuentos_prenomina / depositos_extra
              ├─< prestamos ─< abonos_prestamo
              ├─< ajuste_trabajadores_periodo / ajuste_descuentos >── ajuste_periodos
              ├─< ausencias / saldo_vacaciones
              ├──< proyecto_trabajador >── proyectos          (M:N)
              └─< asignaciones_herramienta >── herramienta_unidades

productos ─┬─< stock_por_almacen >── almacenes ─< estantes
           ├─< producto_estante >── estantes                  (M:N)
           ├─< movimientos_inventario
           ├─< tomas_inventario_detalle >── tomas_inventario
           └─< solicitudes_material_detalle >── solicitudes_material

herramientas ─< herramienta_unidades ─< asignaciones / mantenimientos /
               incidencias / solicitudes_baja / eventos / media
```
