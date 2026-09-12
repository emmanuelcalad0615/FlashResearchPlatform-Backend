"""Verifica que el signup sea atomico. Cierra la DEUDA-02.

Los tests con dobles no pueden demostrarlo: un diccionario no tiene
transacciones. Este corre contra Postgres de verdad, con los repositorios y el
UnitOfWork reales.
"""

import uuid

import pytest
from sqlalchemy import text

from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.domain.repositories import EmailVerificationRepository
from packages.core.infrastructure.db.repositories import (
    SqlAlchemyEmailVerificationRepository,
    SqlAlchemyProfileRepository,
    SqlAlchemyUserRepository,
)
from packages.core.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from tests.conftest import requiere_bd
from tests.fakes import InMemoryEmailSender

pytestmark = [pytest.mark.integration, requiere_bd]

PASSWORD = "una-frase-larga-y-seguraB"


class _VerificacionesQueFalla(EmailVerificationRepository):
    """Revienta en la TERCERA escritura del signup.

    Para entonces users y profiles ya se escribieron. Si cada repositorio
    confirmara por su cuenta, esas dos filas sobrevivirian: justo el estado
    corrupto —usuario sin token de verificacion, imposible de activar— que la
    transaccion debe impedir.
    """

    async def create(self, **kwargs):
        raise RuntimeError("fallo simulado a mitad del registro")

    async def get_by_hash(self, token_hash: str):
        raise NotImplementedError

    async def mark_used(self, token_id) -> None:
        raise NotImplementedError

    # El resto del puerto no participa en el registro. Se declaran porque la
    # ABC obliga, y revientan a proposito: si alguna se llamara aqui, seria un
    # cambio de comportamiento que este test tiene que delatar.
    async def get_latest_for_user(self, user_id):
        raise NotImplementedError

    async def invalidate_for_user(self, user_id) -> None:
        raise NotImplementedError


def _caso(session, verifications):
    return SignupUseCase(
        users=SqlAlchemyUserRepository(session),
        profiles=SqlAlchemyProfileRepository(session),
        verifications=verifications,
        emails=InMemoryEmailSender(),
        uow=SqlAlchemyUnitOfWork(session),
        verification_hours=24,
        build_link=lambda t: f"https://app.test/verify?token={t}",
        hasher=lambda p: f"hash::{p}",
    )


async def _contar(session, email: str) -> tuple[int, int]:
    usuarios = await session.scalar(
        text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}
    )
    perfiles = await session.scalar(
        text(
            "SELECT count(*) FROM profiles p "
            "JOIN users u ON u.id = p.id WHERE u.email = :e"
        ),
        {"e": email},
    )
    return usuarios, perfiles


async def test_a_failure_midway_leaves_nothing_behind(db_session):
    email = f"atomico-{uuid.uuid4()}@ejemplo.com"
    caso = _caso(db_session, _VerificacionesQueFalla())

    with pytest.raises(RuntimeError):
        await caso.execute(email, PASSWORD)

    # Quien llama al caso de uso revierte al ver la excepcion. En la app lo hace
    # la dependencia que abre la sesion.
    await db_session.rollback()

    assert await _contar(db_session, email) == (0, 0)


async def test_the_happy_path_does_persist(db_session):
    """Contrapeso del anterior: sin este, el test de arriba pasaria aunque el
    signup no escribiera NADA nunca."""
    email = f"atomico-ok-{uuid.uuid4()}@ejemplo.com"
    caso = _caso(db_session, SqlAlchemyEmailVerificationRepository(db_session))

    await caso.execute(email, PASSWORD)

    assert await _contar(db_session, email) == (1, 1)
