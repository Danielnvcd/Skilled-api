# Proyectos ↔ Trabajadores: relación M:N y campos derivados

Refactor de la rama `Inventario` (2026-06-10). Regla de negocio:

- Un **trabajador puede estar en varios proyectos** a la vez.
- Un **coordinador puede llevar varios proyectos**.
- Si un proyecto se **desactiva** o **sacan al trabajador** de la lista, esa
  relación deja de figurar en el expediente y en credenciales — los datos
  fantasma de proyectos viejos eran fuente de bugs.

## Cómo funciona

La fuente de verdad es la tabla M:N `proyecto_trabajador`. Los tres campos
legacy del trabajador (`no_proyecto`, `ubicacion_actual`, `coord_a_cargo`) ya
**no se escriben a mano**: se recalculan con
`recalcular_campos_proyecto(t)` (`app/routes/api_proyectos/_core.py`) desde
sus proyectos **activos**:

- Varios proyectos se unen con `', '` (orden alfabético por número) y se
  truncan al ancho de la columna.
- Sin proyectos activos → los tres campos quedan `None`.
- `coord_a_cargo` usa `full_name` (o username) de los coordinadores únicos.

El recálculo corre en `POST /api/proyectos` y `PUT /api/proyectos/<id>` para
el conjunto **afectado** (los que estaban ∪ los que quedan), después del
`flush` (la función lee la tabla asociativa de BD). Eso cubre: sacar/meter
trabajadores, desactivar/reactivar el proyecto, renombrar, y cambiar o quitar
coordinador.

## Consumidores ajustados

- **Credenciales** (`api_trabajadores/credenciales.py`): `coord_a_cargo` y
  `proyectos_activos` se derivan de proyectos activos; se eliminó el fallback
  al string legacy (mostraba relaciones muertas).
- **Dashboard** (`api_dashboard/dashboard.py`): "empleados por proyecto"
  agrupa por la M:N (proyecto activo × trabajador activo) — agrupar por el
  string creaba buckets falsos tipo `"PRY-1, PRY-2"` con multi-proyecto.
- **Expediente / exports**: siguen leyendo las columnas, que ahora siempre
  reflejan la relación real.

## Backfill (correr una vez al desplegar)

Los trabajadores no tocados desde el cambio conservan strings viejos:

```bash
python scripts/backfill_proyecto_trabajador.py
```

Recalcula los tres campos para toda la plantilla y reporta cuántos cambió.

## Otros cambios del mismo refactor

- `GET /api/proyectos` ahora **pagina** (`page`/`per_page`, máx 100) y acepta
  **`sort`/`dir`** con whitelist (`numero`, `nombre`, `estado`,
  `participantes`, `creado`, `coordinador`) — ver `ORDENAMIENTO_LISTADOS.md`.
  El conteo de participantes sale de una subquery (ya no carga las filas).
- Carrera de unicidad en `numero_proyecto`: `IntegrityError` en el flush →
  **409** (antes 500).
- `participantes_ids` inexistentes → se ignoran pero regresan en `warnings`
  (el SPA los muestra como toast); ids no numéricos → 400.
- `/meta` incluye `super_admin` en el selector de coordinadores (faltaba).
- SPA: el botón de editar solo aparece para admin (antes el coordinador veía
  un "Ver" roto que disparaba 403 en `/meta`); el catálogo del modal se
  cachea con `useResource`; aviso de cambios sin guardar al cerrar.

## Tests

`pytest tests/test_api_proyectos.py` — clase `TestDerivacion` cubre:
multi-proyecto con coma, salir de uno conserva el otro, desactivar/reactivar,
credenciales sin proyecto inactivo, coord_a_cargo se llena y limpia, warnings
y payload malformado. `TestListar` cubre paginación y sort.
