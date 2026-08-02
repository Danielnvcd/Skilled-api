# Docker — entorno local y despliegue

Cómo correr la API en contenedores, en local y en el VPS.

> Para el trabajo del día a día (arrancar, qué hacer al cambiar cada cosa,
> tests, problemas comunes) hay una guía aparte: [DIA_A_DIA.md](DIA_A_DIA.md).
>
> **El VPS todavía NO está en Docker.** Lo que falta, en qué orden y qué
> bloquea: [PENDIENTE_PRODUCCION.md](PENDIENTE_PRODUCCION.md).

---

## Qué se contiene y qué no

| Pieza | Antes | Ahora |
|---|---|---|
| API (Flask + Gunicorn/gevent) | venv + `nominas.service` | contenedor `api` |
| PostgreSQL | `postgresql.service` en el host | contenedor `db` |
| Redis | `redis.service` en el host | contenedor `redis` |
| ClamAV | `clamav-daemon` por socket unix | contenedor `clamav` (TCP 3310) |
| nginx | host | **sigue en el host** |
| cloudflared | host | **sigue en el host** |
| SPA React | Vercel (repo `plantilla-frontend`) | igual, no se toca |

nginx y el túnel se quedan fuera a propósito: la API se publica en
`127.0.0.1:8000`, que es exactamente donde nginx la busca hoy. Así el cambio
no toca ni `nginx.config` ni la configuración de Cloudflare, y si algo sale
mal se vuelve atrás parando los contenedores y arrancando el systemd de nuevo.

---

## Archivos

| Archivo | Para qué |
|---|---|
| `Dockerfile` | Imagen de la API. Dos etapas: dependencias y runtime. |
| `docker-compose.yml` | Stack de desarrollo. |
| `docker-compose.prod.yml` | Stack del VPS. |
| `docker/entrypoint.sh` | Espera la base, migra y arranca Gunicorn. |
| `docker/espera_db.py` | Sondeo de PostgreSQL con SQLAlchemy. |
| `docker/bootstrap_esquema.py` | Crea el esquema en una base vacía (ver más abajo). |
| `docker/revision_actual.py` | Lee la revisión de Alembic sin arrancar la app. |
| `.dockerignore` | Deja fuera el `.env`, `venv/`, `uploads/`, `.git/`… |

---

## Local

```bash
docker compose up --build
```

La API queda en `http://localhost:5000` — el mismo puerto que usaba
`python run.py`, para que el SPA no necesite cambios.

### Configuración: el `.env` de siempre

El stack usa el **`.env` de la raíz**, el mismo que leía `python run.py`. Las
únicas variables que el compose sobrescribe son las que dentro de un contenedor
no pueden apuntar a `localhost`: `DATABASE_URL`, `REDIS_URL` y `CLAMAV_HOST`
(más `SOCKETIO_ASYNC_MODE=gevent`, que es a lo que se venía). Todo lo demás
—R2, correo, CORS, límites— se comporta exactamente igual que antes.

> ⚠ Ese `.env` corre con `FLASK_ENV=production` y las llaves de R2 **reales**.
> Lo que subas probando aquí acaba en los buckets de producción. Si algún día
> quieres pruebas aisladas, apunta las variables `R2_*` a un bucket aparte.

El `.env` necesita tres variables que se añadieron para esto:

```bash
POSTGRES_USER=daniel        # los mismos que ya estaban en DATABASE_URL
POSTGRES_PASSWORD=…
POSTGRES_DB=MASTER
```

El compose las usa para dos cosas: crear el Postgres del contenedor con esas
credenciales y armar el `DATABASE_URL` interno apuntando a `db` en vez de a
`localhost`.

### La base de datos

La base vive **dentro de Docker**: es una copia de la `MASTER` que corría en
Windows, restaurada en el volumen `datos_postgres` con el mismo usuario y
contraseña. El PostgreSQL de Windows se quedó intacto y con sus datos, por si
hace falta volver.

Como el contenedor publica el puerto en `127.0.0.1:5433` (no 5432, para no
chocar con el de Windows), puedes conectar pgAdmin o DBeaver ahí.

Para volver a traer datos frescos desde Windows más adelante:

```bash
# 1. Volcar desde el Postgres de Windows
"/c/Program Files/PostgreSQL/18/bin/pg_dump" -h localhost -U daniel \
    -d MASTER -F c --no-owner --no-acl -f /tmp/master.dump

# 2. Restaurar en el contenedor (--clean borra lo que haya)
docker compose exec -T db pg_restore -U daniel -d MASTER \
    --clean --if-exists --no-owner --no-acl < /tmp/master.dump
```

### Lo que esto desbloquea

**gevent.** `SOCKETIO_ASYNC_MODE=gevent` crashea con psycopg en Windows, así
que hasta ahora los WebSockets solo se podían probar contra el VPS. El
contenedor corre Linux con el mismo `GeventWebSocketWorker` de producción, así
que el camino real de Socket.IO ya se prueba en local.

**ClamAV.** Antes no había demonio en Windows y los 503 al subir PDFs solo
aparecían en producción. Ahora hay un clamd de verdad. La primera vez tarda
varios minutos bajando firmas (~350 MB, quedan en un volumen); mientras tanto
el compose fuerza `CLAMAV_FAIL_CLOSED=false` para que las subidas sigan
funcionando. En producción se queda en `true`.

### Comandos del día a día

```bash
docker compose up -d               # levantar en segundo plano
docker compose logs -f api         # ver logs de la API
docker compose exec api sh         # entrar al contenedor
docker compose exec api python -m pytest tests/ -q -p no:warnings   # la suite
docker compose exec db psql -U nominas -d nominas         # psql
docker compose down                # parar (los volúmenes se conservan)
docker compose down -v             # parar Y BORRAR datos, firmas y todo
```

> Para los tests, `python -m pytest` y no `pytest` a secas: el ejecutable
> `pytest` no mete el directorio actual en `sys.path`, así que `from app import
> create_app` de `tests/conftest.py` no resuelve dentro del contenedor.

El código se monta desde el host y Gunicorn corre con `--reload`: editas en
Windows y el cambio aplica solo. Solo hace falta reconstruir
(`docker compose up --build`) cuando cambian `requirements.txt` o el
`Dockerfile`.

---

## Producción

### Antes de empezar

- **Pon `DESPLIEGUE_ACTIVO` en `false`** (variables del repositorio en GitHub),
  antes que nada. El job de despliegue del CI sigue escrito para systemd: si se
  dispara con el VPS ya en Docker, migra la base equivocada y termina en verde
  sin haber desplegado nada. El desglose está en
  [PENDIENTE_PRODUCCION.md → 0.1](PENDIENTE_PRODUCCION.md#01-apagar-el-despliegue-automático).
- Producción tiene datos, aunque todavía sean de demo. **Respalda primero.**
- ClamAV pide ~1.5 GB de RAM para las firmas. Sumado a Postgres y a 4 workers
  de Gunicorn, revisa que el VPS aguante (`free -h`). Si va justo, arranca sin
  el servicio `clamav` y añádelo después.
- La versión de PostgreSQL del contenedor (**18**, en ambos compose) debe ser
  igual o mayor que la del host. Confírmalo con `psql --version` antes de
  restaurar: un dump de 18 no entra en un 16.

### 1. Respaldo y verificación

```bash
cd /opt/nominas
set -a; . ./.env; set +a
mkdir -p ~/respaldos
pg_dump "$DATABASE_URL" -F c -f ~/respaldos/pre_docker_$(date +%F_%H%M%S).dump
ls -lh ~/respaldos/ | tail -3
psql --version          # que el contenedor no sea de una versión menor
free -h                 # memoria disponible para clamd
```

### 2. Docker en el servidor

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER    # cerrar sesión y volver a entrar
docker compose version

# Que lo instalado no arrastre los escapes de contenedor conocidos
# (ver «Mantenimiento de seguridad» más abajo).
docker version --format '{{.Server.Version}}'    # ≥ 29.5.1
runc --version                                   # ≥ 1.2.8 (o 1.3.3)
```

Añadir a alguien al grupo `docker` **equivale a darle root en el host**. Es
inevitable para el despliegue, pero conviene saberlo antes de teclearlo.

### 3. Permisos de los archivos subidos

El contenedor corre como **uid 1000**, y `/opt/nominas/uploads` pertenece hoy a
`sistemanominas`. Si los uid no coinciden, la aplicación arranca bien pero no
puede escribir: subir una foto falla con un error de permisos que no dice nada
útil.

```bash
stat -c '%u %U' /opt/nominas/uploads /opt/nominas/data
id -u sistemanominas
```

Si el uid **ya es 1000**, no hay nada que hacer. Si no lo es, la opción menos
invasiva es dar acceso por grupo sin quitarle la propiedad a `sistemanominas`
(así el camino de vuelta a systemd sigue intacto):

```bash
sudo chgrp -R 1000 /opt/nominas/uploads /opt/nominas/data
sudo chmod -R g+rwX /opt/nominas/uploads /opt/nominas/data
```

### 4. Traer el código y preparar el `.env`

```bash
cd /opt/nominas
git fetch origin && git checkout main && git pull
```

Añade al final de `/opt/nominas/.env` las credenciales que usará el Postgres
del contenedor (el compose las lee de ahí):

```bash
POSTGRES_USER=nominas
POSTGRES_PASSWORD=una_contrasena_larga_y_aleatoria
POSTGRES_DB=nominas
```

`DATABASE_URL` y `REDIS_URL` del `.env` quedan ignoradas para el contenedor:
`docker-compose.prod.yml` las sobrescribe con los hostnames de la red interna.
Déjalas como están para poder volver a systemd si hace falta.

### 5. Levantar la base y restaurar los datos

Se arranca **también ClamAV**, aunque todavía no haga falta: la primera vez
tarda unos 10 minutos bajando firmas (~350 MB), y en producción
`CLAMAV_FAIL_CLOSED=true` significa que mientras tanto **toda subida de PDF
responde 503**. Poniéndolo a descargar ahora, para cuando llegue el cambio de
servicio ya estará listo y nadie nota nada.

```bash
cd /opt/nominas
docker compose -f docker-compose.prod.yml up -d db redis clamav
docker compose -f docker-compose.prod.yml ps      # esperar a que db esté healthy

# Restaurar el respaldo del paso 1 dentro del contenedor.
cat ~/respaldos/pre_docker_*.dump | \
  docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U nominas -d nominas --clean --if-exists --no-owner

# Comprobar que llegaron las tablas.
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U nominas -d nominas -c '\dt' | head -20
```

### 6. Apagar el servicio viejo y levantar el nuevo

El unit de systemd y el contenedor pelean por el puerto 8000, así que este
paso es el único con corte de servicio (menos de un minuto).

Antes de empezar, confirma que ClamAV ya terminó de bajar firmas — si no, las
subidas de PDF darán 503 nada más cambiar:

```bash
docker compose -f docker-compose.prod.yml ps clamav    # debe decir "healthy"
```

```bash
sudo systemctl stop nominas
sudo systemctl disable nominas
# `disable` solo quita el arranque automático: un `systemctl restart` explícito
# —el que hace el CI viejo— seguiría levantándolo a pelear por el puerto 8000.
sudo systemctl mask nominas

cd /opt/nominas
docker compose -f docker-compose.prod.yml build

# Migraciones: paso EXPLÍCITO, antes de levantar el servidor.
docker compose -f docker-compose.prod.yml run --rm api flask db upgrade

docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f api    # Ctrl-C al ver que arranca
```

### 7. Verificar

Que la aplicación responda no basta: los fallos de este cambio son silenciosos
—devuelve 200 y los datos están mal—. Hay que comprobar cuatro cosas concretas.

```bash
# a) Responde, de cerca y a través del túnel
curl -fsS http://127.0.0.1:8000/health
curl -fsS https://app.skilledmx.cloud/health
docker compose -f docker-compose.prod.yml ps      # todo "healthy"
```

**b) La hora.** Si sale UTC, las fechas nuevas entran desplazadas:

```bash
docker compose -f docker-compose.prod.yml exec api date        # debe decir CST
docker compose -f docker-compose.prod.yml exec db  psql -U nominas -d nominas \
    -tAc "SHOW timezone"                                       # America/Mexico_City
```

**c) La IP real del cliente.** Es lo que usa el rate limiting como clave: si
todas las peticiones aparecen con la misma IP, un usuario que tope el límite
bloquea a los demás. Entra al SPA, falla un inicio de sesión a propósito y
mira qué IP quedó registrada:

```bash
docker compose -f docker-compose.prod.yml exec db psql -U nominas -d nominas \
    -tAc "SELECT left(action,90) FROM audit_log ORDER BY id DESC LIMIT 1"
```

Debe salir **tu IP pública**. Si sale una `172.x.x.x`, la cadena de proxies no
se está respetando: revisa `FORWARDED_ALLOW_IPS` y el `x_for=2` de ProxyFix.

**d) Los archivos de siempre.** Abre en el navegador la foto de perfil de algún
trabajador y descarga un documento antiguo. Si dan 404, el montaje de
`/opt/nominas/uploads` no está llegando:

```bash
docker compose -f docker-compose.prod.yml exec api ls /app/uploads
```

Y por último, que el SPA conecte por WebSocket (pestaña Network, el `socket.io`
en estado 101) y que subir un PDF nuevo no dé 503.

### 8. Limpieza (solo cuando lleve días estable)

```bash
sudo systemctl stop postgresql redis-server clamav-daemon
sudo systemctl disable postgresql redis-server clamav-daemon
```

No borres los datos del Postgres del host hasta estar seguro: son la red de
seguridad si hay que volver atrás.

### Volver atrás

```bash
cd /opt/nominas
docker compose -f docker-compose.prod.yml down
sudo systemctl unmask nominas          # por el `mask` del paso 6
sudo systemctl enable --now postgresql redis-server nominas
curl -fsS http://127.0.0.1:8000/health
```

Los datos escritos mientras corrían los contenedores se quedan en el volumen
de Docker; para llevarlos al Postgres del host hay que volcar y restaurar en
sentido contrario.

---

## Respaldos con la base en contenedor

El paso de respaldo del CI usa `pg_dump` del host contra `DATABASE_URL`. Con
la base dentro de Docker ya no la alcanza: hay que entrar por el contenedor.

```bash
cd /opt/nominas
ARCHIVO=~/respaldos/nominas_$(date +%F_%H%M%S).dump
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U nominas -F c nominas > "$ARCHIVO"
ls -1t ~/respaldos/nominas_*.dump | tail -n +11 | xargs -r rm --
```

---

## CI/CD

`.github/workflows/ci-deploy.yml` sigue sirviendo tal cual para el job de
`tests`. El de `deploy` cambia: ya no hay `pip install` ni `systemctl`.

Sustituto de los pasos «Respaldar», «Instalar dependencias», «Aplicar
migraciones» y «Reiniciar el servicio»:

```yaml
      - name: Respaldar la base de datos
        run: |
          set -euo pipefail
          cd "$APP_DIR"
          mkdir -p ~/respaldos
          ARCHIVO=~/respaldos/nominas_$(date +%F_%H%M%S).dump
          docker compose -f docker-compose.prod.yml exec -T db \
            pg_dump -U nominas -F c nominas > "$ARCHIVO"
          echo "Respaldo: $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"
          ls -1t ~/respaldos/nominas_*.dump | tail -n +11 | xargs -r rm --

      - name: Construir la imagen
        run: |
          set -euo pipefail
          cd "$APP_DIR"
          docker compose -f docker-compose.prod.yml build

      - name: Escanear la imagen
        # Después de construir y ANTES de migrar o levantar: si la imagen trae
        # un CVE crítico, el despliegue se detiene con el servicio viejo aún en
        # pie. `--exit-code 1` es lo que convierte el informe en una puerta;
        # sin esa opción el paso siempre pasa y el escaneo es decorativo.
        #
        # Solo CRITICAL a propósito: las imágenes base acumulan HIGH de
        # paquetes del SO que no siempre tienen parche, y una puerta que salta
        # cada semana se acaba ignorando o quitando. Si algún día se quiere
        # subir a HIGH, hazlo junto con `--ignore-unfixed`.
        run: |
          set -euo pipefail
          docker run --rm \
            -v /var/run/docker.sock:/var/run/docker.sock \
            -v "$HOME/.cache/trivy:/root/.cache/trivy" \
            aquasec/trivy:latest image \
              --severity CRITICAL \
              --ignore-unfixed \
              --exit-code 1 \
              --scanners vuln \
              nominas-api:latest

      - name: Aplicar migraciones
        # Paso propio, ANTES de tocar el servidor. Si falla, el contenedor que
        # está sirviendo sigue en pie y el despliegue se detiene aquí.
        run: |
          set -euo pipefail
          cd "$APP_DIR"
          docker compose -f docker-compose.prod.yml run --rm api flask db upgrade

      - name: Levantar
        run: |
          set -euo pipefail
          cd "$APP_DIR"
          docker compose -f docker-compose.prod.yml up -d
          docker compose -f docker-compose.prod.yml ps
```

El paso «Comprobar que responde» funciona igual (sigue siendo
`curl http://127.0.0.1:8000/health`), solo hay que cambiar el diagnóstico del
final por `docker compose -f docker-compose.prod.yml logs --tail=60 api`.

**Este cambio no está aplicado al workflow todavía** — tocar el CI en caliente
merece su propio momento, cuando el stack ya lleve días corriendo a mano.

⚠ Por eso `DESPLIEGUE_ACTIVO` tiene que estar en `false` mientras tanto: el job
tal como está hoy da un despliegue **en verde** contra el VPS dockerizado, con
la migración aplicada al Postgres del host y el contenedor todavía sirviendo la
imagen vieja. Se vuelve a `true` al aplicar el job de arriba, no antes.
También hace falta que el usuario del runner autoalojado esté en el grupo
`docker` y reiniciar el servicio del runner para que la pertenencia surta
efecto.

---

## Lo que salió al levantar un entorno desde cero

Dockerizar obligó a arrancar la app contra una base vacía por primera vez, y
ahí aparecieron dos problemas que **ya existían** y que no se ven en un entorno
que lleva tiempo funcionando.

### La cadena de migraciones no construye el esquema desde cero

La primera revisión (`453bd924fe1d_init_workers`) crea `reportes_semanales` con
una FK a `users`, pero **ninguna de las 58 migraciones crea `users`**: esa tabla
nació de un `db.create_all()` de las primeras versiones y nunca tuvo revisión
propia. En producción no se nota porque el esquema ya está y `alembic_version`
va al día; en una base nueva, `flask db upgrade` muere en la primera migración.

Solución de momento: `docker/bootstrap_esquema.py` crea el esquema desde los
modelos y lo marca con `stamp head` **solo si la base no tiene ni una tabla**.
Si encuentra tablas sin `alembic_version`, se detiene y avisa en vez de
adivinar.

Ojo con el alcance: el bootstrap solo actúa cuando el entrypoint tiene las
migraciones activadas, o sea **en desarrollo**. En producción
(`APLICAR_MIGRACIONES=false`) el esquema viene del volcado restaurado, que es
el procedimiento documentado arriba. Levantar el stack de producción contra una
base realmente vacía no funcionaría — `flask db upgrade` moriría en la primera
migración, igual que antes.

**Arreglo de fondo pendiente:** una migración que cree `users` y las demás
tablas sin revisión, para que la cadena sea autosuficiente, el bootstrap sobre
y montar un entorno nuevo (staging, un dev que entre al proyecto, una
restauración de emergencia) sea posible sin depender de un volcado. Mientras
siga así, el esquema real y las migraciones son dos fuentes de verdad
distintas, y por eso `flask db migrate` genera ruido que hay que borrar a mano
en cada migración nueva.

### `create_app()` moría contra una base sin migrar

`app/__init__.py` crea al arrancar tres tablas auxiliares que no tienen
migración propia (`notificaciones`, `trabajador_notas`; `totp_backup_codes` sí
tiene). Las tres llevan FK a `users`, así que contra una base vacía lanzaban
excepción — y como el CLI de Flask importa la app antes de ejecutar nada, eso
tumbaba al propio `flask db upgrade`. Círculo cerrado: para migrar hacía falta
importar la app, y para importar la app hacía falta estar migrado.

Ahora el bloque avisa en vez de reventar. En los entornos existentes no cambia
nada (las tablas ya están y ni se intenta crearlas).

---

## Mantenimiento de seguridad

Lo que el stack ya trae bien resuelto, para no tocarlo por error:

- **El puerto solo en `127.0.0.1`.** Docker escribe sus reglas en la cadena
  `FORWARD` de iptables, mientras ufw gobierna `INPUT`: las dos nunca se
  cruzan, así que un `-p 8000:8000` queda abierto a internet aunque ufw diga
  lo contrario. Publicar en la loopback es la mitigación recomendada, y es lo
  que hacen los dos compose. `db`, `redis` y `clamav` ni siquiera publican.
- **Gunicorn 23.0.0** (`requirements.txt:35`) ya incluye el arreglo del
  request smuggling TE.CL de CVE-2024-1135 y CVE-2024-6827.
- **Sin `docker cp` en ningún procedimiento.** Los CVE-2026-41567 / -41568 /
  -42306 (ejecución como root del host y redirección de bind mounts) van por
  ahí. El respaldo y la restauración usan `exec -T` con redirección de shell,
  que no toca ese camino.

Lo que sí hay que vigilar en el tiempo:

**Versión de Docker y de runc.** El instalador del paso 2
(`get.docker.com`) trae la última estable, así que un despliegue nuevo sale
parcheado. El hueco está en el después, porque nada actualiza esto solo. La
familia de escapes de contenedor de runc de noviembre de 2025
(CVE-2025-31133, CVE-2025-52565, CVE-2025-52881 — escrituras a procfs vía
symlinks y carreras en `maskedPaths` y `/dev/console`) se corrigió en runc
1.2.8 / 1.3.3 / 1.4.0-rc.3, y hay reportes de explotación activa. Docker
Engine ≥ 29.5.1 cubre además el bypass de autorización CVE-2026-34040 y los
fallos de `docker cp`.

```bash
docker version --format '{{.Server.Version}}'    # ≥ 29.5.1
runc --version                                   # ≥ 1.2.8 (o 1.3.3)
```

Estos escapes exigen que el atacante ya ejecute código dentro de un
contenedor, así que aquí son defensa en profundidad y no la primera línea —
la superficie real sigue siendo la aplicación Flask detrás de nginx. Aun así,
conviene revisarlo con las actualizaciones del sistema.

**El runner del CI está en el grupo `docker`.** Es un requisito para que el
despliegue funcione, pero conviene tenerlo consciente: pertenecer a ese grupo
**equivale a ser root en el host** — con `docker run -v /:/host` se monta el
sistema de archivos entero. No es un fallo de esta configuración (es cómo
funciona el socket de Docker), pero significa que el runner autoalojado hay
que tratarlo con el mismo cuidado que una cuenta con sudo sin contraseña.

**Escaneo de imágenes.** El job de CI de arriba lo incluye. Para mirarlo a
mano en cualquier momento:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:latest image --severity HIGH,CRITICAL --ignore-unfixed \
  nominas-api:latest
```

**Las imágenes base usan tags móviles** (`postgres:18-alpine`,
`python:3.12-slim`, `clamav/clamav:stable`) y no digests. Es deliberado: fijar
el digest daría builds reproducibles pero dejaría de traer parches del sistema
operativo, y para un proyecto de este tamaño recibir los parches pesa más. El
`-c constraints.txt` ya cubre la reproducibilidad de la parte de Python, que es
donde vive el código propio.

---

## Decisiones y cabos sueltos

**`USE_X_ACCEL_REDIRECT` se queda en `false`.** nginx corre en el host y los
archivos ahora viven en un volumen de Docker, así que el `location
/x-accel-uploads/` ya no apunta a nada real. Como los archivos están migrando
a R2 de todos modos, no vale la pena reconectarlo: cuando el backfill termine,
el volumen `archivos_subidos` casi no tendrá tráfico.

**Las migraciones son un paso explícito en producción, no del arranque.** En
desarrollo el entrypoint las aplica al levantar (`APLICAR_MIGRACIONES=true`,
por comodidad: un solo comando y el stack está listo). En el VPS va en `false`
y se corren con `docker compose run --rm api flask db upgrade` antes de
recrear el servicio.

La diferencia importa cuando algo falla: si la migración corriera en el
arranque, un error dejaría el contenedor en bucle de reinicio **con el servicio
caído**. Como paso aparte, el contenedor viejo sigue sirviendo y decides con
calma. También es lo que haría falta si algún día hay más de una réplica: dos
`upgrade` simultáneos compiten por la tabla `alembic_version`.

**Las migraciones corren sin `statement_timeout`.** El engine de la aplicación
abre las conexiones con `statement_timeout=30s` (bien para peticiones HTTP), y
Alembic usa ese mismo engine — así que un `CREATE INDEX` o un backfill sobre
una tabla grande se abortaría a los 30 segundos. `migrations/env.py` lo
neutraliza para las migraciones y **conserva `lock_timeout=5s`**: si una
consulta larga tiene tomada la tabla, es mejor que la migración falle rápido a
que se quede encolada bloqueando a todos los que lleguen detrás.

**El puerto se publica en `127.0.0.1:8000`, nunca en `8000:8000`.** Docker
escribe sus reglas de iptables por encima de ufw, así que publicar a secas
abriría Gunicorn a internet saltándose el firewall. Es el error clásico al
dockerizar un VPS.

**Los contenedores corren en `America/Mexico_City`, no en UTC.** El default de
cualquier imagen es UTC, y eso aquí corrompe datos: hay 32 llamadas a
`datetime.now()` **sin zona** en las rutas (`last_seen`, `audit_log`, nombres de
archivos exportados…), y esa función devuelve la hora local del proceso. Con el
contenedor en UTC, las fechas nuevas entraban 6 horas adelantadas respecto a
todo lo que había escrito la máquina de Windows — en la misma columna, sin
ninguna marca que las distinga.

Va en los dos compose: `TZ` en los servicios `api` y `db`, más
`-c timezone=America/Mexico_City` en el comando de Postgres. Lo segundo hace
falta porque el `timezone` del servidor lo fija `initdb` al crear el clúster y
queda escrito en `postgresql.conf`; la variable `TZ` por sí sola no lo mueve
después. Afecta al default `now()` de tres columnas
(`producto_estante.updated_at`, `tomas_inventario.fecha_inicio`,
`registros_diarios_horas.modificado_en`).

**Antes de desplegar esto al VPS**, comprueba en qué hora están los datos que
producción ya tiene:

```bash
date
sudo -u postgres psql -d <base> -c "SELECT max(created_at) FROM audit_log"
```

Si esa fecha va con la hora de México, deja el `TZ` puesto. Si va con UTC,
quítalo del compose de producción — o convierte los datos primero. Poner la
zona "correcta" sobre datos escritos en otra es el mismo problema al revés.

El arreglo de fondo sería que la aplicación usara siempre fechas con zona
(`datetime.now(timezone.utc)`, como ya hacen 15 sitios) y columnas
`TIMESTAMP WITH TIME ZONE`. Mientras conviva con `datetime.now()` ingenuo, la
zona del proceso es parte de la configuración y hay que cuidarla.

**El endurecimiento de systemd se reconstruyó parcialmente.** `cap_drop: ALL`
y `no-new-privileges` cubren lo esencial; el `ProtectSystem=strict` del unit
equivale a que el contenedor solo escriba en sus volúmenes.

Ese equivalente de `ProtectSystem=strict` es `read_only: true`, y está puesto
en `db` y en `redis` — los dos verificados a mano, con los tmpfs que Postgres
necesita para el socket y los temporales. **`api` se queda fuera a propósito**:
xhtml2pdf y pandas escriben temporales fuera de sus volúmenes al generar PDFs
y exports de Excel, así que el rootfs de solo lectura fallaría en caliente y
solo en los endpoints de reportes, que son los que menos se ejercitan. Se puede
intentar más adelante con `tmpfs: /tmp`, probando export por export.

En `redis`, el `cap_drop: ALL` **depende de `user: redis`**. El entrypoint de
la imagen arranca como root y baja de usuario con setpriv; sin
CAP_SETUID/CAP_SETGID muere en el arranque con `setpriv: setresuid failed`
(exit 127). Arrancando ya como `redis` no hace falta ninguna capability. En
`db` no se aplica `cap_drop` por el mismo motivo, y ahí no se ha tocado.
