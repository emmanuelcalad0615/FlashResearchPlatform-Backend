import jwt
import pytest

from packages.core.domain.errors import InvalidTokenError, TokenExpiredError
from packages.core.domain.policies.tokens import (
    TOKEN_TYPE_ACCESS,
    create_access_token,
    decode_access_token,
    generate_opaque_token,
    hash_opaque_token,
)

SECRET = "un-secreto-de-prueba-de-al-menos-32-bytes-de-largo"
ALGORITHM = "HS256"
USER_ID = "a3f2b1c8-0000-4000-8000-000000000001"


def _token(**kwargs) -> str:
    opciones = {
        "secret": SECRET,
        "algorithm": ALGORITHM,
        "expires_minutes": 15,
    } | kwargs
    return create_access_token(USER_ID, **opciones)


# ---- Access token ----------------------------------------------------------


def test_roundtrip_returns_the_user_id():
    assert decode_access_token(_token(), secret=SECRET, algorithm=ALGORITHM) == USER_ID


def test_two_tokens_for_the_same_user_are_different():
    """El jti es unico por token, asi que ninguno se repite."""
    primero = _token()
    segundo = _token()

    assert primero != segundo


def test_token_carries_the_expected_claims():
    payload = jwt.decode(_token(), options={"verify_signature": False})

    assert payload["sub"] == USER_ID
    assert payload["type"] == TOKEN_TYPE_ACCESS
    assert {"iat", "exp", "jti"} <= payload.keys()


def test_a_token_signed_with_another_secret_is_rejected():
    """SEGURIDAD: es el test que impide falsificar un token.

    Si alguien relajara `algorithms=[...]` al decodificar, un atacante podria
    mandar un token con "alg": "none" —sin firma— y colar cualquier identidad.
    """
    ajeno = create_access_token(
        USER_ID,
        secret="otro-secreto-completamente-distinto-y-largo",
        algorithm=ALGORITHM,
        expires_minutes=15,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(ajeno, secret=SECRET, algorithm=ALGORITHM)


def test_a_malformed_token_is_rejected():
    with pytest.raises(InvalidTokenError):
        decode_access_token("esto-no-es-un-jwt", secret=SECRET, algorithm=ALGORITHM)


def test_an_expired_token_raises_token_expired_not_invalid():
    """La distincion de la que depende el refresh automatico.

    410 token_expired -> el cliente pide un refresh y reintenta en silencio.
    400 invalid_token -> el cliente manda al usuario al login.

    Con un solo error, cada usuario acabaria en el login cada 15 minutos.
    Los minutos negativos dejan el exp en el pasado sin necesidad de esperar.
    """
    expirado = _token(expires_minutes=-1)

    with pytest.raises(TokenExpiredError):
        decode_access_token(expirado, secret=SECRET, algorithm=ALGORITHM)


def test_a_token_of_another_type_is_rejected():
    """Defensa en profundidad frente a tokens futuros firmados con la misma
    clave: uno de reset de contrasena no puede colarse como sesion."""
    otro_tipo = jwt.encode(
        {"sub": USER_ID, "type": "password_reset"}, SECRET, algorithm=ALGORITHM
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(otro_tipo, secret=SECRET, algorithm=ALGORITHM)


def test_a_token_without_subject_is_rejected():
    sin_sub = jwt.encode({"type": TOKEN_TYPE_ACCESS}, SECRET, algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError):
        decode_access_token(sin_sub, secret=SECRET, algorithm=ALGORITHM)


# ---- Tokens opacos ---------------------------------------------------------


def test_opaque_tokens_are_never_repeated():
    primero = generate_opaque_token()
    segundo = generate_opaque_token()

    assert primero != segundo


def test_opaque_token_is_long_enough():
    """32 bytes en base64 urlsafe: 43 caracteres, 256 bits de entropia."""
    assert len(generate_opaque_token()) >= 43


def test_the_same_token_always_hashes_the_same():
    """Sin esto no se podria buscar el token en la base.

    Las dos llamadas van a variables con nombre para dejar claro que son
    invocaciones distintas y no una expresion repetida por error.
    """
    token = generate_opaque_token()

    primero = hash_opaque_token(token)
    segundo = hash_opaque_token(token)

    assert primero == segundo


def test_different_tokens_hash_differently():
    primero = hash_opaque_token(generate_opaque_token())
    segundo = hash_opaque_token(generate_opaque_token())

    assert primero != segundo


def test_hash_does_not_reveal_the_token():
    """SEGURIDAD: lo que se guarda en la base no sirve para entrar."""
    token = generate_opaque_token()
    resultado = hash_opaque_token(token)

    assert token not in resultado
    assert len(resultado) == 64  # SHA-256 en hexadecimal
