# Notas internas por trabajador ("chatter" de la ficha)

Feature de la rama `Inventario` (2026-06-09). Notas de texto libre sobre un
trabajador, escritas por admin/coordinador: acuerdos verbales, incidencias
informales, contexto que no cabe en los campos estructurados de la ficha.

## Componentes

| Pieza | Archivo |
|---|---|
| Modelo `NotaTrabajador` (tabla `trabajador_notas`) | `app/models/trabajador.py` |
| Endpoints REST | `app/routes/api_trabajadores/notas.py` |
| Autocreación de la tabla en arranque | `app/__init__.py` (bloque `has_table`) |
| Tests | `tests/test_api_trabajadores.py` → `TestNotas` |

La tabla se **autocrea en el arranque** con `inspect().has_table()` (mismo
patrón que `notificaciones` y `totp_backup_codes`); **no requiere**
`flask db upgrade`.

## Modelo

```text
trabajador_notas
├── id             PK
├── trabajador_id  FK trabajadores.id  (index)
├── user_id        FK users.id         (index) — autor
├── texto          String(2000), not null
└── created_at     DateTime UTC        (index)
```

`to_dict()` incluye `autor` (full_name o username del user), listo para
pintar en el SPA sin segundo fetch.

## Endpoints

Todos bajo el blueprint de trabajadores (`/api/trabajadores`), con `@jwt_required`:

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/<id>/notas` | Lista (desc por fecha, máx. 200) |
| `POST` | `/<id>/notas` | Crea. Body: `{"texto": "..."}` → 201 con la nota |
| `DELETE` | `/<id>/notas/<nota_id>` | Elimina → `{"ok": true}` |

### Validaciones (422)

- `texto` vacío o solo espacios → `"La nota no puede estar vacía"`.
- `texto` > 2000 caracteres → `"Máximo 2000 caracteres"`.

### Permisos

- **Ver/crear**: misma regla que la ficha (`_authorized` de `_core.py`):
  `admin`/`super_admin` siempre; `coordinador` solo si el trabajador pertenece
  a uno de sus proyectos (si no → 403).
- **Eliminar**: solo el **autor** de la nota o un **admin/super_admin** (403
  para cualquier otro, aunque tenga acceso a la ficha).
- Trabajador inexistente → 404; nota inexistente en ese trabajador → 404.

## Tiempo real (Socket.IO)

Siguiendo la política websockets-first del proyecto, cada mutación
(POST/DELETE) emite **después del commit**:

```python
emit_to_role(['admin', 'super_admin', 'coordinador'],
             'nota:changed', {'trabajador_id': t.id, 'action': 'created'|'deleted'})
```

El panel de notas del SPA escucha `nota:changed` en su `invalidateOn` y
refresca la lista en todas las pestañas/usuarios abiertos. El payload solo
lleva `trabajador_id` + `action` (no el texto): el cliente decide si el
trabajador visible es el afectado y re-fetchea por REST.

## Auditoría

Cada creación/eliminación registra en bitácora vía `log_action(...)`
("Agregó nota al trabajador X (#id)" / "Eliminó nota #n del trabajador...").

## Tests

`pytest tests/test_api_trabajadores.py -k TestNotas` cubre: crear+listar con
autor, texto vacío (422), coordinador sin proyecto (403), coordinador con
proyecto (201), borrado autor-o-admin (403 al no-autor), y 404 de trabajador
inexistente.
