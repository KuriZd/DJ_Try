"""Smoke test real: API Django -> PUT S3 -> confirmacion -> GET Range.

No requiere credenciales AWS locales. VIDEO_SMOKE_TOKEN contiene el JWT de API.
No imprime tokens, respuestas de error remotas ni URLs firmadas.
"""

import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID

import requests


class SmokeFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def checked_url(url, *, local_http=False):
    require(isinstance(url, str), 'URL ausente o invalida.')
    parsed = urlsplit(url)
    require(bool(parsed.hostname) and not parsed.username and not parsed.password,
            'URL invalida o con credenciales embebidas.')
    require(parsed.scheme == 'https' or (
        local_http and parsed.scheme == 'http'
        and parsed.hostname in ('localhost', '127.0.0.1', '::1')
    ), 'Se requiere HTTPS (HTTP solo para la API en localhost).')
    return url


def request(method, url, **kwargs):
    # Sin redirecciones: no reenviar JWT ni ocultar un endpoint mal configurado.
    try:
        return requests.request(method, url, timeout=(10, 120),
                                allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise SmokeFailure('Error de red/TLS/timeout; comprueba API, region y conectividad.') from None


def api(base, token, path, *, body=None, expected=200):
    method = 'GET' if body is None else 'POST'
    with request(method, base + path,
                 headers={'Authorization': f'Bearer {token}'},
                 **({} if body is None else {'json': body})) as response:
        require(response.status_code == expected,
                f'API {path}: HTTP {response.status_code}; esperado {expected}.')
        try:
            data = response.json()
        except ValueError:
            raise SmokeFailure(f'API {path}: respuesta no JSON.') from None
    require(isinstance(data, dict), f'API {path}: formato inesperado.')
    return data


def check_origin(response, origin):
    require(response.headers.get('Access-Control-Allow-Origin') in (origin, '*'),
            'CORS: Access-Control-Allow-Origin no permite el frontend.')


def preflight(url, origin, method, header_names):
    with request('OPTIONS', url, headers={
        'Origin': origin,
        'Access-Control-Request-Method': method,
        'Access-Control-Request-Headers': ', '.join(header_names),
    }) as response:
        require(response.status_code in (200, 204),
                f'CORS {method}: preflight HTTP {response.status_code}.')
        check_origin(response, origin)
        methods = {v.strip().upper() for v in response.headers.get('Access-Control-Allow-Methods', '').split(',')}
        allowed = {v.strip().lower() for v in response.headers.get('Access-Control-Allow-Headers', '').split(',')}
        require(method in methods, f'CORS: metodo {method} no permitido.')
        require('*' in allowed or set(h.lower() for h in header_names) <= allowed,
                f'CORS: faltan encabezados permitidos para {method}.')


def check_range(response, size, prefix):
    if response.status_code == 200:
        raise SmokeFailure('FALLO RANGE: HTTP 200 OK en vez de 206. Range fue ignorado; '
                           'revisa proxy/CDN, endpoint y encabezados. No se descarga el cuerpo completo.')
    require(response.status_code == 206,
            f'GET Range: HTTP {response.status_code}; esperado 206 Partial Content.')
    expected = f'bytes 0-1023/{size}'
    require(response.headers.get('Content-Range') == expected,
            f'Content-Range incorrecto; esperado {expected}.')
    require(response.headers.get('Content-Length') == '1024',
            'Content-Length incorrecto; esperado 1024.')
    # Lectura acotada incluso si un proxy devuelve un cuerpo mayor al declarado.
    received = response.raw.read(1025)
    require(received == prefix, 'El fragmento descargado no coincide con el archivo subido.')


def run(args):
    token = os.getenv('VIDEO_SMOKE_TOKEN', '').strip()
    require(bool(token), 'Configura VIDEO_SMOKE_TOKEN con un access token JWT vigente.')
    base = checked_url(args.api_base, local_http=True).rstrip('/') + '/'
    require(not urlsplit(base).query and not urlsplit(base).fragment,
            '--api-base no debe contener query ni fragmento.')
    parsed_origin = urlsplit(args.origin)
    require(parsed_origin.scheme in ('http', 'https') and bool(parsed_origin.netloc)
            and parsed_origin.path == '' and not parsed_origin.query
            and not parsed_origin.fragment and not parsed_origin.username,
            '--origin debe ser un origen exacto sin ruta ni barra final.')
    require(args.file.is_file() and args.file.suffix.lower() == '.mp4',
            'Proporciona un archivo MP4 real.')
    size = args.file.stat().st_size
    require(1024 < size <= 20 * 1024 * 1024,
            'Usa un MP4 pequeno: mayor de 1024 bytes y hasta 20 MiB.')
    with args.file.open('rb') as source:
        prefix = source.read(1024)
        require(prefix[4:8] == b'ftyp', 'El archivo no parece un MP4 (falta cabecera ftyp).')

    upload = api(base, token, 'upload/', expected=201, body={
        'filename': args.file.name, 'content_type': 'video/mp4', 'visibility': 'private',
    })
    try:
        video_id = str(UUID(str(upload['video']['id'])))
    except (KeyError, TypeError, ValueError):
        raise SmokeFailure('La API no devolvio un UUID de video valido.') from None
    print(f'Video de prueba creado: {video_id}', flush=True)
    print('El registro y objeto se conservan para inspeccion; cada ejecucion crea uno nuevo.', flush=True)
    put_url = checked_url(upload.get('upload_url'))
    headers = upload.get('headers')
    require(upload.get('method') == 'PUT' and isinstance(headers, dict),
            'Contrato de subida invalido.')
    require(headers == {'Content-Type': 'video/mp4', 'If-None-Match': '*'},
            'Encabezados de subida distintos del contrato esperado.')
    preflight(put_url, args.origin, 'PUT', list(headers))
    with args.file.open('rb') as source:
        with request('PUT', put_url, headers={**headers, 'Origin': args.origin}, data=source) as response:
            require(response.status_code == 200, f'S3 PUT: HTTP {response.status_code}; esperado 200.')
            check_origin(response, args.origin)
    print('PUT y CORS correctos.', flush=True)
    confirmed = api(base, token, f'{video_id}/confirm/', body={})
    require(confirmed.get('status') == 'uploaded', 'Confirmacion: el video no esta uploaded.')
    originals = [r for r in confirmed.get('renditions', []) if r.get('profile') == 'original']
    require(len(originals) == 1 and originals[0].get('status') == 'uploaded'
            and originals[0].get('file_size') == size,
            'Confirmacion: estado/tamano del original incorrecto.')
    playback = api(base, token, f'{video_id}/playback/')
    get_url = checked_url(playback.get('url'))
    preflight(get_url, args.origin, 'GET', ['Range'])
    with request('GET', get_url, headers={
        'Range': 'bytes=0-1023', 'Origin': args.origin, 'Accept-Encoding': 'identity',
    }, stream=True) as response:
        check_range(response, size, prefix)
        check_origin(response, args.origin)
    print(f'OK: uploaded, GET 206, Content-Range bytes 0-1023/{size}, 1024 bytes identicos y CORS correcto.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-base', required=True, help='Ejemplo: https://api.example/api/videos/')
    parser.add_argument('--origin', required=True, help='Origen real del frontend, sin barra final')
    parser.add_argument('--file', type=Path, required=True, help='MP4 real de hasta 20 MiB')
    args = parser.parse_args()
    try:
        run(args)
    except (SmokeFailure, OSError, requests.RequestException) as exc:
        # Las excepciones de requests pueden incluir la URL firmada: no imprimirlas.
        message = str(exc) if isinstance(exc, SmokeFailure) else 'Error de lectura local o de red durante la transferencia.'
        print(f'ERROR: {message}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
