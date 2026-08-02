# Antivirus (ClamAV) para documentos subidos

Estado: código listo y probado. **Falta instalar `clamd` en el VPS** y poner las
variables. Mientras no lo hagas, el módulo está apagado y todo funciona igual.

## Qué cubre y qué no

Cubre **los PDF de trabajadores**, que es el único archivo que se almacena tal
cual. Todo lo que es imagen (fotos de perfil, INE en JPG, fotos de herramientas)
se re-encodea a WebP con Pillow, y ese re-render ya destruye cualquier payload
embebido — escanearlas sería redundante y costaría latencia en cada subida.

No cubre lo que ya está guardado: solo se escanea en el momento de subir.

## Antes de instalar: la RAM

`clamd` mantiene la base de firmas **en memoria**: entre 1 y 1.5 GB, y creciendo
con cada actualización. Es lo que hace que escanee en milisegundos en vez de
segundos, pero también lo vuelve la pieza más pesada que le vas a meter al VPS
—más que Postgres y Redis juntos en un servidor chico—.

Comprueba primero cuánto te sobra:

```bash
free -h
```

Si en la columna `available` no tienes al menos **2 GB libres**, no lo instales
todavía: `clamd` no arrancará o el OOM killer empezará a matar procesos, y lo
primero que suele caer es gunicorn. En ese caso las opciones son subir el plan
del VPS o correr clamd en otra máquina (ver «clamd remoto» abajo).

## Instalación en el VPS

Ejecuta tú estos comandos por SSH:

```bash
# 1. Instalar el demonio y el actualizador de firmas
sudo apt update
sudo apt install -y clamav clamav-daemon

# 2. Descargar la base de firmas por primera vez.
#    freshclam no puede correr mientras su servicio está activo.
sudo systemctl stop clamav-freshclam
sudo freshclam
sudo systemctl start clamav-freshclam
sudo systemctl enable clamav-freshclam

# 3. Arrancar el demonio (la primera vez tarda ~1 min en cargar firmas)
sudo systemctl enable --now clamav-daemon
sudo systemctl status clamav-daemon --no-pager
```

Que el usuario de la app pueda hablarle al socket. El demonio corre como
`clamav`; gunicorn normalmente no:

```bash
# Ver quién corre gunicorn (ajusta el usuario en el paso siguiente)
systemctl show -p User nominas

# Meter ese usuario al grupo clamav (ejemplo con 'www-data')
sudo usermod -aG clamav www-data
```

Subir el tamaño máximo de escaneo: nuestro tope por documento son 20 MB y el
valor por defecto de `StreamMaxLength` es 25 MB, pero conviene dejarlo holgado.

```bash
sudo nano /etc/clamav/clamd.conf
```

Asegúrate de que estas líneas existan (descoméntalas o agrégalas):

```
LocalSocket /var/run/clamav/clamd.ctl
StreamMaxLength 30M
MaxFileSize 30M
MaxScanSize 100M
```

Reinicia y comprueba que responde:

```bash
sudo systemctl restart clamav-daemon
sudo clamdscan --version
echo "prueba" | sudo clamdscan -
```

## Probar que de verdad detecta

EICAR es un archivo de prueba estándar, inofensivo, que todos los antivirus
reconocen. **Hay que escanearlo por el socket, no por ruta**:

```bash
curl -sO https://secure.eicar.org/eicar.com
clamdscan - < eicar.com     # debe decir: stream: Win.Test.EICAR_HDB-1 FOUND
rm eicar.com
```

El guion (`-`) hace que el contenido viaje **por el socket**, que es exactamente
lo que hace la aplicación (`instream`).

Si en su lugar corres `clamdscan eicar.com`, verás
`File path check failure: Permission denied`: clamd corre como usuario `clamav`
y no puede leer dentro de `/home/tu-usuario/`. Eso es una limitación del
escaneo **por ruta** y **no afecta a la app**, que nunca le pasa rutas a clamd.

## Dos avisos normales que no son problemas

**`WARNING: VERSION command disabled in clamd`** al correr `clamdscan --version`.
Muchas instalaciones deshabilitan ese comando. No pasa nada: la salud se mide
con `PING`, y si `VERSION` no está disponible el panel simplemente muestra
«clamd responde» en vez del número de versión.

**`clamdscan` pide sudo para algunas rutas.** Solo afecta al escaneo manual por
ruta, no a la aplicación.

## Configurar la app

En el `.env` del VPS:

```ini
CLAMAV_SOCKET=/var/run/clamav/clamd.ctl
CLAMAV_TIMEOUT=30
CLAMAV_FAIL_CLOSED=true
```

Instala la dependencia de Python y reinicia:

```bash
cd /ruta/del/proyecto
source venv/bin/activate
pip install -r requirements.txt -c constraints.txt
sudo systemctl restart nominas
```

Prueba de extremo a extremo: sube el `eicar.com` renombrado a `.pdf` como
documento de un trabajador. Debe rechazarse con *«El archivo fue rechazado por
el antivirus»*, y el intento aparecer en **Sistemas → Eventos de seguridad**.

## `CLAMAV_FAIL_CLOSED`: la decisión que hay que tomar

Qué pasa si clamd se cae y alguien intenta subir un PDF:

| Valor | Comportamiento |
|---|---|
| `true` (por defecto) | La subida se rechaza con 503. Nadie sube documentos sin revisar. |
| `false` | La subida pasa y solo queda registrado en el log de la app. |

El default es `true` a propósito: dar por bueno un archivo que nadie revisó es
justo lo que vuelve inútil tener antivirus. Pero significa que **una caída de
clamd bloquea la subida de documentos** (no el resto de la app). Si prefieres
disponibilidad sobre control, ponlo en `false` — pero que sea una decisión
tomada, no un descuido.

## Cómo saber si está vivo

`Sistemas → Estado del servidor` tiene un semáforo **Antivirus** junto a Redis y
la base de datos:

- **gris** — no configurado. Los PDF se guardan sin escanear (estado normal
  antes de instalarlo; no es una alarma).
- **verde** — clamd responde; el detalle muestra versión del motor y firmas.
- **rojo** — configurado pero sin respuesta. Además aparece en «Defensas
  degradadas» diciendo si las subidas se están rechazando (fail-closed) o
  aceptando sin escanear.

Si el título dice «Antivirus (bloqueante)» es que `CLAMAV_FAIL_CLOSED=true`, o
sea que una caída detiene la subida de documentos.

## Probar en local sin instalar ClamAV

En Windows no hay clamd y no hace falta: sin `CLAMAV_SOCKET` ni `CLAMAV_HOST` el
módulo está apagado y las subidas funcionan como siempre. Tres formas de
ejercitar el camino del antivirus según lo que quieras probar:

**1. La lógica, sin nada instalado** — es lo que hace la suite. Se sustituye el
cliente por uno falso (`tests/test_antivirus.py`); no requiere ClamAV ni red:

```bash
pytest tests/test_antivirus.py -v
```

**2. Simular que está caído**, para ver el 503 en la app real. En tu `.env`
local pon una ruta que no existe:

```ini
CLAMAV_SOCKET=/tmp/no-existe.ctl
CLAMAV_FAIL_CLOSED=true
```

Reinicia y sube un PDF: debe responder *«El antivirus no está disponible»*. Con
`CLAMAV_FAIL_CLOSED=false` la misma subida pasa. Déjalo **vacío** al terminar.

**3. Un clamd de verdad con Docker**, si quieres ver una detección real:

```bash
docker run -d --name clamav -p 3310:3310 clamav/clamav:stable
docker logs -f clamav        # espera a "socket found, clamd started"
```

En el `.env` local (deja `CLAMAV_SOCKET` vacío, en Windows va por TCP):

```ini
CLAMAV_SOCKET=
CLAMAV_HOST=127.0.0.1
CLAMAV_PORT=3310
```

Descarga el EICAR, renómbralo a `.pdf` y súbelo: debe rechazarse. Para quitarlo:
`docker rm -f clamav` y vaciar las variables.

## Mantenimiento

`freshclam` actualiza firmas solo, varias veces al día. Para verificar:

```bash
systemctl status clamav-freshclam --no-pager
sudo tail -n 30 /var/log/clamav/freshclam.log
```

Si la app empieza a devolver 503 al subir documentos, lo primero:

```bash
sudo systemctl status clamav-daemon --no-pager
sudo journalctl -u clamav-daemon -n 50 --no-pager
free -h
```

Casi siempre es que el demonio se murió por falta de RAM.

## clamd remoto (si el VPS no da)

Si no quieres 1.5 GB de firmas en el mismo servidor, `clamd` puede correr en
otra máquina y hablarse por TCP. En el `.env` de la app, deja `CLAMAV_SOCKET`
vacío y usa:

```ini
CLAMAV_SOCKET=
CLAMAV_HOST=10.0.0.5
CLAMAV_PORT=3310
```

En el `clamd.conf` de esa otra máquina hay que habilitar `TCPSocket 3310` y
`TCPAddr`. **El tráfico va en claro**, así que solo por red privada o VPN —
nunca exponiendo el 3310 a internet.

## Notas de implementación

- `app/utils/antivirus.py` — gate (`habilitado()`), `escanear()`, `ping()`.
  El import de `clamd` es perezoso: en local/Windows nunca se carga.
- Se usa el demonio y no el binario `clamscan`: `clamscan` recarga ~1 GB de
  firmas en cada invocación (segundos por archivo).
- `escanear()` **nunca** devuelve "limpio" por un fallo de conexión: lanza
  `AntivirusNoDisponible` y el caller decide según `CLAMAV_FAIL_CLOSED`.
  Confundir «no pude revisar» con «está limpio» es el error clásico.
- Los tests (`tests/test_antivirus.py`) simulan el demonio; no hace falta
  tener ClamAV instalado para correr la suite.
