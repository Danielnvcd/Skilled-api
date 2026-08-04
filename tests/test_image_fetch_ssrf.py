"""Defensas anti-SSRF de la descarga de imágenes externas.

El caso central es el rebinding de DNS. La defensa anterior comprobaba el host
con `getaddrinfo` y luego le entregaba la URL a httpx, que resolvía el nombre
POR SEGUNDA VEZ al conectar. El DNS del atacante controla esa ventana: responde
una IP pública a la comprobación y una interna a la conexión.

El invariante que lo cierra, y que verifican estos tests: **la petición que sale
lleva siempre una IP literal, nunca un nombre de dominio**. Si no hay nombre que
resolver, no hay segunda resolución que envenenar.
"""
import io

import pytest
from PIL import Image

from app.utils import image_fetch
from app.utils.image_fetch import (
    ImagenDescargaError,
    _destino_fijado,
    _ip_es_publica,
    descargar_imagen_segura,
)


def _png(ancho=8, alto=8) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (ancho, alto), 'red').save(buf, format='PNG')
    return buf.getvalue()


class _RespuestaFalsa:
    def __init__(self, contenido=b'', status=200, headers=None):
        self._contenido = contenido
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = 300 <= status < 400

    def iter_bytes(self):
        yield self._contenido

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ClienteFalso:
    """Sustituye a httpx.Client y anota exactamente qué se pidió."""

    peticiones = []
    respuestas = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, metodo, url, headers=None, extensions=None):
        type(self).peticiones.append({
            'url': url,
            'host': (headers or {}).get('Host'),
            'sni': (extensions or {}).get('sni_hostname'),
        })
        if type(self).respuestas:
            return type(self).respuestas.pop(0)
        return _RespuestaFalsa(_png())


@pytest.fixture
def cliente_falso(monkeypatch):
    _ClienteFalso.peticiones = []
    _ClienteFalso.respuestas = []
    monkeypatch.setattr(image_fetch.httpx, 'Client', _ClienteFalso)
    return _ClienteFalso


def _dns(monkeypatch, *respuestas):
    """Hace que `getaddrinfo` devuelva una lista de IPs distinta en cada llamada."""
    estado = {'n': 0}

    def falso(host, port=None, *a, **kw):
        i = min(estado['n'], len(respuestas) - 1)
        estado['n'] += 1
        return [(2, 1, 6, '', (ip, 443)) for ip in respuestas[i]]

    monkeypatch.setattr(image_fetch.socket, 'getaddrinfo', falso)
    return estado


# ─────────────────────────────────────────────────────────────────────────────
# El caso que motivó el cambio
# ─────────────────────────────────────────────────────────────────────────────

class TestRebindingDeDNS:

    def test_la_peticion_sale_a_la_ip_validada_no_al_nombre(self, monkeypatch, cliente_falso):
        """Sin nombre en la URL, httpx no puede volver a resolver."""
        _dns(monkeypatch, ['93.184.216.34'])
        descargar_imagen_segura('https://cdn.ejemplo.com/foto.png')

        pedido = cliente_falso.peticiones[0]
        assert pedido['url'].startswith('https://93.184.216.34:443/')
        assert 'cdn.ejemplo.com' not in pedido['url']

    def test_una_segunda_resolucion_envenenada_ya_no_influye(self, monkeypatch, cliente_falso):
        """El DNS responde pública la 1ª vez e interna la 2ª: el clásico rebinding.

        Con el código anterior, httpx habría hecho esa segunda consulta y
        conectado a 127.0.0.1. Ahora la IP se fija en la primera.
        """
        _dns(monkeypatch, ['93.184.216.34'], ['127.0.0.1'])
        descargar_imagen_segura('https://malicioso.ejemplo/foto.png')

        assert cliente_falso.peticiones[0]['url'].startswith('https://93.184.216.34:')
        assert '127.0.0.1' not in cliente_falso.peticiones[0]['url']

    def test_conserva_host_y_sni_para_que_el_tls_siga_validando(self, monkeypatch, cliente_falso):
        """Fijar la IP no debe degradar la comprobación del certificado."""
        _dns(monkeypatch, ['93.184.216.34'])
        descargar_imagen_segura('https://cdn.ejemplo.com/foto.png')

        pedido = cliente_falso.peticiones[0]
        assert pedido['host'] == 'cdn.ejemplo.com'
        assert pedido['sni'] == 'cdn.ejemplo.com'


# ─────────────────────────────────────────────────────────────────────────────
# Defensas que ya existían y no deben perderse
# ─────────────────────────────────────────────────────────────────────────────

class TestValidacionDeDestino:

    def test_solo_https(self):
        with pytest.raises(ImagenDescargaError, match='HTTPS'):
            _destino_fijado('http://cdn.ejemplo.com/f.png')

    def test_rechaza_url_sin_host(self):
        with pytest.raises(ImagenDescargaError):
            _destino_fijado('https:///f.png')

    @pytest.mark.parametrize('ip', [
        '127.0.0.1',        # loopback
        '10.0.0.5',         # privada
        '192.168.1.10',     # privada
        '172.17.0.2',       # privada — la red de Docker: redis, db, clamav
        '169.254.169.254',  # link-local — metadatos de nube
        '0.0.0.0',          # sin especificar
        '224.0.0.1',        # multicast
    ])
    def test_rechaza_ips_internas(self, monkeypatch, ip):
        _dns(monkeypatch, [ip])
        with pytest.raises(ImagenDescargaError, match='SSRF'):
            _destino_fijado('https://interno.ejemplo/f.png')

    def test_rechaza_respuesta_dns_mixta(self, monkeypatch):
        """Una pública y una interna: no vale quedarse con la que conviene."""
        _dns(monkeypatch, ['93.184.216.34', '127.0.0.1'])
        with pytest.raises(ImagenDescargaError, match='SSRF'):
            _destino_fijado('https://mixto.ejemplo/f.png')

    def test_host_que_no_resuelve(self, monkeypatch):
        def explota(*a, **kw):
            raise image_fetch.socket.gaierror('sin registro')
        monkeypatch.setattr(image_fetch.socket, 'getaddrinfo', explota)
        with pytest.raises(ImagenDescargaError, match='resolver'):
            _destino_fijado('https://noexiste.ejemplo/f.png')

    def test_ipv6_va_entre_corchetes(self, monkeypatch):
        _dns(monkeypatch, ['2606:2800:220:1:248:1893:25c8:1946'])
        destino, _, _ = _destino_fijado('https://v6.ejemplo/f.png')
        assert destino.startswith('https://[2606:2800:220:1:248:1893:25c8:1946]:443/')

    def test_puerto_no_estandar_va_en_la_cabecera_host(self, monkeypatch):
        _dns(monkeypatch, ['93.184.216.34'])
        destino, host, _ = _destino_fijado('https://cdn.ejemplo.com:8443/f.png')
        assert ':8443' in destino
        assert host == 'cdn.ejemplo.com:8443'

    def test_conserva_ruta_y_query(self, monkeypatch):
        _dns(monkeypatch, ['93.184.216.34'])
        destino, _, _ = _destino_fijado('https://cdn.ejemplo.com/a/b.png?v=2&x=1')
        assert destino.endswith('/a/b.png?v=2&x=1')

    def test_ip_es_publica(self):
        assert _ip_es_publica('93.184.216.34')
        assert not _ip_es_publica('127.0.0.1')
        assert not _ip_es_publica('no-es-una-ip')


class TestRedirects:

    def test_un_redirect_a_ip_interna_se_corta(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'], ['127.0.0.1'])
        cliente_falso.respuestas = [
            _RespuestaFalsa(status=302, headers={'location': 'https://interno.ejemplo/f.png'}),
        ]
        with pytest.raises(ImagenDescargaError, match='SSRF'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')

    def test_redirect_relativo_se_resuelve_contra_el_nombre(self, monkeypatch, cliente_falso):
        """La URL lógica debe conservar el dominio, o un `/otra.png` se perdería."""
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [
            _RespuestaFalsa(status=302, headers={'location': '/otra.png'}),
            _RespuestaFalsa(_png()),
        ]
        descargar_imagen_segura('https://cdn.ejemplo.com/f.png')
        assert cliente_falso.peticiones[1]['url'].endswith('/otra.png')
        assert cliente_falso.peticiones[1]['host'] == 'cdn.ejemplo.com'

    def test_corta_tras_demasiados_redirects(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [
            _RespuestaFalsa(status=302, headers={'location': f'https://cdn.ejemplo.com/{i}.png'})
            for i in range(10)
        ]
        with pytest.raises(ImagenDescargaError, match='redirect'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')

    def test_redirect_sin_location(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [_RespuestaFalsa(status=302)]
        with pytest.raises(ImagenDescargaError, match='Location'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')


class TestContenido:

    def test_rechaza_lo_que_no_es_imagen(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [_RespuestaFalsa(b'{"token": "secreto"}')]
        with pytest.raises(ImagenDescargaError, match='no es una imagen'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')

    def test_rechaza_por_content_length(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [
            _RespuestaFalsa(_png(), headers={'content-length': str(99 * 1024 * 1024)}),
        ]
        with pytest.raises(ImagenDescargaError, match='Content-Length'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')

    def test_corta_por_tamano_aunque_mienta_el_content_length(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [_RespuestaFalsa(b'x' * 5000)]
        with pytest.raises(ImagenDescargaError, match='demasiado grande'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png', max_bytes=1000)

    def test_rechaza_http_distinto_de_200(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        cliente_falso.respuestas = [_RespuestaFalsa(b'', status=404)]
        with pytest.raises(ImagenDescargaError, match='HTTP 404'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')

    def test_una_imagen_valida_pasa(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        datos, mime = descargar_imagen_segura('https://cdn.ejemplo.com/f.png')
        assert mime == 'image/png'
        assert datos.startswith(b'\x89PNG')

    def test_rechaza_bomba_de_descompresion(self, monkeypatch, cliente_falso):
        """Rechaza por DIMENSIONES antes de que Pillow cargue los píxeles.

        Se baja el tope por entorno en lugar de generar una imagen de 50 MP: lo
        que se prueba es el guardián, y un PNG de esas dimensiones tardaría
        segundos en construirse en cada corrida.
        """
        _dns(monkeypatch, ['93.184.216.34'])
        monkeypatch.setenv('IMG_MAX_PIXELS', '100')  # 10×10 px
        cliente_falso.respuestas = [_RespuestaFalsa(_png(40, 40))]  # 1 600 px
        with pytest.raises(ImagenDescargaError, match='demasiado grande'):
            descargar_imagen_segura('https://cdn.ejemplo.com/f.png')

    def test_una_imagen_dentro_del_tope_de_pixeles_pasa(self, monkeypatch, cliente_falso):
        _dns(monkeypatch, ['93.184.216.34'])
        monkeypatch.setenv('IMG_MAX_PIXELS', '100')
        cliente_falso.respuestas = [_RespuestaFalsa(_png(8, 8))]  # 64 px
        datos, mime = descargar_imagen_segura('https://cdn.ejemplo.com/f.png')
        assert mime == 'image/png'
