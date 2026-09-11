"""Cableado de la aplicacion.

UNICO sitio del proyecto que conoce las dos mitades: los puertos que los casos
de uso declaran, y las implementaciones concretas que los cumplen. Los casos de
uso ven interfaces; aqui se decide que hay detras.

Si esto viviera dentro de application/, el dominio conoceria SQLAlchemy y SMTP,
y se acabaria la posibilidad de probarlo con dobles.
"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.infrastructure.cookies import ACCESS_COOKIE
from packages.core.application.usecases.auth.login import LoginUseCase
from packages.core.application.usecases.auth.me import GetMeUseCase
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase
from packages.core.domain.entities import User
from packages.core.domain.errors import InvalidTokenError, UnauthorizedError
from packages.core.domain.policies.tokens import decode_access_token
from packages.core.infrastructure.db.repositories import (
    SqlAlchemyEmailVerificationRepository,
    SqlAlchemyProfileRepository,
    SqlAlchemyRefreshTokenRepository,
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


def get_login_use_case(session: SessionDep) -> LoginUseCase:
    return LoginUseCase(
        users=SqlAlchemyUserRepository(session),
        tokens=SqlAlchemyRefreshTokenRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
        access_token_minutes=settings.access_token_minutes,
        refresh_token_days=settings.refresh_token_days,
    )


def get_verify_email_use_case(session: SessionDep) -> VerifyEmailUseCase:
    return VerifyEmailUseCase(
        users=SqlAlchemyUserRepository(session),
        verifications=SqlAlchemyEmailVerificationRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


LoginUseCaseDep = Annotated[LoginUseCase, Depends(get_login_use_case)]
SignupUseCaseDep = Annotated[SignupUseCase, Depends(get_signup_use_case)]
VerifyEmailUseCaseDep = Annotated[VerifyEmailUseCase, Depends(get_verify_email_use_case)]


async def get_current_user(request: Request, session: SessionDep) -> User:
    """El usuario dueno de la peticion, a partir de la cookie de sesion.

    Es la unica puerta de entrada a una ruta protegida: quien la pide como
    dependencia recibe un User real o no llega a ejecutarse nunca.

    No atrapa los errores del token a proposito. `decode_access_token` distingue
    expirado (410) de invalido (400), y esa diferencia es justo la que necesita
    el cliente para decidir entre pedir un refresh o mandar al login. Envolverlos
    en un 401 unico la borraria.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if token is None:
        # Sin cookie no hay nada que verificar: no es un token malo, es la
        # ausencia de sesion. 401 y no 400.
        raise UnauthorizedError("No active session")

    user_id = decode_access_token(
        token,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    try:
        user_uuid = UUID(user_id)
    except ValueError as exc:
        # El `sub` lo escribimos nosotros, asi que llegar aqui significa que el
        # token venia manipulado o de otra version del sistema. Firma valida no
        # implica contenido con sentido.
        raise InvalidTokenError("The token subject is not a valid user id") from exc

    usuario = await SqlAlchemyUserRepository(session).get_by_id(user_uuid)
    if usuario is None:
        # El token es autentico pero su dueno ya no existe: cuenta borrada con
        # un access token todavia vivo. Esta consulta es la razon de que un
        # borrado surta efecto al instante en vez de esperar a que caduque.
        raise UnauthorizedError("No active session")

    return usuario


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_authenticated_session(
    usuario: CurrentUserDep, session: SessionDep
) -> AsyncSession:
    """La sesion de la peticion, declarando ante Postgres quien la usa.

    Es la MISMA sesion que devuelve get_session: FastAPI cachea cada dependencia
    dentro de una peticion, asi que get_session se ejecuta una sola vez por muy
    a menudo que se pida. Aqui no se abre una conexion nueva, se le anade un dato
    a la que ya hay.

    Ese dato es lo que leen las politicas RLS de la base:

        USING (id = current_setting('app.current_user_id', true)::uuid)

    Sin el, la politica compara contra vacio y la consulta no devuelve nada. Con
    el, Postgres filtra por usuario aunque el SELECT no lleve WHERE. Es una
    segunda barrera por debajo de la aplicacion: aunque un endpoint se olvidara
    de filtrar, la base no devolveria filas ajenas.

    Se separa de get_session a proposito. Las rutas publicas —signup, login—
    corren antes de que exista un usuario y usan la de siempre; las protegidas
    piden esta, y asi es imposible olvidarse del SET: o hay usuario, o no hay
    sesion que dar.

    OJO con dos cosas:

    - `set_config(clave, valor, true)` y no `SET LOCAL x = :uid`. SET es una
      sentencia de configuracion y no admite parametros; con asyncpg revienta
      con 'syntax error at or near "$1"'.

    - El `true` final significa LOCAL: el valor vive hasta el final de la
      transaccion actual. Si el caso de uso hace commit y despues sigue
      consultando, esas consultas ya no lo tienen. Hoy no pasa —se confirma al
      final—, pero es la trampa a vigilar cuando aparezca un endpoint que
      escriba y lea despues.

    Recordatorio: mientras la API se conecte como `flash`, que es dueno de las
    tablas y superusuario, la RLS no se aplica y esto no filtra nada todavia.
    Queda listo para el rol `flash_app` (ver deployment-decisions.md).
    """
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(usuario.id)},
    )
    return session


AuthSessionDep = Annotated[AsyncSession, Depends(get_authenticated_session)]


def get_me_use_case(session: AuthSessionDep) -> GetMeUseCase:
    # AuthSessionDep y no SessionDep: es la sesion que ya declaro quien es el
    # usuario ante Postgres. Sin eso, la politica RLS de profiles no devolveria
    # ninguna fila y el perfil llegaria vacio sin que nada fallara.
    return GetMeUseCase(profiles=SqlAlchemyProfileRepository(session))


GetMeUseCaseDep = Annotated[GetMeUseCase, Depends(get_me_use_case)]
