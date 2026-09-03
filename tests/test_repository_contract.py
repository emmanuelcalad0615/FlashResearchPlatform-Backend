"""Bateria de contrato: los mismos tests contra las DOS implementaciones.

Cierra la DEUDA-01. Los dobles de tests/fakes.py imitan a mano el
comportamiento de SQLAlchemy —comparacion de email sin mayusculas, devolver
tokens usados, no pisar revoked_at—, y nada garantizaba que siguieran
coincidiendo. Aqui cada test corre dos veces:

    in_memory   -> siempre, sin Docker
    sqlalchemy  -> solo con `-m integration` y TEST_DATABASE_URL

Si una implementacion se desvia de la otra, el test lo delata. Es la unica
forma de que un doble no pueda mentir en silencio.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from packages.core.infrastructure.db.repositories import (
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)
from tests.conftest import requiere_bd
from tests.fakes import InMemoryRefreshTokenRepository, InMemoryUserRepository


def _en(dias: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=dias)


def _email() -> str:
    return f"contrato-{uuid.uuid4()}@ejemplo.com"


# Cada test recibe una fabrica y se ejecuta una vez por implementacion.
# La de SQLAlchemy va marcada para que solo corra con -m integration.
IMPLEMENTACIONES = [
    pytest.param("in_memory", id="in_memory"),
    pytest.param(
        "sqlalchemy",
        id="sqlalchemy",
        marks=[pytest.mark.integration, requiere_bd],
    ),
]


@pytest.fixture
def usuarios(request):
    if request.param == "in_memory":
        return InMemoryUserRepository()
    # getfixturevalue resuelve db_session SOLO en la variante de SQLAlchemy:
    # la del doble no debe exigir Postgres.
    return SqlAlchemyUserRepository(request.getfixturevalue("db_session"))


@pytest.fixture
def tokens(request):
    if request.param == "in_memory":
        return InMemoryRefreshTokenRepository()
    return SqlAlchemyRefreshTokenRepository(request.getfixturevalue("db_session"))


# ---- Usuarios --------------------------------------------------------------


@pytest.mark.parametrize("usuarios", IMPLEMENTACIONES, indirect=True)
async def test_email_lookup_ignores_case(usuarios):
    """La columna es CITEXT. Si el doble comparara exacto, el signup creeria
    que el email esta libre y se crearian dos cuentas para el mismo correo."""
    email = _email()
    await usuarios.create(uuid.uuid4(), email.replace("contrato", "CONTRATO"), "hash")

    assert await usuarios.get_by_email(email) is not None
    assert await usuarios.get_by_email(email.upper()) is not None


@pytest.mark.parametrize("usuarios", IMPLEMENTACIONES, indirect=True)
async def test_unknown_email_returns_none(usuarios):
    assert await usuarios.get_by_email(_email()) is None


@pytest.mark.parametrize("usuarios", IMPLEMENTACIONES, indirect=True)
async def test_new_user_starts_unverified(usuarios):
    creado = await usuarios.create(uuid.uuid4(), _email(), "hash")

    assert creado.email_verified is False


@pytest.mark.parametrize("usuarios", IMPLEMENTACIONES, indirect=True)
async def test_created_user_can_be_read_back(usuarios):
    user_id = uuid.uuid4()
    email = _email()
    await usuarios.create(user_id, email, "hash-argon2")

    leido = await usuarios.get_by_id(user_id)

    assert leido is not None
    assert leido.email == email
    assert leido.password_hash == "hash-argon2"


@pytest.mark.parametrize("usuarios", IMPLEMENTACIONES, indirect=True)
async def test_marking_verified_persists(usuarios):
    user_id = uuid.uuid4()
    await usuarios.create(user_id, _email(), "hash")

    await usuarios.mark_email_verified(user_id)

    assert (await usuarios.get_by_id(user_id)).email_verified is True


@pytest.mark.parametrize("usuarios", IMPLEMENTACIONES, indirect=True)
async def test_password_hash_can_be_replaced(usuarios):
    """Lo usa el rehash progresivo durante el login."""
    user_id = uuid.uuid4()
    await usuarios.create(user_id, _email(), "hash-viejo")

    await usuarios.update_password_hash(user_id, "hash-nuevo")

    assert (await usuarios.get_by_id(user_id)).password_hash == "hash-nuevo"


# ---- Refresh tokens --------------------------------------------------------


@pytest.mark.parametrize("usuarios,tokens", [(i, i) for i in IMPLEMENTACIONES],
                         indirect=True)
async def test_lookup_returns_a_used_token(usuarios, tokens):
    """SEGURIDAD: si una implementacion escondiera los usados, la deteccion de
    reutilizacion nunca se disparia y el robo pasaria desapercibido."""
    user_id = uuid.uuid4()
    await usuarios.create(user_id, _email(), "hash")
    hash_token = f"h-{uuid.uuid4()}"
    token = await tokens.create(
        user_id=user_id, token_hash=hash_token, family_id=uuid.uuid4(),
        expires_at=_en(7), user_agent=None,
    )

    await tokens.mark_used(token.id)

    encontrado = await tokens.get_by_hash(hash_token)
    assert encontrado is not None
    assert encontrado.is_used is True


@pytest.mark.parametrize("usuarios,tokens", [(i, i) for i in IMPLEMENTACIONES],
                         indirect=True)
async def test_revoking_a_family_hits_every_token_in_it(usuarios, tokens):
    user_id = uuid.uuid4()
    await usuarios.create(user_id, _email(), "hash")
    familia = uuid.uuid4()
    hashes_dentro = [f"h-{uuid.uuid4()}" for _ in range(2)]
    for h in hashes_dentro:
        await tokens.create(
            user_id=user_id, token_hash=h, family_id=familia,
            expires_at=_en(7), user_agent=None,
        )
    hash_ajeno = f"h-{uuid.uuid4()}"
    await tokens.create(
        user_id=user_id, token_hash=hash_ajeno, family_id=uuid.uuid4(),
        expires_at=_en(7), user_agent=None,
    )

    await tokens.revoke_family(familia)

    for h in hashes_dentro:
        assert (await tokens.get_by_hash(h)).is_revoked
    assert (await tokens.get_by_hash(hash_ajeno)).is_revoked is False


@pytest.mark.parametrize("usuarios,tokens", [(i, i) for i in IMPLEMENTACIONES],
                         indirect=True)
async def test_revoking_twice_keeps_the_original_date(usuarios, tokens):
    """La primera fecha es la pista para investigar por que se cerro una
    sesion. Pisarla la borraria."""
    user_id = uuid.uuid4()
    await usuarios.create(user_id, _email(), "hash")
    hash_token = f"h-{uuid.uuid4()}"
    familia = uuid.uuid4()
    await tokens.create(
        user_id=user_id, token_hash=hash_token, family_id=familia,
        expires_at=_en(7), user_agent=None,
    )

    await tokens.revoke_family(familia)
    primera = (await tokens.get_by_hash(hash_token)).revoked_at

    await tokens.revoke_family(familia)

    assert (await tokens.get_by_hash(hash_token)).revoked_at == primera


@pytest.mark.parametrize("usuarios,tokens", [(i, i) for i in IMPLEMENTACIONES],
                         indirect=True)
async def test_revoke_all_closes_every_session(usuarios, tokens):
    user_id = uuid.uuid4()
    await usuarios.create(user_id, _email(), "hash")
    hashes = [f"h-{uuid.uuid4()}" for _ in range(3)]
    for h in hashes:
        await tokens.create(
            user_id=user_id, token_hash=h, family_id=uuid.uuid4(),
            expires_at=_en(7), user_agent=None,
        )

    await tokens.revoke_all_for_user(user_id)

    for h in hashes:
        assert (await tokens.get_by_hash(h)).is_revoked
