"""Hashing y politica de contrasenas.

Python puro: no conoce FastAPI, ni la base de datos, ni la configuracion.
Lo comparten la API y el worker.
"""

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from argon2.low_level import Type

from packages.core.domain.errors import DomainValidationError

MIN_PASSWORD_LENGTH = 12

# Tope para que nadie mande 10 MB de "contrasena" y ocupe la CPU del servidor
# hasheandola. Argon2 no trunca (bcrypt si lo hace, en silencio, a 72 bytes),
# pero su coste crece con el tamano de la entrada.
MAX_PASSWORD_LENGTH = 128

# Argon2id con los parametros por defecto de la libreria, alineados con las
# recomendaciones actuales de OWASP. Tardar ~70 ms es DELIBERADO: hace inviable
# probar millones de contrasenas por segundo contra un volcado de la base.
#
# type=Type.ID se escribe aunque YA sea el valor por defecto de argon2-cffi.
# Es la eleccion del algoritmo, no un ajuste fino: Argon2i resiste peor los
# ataques con hardware dedicado y Argon2d es vulnerable a ataques por canal
# lateral. Depender de un valor por defecto significaria que un cambio en la
# libreria cambiaria el algoritmo sin que nadie lo revisara.
_hasher = PasswordHasher(type=Type.ID)

# Muestra de las contrasenas mas filtradas que ademas superan la longitud
# minima (las cortas ya las corta la regla de longitud). No pretende ser
# exhaustiva: corta lo obvio sin cargar una lista de millones de entradas.
# Una comprobacion real contra Have I Been Pwned es otra HU: implica una
# llamada de red en el registro, con su latencia y su modo de fallo.
_COMMON_PASSWORDS = frozenset({
    "123456789012",
    "1234567890123",
    "password1234",
    "passwordpassword",
    "qwertyuiop12",
    "qwerty123456",
    "letmein12345",
    "welcome123456",
    "iloveyou1234",
    "administrador",
    "contrasena123",
    "flashresearch",
})

# Hash contra el que se verifica cuando el usuario NO existe. Ver decoy_verify().
# Se calcula una sola vez al importar el modulo: generarlo en cada peticion
# costaria tiempo y reintroduciria por otro lado el problema que evita.
_DECOY_HASH = _hasher.hash("una-contrasena-que-nunca-va-a-coincidir")


def validate_password_policy(password: str) -> None:
    """Comprueba la politica. Lanza DomainValidationError si no la cumple.

    Se exige longitud y NO composicion ("una mayuscula, un numero, un simbolo").
    Esas reglas empujan a la gente hacia `Password1!`, que las cumple todas y
    esta en el top de las listas filtradas. La longitud aporta mucha mas
    entropia que los simbolos obligatorios; es lo que recomiendan OWASP y NIST.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise DomainValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long",
            details={"field": "password", "min_length": MIN_PASSWORD_LENGTH},
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise DomainValidationError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters long",
            details={"field": "password", "max_length": MAX_PASSWORD_LENGTH},
        )
    if password.lower() in _COMMON_PASSWORDS:
        raise DomainValidationError(
            "This password appears in known breach lists",
            details={"field": "password"},
        )


def hash_password(password: str) -> str:
    """Devuelve el hash Argon2id. La contrasena en claro no se guarda jamas.

    Cada llamada usa una sal aleatoria distinta, asi que dos usuarios con la
    misma contrasena producen hashes distintos y no se puede saber que la
    comparten.
    """
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True si la contrasena corresponde al hash.

    Devuelve False en vez de propagar la excepcion de argon2: al llamador solo
    le importa si coincide, y un hash corrupto en la base no debe convertir un
    login fallido en un error 500.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        # InvalidHashError hereda de ValueError, NO de Argon2Error: un hash
        # malformado en la base se escaparia de un `except Argon2Error` a secas
        # y convertiria un login fallido en un error 500.
        return False


def decoy_verify(password: str) -> bool:
    """Verifica contra un hash falso. SIEMPRE devuelve False.

    SEGURIDAD - ataque de temporizacion. Si al no existir el email respondemos
    al instante, mientras que con un email real tardamos ~70 ms hasheando, esa
    diferencia es medible desde fuera y revela que cuentas estan registradas.

    Llamando a esto en la rama del "usuario no existe", ambos caminos tardan lo
    mismo y no se filtra nada.
    """
    verify_password(password, _DECOY_HASH)
    return False


def needs_rehash(password_hash: str) -> bool:
    """True si el hash se creo con parametros mas debiles que los actuales.

    Permite reforzar las contrasenas de forma progresiva. Cuando el hardware
    avance y haya que subir los parametros de Argon2, los hashes ya guardados
    no se pueden recalcular: haria falta la contrasena en claro, que no se
    guarda. Pero SI se tiene durante el login, asi que ahi se rehashea y se
    guarda de nuevo. Cada usuario que inicie sesion queda al dia sin enterarse.
    """
    return _hasher.check_needs_rehash(password_hash)
