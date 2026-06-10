# Ordenamiento por columna en listados (`sort` / `dir`)

Feature de la rama `Inventario` (2026-06-09; proyectos agregado 2026-06-10).
Los listados paginados de **trabajadores**, **préstamos** y **proyectos**
aceptan orden por columna desde el SPA (click en el header de la tabla).

## Contrato

```
GET /api/trabajadores?sort=<campo>&dir=asc|desc
GET /api/prestamos?sort=<campo>&dir=asc|desc
GET /api/proyectos?sort=<campo>&dir=asc|desc
```

- `sort` se valida contra una **whitelist** por endpoint (abajo). Un valor
  desconocido o vacío **no devuelve 400**: cae al orden default histórico,
  así un link viejo con un campo renombrado sigue funcionando y no hay
  superficie de inyección (nunca se interpola el string en SQL).
- `dir` default `asc`; cualquier cosa distinta de `desc` se trata como `asc`.
- Compatible con los demás filtros existentes (`q`, `estado`, paginación).

## Campos ordenables

### `/api/trabajadores` (`app/routes/api_trabajadores/crud.py`)

| `sort` | Columna |
|---|---|
| `nombre` (default) | `lower(nombre)` |
| `no_empleado` | `no_empleado` |
| `area` | `lower(area)` |
| `puesto` | `lower(puesto)` |
| `tipo_nomina` | `lower(tipo_nomina)` |
| `salario` | `salario_real_pactado_x_sem` |
| `ingreso` | `fecha_ingreso` |
| `baja` | `fecha_baja` |

### `/api/prestamos` (`app/routes/api_prestamos/crud.py`)

| `sort` | Columna |
|---|---|
| *(vacío, default)* | `creado_en desc` (orden histórico) |
| `trabajador` | `lower(Trabajador.nombre)` (requiere join, ver abajo) |
| `monto` | `monto_total` |
| `restante` | `monto_restante` |
| `descuento` | `descuento_semanal` |
| `inicio` | `fecha_inicio` |
| `estado` | `estado` |

### `/api/proyectos` (`app/routes/api_proyectos/lectura.py`)

| `sort` | Columna |
|---|---|
| *(vacío, default)* | `numero_proyecto` |
| `numero` | `numero_proyecto` |
| `nombre` | `lower(nombre)` |
| `estado` | `activo` |
| `participantes` | conteo de la M:N (subquery, `coalesce 0`) |
| `creado` | `created_at` |
| `coordinador` | `lower(User.username)` (outerjoin) |

## Decisiones de diseño

- **NULLs al final** (`nullslast()`): un trabajador sin salario no encabeza
  el orden ascendente por salario.
- **Desempate estable**: tras la columna pedida se ordena por
  `nombre, id` (trabajadores) o `creado_en desc, id` (préstamos). Sin esto,
  la paginación puede duplicar/omitir filas cuando la columna ordenada tiene
  valores repetidos.
- **Join condicional en préstamos**: `sort=trabajador` necesita unir
  `Trabajador`. Se usa `outerjoin` para no ocultar préstamos huérfanos, y
  **solo si el filtro `q` no agregó ya el join** (un segundo join a la misma
  tabla revienta la query). El flag `joined_trabajador` controla esto.

## Tests

- `tests/test_api_trabajadores.py` → `test_sort_salario_desc/asc`,
  `test_sort_campo_invalido_cae_a_default`, `test_sort_nulls_al_final`.
- `tests/test_api_prestamos.py` → `test_sort_monto_asc`,
  `test_sort_trabajador_con_q` (join duplicado), `test_sort_invalido_cae_a_default`.
