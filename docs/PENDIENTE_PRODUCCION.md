# Pendiente: pasar producción a Docker

**Estado al 2026-08-02:** el entorno local ya corre en Docker y está verificado.
El VPS **no se ha tocado**: sigue con `nominas.service` (systemd), venv y los
PostgreSQL / Redis / ClamAV del host.

Todo lo necesario está preparado y revisado. Este documento existe para que, al
retomarlo dentro de semanas, no haya que reconstruir el contexto ni redescubrir
los problemas que ya se encontraron.

El procedimiento paso a paso está en [DOCKER.md](DOCKER.md). Aquí van el orden,
lo que bloquea y el porqué de cada cosa.

---

## 0. Bloqueante: en qué hora están los datos de producción

**Hay que resolverlo antes de desplegar.** Es lo único que no se pudo verificar
desde la máquina de desarrollo.

La aplicación guarda fechas con `datetime.now()` **sin zona** en 32 sitios, así
que la hora del proceso acaba dentro de la base. Los compose fijan
`TZ=America/Mexico_City`. Si el VPS venía corriendo en UTC, poner esa zona haría
que las filas nuevas queden 6 horas por detrás de las viejas — el mismo lío que
ya pasó en local, al revés.

```bash
date
sudo -u postgres psql -d <base> -c "SELECT max(created_at) FROM audit_log"
```

- Si esa fecha coincide con la **hora de México** → deja el `TZ` como está.
- Si coincide con **UTC** → quita la línea `TZ` de `docker-compose.prod.yml`
  (servicios `api` y `db`) y el `-c timezone=…` del comando de Postgres. O
  convierte los datos antes, pero entonces hay que hacerlo en todas las
  columnas `timestamp without time zone`, no solo en `audit_log`.

Para localizar filas descuadradas después, el truco que funcionó en local: las
escritas en la zona equivocada quedan **con fecha futura**.

```sql
SELECT count(*) FROM audit_log WHERE created_at > now();
```

Ojo: `refresh_tokens.expires_at` sale siempre en el futuro y **es correcto** —
es una fecha de expiración, y además es `TIMESTAMP WITH TIME ZONE`, inmune al
problema.

---

## 1. Desplegar

Los 8 pasos están en [DOCKER.md → Producción](DOCKER.md#producción). Resumen del
orden, que importa:

1. Respaldo con `pg_dump` y comprobar RAM (`free -h`) y versión de PostgreSQL
2. Instalar Docker
3. **Permisos** de `uploads/` y `data/` (ver abajo)
4. Añadir `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` al `.env`
5. Levantar `db`, `redis` **y `clamav`**, y restaurar el volcado
6. Parar systemd, construir, **migrar como paso aparte**, levantar
7. Verificar (4 comprobaciones, no solo `/health`)
8. Limpieza, solo tras días estable

El camino de vuelta es parar los contenedores y `systemctl enable --now nominas`.
Por eso el paso 8 se deja para el final: mientras el Postgres del host conserve
sus datos, hay red de seguridad.

---

## 2. Los cuatro fallos silenciosos que ya se encontraron

Están todos resueltos en los archivos, pero **si alguien reescribe el compose,
hay que volver a tenerlos en cuenta**. Los cuatro comparten una característica:
la aplicación arranca sin quejarse y `/health` responde en verde.

### `SOCKETIO_ASYNC_MODE=gevent`

Vivía en el unit de systemd (`Environment=`), **no en el `.env`**. Al jubilar el
unit se perdería. Sin ella, Flask-SocketIO cae a `threading` y escribe frames
con simple-websocket mientras el worker `geventwebsocket` maneja el mismo socket:
el WebSocket conecta y se cierra con `1002 Invalid attempt to fragment control
frame`. Está en `docker-compose.prod.yml`.

Es un problema **distinto** al de QUIC en el túnel de Cloudflare (arreglado con
`protocol: http2`). Aquel se degradaba tras horas funcionando; este falla desde
el primer handshake.

### Los archivos subidos (matizado: todo está ya en R2)

Comprobado el 2026-08-02: **las 18 claves referenciadas en la base existen en
R2** (4 fotos de perfil, 6 documentos, 8 medios de herramientas). O sea que las
lecturas no dependen del disco — `leer()` va solo a R2 y nunca lo toca.

Aun así, `/app/uploads` se monta al **directorio real** `/opt/nominas/uploads` y
no a un volumen nombrado, porque el disco sigue siendo la **red de seguridad de
escritura**: `guardar()` (app/utils/archivos.py:187) intenta R2 y, si falla,
escribe en disco a propósito — "un incidente de red no debe costarle al usuario
su documento". Con un volumen nombrado eso seguiría funcionando, pero los
archivos caídos ahí quedarían fuera de la vista del backfill y de los respaldos
del servidor.

### Los permisos de esos directorios

El contenedor corre como **uid 1000** y los directorios son de `sistemanominas`.
Si los uid no coinciden, la app no puede escribir ahí — y entonces un fallo
puntual de R2 deja de degradarse con elegancia y se convierte en un error al
subir. No es urgente estando R2 sano, pero es justo la protección que quieres
que funcione el día que R2 no lo esté.

```bash
stat -c '%u %U' /opt/nominas/uploads /opt/nominas/data
id -u sistemanominas
```

Si no es 1000, dar acceso por grupo sin quitarle la propiedad (así el camino de
vuelta a systemd sigue intacto):

```bash
sudo chgrp -R 1000 /opt/nominas/uploads /opt/nominas/data
sudo chmod -R g+rwX /opt/nominas/uploads /opt/nominas/data
```

### ClamAV en frío

La primera vez tarda ~10 minutos bajando firmas, y producción tiene
`CLAMAV_FAIL_CLOSED=true`: durante ese rato **toda subida de PDF responde 503**.
Por eso se arranca en el paso 5, mientras se restaura la base, y se confirma
`healthy` antes de cambiar el servicio.

---

## 3. Después del despliegue

Que `/health` responda no prueba nada: los fallos de arriba dan 200. Las cuatro
comprobaciones están en [DOCKER.md → Verificar](DOCKER.md#7-verificar):

- **hora** — `exec api date` debe decir CST
- **IP real del cliente** — falla un login a propósito y mira el `audit_log`; si
  sale una `172.x.x.x` en vez de tu IP pública, el rate limiting está agrupando
  a todos los usuarios bajo la misma clave y uno solo puede bloquear al resto
- **archivos antiguos** — abre una foto de perfil y descarga un documento viejo
- **WebSocket** — el `socket.io` en estado 101 en la pestaña Network

---

## 4. El CI, después

`.github/workflows/ci-deploy.yml` **sigue sin tocar**, a propósito: cambiar el
despliegue en caliente merece su propio momento, cuando el stack lleve días
corriendo a mano.

El job de despliegue ya reescrito está en
[DOCKER.md → CI/CD](DOCKER.md#cicd). Los cambios son tres: el respaldo entra por
`docker compose exec db pg_dump`, las migraciones son un paso propio, y el
diagnóstico de fallo usa `docker compose logs` en vez de `journalctl`.

El usuario del runner autoalojado tiene que estar en el grupo `docker`, y hace
falta reiniciar el servicio del runner para que la pertenencia surta efecto.

---

## 5. Deuda que no es de Docker, pero afecta a los despliegues

**La cadena de migraciones no construye el esquema desde cero.** La primera
revisión crea `reportes_semanales` con FK a `users`, pero ninguna de las 58
migraciones crea `users`: nació de un `db.create_all()` de las primeras
versiones. Consecuencias del día a día:

- `flask db migrate` mezcla cambios reales con ruido, y hay que revisar y podar
  cada archivo generado a mano
- no se puede montar un entorno nuevo (staging, un dev que entre al proyecto,
  una restauración de emergencia) sin partir de un volcado

El arreglo es una migración *baseline* que cree lo que falta de forma
condicional, más quitar el bloque de `create_all` de `create_app()`
(`app/__init__.py`, el que crea `notificaciones` y `trabajador_notas`).

Y ahora **se puede ensayar sin riesgo**, cosa que antes de Docker no: en local,
`docker compose down -v` y levantar sin restaurar el volcado dice si la cadena
aguanta desde cero.

Relacionado: 20 de las 58 migraciones tienen `downgrade()` vacío, incluida la
inicial. O sea que `flask db downgrade` no es un plan de reversión real — el
plan es restaurar el respaldo. Está bien mientras sea una decisión consciente,
pero conviene exigir `downgrade` en las nuevas.
