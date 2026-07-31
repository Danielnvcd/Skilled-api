# Asignación de material por proyecto — sección nueva

Diseño de una sección dedicada, con flujo por modales, para asignar material a
proyectos a mano o desde Excel. Sustituye al apartado 5 del plan anterior, que
se quedaba corto.

---

## 1. Por qué hoy es difícil (con nombres y números)

### El Excel te pide 13 columnas para decir una cosa

Para asignar 50 metros de cable al proyecto Norte, la plantilla actual exige:

```
Código (SKU) · Descripción · Marca · Categoría · Tipo (cable) ·
Tamaño mm²/AWG · Unidad · Stock Inicial · Almacén · Proyecto ·
Stock Mínimo · Precio Unitario · URL Imagen
```

De esas trece, **solo tres importan** para asignar: SKU, cantidad y proyecto.
Las otras diez describen un material que **ya existe en el catálogo**. Se está
usando una plantilla de *alta de catálogo* para una tarea de *movimiento de
stock*. Son cosas distintas y por eso se siente forzado.

### Y aun llenándolas, no funciona

La columna `Proyecto` lleva esta nota:

> *Solo aplica a productos NUEVOS. En existentes el stock no se toca.*

O sea: si el cable ya está dado de alta —el caso normal— la columna se ignora.
La asignación masiva por Excel **no existe** para materiales existentes.

### La alternativa manual es un formulario por material

*Registrar movimiento* pide tipo, producto, bodega y proyecto. Para diez
materiales del mismo proyecto son diez formularios, repitiendo tres de los
cuatro campos cada vez.

### No hay un lugar donde «asignar» viva

Las piezas están repartidas entre Movimientos, Catálogo, Importar y la portada
de Almacenes. Ninguna se llama «asignar material a un proyecto», que es la
tarea que la persona tiene en la cabeza.

---

## 2. La idea

> **Una sección propia donde el proyecto es el contexto y todo lo demás son
> acciones dentro de él.**

Se entra eligiendo proyecto. A partir de ahí, todo —agregar, importar, mover,
devolver— ocurre sin volver a preguntar a qué proyecto, porque ya se sabe.

---

## 3. Mapa de la sección

```
   Menú → Inventario → Material por proyecto
                        │
                        ├── Pantalla principal: lista de proyectos
                        │
                        └── Proyecto elegido: sus materiales
                                 │
                                 ├── [modal] Agregar material
                                 ├── [modal] Importar desde Excel
                                 ├── [modal] Mover a otro proyecto
                                 └── [modal] Devolver a General
```

Una entrada de menú, una pantalla, cuatro modales. Nada más.

---

## 4. Pantalla principal — elegir proyecto

```
┌────────────────────────────────────────────────────────────────────────┐
│  Material por proyecto                                                 │
│  Qué material tiene apartado cada obra, y cómo asignarlo.              │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌──────────────────────┐ ┌──────────────────────┐ ┌─────────────────┐ │
│  │ ● General (libre)    │ │ P-001  Obra Norte    │ │ P-002  Sur      │ │
│  │                      │ │                      │ │                 │ │
│  │ 240 materiales       │ │  14 materiales       │ │  6 materiales   │ │
│  │ 12 400 unidades      │ │ 1 240 unidades       │ │  320 unidades   │ │
│  │ $ 840 000            │ │ $ 86 400             │ │ $ 21 000        │ │
│  └──────────────────────┘ └──────────────────────┘ └─────────────────┘ │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

- **General va primero y se ve distinto.** Es el stock libre, el origen de casi
  toda asignación. No es un proyecto más.
- **Tres números por tarjeta**, no más: cuántos materiales distintos, cuántas
  unidades, cuánto vale. Suficiente para decidir dónde entrar.
- Solo aparecen proyectos activos y General.

---

## 5. Pantalla del proyecto — sus materiales

```
┌────────────────────────────────────────────────────────────────────────┐
│  ← Volver     P-001 · Obra Norte                                       │
│                                                                        │
│               [ + Agregar material ]  [ Importar Excel ]  [ ⋯ ]        │
├────────────────────────────────────────────────────────────────────────┤
│  14 materiales · 1 240 unidades · $ 86 400                             │
│                                                                        │
│  ┌ Buscar material… ────────────────────────────────────────────────┐  │
│                                                                        │
│  ☐  MATERIAL                        CDMX     QRO    TOTAL             │
│  ───────────────────────────────────────────────────────────────────   │
│  ☐  CBL-001  Cable THW 12 AWG      120 m    50 m   170 m      [ ⋯ ]   │
│  ☐  TUB-004  Tubo conduit 1/2"      58 pz     —     58 pz     [ ⋯ ]   │
│  ☐  CON-012  Conector recto         100 pz    —    100 pz     [ ⋯ ]   │
│                                                                        │
│  ── 2 seleccionados ────────────────────────────────────────────────   │
│     [ Mover a otro proyecto ]   [ Devolver a General ]                 │
└────────────────────────────────────────────────────────────────────────┘
```

- **Selección múltiple con casillas.** Devolver diez materiales a General debe
  ser una acción, no diez.
- **La barra de acciones aparece solo al seleccionar.** Sin selección no
  estorba.
- **El menú `⋯` de cada fila** repite las acciones para el caso de una sola
  línea, que es el más común.

---

## 6. Modal — Agregar material

El sustituto del formulario de movimiento. El proyecto ya no se pregunta.

```
┌──────────────────────────────────────────────────────────────────┐
│  Agregar material a P-001 · Obra Norte                       ✕   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ¿De dónde sale?                                                │
│     ● Del stock General          ○ Es material que acaba de      │
│       (ya está en bodega)          llegar (entrada nueva)        │
│                                                                  │
│   Bodega   ┌──────────────┐                                      │
│            │ CDMX       ▾ │                                      │
│            └──────────────┘                                      │
│                                                                  │
│   ┌────────────────────────────────────────────────────────────┐ │
│   │  Buscar material o escanear código…                 [ QR ] │ │
│   └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│   MATERIAL                  EN GENERAL   ASIGNAR    QUEDA        │
│   ─────────────────────────────────────────────────────────────  │
│   CBL-001  Cable THW         340 m       [  50 ]    290 m    ✕   │
│   TUB-004  Tubo conduit       12 pz      [  40 ]   ⚠ solo 12 pz  │
│                                                                  │
│   + Agregar otra línea                                           │
│                                                                  │
│   ──────────────────────────────────────────────────────────────│
│   1 línea válida · 1 con problema        [Cancelar]  [Asignar]   │
└──────────────────────────────────────────────────────────────────┘
```

Decisiones y su porqué:

- **La primera pregunta es de dónde sale.** No es un detalle técnico: decide si
  esto es una **reasignación** (mover de General al proyecto) o una **entrada**
  (material que llega de fuera). Confundirlas descuadra el inventario, así que
  se pregunta al principio y en lenguaje llano, no con los nombres de los tipos
  de movimiento.
- **Columna «EN GENERAL».** Muestra el disponible *antes* de escribir. Sin ese
  dato se captura a ciegas.
- **Validación por línea, en vivo.** Pedir 40 cuando hay 12 se marca en el
  momento, no al guardar. El botón se mantiene activo: se aplican las líneas
  válidas y se informa de las otras — no se castiga todo el trabajo por un error
  en una línea.
- **Un solo botón para N líneas**, en una transacción.

---

## 7. Modal — Importar desde Excel

Tres pasos dentro del mismo modal, sin cambiar de pantalla.

### Paso 1 — Descargar la plantilla

```
┌──────────────────────────────────────────────────────────────────┐
│  Importar material a P-001 · Obra Norte                      ✕   │
├──────────────────────────────────────────────────────────────────┤
│   Paso 1 de 3 ─ ○ ─ ○                                            │
│                                                                  │
│   Descarga la plantilla. Ya viene con el proyecto puesto y con   │
│   los materiales que este proyecto ya tiene, para que solo       │
│   ajustes cantidades.                                            │
│                                                                  │
│        [ Descargar plantilla de Obra Norte ]                     │
│                                                                  │
│   Son solo tres columnas:                                        │
│   ┌───────────────┬──────────┬──────────┐                        │
│   │ SKU           │ Cantidad │ Bodega   │                        │
│   ├───────────────┼──────────┼──────────┤                        │
│   │ CBL-001       │    50    │ CDMX     │                        │
│   │ TUB-004       │    40    │ CDMX     │                        │
│   └───────────────┴──────────┴──────────┘                        │
│                                                                  │
│   El proyecto no es columna: lo estás importando desde adentro.  │
└──────────────────────────────────────────────────────────────────┘
```

**Tres columnas en vez de trece.** El proyecto sale del contexto y todo lo
demás (descripción, marca, unidad, precio) ya está en el catálogo — no hay por
qué repetirlo.

Y la plantilla **llega pre-llenada** con lo que el proyecto ya tiene: para
ajustar cantidades no hay que teclear los SKU.

### Paso 2 — Subir y revisar

```
│   Paso 2 de 3 ─ ● ─ ○                                            │
│                                                                  │
│   ┌────────────────────────────────────────────────────────────┐ │
│   │        Arrastra el archivo aquí  ·  o busca en tu equipo   │ │
│   └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│   materiales-norte.xlsx · 24 filas                               │
│                                                                  │
│   ✓ 21 filas listas                                              │
│   ⚠  2 avisos                                                    │
│   ✕  1 error — esa fila se omite                                 │
│                                                                  │
│   MATERIAL                          AHORA →  QUEDA               │
│   ─────────────────────────────────────────────────────────────  │
│   ✓ CBL-001  Cable THW 12 AWG        120  →   170 m              │
│   ✓ TUB-004  Tubo conduit 1/2"        18  →    58 pz             │
│   ⚠ CON-012  Conector recto            0  →   100 pz             │
│       No hay suficiente en General (hay 80). Se asignarán 80.     │
│   ✕ XYZ-999                                                       │
│       Ese SKU no existe en el catálogo.                           │
│                                                                  │
│                              [Cancelar]  [Aplicar 23 filas]      │
```

- **Nada se aplica hasta el paso 3.** Esto es solo una simulación.
- **Se ve el resultado, no la entrada.** «120 → 170» dice más que «+50».
- **Tres niveles, no dos.** Un aviso (se aplica ajustado) no es lo mismo que un
  error (se omite). Mezclarlos hace que la gente ignore ambos.
- **El botón dice cuántas filas aplica**, no un genérico «Aceptar».

### Paso 3 — Confirmación

```
│   Paso 3 de 3 ─ ● ─ ●                                            │
│                                                                  │
│   Se asignaron 23 materiales a Obra Norte.                       │
│                                                                  │
│   1 240  →  2 380 unidades en el proyecto                        │
│                                                                  │
│   1 fila se omitió: XYZ-999 no existe en el catálogo.            │
│                                                                  │
│              [ Descargar las filas omitidas ]   [ Cerrar ]       │
```

Poder **descargar lo omitido** cierra el ciclo: se corrige ese archivito y se
vuelve a subir, sin rehacer las 23 que ya entraron.

---

## 8. Modales de mover y devolver

Los dos son el mismo modal con distinto destino, y ambos parten de la selección
de la tabla.

```
┌──────────────────────────────────────────────────────────────────┐
│  Devolver a General                                          ✕   │
├──────────────────────────────────────────────────────────────────┤
│   3 materiales seleccionados de Obra Norte                       │
│                                                                  │
│   MATERIAL                    EN PROYECTO   DEVOLVER             │
│   ─────────────────────────────────────────────────────────────  │
│   CBL-001  Cable THW              170 m     [ 170 ]  ⟲ todo      │
│   TUB-004  Tubo conduit            58 pz    [  58 ]  ⟲ todo      │
│   CON-012  Conector recto         100 pz    [  30 ]  ⟲ todo      │
│                                                                  │
│   ☑ Devolver todo lo seleccionado                                │
│                                                                  │
│                              [Cancelar]  [Devolver a General]    │
└──────────────────────────────────────────────────────────────────┘
```

- **Se propone devolver todo** —es el caso típico al cerrar una obra— pero cada
  cantidad es editable.
- **Mover a otro proyecto** es idéntico, con un selector de destino arriba.

---

## 9. Qué hay que construir

### Backend

| Endpoint | Para qué | Notas |
|---|---|---|
| `GET /proyectos-materiales/resumen-asignacion` | Tarjetas de la pantalla principal | Agrega `stock_almacen_proyecto` por proyecto |
| `GET .../<id>/existencias` | Tabla del proyecto | **Ya existe** |
| `POST .../<id>/asignar` | Alta manual y aplicación del Excel | Recibe N líneas; una transacción |
| `POST .../<id>/asignar/previsualizar` | Paso 2 del Excel | **No escribe nada**; solo valida y proyecta |
| `POST .../<id>/devolver` | Devolver a General o mover | Destino: General u otro proyecto |
| `GET .../<id>/plantilla-asignacion` | Excel de 3 columnas pre-llenado | |

La reutilización importa: `asignar` y `devolver` deben apoyarse en los helpers
que ya existen (`_depositar`, `_consumir_bucket_exacto`, `_ajustar_bucket`) y
generar los mismos movimientos trazables que hoy. **No se inventa una vía
paralela de tocar el stock** — eso rompería el kardex.

### Frontend

Una entrada de menú, una página con dos vistas y cuatro modales.

### Orden sugerido

| # | Qué | Por qué en ese orden |
|---|---|---|
| 1 | `previsualizar` + `asignar` (backend) | Es el corazón; todo lo demás lo consume |
| 2 | Pantalla principal + tabla del proyecto | Ya hay endpoint para la tabla |
| 3 | Modal *Agregar material* | Sustituye el formulario por material |
| 4 | Modal *Importar Excel* | Lo que más tiempo ahorra |
| 5 | Modales *Mover* y *Devolver* | Reutilizan casi todo lo anterior |

---

## 10. Decisiones antes de empezar

1. **Cuando el Excel pide más de lo que hay en General**, ¿se asigna lo
   disponible con aviso (como dibujé), se omite la fila, o se bloquea todo el
   archivo? Cambia el paso 2 entero.
2. **¿La importación suma o reemplaza?** Si una fila dice 50 y el proyecto ya
   tiene 120, ¿queda en 170 o en 50? Sumar sirve para recepciones; reemplazar,
   para cuadrar tras un conteo. Propongo **sumar** por defecto y ofrecer
   reemplazar como casilla explícita en el paso 2.
3. **¿Se permite asignar material que no está en General** (entrada directa al
   proyecto)? Lo dibujé como opción en el modal, pero implica que puede entrar
   stock sin pasar por General.
4. **¿Qué pasa con el material asignado cuando se cierra un proyecto?**
   ¿Se avisa, se devuelve solo, o se deja?

Las cuatro cambian el diseño; ninguna la puedo decidir yo.
