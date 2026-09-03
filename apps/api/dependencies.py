"""Cableado de la aplicacion.

UNICO sitio del proyecto que conoce las dos mitades: los puertos que los casos
de uso declaran, y las implementaciones concretas que los cumplen. Los casos de
uso ven interfaces; aqui se decide que hay detras.

Si esto viviera dentro de application/, el dominio conoceria SQLAlchemy y SMTP,
y se acabaria la posibilidad de probarlo con dobles.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase
from packages.core.infrastructure.db.repositories import (
    SqlAlchemyEmailVerificationRepository,
    SqlAlchemyProfileRepository,
    SqlAlchemyUserRepository,
)
from packages.core.infrastructure.db.session import SessionLocal
from packages.core.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from packages.core.infrastructure.email.smtp_sender import SmtpEmailSender


async def get_session() -> AsyncIterator[AsyncSession]:
    """Una sesion por peticion.

    NO hace commit al terminar: quien decide cuando confirmar es el caso de
    uso, a traves de UnitOfWork, porque es el unico que sabe que escrituras van
    juntas. Lo que si hace es cerrar la sesion pase lo que pase, y revertir si
    la peticion revienta antes de confirmar.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_email_sender() -> SmtpEmailSender:
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        sender=settings.smtp_from,
        frontend_base_url=settings.frontend_base_url,
        verification_hours=settings.email_verification_hours,
    )


# Alias con Annotated en vez de Depends() en el default: es la forma que
# recomienda FastAPI hoy, evita repetir la dependencia en cada firma, y no
# ejecuta una llamada al definir la funcion.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
EmailSenderDep = Annotated[SmtpEmailSender, Depends(get_email_sender)]


def get_signup_use_case(
    session: SessionDep,
    emails: EmailSenderDep,
) -> SignupUseCase:
    return SignupUseCase(
        users=SqlAlchemyUserRepository(session),
        profiles=SqlAlchemyProfileRepository(session),
        verifications=SqlAlchemyEmailVerificationRepository(session),
        emails=emails,
        uow=SqlAlchemyUnitOfWork(session),
        verification_hours=settings.email_verification_hours,
        # El caso de uso genera el token pero no sabe donde vive el frontend:
        # eso es configuracion de entrega y la resuelve el adapter de correo.
        build_link=emails.build_verification_link,
    )


def get_verify_email_use_case(session: SessionDep) -> VerifyEmailUseCase:
    return VerifyEmailUseCase(
        users=SqlAlchemyUserRepository(session),
        verifications=SqlAlchemyEmailVerificationRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


SignupUseCaseDep = Annotated[SignupUseCase, Depends(get_signup_use_case)]
VerifyEmailUseCaseDep = Annotated[VerifyEmailUseCase, Depends(get_verify_email_use_case)]
