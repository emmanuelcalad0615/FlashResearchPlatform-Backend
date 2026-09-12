"""Emision y verificacion de tokens de autenticacion.

Python puro: no conoce FastAPI, ni la base de datos, ni la configuracion. El
secreto y los tiempos llegan por parametro, no se leen de Settings: este modulo
vive en la capa de dominio y no puede importar hacia arriba.

Dos formas de token, cada una por una razon:

  access token   JWT firmado. Viaja en CADA peticion, asi que se valida con la
                 firma y sin tocar la base. Ese es todo el beneficio del JWT.

  refresh token  Cadena aleatoria opaca. Se valida SIEMPRE contra la base (si
                 fue usado, si esta revocado, si su familia vive), y ninguna de
                 esas preguntas la responde una firma. Ademas, al no ser un JWT,
                 no puede colarse como access token: el riesgo desaparece por
                 construccion en vez de mitigarse.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from packages.core.domain.errors import InvalidTokenError, TokenExpiredError

# Clase de token, en el claim `type`. Defensa en profundidad: si manana existe
# un token de reset de contrasena firmado con el mismo secreto, no podra
# colarse como credencial de sesion.
TOKEN_TYPE_ACCESS = "access"


@dataclass(frozen=True)
class AccessTokenClaims:
    """Lo que se saca de un access token valido.

    Un objeto y no una tupla: quien lo recibe escribe `claims.user_id` en vez
    de `claims[0]`, y anadir un claim manana no rompe a quien ya desempaquetaba.
    """

    user_id: str
    family_id: str | None


def create_access_token(
    user_id: str,
    *,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    family_id: str | None = None,
) -> str:
    """Firma un access token para `user_id`.

    OJO: un JWT es Base64, NO cifrado. Cualquiera puede leer su contenido sin
    la clave; la firma solo impide falsificarlo. Por eso aqui no va el email,
    ni el nombre, ni nada privado: solo el identificador.
    """
    ahora = datetime.now(UTC)

    payload = {
        # Claims estandar del RFC 7519.
        "sub": user_id,                                       # de quien es
        "iat": ahora,                                         # cuando se emitio
        "exp": ahora + timedelta(minutes=expires_minutes),    # cuando caduca
        "jti": str(uuid.uuid4()),                             # id unico de ESTE token
        # Claim propio.
        "type": TOKEN_TYPE_ACCESS,
    }

    if family_id is not None:
        # La cadena de rotacion a la que pertenece esta sesion. Sirve para que
        # el cierre de sesion sepa QUE familia revocar sin necesitar el refresh
        # token, que el navegador solo manda a /api/auth/refresh.
        #
        # Va aqui aunque un JWT sea legible: no es una credencial. Con el
        # family_id no se puede emitir nada —la base busca por hash del token,
        # no por familia— y lo unico que habilita es cerrar la sesion, que
        # quien tenga este access token ya podia hacer de todos modos.
        payload["fid"] = family_id

    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(
    token: str, *, secret: str, algorithm: str
) -> AccessTokenClaims:
    """Devuelve los claims utiles si el token es valido.

    Lanza TokenExpiredError si caduco, InvalidTokenError en cualquier otro caso.
    Son errores distintos a proposito: ante un token expirado el cliente pide un
    refresh y reintenta sin que el usuario se entere; ante uno invalido, lo manda
    al login. Con un solo error no podria distinguir los dos casos y echaria al
    usuario cada vez que caduca el access token.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            # En plural y obligatorio: sin esto, pyjwt aceptaria el algoritmo
            # escrito DENTRO del token, y un atacante podria mandar uno que
            # diga "alg": "none" —sin firma— y colar lo que quisiera. Es el
            # ataque de confusion de algoritmo.
            algorithms=[algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError from exc
    except jwt.PyJWTError as exc:
        # Firma incorrecta, token malformado, algoritmo no permitido...
        raise InvalidTokenError from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise InvalidTokenError("The token is not an access token")

    user_id = payload.get("sub")
    if not user_id:
        raise InvalidTokenError("The token has no subject")

    # `fid` es opcional a proposito. Los access tokens emitidos antes de que
    # existiera este claim siguen siendo validos hasta que caduquen: sin esto,
    # desplegar el cambio echaria a la calle a todo el que tuviera sesion
    # abierta. Quince minutos despues ya no queda ninguno sin fid.
    return AccessTokenClaims(user_id=user_id, family_id=payload.get("fid"))


# 32 bytes = 256 bits de entropia. Adivinar uno por fuerza bruta es inviable.
_OPAQUE_TOKEN_BYTES = 32


def generate_opaque_token() -> str:
    """Genera un token opaco: refresh token o enlace de verificacion de email.

    `secrets` y NO `random`: el generador de random es determinista y, viendo
    unos cuantos valores, se puede predecir el siguiente. Para una credencial
    eso permitiria calcular el token de otro usuario.

    `token_urlsafe` y no `token_hex`: misma entropia en 43 caracteres en vez de
    64, y con un alfabeto valido dentro de una URL, que es por donde viaja el
    enlace de verificacion.
    """
    return secrets.token_urlsafe(_OPAQUE_TOKEN_BYTES)


def hash_opaque_token(token: str) -> str:
    """Hash con el que se guarda un token opaco en la base.

    Nunca se guarda el token en claro: quien consiguiera un volcado de la base
    podria entrar como cualquier usuario sin saber su contrasena. El hash no se
    puede revertir, asi que la tabla por si sola no sirve de nada.

    SHA-256 y NO Argon2, al reves que con las contrasenas. Argon2 es lento a
    proposito para frenar a quien prueba millones de candidatos, y eso tiene
    sentido con una contrasena que eligio un humano. Un token de 256 bits
    aleatorios no es adivinable: no hay diccionario ni fuerza bruta posible, asi
    que frenar al atacante no aporta nada y cada refresh pagaria 70 ms de mas.

    Sin sal, tambien a proposito: dos tokens aleatorios nunca coinciden, y una
    sal impediria buscar por hash en la base, que es justo lo que hace falta.
    """
    return hashlib.sha256(token.encode()).hexdigest()
