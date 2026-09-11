"""Cookies de sesion.

Convierte los tokens que devuelve el caso de uso en cabeceras Set-Cookie. Vive
en la capa de infraestructura de la API porque las cookies son un detalle de
HTTP: el dominio devuelve dos cadenas y no sabe que existen.
"""

from fastapi import Response

from apps.api.config import settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

# El access token viaja en TODAS las peticiones.
ACCESS_COOKIE_PATH = "/"

# El refresh token, SOLO en la ruta que lo canjea. El navegador manda una cookie
# unicamente a las rutas que cuelgan de su Path, asi que la llave larga —la que
# vale dias— no aparece en las cientos de peticiones normales de un dashboard.
# Menos veces viaja, menos oportunidades hay de que se filtre en un log de
# proxy, una cabecera capturada o un volcado de peticion.
REFRESH_COOKIE_PATH = "/api/auth/refresh"


def _set(
    response: Response, nombre: str, valor: str, *, path: str, max_age: int
) -> None:
    response.set_cookie(
        key=nombre,
        value=valor,
        max_age=max_age,
        path=path,
        # El JavaScript no puede leerla. Es lo que impide que un XSS se lleve
        # el token: no hay API del navegador que la exponga.
        httponly=True,
        # Solo por HTTPS. False en desarrollo porque en local no hay TLS; una
        # guarda de arranque impide que llegue asi a produccion.
        secure=settings.cookie_secure,
        # El navegador solo la manda cuando la peticion nace del propio sitio.
        # Es lo que corta el CSRF sin escribir una linea de codigo.
        samesite=settings.cookie_samesite,
    )


def set_session_cookies(
    response: Response, *, access_token: str, refresh_token: str
) -> None:
    """Escribe las dos cookies de una sesion recien abierta.

    Cada Max-Age coincide con la vida de su token. Si la cookie durara mas, el
    navegador seguiria mandando un token muerto y la API respondería 410 en
    cada peticion hasta que el usuario cerrara el navegador.
    """
    _set(
        response,
        ACCESS_COOKIE,
        access_token,
        path=ACCESS_COOKIE_PATH,
        max_age=settings.access_token_minutes * 60,
    )
    _set(
        response,
        REFRESH_COOKIE,
        refresh_token,
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
    )


def clear_session_cookies(response: Response) -> None:
    """Borra las dos cookies de sesion.

    OJO con el path: para eliminar una cookie hay que reenviarla con el MISMO
    path con el que se creo. Si no coincide exactamente, el navegador la deja
    donde estaba, la respuesta dice que se cerro sesion, y la cookie sigue ahi.
    Es un fallo clasico y silencioso.

    Borrar la cookie NO basta para cerrar sesion: el refresh token tambien hay
    que revocarlo en la base, porque un atacante que ya lo copiara no pierde
    nada porque desaparezca del navegador de la victima.
    """
    response.delete_cookie(
        ACCESS_COOKIE,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )
