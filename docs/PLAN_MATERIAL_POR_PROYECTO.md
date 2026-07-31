# Plan: material por proyecto — entrada, salida y existencias

Propuesta de diseño para hacer más fácil trabajar con material apartado a
proyectos. Es un plan, no una implementación: nada de esto está construido
todavía.

---

## 1. Qué ya existe (y funciona)

Conviene decirlo primero, porque el plan **no parte de cero**:

- **`stock_almacen_proyecto`** es la fuente de verdad del stock por proyecto.
  Guarda `(producto, almacén, proyecto)` y `proyecto_id = NULL` significa stock
  General/libre. Los totales por almacén y por producto son caches derivados.
- **Los movimientos ya entienden de proyecto.** ENTRADA, SALIDA, AJUSTE y
  TRASPASO mueven un bucket; REASIGNACIÓN mueve stock de un proyecto a otro
  dentro de la misma bodega.
- **La portada de almacenes** tiene una matriz proyecto × almacén con totales.
- **La galería de un almacén** se puede filtrar por proyecto.
- **El editor de buckets** (`/productos/<id>/ajustar-buckets`) permite corregir
  a mano las existencias por proyecto, generando ajustes trazables.

El motor está bien. Lo que falla es cómo se llega a él.

---

## 2. Los tres problemas reales

### Problema A — La aplicación piensa en bodegas; tú piensas en proyectos

Para meter material de un proyecto hoy hay que ir a *Registrar movimiento* y
llenar, en este orden: tipo → producto → bodega → **y recién entonces**
proyecto. El proyecto es el último campo, un desplegable más entre otros.

Pero el usuario no llega pensando «voy a hacer una entrada»; llega pensando
**«llegó material del proyecto Norte»**. La pantalla le pide invertir su modelo
mental en cada captura.

Peor: es **un movimiento por material**. Si llegan 12 materiales del mismo
proyecto, son 12 formularios completos, repitiendo tipo, bodega y proyecto cada
vez.

### Problema B — La importación ignora el proyecto en materiales existentes

En la plantilla de importación, la columna `Proyecto` dice:

> *OPCIONAL — Número/nombre del proyecto al que se aparta el stock inicial.
> Vacío = General (libre). **Solo aplica a productos NUEVOS.***

O sea: **la importación masiva por proyecto solo sirve la primera vez**. Si el
cable ya está en el catálogo —que es lo normal— la columna se ignora y el stock
no se aparta. Para cargar material de un proyecto sobre materiales que ya
existen, no queda más remedio que ir uno por uno por movimientos.

Esto es, con diferencia, lo que más tiempo cuesta.

### Problema C — No hay una pantalla que responda «¿cuánto me queda del proyecto X?»

La información está, pero repartida y ninguna vista la junta:

| Dónde | Qué muestra | Qué le falta |
|---|---|---|
| Portada de almacenes | Matriz proyecto × almacén | Solo totales; no dice **qué** materiales |
| Galería de un almacén | Materiales filtrados por proyecto | Solo **una** bodega a la vez |
| Detalle de proyecto | Planeado vs. consumido | **No muestra la existencia física** del bucket |
| Catálogo | Stock total del producto | No desglosa cuánto es de cada proyecto |

Para saber cuánto cable queda del proyecto Norte hay que entrar bodega por
bodega y sumar a mano. Y en la ficha de un material no se ve que sus 100 metros
son en realidad 40 generales + 35 del Norte + 25 del Sur.

---

## 3. La idea que organiza todo el plan

> **Invertir el punto de entrada: primero el proyecto, después el material.**

Hoy el proyecto es un filtro que se aplica al final. La propuesta es que sea el
**contexto** dentro del cual se trabaja. Todo lo demás se deriva de ahí.

---

## 4. Las pantallas

### 4.1 Entrada rápida por proyecto — *la más importante*

Una sola pantalla que reemplaza N formularios de movimiento. Se elige el
destino una vez y se capturan todos los materiales en lista.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Entrada de material                                                 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   Destino          ┌────────────────────┐  ┌──────────────────────┐  │
│                    │ Bodega:  CDMX    ▾ │  │ Proyecto: Norte    ▾ │  │
│                    └────────────────────┘  └──────────────────────┘  │
│                                            ○ General (sin proyecto)  │
│                                                                      │
│   ┌────────────────────────────────────────────────────────────────┐ │
│   │  Buscar o escanear material…                            [ QR ] │ │
│   └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│   MATERIAL                        EN PROYECTO   ENTRA      QUEDA     │
│   ─────────────────────────────────────────────────────────────────  │
│   CBL-001  Cable THW 12 AWG            120 m   [  50 ]     170 m  ✕  │
│   TUB-004  Tubo conduit 1/2"            18 pz  [  40 ]      58 pz  ✕ │
│   CON-012  Conector recto 1/2"           0 pz  [ 100 ]     100 pz  ✕ │
│                                                                      │
│   + Agregar otro material                                            │
│                                                                      │
│   ──────────────────────────────────────────────────────────────────│
│   3 materiales · 190 unidades                    [Cancelar] [Guardar]│
└──────────────────────────────────────────────────────────────────────┘
```

Decisiones de diseño y su porqué:

- **Destino arriba y fijo.** Se elige una vez. Es el contexto, no un campo.
- **Columna «EN PROYECTO».** Muestra lo que ya hay en ese bucket *antes* de
  capturar. Sin ese dato uno captura a ciegas y descubre el error después.
- **Columna «QUEDA».** Cálculo en vivo. Convierte el formulario en una
  confirmación en lugar de un salto de fe.
- **Escáner integrado.** Ya existe `ScannerMovil`; aquí agrega la línea directa.
- **Un solo guardado.** Genera N movimientos ENTRADA en una transacción. Si uno
  falla, no se aplica ninguno — nada de quedarse a medias.

### 4.2 Salida / consumo por proyecto

La misma pantalla, en espejo. Cambia una cosa importante:

```
│   MATERIAL                        DISPONIBLE    SALE      QUEDA      │
│   ─────────────────────────────────────────────────────────────────  │
│   CBL-001  Cable THW 12 AWG            170 m   [  80 ]      90 m  ✕  │
│   TUB-004  Tubo conduit 1/2"            58 pz  [  60 ]   ⚠ faltan 2  │
```

- **Validación en vivo contra el bucket**, no contra el stock total. Pedir 60
  cuando el proyecto tiene 58 se marca en el momento, no al guardar.
- **Sin salidas a ciegas.** Si el material no tiene existencia en ese proyecto,
  se ofrece explícitamente: *«No hay en Norte. ¿Tomar del stock General?»* — que
  es una **reasignación + salida**, y debe quedar registrada como tal.

### 4.3 Existencias del proyecto — la pantalla que falta

Una pestaña nueva en el detalle de proyecto, junto a Plan / Consumo / Pedidos.
Responde de un vistazo «¿qué tengo apartado y dónde?».

```
┌──────────────────────────────────────────────────────────────────────┐
│  Proyecto Norte     [ Plan ] [ Consumo ] [ Pedidos ] [ EXISTENCIAS ] │
├──────────────────────────────────────────────────────────────────────┤
│   14 materiales · 1 240 unidades · $ 86 400 en almacén               │
│                                                                      │
│   MATERIAL                     CDMX    QRO   TOTAL   PLANEADO  ESTADO│
│   ───────────────────────────────────────────────────────────────────│
│   Cable THW 12 AWG             120 m   50 m  170 m     200 m   ▓▓▓░ │
│   Tubo conduit 1/2"             58 pz    —    58 pz     40 pz  ▓▓▓▓ │
│   Conector recto 1/2"          100 pz    —   100 pz       —    ▓    │
│                                                                      │
│   [ Exportar a Excel ]                        [ Devolver a General ] │
└──────────────────────────────────────────────────────────────────────┘
```

- **Una fila por material, una columna por bodega.** Se acabó entrar bodega por
  bodega.
- **Contra el plan.** Sirve para responder «¿ya tengo lo que planeé?». La barra
  compara existencia con planeado; sin plan, se muestra vacía en lugar de
  inventar un porcentaje.
- **«Devolver a General»** para material que sobró al cerrar un proyecto. Hoy
  hay que hacerlo material por material con reasignaciones.

### 4.4 Desglose en la ficha del material — *ya existe, hay que reagruparlo*

**Corrección:** esto ya está construido. El modal «Stock por bodega y proyecto»
del catálogo muestra el desglose. Lo que falla es la forma de presentarlo.

Hoy es una lista plana, una línea por combinación bodega+proyecto:

```
   CDMX · General          100 m
   CDMX · Proyecto Norte   120 m
   QRO  · General           45 m
   QRO  · Proyecto Norte    50 m
   CDMX · Proyecto Sur      25 m
```

Para responder «¿cuánto tiene el Norte?» hay que buscar sus líneas entre todas
y sumarlas mentalmente. El agrupamiento está al revés de la pregunta.

Agrupado por proyecto, con las bodegas como detalle:

```
   General            145 m    CDMX 100 · QRO 45
   Proyecto Norte     170 m    CDMX 120 · QRO 50
   Proyecto Sur        25 m    CDMX  25
```

Mismo dato, misma llamada al API. Solo cambia cómo se agrupa en pantalla —
media hora de trabajo y responde la pregunta sin aritmética mental.

---

## 5. La importación

Dos cambios, uno imprescindible y otro cómodo.

### 5.1 Quitar la limitación de «solo productos nuevos» — imprescindible

Que la columna `Proyecto` funcione también con materiales existentes. Es lo que
convierte la importación en una herramienta de carga real y no solo de alta
inicial.

**Cuidado, y es la parte delicada:** hoy la importación *no toca el stock* de
productos existentes, a propósito, para que reimportar el catálogo no altere
existencias. Ese principio no se puede romper. La forma correcta es separar los
dos usos:

| Modo | Qué hace con el stock |
|---|---|
| **Catálogo** (el de hoy) | Alta y actualización de datos. **Nunca** toca existencias. |
| **Carga de material** (nuevo) | Solo suma existencias al bucket indicado. No edita datos del material. |

Que sean dos modos y no una casilla evita el peor accidente posible: creer que
estás actualizando precios y acabar duplicando el inventario.

### 5.2 Plantilla de carga por proyecto

Una plantilla mínima, de cuatro columnas, para el segundo modo:

```
   SKU        │ Cantidad │ Bodega │ Proyecto
   ───────────┼──────────┼────────┼──────────
   CBL-001    │    50    │ CDMX   │ Norte
   TUB-004    │    40    │ CDMX   │ Norte
```

Y sobre todo: **vista previa antes de aplicar**, con el estado resultante.

```
   ✓ CBL-001  Cable THW 12 AWG     120 → 170 m
   ✓ TUB-004  Tubo conduit 1/2"     18 →  58 pz
   ⚠ XYZ-999  no existe en el catálogo — se omite
   ⚠ CON-012  bodega «CDMS» no existe — ¿quisiste decir CDMX?
```

Nada se aplica hasta confirmar. La importación actual ya tiene un mecanismo
parecido para categorías nuevas; se reutiliza el mismo patrón.

---

## 6. Orden sugerido

Ordenado por relación entre lo que ahorra y lo que cuesta:

| # | Qué | Por qué primero | Esfuerzo |
|---|---|---|---|
| 1 | Desglose por proyecto en la ficha (§4.4) | Los datos y el endpoint ya existen. Es casi solo interfaz. | Bajo |
| 2 | Pestaña Existencias del proyecto (§4.3) | Responde tu pregunta central. Solo lectura, sin riesgo. | Medio |
| 3 | Entrada rápida por proyecto (§4.1) | El mayor ahorro de tiempo diario. | Medio-alto |
| 4 | Importación de carga por proyecto (§5) | Resuelve la carga masiva. Delicado por lo del stock. | Alto |
| 5 | Salida rápida por proyecto (§4.2) | Reutiliza casi todo lo del punto 3. | Medio |

Los puntos 1 y 2 **no modifican datos**: solo muestran lo que ya está guardado.
Se pueden hacer y desplegar sin ningún riesgo, y ya con eso se contesta buena
parte de lo que hoy no se ve.

---

## 7. Decisiones que hacen falta antes de construir

Estas cambian el diseño y no las puedo decidir yo:

1. **Al dar salida sin existencia en el proyecto**, ¿se permite tomar del
   General automáticamente, se pregunta, o se bloquea? Afecta a toda la pantalla
   de salida.
2. **¿El material apartado a un proyecto debe poder consumirse desde otro?** Hoy
   los buckets son contables, no candados. Si debe ser un candado real, cambia
   la lógica de reservas.
3. **La pestaña Existencias, ¿debe comparar contra el plan?** Solo tiene sentido
   si los proyectos se planean siempre; si no, la columna sobra.
4. **En la importación de carga, ¿suma o reemplaza?** Sumar es lo natural para
   recepciones; reemplazar sirve para cuadrar tras un conteo. Son dos usos
   distintos y conviene no mezclarlos.
