import pytest

from packages.core.domain.errors import DomainValidationError
from packages.core.domain.policies.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    decoy_verify,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)

VALID = "una-frase-larga-y-facil-de-recordar"


# ---- Politica --------------------------------------------------------------


def test_accepts_a_long_passphrase():
    validate_password_policy(VALID)  # no lanza


def test_rejects_a_password_below_the_minimum():
    with pytest.raises(DomainValidationError) as caught:
        validate_password_policy("a" * (MIN_PASSWORD_LENGTH - 1))

    error = caught.value
    assert error.code == "validation_error"
    # El detalle deja que el frontend muestre el limite sin duplicarlo.
    assert error.details == {"field": "password", "min_length": MIN_PASSWORD_LENGTH}


def test_accepts_exactly_the_minimum_length():
    validate_password_policy("a" * MIN_PASSWORD_LENGTH)


def test_rejects_a_password_above_the_maximum():
    """El tope no molesta al usuario: evita que hashear una entrada enorme
    consuma la CPU del servidor."""
    with pytest.raises(DomainValidationError) as caught:
        validate_password_policy("a" * (MAX_PASSWORD_LENGTH + 1))

    assert caught.value.details["max_length"] == MAX_PASSWORD_LENGTH


def test_rejects_a_common_password():
    with pytest.raises(DomainValidationError, match="breach"):
        validate_password_policy("passwordpassword")


def test_common_password_check_ignores_case():
    with pytest.raises(DomainValidationError):
        validate_password_policy("PasswordPassword")


def test_composition_rules_are_not_enforced():
    """A proposito: exigir mayuscula + numero + simbolo produce `Password1!`,
    que cumple todas las reglas y esta en las listas filtradas. La longitud
    aporta mucha mas entropia."""
    validate_password_policy("todo en minusculas y sin numeros")


# ---- Hashing ---------------------------------------------------------------


def test_hash_does_not_contain_the_password():
    assert VALID not in hash_password(VALID)


def test_hash_uses_argon2id():
    assert hash_password(VALID).startswith("$argon2id$")


def test_same_password_produces_different_hashes():
    """Cada hash lleva su propia sal aleatoria: dos usuarios con la misma
    contrasena no se delatan entre si.

    Las dos llamadas se guardan en variables con nombre para que quede claro
    que son invocaciones distintas y no una expresion repetida por error.
    """
    primero = hash_password(VALID)
    segundo = hash_password(VALID)

    assert primero != segundo


def test_verify_accepts_the_right_password():
    assert verify_password(VALID, hash_password(VALID)) is True


def test_verify_rejects_a_wrong_password():
    assert verify_password("otra-cosa-completamente", hash_password(VALID)) is False


def test_verify_returns_false_on_a_corrupt_hash():
    """Un hash corrupto en la base es un login fallido, no un error 500."""
    assert verify_password(VALID, "esto-no-es-un-hash") is False


# ---- Defensa contra ataques de temporizacion -------------------------------


def test_decoy_always_returns_false():
    assert decoy_verify(VALID) is False
    assert decoy_verify("") is False


def test_decoy_does_real_work():
    """El senuelo tiene que gastar el mismo tiempo que una verificacion real.

    Se comprueba estructuralmente y no cronometrando: un test con umbrales de
    tiempo falla de forma aleatoria segun la carga de la maquina.
    """
    from packages.core.domain.policies.passwords import _DECOY_HASH

    assert _DECOY_HASH.startswith("$argon2id$")
    # Verificar contra el es tan caro como contra cualquier hash real.
    assert verify_password("loquesea", _DECOY_HASH) is False


# ---- Rehash progresivo -----------------------------------------------------


def test_fresh_hash_does_not_need_rehash():
    assert needs_rehash(hash_password(VALID)) is False


def test_weaker_hash_needs_rehash():
    """Simula un hash creado con parametros mas debiles, como los que quedarian
    en la base tras subir la configuracion de Argon2."""
    from argon2 import PasswordHasher

    debil = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    assert needs_rehash(debil.hash(VALID)) is True
