# Migración a `useResource` + push real-time

Guía paso a paso para llevar el patrón ya implementado en `/empleados` y
`/usuarios` al resto de los módulos admin del SPA. Cada migración toma ~10
minutos y los cambios son aditivos — no rompe nada existente.

## TL;DR del patrón

**Frontend:** reemplazar `useState + useEffect + load + refetch` por
`useResource(key, fetcher, opts)`.

**Backend:** añadir `emit_to_role([roles], 'recurso:changed', payload)` después
de cada `db.session.commit()` de mutación.

**Resultado:** caché de 30 s + revalidación al recuperar foco + push automático
cuando otro usuario muta el recurso.

## Estado actual

| Módulo | Frontend | Backend | Evento |
|---|---|---|---|
| Empleados | ✅ | ✅ | `empleado:changed` |
| Usuarios | ✅ | ✅ | `usuario:changed` |
| Proyectos | ❌ | ❌ | `proyecto:changed` |
| Horas (lista) | ❌ | ❌ | `reporte:lista_changed` |
| Prenómina | ❌ | ❌ | `prenomina:changed` |
| Préstamos | ❌ | ❌ | `prestamo:changed` |
| Ajustes | ❌ | ❌ | `ajuste:changed` |
| Histórico | ❌ | ❌ | `historico:changed` |
| Credenciales | ❌ | ❌ | `credencial:changed` |
| Bitácora | ❌ | — (read-only) | — |
| Métricas | ❌ | — (read-only) | — |
| Proyecto Total | ❌ | — (read-only) | — |

---

## Receta — frontend (pasos 1 y 2)

### Paso 1. Imports

Al inicio del archivo de la página:

```jsx
import { useResource } from '../../hooks/useResource'
```

(Para páginas en `src/pages/<X>.jsx` la ruta es `../hooks/useResource`. Para
páginas en `src/pages/<X>/<Y>.jsx` es `../../hooks/useResource`.)

### Paso 2. Reemplazar el bloque de carga

**Borrar:**

```jsx
const [items, setItems] = useState([])
const [loading, setLoading] = useState(true)

const load = () => {
  setLoading(true)
  apiFetcher(params)
    .then(setItems)
    .catch((err) => toast.error(extractApiError(err, '...')))
    .finally(() => setLoading(false))
}

useEffect(() => { load() }, [/* deps */])
```

**Por:**

```jsx
const {
  data: rawItems,
  loading,
  error,
  refetch,
} = useResource(
  ['<namespace>', { /* params relevantes */ }],
  () => apiFetcher(params),
  {
    staleMs: 30_000,
    invalidateOn: ['<namespace>:changed'],
  },
)
const items = rawItems ?? []  // fallback si la API regresa null/undefined

useEffect(() => {
  if (error) toast.error(extractApiError(error, 'Error al cargar ...'))
}, [error])
```

### Paso 3. Cambiar las llamadas a `load()` por `await refetch()`

En cada `handleCreate`, `handleEdit`, `handleDelete`, etc. tras la mutación:

```jsx
await apiMutation(...)
toast.success('...')
await refetch()           // ← antes era load()
```

Si el código hacía `setItems(prev => ...)` para actualización optimista,
quítalo y deja solo `await refetch()`. Cuando el backend emita el evento, el
hook invalida la caché automáticamente y refetchea — el optimistic ya no es
necesario.

### Convención de claves

Si la página tiene parámetros (paginación, búsqueda, filtro):

```jsx
useResource(
  ['empleados', { page, q, variante }],
  () => listarTrabajadores({ page, q, estado: variante, perPage: PER_PAGE }),
  ...,
)
```

Si la página carga **un solo recurso global** (sin params):

```jsx
useResource(
  'usuarios',
  () => listarUsuarios(),
  ...,
)
```

> **Importante:** el primer elemento de la clave (`'empleados'` /
> `'usuarios'`) es el **namespace**. Cuando llega un `invalidateOn`, el hook
> invalida **todas** las claves con ese namespace — así, si admin crea un
> empleado, refresca la página 1, la página 2 y la vista de "bajas" cuando
> vuelvas a ellas, no solo la actual.

---

## Receta — backend (pasos 3 y 4)

### Paso 4. Import del emisor

Al inicio del archivo `api_<modulo>.py`:

```python
from app.realtime import emit_to_role
```

### Paso 5. Emitir tras cada commit de mutación

Después de **cada** `db.session.commit()` de un endpoint que **modifica** datos
visibles en la lista, añade:

```python
db.session.commit()
emit_to_role(['admin', 'super_admin'], '<recurso>:changed', {
    'id': obj.id,
    'action': 'created',  # o 'updated', 'deleted', 'baja', 'reactivado', etc.
})
return jsonify(...)
```

Roles que deben recibir el evento, por módulo:

| Módulo | Roles que reciben |
|---|---|
| Empleados, Usuarios, Prenómina, Préstamos, Ajustes, Histórico, Bitácora | `['admin', 'super_admin']` |
| Proyectos | `['admin', 'super_admin', 'coordinador']` |
| Reportes de Horas | `['admin', 'super_admin', 'coordinador']` |
| Credenciales | `['admin', 'super_admin', 'coordinador']` |

### Reglas para decidir si un endpoint emite

- ✅ **Sí emitir** si la mutación cambia algo **visible en la lista** o detalle
  del módulo.
- ❌ **No emitir** si solo afecta datos privados del usuario (cambio de
  contraseña propia, revocar sesión, log_action).
- ❌ **No emitir** si el endpoint es solo lectura (`GET`).

### Importante: emitir DESPUÉS del commit

Si emites antes de `commit()`, un rollback dejaría a los clientes con datos
fantasma. La regla: `commit()` → `emit_to_role()` → `return jsonify()`.

---

## Receta por módulo

Para cada módulo pendiente, abajo está el plan concreto. Sigue las recetas
arriba con estos parámetros.

### Proyectos

**Frontend:** `src/pages/proyectos/ProyectosList.jsx`

```jsx
useResource(
  ['proyectos', { page, q, estado }],
  () => listarProyectos({ page, q, estado, perPage: PER_PAGE }),
  { staleMs: 30_000, invalidateOn: ['proyecto:changed'] },
)
```

**Backend:** `app/routes/api_proyectos.py` — emitir en `crear`, `actualizar`,
`eliminar`, `asignar_coordinador`, `agregar_participante`,
`quitar_participante`.

```python
emit_to_role(['admin', 'super_admin', 'coordinador'], 'proyecto:changed', {
    'id': p.id, 'action': 'updated',
})
```

### Reportes de Horas (la lista)

**Frontend:** `src/pages/horas/ReportesList.jsx`

```jsx
useResource(
  ['reportes-horas', { page, q, estado }],
  () => listarReportes({ page, q, estado, perPage: PER_PAGE }),
  { staleMs: 30_000, invalidateOn: ['reporte:lista_changed'] },
)
```

**Backend:** `app/routes/api_horas.py` — emitir en `crear_reporte` y
`cerrar_reporte` (no en `crear_registro` / `editar_registro` / `eliminar_registro`
— esos son cambios dentro de un reporte, no de la lista).

```python
emit_to_role(['admin', 'super_admin', 'coordinador'], 'reporte:lista_changed', {
    'id': r.id, 'action': 'created',
})
```

### Prenómina

**Frontend:** `src/pages/prenomina/PrenominaList.jsx`

```jsx
useResource(
  ['prenomina', { fecha_inicio, fecha_fin }],
  () => listarPrenominas({ fecha_inicio, fecha_fin }),
  { staleMs: 30_000, invalidateOn: ['prenomina:changed'] },
)
```

**Backend:** `app/routes/api_prenomina.py` — emitir en `generar`, `cerrar`,
`reabrir`, `enviar_correo`, `aplicar_ajuste`.

```python
emit_to_role(['admin', 'super_admin'], 'prenomina:changed', {
    'fecha': fecha.isoformat(), 'action': 'cerrada',
})
```

### Préstamos

**Frontend:** `src/pages/prestamos/PrestamosList.jsx`

```jsx
useResource(
  ['prestamos', { page, q, estado }],
  () => listarPrestamos({ page, q, estado, perPage: PER_PAGE }),
  { staleMs: 30_000, invalidateOn: ['prestamo:changed'] },
)
```

**Backend:** `app/routes/api_prestamos.py` — emitir en `crear`, `actualizar`,
`cancelar`, `marcar_pagado`.

```python
emit_to_role(['admin', 'super_admin'], 'prestamo:changed', {
    'id': p.id, 'action': 'created',
})
```

### Ajustes

**Frontend:** `src/pages/ajustes/AjustesList.jsx`

```jsx
useResource(
  'ajustes',  // clave simple, sin params
  () => listarAjustes(),
  { staleMs: 30_000, invalidateOn: ['ajuste:changed'] },
)
```

**Backend:** `app/routes/api_ajustes.py` — emitir en `crear_periodo`,
`agregar_descuento`, `eliminar_descuento`, `cerrar_periodo`.

```python
emit_to_role(['admin', 'super_admin'], 'ajuste:changed', {
    'id': periodo.id, 'action': 'created',
})
```

### Histórico

**Frontend:** `src/pages/historico/HistoricoList.jsx`

Solo `staleMs` + `revalidateOnFocus`. Por la naturaleza del recurso (cerrado
y archivado), no necesita push.

```jsx
useResource(
  ['historico', { page, year }],
  () => listarHistorico({ page, year }),
  { staleMs: 60_000 },  // 1 min, casi nunca cambia
)
```

### Credenciales

**Frontend:** `src/pages/credenciales/CredencialesList.jsx`

```jsx
useResource(
  ['credenciales', { page, q }],
  () => listarCredenciales({ page, q, perPage: PER_PAGE }),
  { staleMs: 30_000, invalidateOn: ['credencial:changed'] },
)
```

**Backend:** ya vive en `api_trabajadores.py` (endpoint
`guardar_credenciales`). Añadir un emit ahí:

```python
emit_to_role(['admin', 'super_admin', 'coordinador'], 'credencial:changed', {
    'trabajador_id': t.id,
})
```

### Bitácora, Métricas, Proyecto Total (solo lectura)

**Frontend:** solo `useResource` con `staleMs` apropiado, **sin**
`invalidateOn`.

```jsx
// Bitacora.jsx
useResource(
  ['bitacora', { page, filtros }],
  () => listarBitacora({ page, filtros }),
  { staleMs: 60_000 },  // bitácora es append-only, 1 min está bien
)

// Metricas.jsx
useResource(
  ['metricas', { periodo }],
  () => obtenerMetricas({ periodo }),
  { staleMs: 120_000 },  // métricas agregadas no necesitan ser instantáneas
)
```

---

## Smoke test después de cada migración

1. **Build local:** `npx vite build` en `plantilla-frontend/` — debe pasar sin
   errores.
2. **Import de la app:** en el server,
   `venv/bin/python -c "from app import create_app; create_app()"` — debe imprimir OK.
3. **Smoke en browser:**
   - Hard refresh.
   - Entrar a la página migrada → ver el fetch en Network.
   - Salir y volver dentro de 30s → ya no debe haber fetch.
   - Otra pestaña / otro admin: hacer una mutación.
   - Confirmar que la primera pestaña refresca en <1s sin recargar.

---

## Errores comunes y cómo evitarlos

### `data` es `null` al primer render

`useResource` devuelve `data: null` hasta que llega el primer fetch. Siempre
usa fallback:

```jsx
const items = rawItems ?? []
const dataSafe = rawData ?? { items: [], total: 0 }
```

### Llave inestable causa refetch en cada render

❌ Mal:
```jsx
useResource(['x', { fecha: new Date() }], ...)  // Date nuevo cada render
useResource(['x', { config: { ...defaults } }], ...)  // objeto nuevo cada render
```

✅ Bien: usa primitivos o referencias estables (estado, memo).

### Refetch infinito

Si el hook hace fetch en loop, probablemente tu fetcher recibe un parámetro que
viene de `data` (lo que devuelve el propio hook). Rompe la dependencia
circular pasando solo lo que viene de `useState` o props.

### Backend emite a la sala equivocada

`emit_to_role(['admin'], ...)` solo llega a usuarios cuyo `user.role` sea
`'admin'`. Si el coordinador también debe ver el cambio, incluye su rol:
`emit_to_role(['admin', 'coordinador'], ...)`.

---

## Para añadir un nuevo módulo desde cero

1. Decide el namespace: `<recurso>` en singular (p. ej. `vehiculo`).
2. Backend: emite `vehiculo:changed` en cada commit de mutación.
3. Frontend: `useResource(['vehiculos', params], fetcher, { invalidateOn: ['vehiculo:changed'] })`.
4. Migración a producción: ningún paso de infra adicional — la sala
   `role:<rol>` se une sola en el handshake del socket, y el `message_queue`
   Redis ya replica entre workers.

---

## Referencia rápida — archivos clave

| Archivo | Rol |
|---|---|
| `src/utils/resourceCache.js` | Caché en memoria + invalidación por prefijo |
| `src/hooks/useResource.js` | Hook que envuelve fetcher + caché + socket |
| `src/context/SocketContext.jsx` | Conexión WS única por sesión |
| `app/realtime.py` | `emit_to_role`, `emit_to_user`, `emit_to_reporte` |
| `Gunicorn .config` | Worker class `gthread` (NO eventlet con psycopg3) |

---

## Por qué este patrón y no React Query / SWR

- Cero dependencias nuevas (~150 líneas total entre hook y caché).
- API idéntica a SWR — si después prefieres SWR, migrar es cambiar el import.
- Integración nativa con tu `SocketContext` existente.
- El `emit_to_role` reusa la misma infra Redis del `message_queue` que ya
  tenías para notificaciones — cero overhead.
