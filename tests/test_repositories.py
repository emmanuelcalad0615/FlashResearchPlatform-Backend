"""Tests de los dobles en memoria.

No prueban que un diccionario guarde cosas —eso probaria el diccionario—, sino
los comportamientos que los dobles imitan A MANO del real y que son faciles de
equivocar. Si uno de estos se desvia, los tests de los casos de uso pasarian en
verde contra un doble que miente.

La garantia de verdad es la bateria de contrato contra Postgres: DEUDA-01 en
testing-decisions.md, planificada como HU-A15.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.fakes import (
    InMemoryEmailVerificationRepository,
    InMemoryProfileRepository,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)


def _en(dias: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=dias)


# ---- Usuarios --------------------------------------------------------------


async def test_email_lookup_ignores_case():
    """La columna real es CITEXT. Si el doble comparara exacto, el caso de uso
    de signup creeria que el email esta libre y se crearian dos cuentas para el
    mismo correo."""
    repo = InMemoryUserRepository()
    await repo.create(uuid4(), "Ana@Ejemplo.com", "hash")

    assert await repo.get_by_email("ana@ejemplo.com") is not None
    assert await repo.get_by_email("ANA@EJEMPLO.COM") is not None


async def test_unknown_email_returns_none():
    repo = InMemoryUserRepository()

    assert await repo.get_by_email("nadie@ejemplo.com") is None


async def test_new_user_starts_unverified():
    """Si naciera verificado, el bloqueo del login no serviria de nada."""
    repo = InMemoryUserRepository()
    usuario = await repo.create(uuid4(), "ana@ejemplo.com", "hash")

    assert usuario.email_verified is False


async def test_marking_verified_does_not_mutate_the_previous_entity():
    """Las entidades son frozen: quien tuviera una referencia vieja no ve el
    cambio a sus espaldas."""
    repo = InMemoryUserRepository()
    usuario = await repo.create(uuid4(), "ana@ejemplo.com", "hash")

    await repo.mark_email_verified(usuario.id)

    assert usuario.email_verified is False                      # la copia vieja
    assert (await repo.get_by_id(usuario.id)).email_verified    # la guardada


# ---- Refresh tokens --------------------------------------------------------


async def _token(repo, *, family_id=None, expires_in_days=7):
    return await repo.create(
        user_id=uuid4(),
        token_hash=f"hash-{uuid4()}",
        family_id=family_id or uuid4(),
        expires_at=_en(expires_in_days),
        user_agent=None,
    )


async def test_lookup_returns_a_used_token():
    """SEGURIDAD: si el doble escondiera los usados, el test de deteccion de
    reutilizacion nunca se ejecutaria y el robo pasaria desapercibido."""
    repo = InMemoryRefreshTokenRepository()
    token = await repo.create(
        user_id=uuid4(), token_hash="h", family_id=uuid4(),
        expires_at=_en(7), user_agent=None,
    )
    await repo.mark_used(token.id)

    encontrado = await repo.get_by_hash("h")

    assert encontrado is not None
    assert encontrado.is_used is True


async def test_lookup_returns_a_revoked_token():
    repo = InMemoryRefreshTokenRepository()
    token = await repo.create(
        user_id=uuid4(), token_hash="h", family_id=uuid4(),
        expires_at=_en(7), user_agent=None,
    )
    await repo.revoke_family(token.family_id)

    assert (await repo.get_by_hash("h")).is_revoked is True


async def test_revoking_a_family_hits_every_token_in_it():
    repo = InMemoryRefreshTokenRepository()
    familia = uuid4()
    a = await _token(repo, family_id=familia)
    b = await _token(repo, family_id=familia)
    ajeno = await _token(repo)

    await repo.revoke_family(familia)

    assert repo.por_id[a.id].is_revoked
    assert repo.por_id[b.id].is_revoked
    assert not repo.por_id[ajeno.id].is_revoked


async def test_revoking_twice_keeps_the_original_date():
    """La primera fecha es la que sirve para investigar por que se cerro una
    sesion. Pisarla borraria la pista."""
    repo = InMemoryRefreshTokenRepository()
    token = await _token(repo)
    await repo.revoke_family(token.family_id)
    primera = repo.por_id[token.id].revoked_at

    await repo.revoke_family(token.family_id)

    assert repo.por_id[token.id].revoked_at == primera


async def test_revoke_all_closes_every_session_of_the_user():
    repo = InMemoryRefreshTokenRepository()
    usuario = uuid4()
    sesiones = [
        await repo.create(
            user_id=usuario, token_hash=f"h{i}", family_id=uuid4(),
            expires_at=_en(7), user_agent=None,
        )
        for i in range(3)
    ]
    ajeno = await _token(repo)

    await repo.revoke_all_for_user(usuario)

    assert all(repo.por_id[s.id].is_revoked for s in sesiones)
    assert not repo.por_id[ajeno.id].is_revoked


@pytest.mark.parametrize(
    ("campo", "esperado"),
    [
        ("recien_emitido", True),
        ("usado", False),
        ("revocado", False),
        ("vencido", False),
    ],
)
async def test_is_usable_covers_every_state(campo, esperado):
    repo = InMemoryRefreshTokenRepository()
    token = await _token(repo)

    if campo == "usado":
        await repo.mark_used(token.id)
    elif campo == "revocado":
        await repo.revoke_family(token.family_id)
    elif campo == "vencido":
        repo.por_id[token.id] = replace(token, expires_at=_en(-1))

    assert repo.por_id[token.id].is_usable is esperado


# ---- Verificacion de correo ------------------------------------------------


async def test_verification_token_is_single_use():
    repo = InMemoryEmailVerificationRepository()
    token = await repo.create(user_id=uuid4(), token_hash="h", expires_at=_en(1))
    assert token.is_usable is True

    await repo.mark_used(token.id)

    assert (await repo.get_by_hash("h")).is_usable is False


# ---- Perfiles --------------------------------------------------------------


async def test_profile_shares_the_user_id():
    """1:1 con User: la FK de la tabla real lo exige."""
    repo = InMemoryProfileRepository()
    user_id = uuid4()

    perfil = await repo.create(user_id)

    assert perfil.id == user_id
    assert perfil.settings == {}
