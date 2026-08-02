# Migración de archivos privados a Cloudflare R2

Estado: implementado en backend, **pendiente de correr el backfill** en cada entorno.

## Qué se migra

Las imágenes del **catálogo de productos** ya vivían en R2 (bucket público
`skilled-productos`, ver `app/utils/r2.py`). Lo que faltaba eran los archivos
privados, que seguían en el disco del VPS bajo `uploads/`:

| Familia | Columna | Key / ruta relativa |
|---|---|---|
| Foto de perfil de usuario | `User.profile_pic` | `profile_7_ab12cd34.webp` |
| Foto de perfil de trabajador | `Trabajador.foto_perfil` | `perfiles/pp_1712345_foto.webp` |
| Thumbnail del trabajador | *(derivada)* | `perfiles/thumb_pp_1712345_foto.webp` |
| Documentos de RRHH | `DocumentoTrabajador.ruta_archivo` | `trabajadores/<id>/<archivo>` |
| Fotos/evidencias de herramientas | `MediaHerramienta.ruta_archivo` | `herramientas/<id>/<archivo>` |

## Decisiones de diseño

**Bucket separado y privado.** Aquí viven documentos de RRHH con PII (contratos,
INE, CURP) y fotos de personal. El bucket del catálogo es público por dominio;
meter estos archivos ahí los volvería legibles por cualquiera que tuviera la URL.
El bucket privado **no lleva dominio conectado ni "Allow public access"**.

**La key es la ruta que ya guardaba la BD.** `perfiles/x.webp` era un path en
disco y ahora es también el object key en R2. Consecuencia: **cero migraciones de
Alembic, cero columnas nuevas**, y el backfill es idempotente.

**Los endpoints no cambiaron.** El SPA nunca pegó a `/uploads/` — siempre pasó
por rutas con JWT (`GET /api/trabajadores/<id>/foto`,
`/api/trabajadores/documentos/<id>`, `/api/auth/users/<id>/foto`,
`/api/herramientas-unidades/<uid>/media/<id>`). El backend lee de R2 y devuelve
los mismos bytes con el mismo `Content-Type`. **El frontend no se tocó.**

**Streaming, no URLs firmadas.** El archivo pasa por el VPS. Son fotos y PDFs
chicos (tope 20 MB por documento, 5 MB por foto de herramienta); a cambio, el
control de acceso sigue siendo exactamente el de hoy y no hubo que tocar CORS.

## El seguro: comportamiento dual

`app/utils/archivos.py` es la única puerta. Su gate es `habilitado()` — hay
bucket privado + llaves resolubles:

- **Escritura** → R2. Si R2 falla, cae a disco y lo registra: un incidente de red
  no le cuesta al usuario su documento (el backfill lo recoge después).
- **Lectura** → R2 primero; si el object no existe, **disco**. Por eso se puede
  desplegar el código antes de correr el backfill: lo no migrado sigue sirviendo.
- **Borrado** → ambos lados, best-effort.
- **Sin configurar** → todo en disco, idéntico a antes de este cambio.

## Configuración

En el `.env` de **cada entorno** (las llaves de local y del VPS son distintas):

```ini
R2_PRIVADO_BUCKET=skilled-privados
# Opcionales: si van vacías, se reusan las R2_* del mismo entorno.
R2_PRIVADO_ACCOUNT_ID=
R2_PRIVADO_ACCESS_KEY_ID=
R2_PRIVADO_SECRET_ACCESS_KEY=
```

Si `R2_PRIVADO_BUCKET` queda vacía, el módulo se apaga solo. No hay fallback al
bucket público: nunca se escriben documentos de RRHH en `skilled-productos`.

## Apartado en el panel de sistemas

`Sistemas → Mantenimiento` tiene una sección **«Archivos privados en la nube»**,
equivalente a la de imágenes del catálogo:

- **Inventario** en tarjetas y desglose por tipo de archivo: cuántos están en la
  nube, cuántos siguen en disco y cuántas referencias quedaron sin archivo.
- **Botón «Sincronizar N»**: sube los pendientes en segundo plano. No borra la
  copia local y es idempotente, por eso no pide confirmación.
- **Barra de progreso en vivo** por el socket `archivo:sync_progreso`
  (`emit_to_user`, solo al que lanzó el trabajo).
- Si `R2_PRIVADO_BUCKET` no está configurada, la sección explica que se está
  guardando en disco en vez de mostrar ceros como si algo estuviera roto.

Endpoints (rol `sistemas` o `super_admin`, con 2FA):

```
GET  /api/sistemas/archivos              inventario
POST /api/sistemas/archivos/sincronizar  encola los pendientes
```

El estado NO se calcula con un `head_object` por archivo: se lista el bucket
completo con `list_objects_v2` (1000 keys por llamada) y se compara contra las
keys que referencia la BD. `keys_referenciadas()` vive en
`app/routes/api_sistemas/archivos.py` y **el script CLI la importa de ahí**, para
que la terminal y la UI nunca reporten cosas distintas.

Sincronizar desde el panel deja constancia en la bitácora con el usuario que lo
ejecutó. El único paso que el panel NO hace es borrar la copia local: eso sigue
siendo exclusivo de `--borrar-local` en la terminal, a propósito.

## Procedimiento de despliegue

1. **Cloudflare** → R2 → Create bucket (ej. `skilled-privados`). No conectar
   dominio ni activar acceso público.
2. Poner `R2_PRIVADO_BUCKET` en el `.env` del entorno. Si el token de R2 actual
   no alcanza ese bucket, crear uno (Object Read & Write) y llenar las tres
   variables opcionales.
3. Desplegar el código y reiniciar. **Nada se rompe todavía**: lo nuevo va a R2,
   lo viejo se sigue leyendo de disco.
4. Respaldar `uploads/`.
5. Subir los archivos, por cualquiera de los dos caminos:

   - **Desde la app:** `Sistemas → Mantenimiento → Archivos privados en la nube
     → Sincronizar`. Es lo más cómodo y muestra el progreso en vivo.
   - **Desde la terminal**, si prefieres el reporte completo (incluye huérfanos):

```bash
python scripts/migrar_archivos_a_r2.py --dry-run
python scripts/migrar_archivos_a_r2.py
```

6. Verificar en la app: abrir un expediente con foto, descargar un documento, ver
   una foto de unidad de herramienta y un avatar de usuario.
7. Sólo cuando todo se vea bien, y con el respaldo a mano, liberar el disco:

```bash
python scripts/migrar_archivos_a_r2.py --borrar-local
```

`--borrar-local` sólo borra un archivo tras confirmar con `head_object` que ya
está en R2.

## Qué se re-encodea a WebP

Todo lo que es imagen. El re-encode no es solo ahorro de espacio: lo que se
almacena es un ráster recién renderizado por Pillow, así que ningún payload
embebido en el original sobrevive. Validar los magic bytes dice que el archivo
**es** una imagen, no que sea inofensiva.

| Archivo | ¿WebP? |
|---|---|
| Foto de perfil de usuario | Sí |
| Foto de trabajador + miniatura | Sí |
| Documento de trabajador (JPG/PNG/HEIC) | Sí |
| Documento de trabajador (PDF) | No — se guarda intacto |
| Foto de unidad de herramienta | Sí (desde 2026-08-01) |

Los PDF son la única excepción y no hay forma de evitarlo: convertirlos
destruiría el documento. El riesgo que quedaría de un PDF malicioso es para
**quien lo descarga y lo abre**, no para el servidor: no hay ninguna librería de
PDF ni `subprocess` en `app/`, así que nada del backend interpreta un archivo
subido — solo almacena y devuelve bytes. Mitigaciones vigentes: extensión
restringida a PDF/JPG/PNG/HEIC para documentos de RRHH (se excluyeron a
propósito xlsm/docx y demás formatos con macros), verificación de que la cabecera
sea realmente `%PDF`, tope de 20 MB, descarga forzada con
`Content-Disposition: attachment` y `X-Content-Type-Options: nosniff`.

## Cómo comprobar que se sirve desde el bucket

Con lectura dual, mirar la foto no dice de dónde vino. Tres formas, de la más
rápida a la más concluyente:

1. **El header.** Toda respuesta de archivo lleva `X-Almacenamiento: r2` o
   `disco`. En el navegador: F12 → pestaña Red → abre una foto o descarga un
   documento → mira los headers de la respuesta. Es lo que de verdad pasó en esa
   petición, no una inferencia.
2. **El panel.** `Sistemas → Mantenimiento`: si «Por subir» está en 0, no queda
   nada en disco que R2 no tenga, así que toda lectura la resuelve R2 (siempre se
   intenta R2 primero y solo se cae a disco si el object no está).
3. **La prueba destructiva** (la única concluyente): renombra la carpeta
   `uploads/` en el servidor y recarga la app. Si las fotos y documentos siguen
   apareciendo, no hay duda de que salen del bucket. Al terminar, devuélvele el
   nombre — sigue siendo el respaldo hasta que corras `--borrar-local`.

También sirve el panel de Cloudflare (R2 → tu bucket → Metrics): las operaciones
Class B deberían subir mientras navegas por expedientes.

## Salvaguardas

- **Nunca al bucket público.** Si `R2_PRIVADO_BUCKET` coincide con `R2_BUCKET`
  (el del catálogo, que tiene dominio conectado), el módulo se desactiva solo,
  lo registra como ERROR y sigue guardando en disco. Un error de tecleo no puede
  acabar publicando contratos.
- **Traversal.** `_norm` rechaza `..`, `.`, keys vacías y unidades absolutas;
  `ruta_local` además comprueba la contención real del path resuelto. Escribir
  con una key inválida lanza; leer se degrada a 404.
- **Una fila sucia no tumba el panel.** Tanto `clasificar()` como el script CLI
  catalogan las rutas inválidas como faltantes y siguen.
- **Sin doble corrida.** Sincronizar toma un candado en Redis (`SET NX EX`, TTL
  30 min, liberado en `finally` y solo por su dueño). Un segundo intento recibe
  409. Sin Redis no se bloquea: subir es idempotente y trabajo duplicado es
  preferible a no poder operar.
- **Reemplazo de foto.** Se sube la nueva antes de borrar la anterior, así que
  un fallo de red deja al trabajador con su foto vieja, no sin ninguna.
- **Errores sin filtrar secretos.** El mensaje de botocore lleva el endpoint, y
  el endpoint lleva el valor de `R2_PRIVADO_ACCOUNT_ID`. `_mensaje_de_error`
  nunca reenvía `str(e)`.

## Notas

- El script recorre la **BD**, no el árbol de directorios: al final reporta los
  archivos huérfanos de `uploads/` (no referenciados por ninguna fila). No los
  sube ni los borra — quedan para revisión manual.
- También reporta lo contrario: filas que apuntan a un archivo que no está ni en
  R2 ni en disco. Eso es dato roto de antes de la migración, no lo causa el
  backfill.
- Los tests corren siempre con el bucket apagado (`tests/conftest.py` vacía las
  `R2_PRIVADO_*`), así que nunca tocan la red. La cobertura del módulo está en
  `tests/test_archivos_r2_privado.py`, con un cliente R2 falso en memoria.
