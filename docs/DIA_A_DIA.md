# Día a día — arrancar, cambiar cosas y comprobar que funcionan

Guía práctica del trabajo diario con el stack en Docker. Para el montaje y el
despliegue en el VPS, ver [DOCKER.md](DOCKER.md).

---

## Arrancar

```bash
cd "C:\Users\ppedo\OneDrive\Documentos\Sistema de nominas"
docker compose up -d
```

Tarda ~30 s: espera la base, aplica migraciones pendientes y arranca Gunicorn.
Para ver el arranque en vivo, `docker compose up` sin `-d`.

Comprobar que está viva:

```bash
curl http://localhost:5000/health      # {"status":"ok"}
docker compose ps                      # los 4 servicios en "healthy"
```

Con el SPA (repo aparte):

```bash
cd "C:\Users\ppedo\OneDrive\Documentos\plantilla-frontend"
npm run dev                            # http://localhost:5173
```

El front ya apunta a `http://localhost:5000/api` (`VITE_API_URL`), que es
justo donde publica el contenedor. No hay que cambiar nada.

---

## Qué hacer según lo que cambies

| Cambiaste… | Qué hacer | Por qué |
|---|---|---|
| Código Python (`app/`, `run.py`) | **Nada** | Gunicorn corre con `--reload` y el código se monta desde Windows |
| Plantillas de PDF (`templates/`) | **Nada** | También van en el montaje |
| `requirements.txt` o `constraints.txt` | `docker compose up -d --build` | Las dependencias viven en la imagen |
| `Dockerfile` o `docker/*` | `docker compose up -d --build` | Igual |
| `docker-compose.yml` | `docker compose up -d` | Compose recrea lo que haga falta |
| `.env` | `docker compose up -d` | Las variables se leen al crear el contenedor |
| Un modelo (`app/models/`) | Generar migración, ver abajo | El esquema no se actualiza solo |

> `--reload` vigila los `.py`. Si tocas algo que solo se lee al arrancar
> (variables de entorno, el propio `run.py`) y no ves el cambio, reinicia:
> `docker compose restart api`.

---

## Cambios en la base de datos

Cuando modificas un modelo, hay que crear la migración y aplicarla:

```bash
docker compose exec api flask db migrate -m "descripcion del cambio"
docker compose exec api flask db upgrade
```

El archivo nuevo aparece en `migrations/versions/` en Windows (el directorio
está montado), así que se edita y se versiona como cualquier otro.

En **desarrollo** el contenedor además aplica las migraciones pendientes al
arrancar, así que muchas veces basta con `docker compose restart api`. En
**producción** no: allí son un paso explícito del despliegue
(`docker compose run --rm api flask db upgrade`), para que una migración
fallida no deje el servicio caído en bucle de reinicio.

**Revisa siempre el archivo generado antes de aplicarlo.** El autogenerado de
Alembic no detecta renombres (los ve como borrar + crear, y eso pierde datos) y
a veces propone cambios que no pediste.

**Si la migración toca una tabla grande** (crear un índice, rellenar una
columna nueva), piensa en cuánto va a tardar. Las migraciones corren sin
`statement_timeout`, así que no se abortan solas — pero un `ALTER TABLE`
mantiene la tabla bloqueada mientras dura, y eso sí se nota en producción. Para
índices sobre tablas con volumen, `CREATE INDEX CONCURRENTLY` dentro de un
`op.get_context().autocommit_block()`.

### Ruido conocido en `flask db check`

Hoy `flask db check` reporta una diferencia que **no hay que arreglar**:

```
remove_index ix_impcambios_producto_id
add_index    ix_importaciones_catalogo_cambios_producto_id
```

Son los índices de `importaciones_catalogo_cambios`: la migración los creó con
nombres cortos y el modelo genera los nombres largos por defecto. Es solo
nomenclatura, el índice existe y funciona. Si corres `flask db migrate`,
**bórralo del archivo generado** o acabarás recreando índices sin motivo.

### Volver a traer datos frescos desde el Postgres de Windows

```bash
"/c/Program Files/PostgreSQL/18/bin/pg_dump" -h localhost -U daniel \
    -d MASTER -F c --no-owner --no-acl -f /tmp/master.dump

docker compose exec -T db pg_restore -U daniel -d MASTER \
    --clean --if-exists --no-owner --no-acl < /tmp/master.dump
```

### Mirar la base

```bash
docker compose exec db psql -U daniel -d MASTER
```

O con pgAdmin/DBeaver: `localhost:5433`, base `MASTER`, usuario `daniel`
(el 5433 es del contenedor; el 5432 sigue siendo el Postgres de Windows).

---

## Tests

```bash
docker compose exec api python -m pytest tests/ -q -p no:warnings
```

Unos 6 minutos, 1158 tests. Para uno solo mientras trabajas:

```bash
docker compose exec api python -m pytest tests/test_api_auth.py -q
docker compose exec api python -m pytest tests/ -q -k "inventario"
```

> `python -m pytest`, no `pytest` a secas: el ejecutable no mete el directorio
> actual en `sys.path` y `tests/conftest.py` no encuentra `app`.

Los tests usan SQLite en memoria y no tocan la base del contenedor.

---

## Ver qué está pasando

```bash
docker compose logs -f api             # seguir los logs de la API
docker compose logs --tail=50 api      # solo lo último
docker compose logs db                 # o redis, clamav
docker compose exec api sh             # entrar al contenedor
```

En los logs de la API salen las peticiones y, gracias al middleware de
observabilidad, un `[PERF]` por cada request que pase de 500 ms o devuelva
error.

---

## Parar

```bash
docker compose stop      # parar sin borrar nada (lo normal al terminar el día)
docker compose down      # además elimina los contenedores; los datos quedan
docker compose down -v   # BORRA la base, las firmas de ClamAV y los archivos
```

`down -v` deja el entorno como recién instalado. Solo si quieres empezar de
cero — habría que volver a restaurar el volcado de `MASTER`.

---

## Cuando algo falla

**El contenedor `api` no arranca.** Mira el final de `docker compose logs api`.
El entrypoint avisa en claro de los fallos típicos: falta `SECRET_KEY`, la base
no responde, una migración reventó.

**`curl: (52) Empty reply from server`.** Todavía está arrancando (migraciones).
Espera unos segundos y reintenta.

**Subir un PDF da 503.** ClamAV aún está bajando firmas (varios minutos la
primera vez). `docker compose logs clamav` lo confirma. En local el compose
pone `CLAMAV_FAIL_CLOSED=false`, así que no debería bloquear; si bloquea, es
que se levantó con otra configuración.

**"port is already allocated".** Otra cosa usa el 5000, el 5433 o el 6379.
`docker compose down` y vuelve a levantar, o busca el proceso culpable.

**Cambié código y no se refleja.** `docker compose restart api`. Si sigue
igual, es que el cambio afecta a la imagen: `docker compose up -d --build`.

**La base quedó en un estado raro.** `docker compose down -v`, levantar de
nuevo y restaurar el volcado de `MASTER`.

---

## Antes de dar por bueno un cambio

1. La suite en verde: `docker compose exec api python -m pytest tests/ -q -p no:warnings`
2. Probado contra el SPA de verdad (`npm run dev` en el otro repo), no solo con `curl`
3. Si toca WebSockets: que el SPA reciba el evento — emit **después** del commit
   en el backend, y el listener/`invalidateOn` correspondiente en el front
4. Si toca subida de archivos: probado con el `.env` real, que escribe en los
   buckets de R2 de **producción**
5. Si añadiste un modelo o un campo: la migración generada, revisada y aplicada
6. `npm run build` en el repo del front si tocaste el SPA

---

## Recordatorios del entorno

- El `.env` que usa Docker es el **real**: `FLASK_ENV=production` y llaves de R2
  de producción. Lo que subas probando acaba en los buckets buenos.
- El PostgreSQL de Windows sigue instalado con sus datos, en el 5432. El
  contenedor usa una **copia** en el 5433. Tocar uno no afecta al otro.
- El SPA vive en `C:\Users\ppedo\OneDrive\Documentos\plantilla-frontend`. Casi
  toda feature toca los dos repos.
- Los contenedores van en **hora de México**, igual que Windows. No es un
  detalle: la app guarda fechas con `datetime.now()` sin zona en 32 sitios, así
  que la hora del contenedor acaba dentro de la base. Si alguna vez ves fechas
  6 horas adelantadas, es que algo arrancó sin la variable `TZ`.
