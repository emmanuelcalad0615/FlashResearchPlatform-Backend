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
from packages.core.application.usecases.auth.logout import LogoutUseCase
from packages.core.application.usecases.auth.logout_all import LogoutAllUseCase
from packages.core.application.usecases.auth.me import GetMeUseCase
from packages.core.application.usecases.auth.refresh import RefreshUseCase
from packages.core.application.usecases.auth.resend_verification import (
    ResendVerificationUseCase,
)
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase
from packages.core.domain.entities import User
from packages.core.domain.errors import InvalidTokenError, UnauthorizedError
from packages.core.domain.policies.tokens import (
    AccessTokenClaims,
    decode_access_token,
)
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


def get_refresh_use_case(session: SessionDep) -> RefreshUseCase:
    # SessionDep y no AuthSessionDep: el refresh corre ANTES de que exista un
    # usuario autenticado —para eso esta— y solo toca refresh_tokens, que no
    # lleva RLS.
    return RefreshUseCase(
        tokens=SqlAlchemyRefreshTokenRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
        access_token_minutes=settings.access_token_minutes,
        refresh_token_days=settings.refresh_token_days,
    )


def get_resend_verification_use_case(
    session: SessionDep,
    emails: EmailSenderDep,
) -> ResendVerificationUseCase:
    return ResendVerificationUseCase(
        users=SqlAlchemyUserRepository(session),
        verifications=SqlAlchemyEmailVerificationRepository(session),
        emails=emails,
        uow=SqlAlchemyUnitOfWork(session),
        verification_hours=settings.email_verification_hours,
        cooldown_seconds=settings.resend_verification_cooldown_seconds,
        build_link=emails.build_verification_link,
    )


def get_verify_email_use_case(session: SessionDep) -> VerifyEmailUseCase:
    return VerifyEmailUseCase(
        users=SqlAlchemyUserRepository(session),
        verifications=SqlAlchemyEmailVerificationRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


LoginUseCaseDep = Annotated[LoginUseCase, Depends(get_login_use_case)]
RefreshUseCaseDep = Annotated[RefreshUseCase, Depends(get_refresh_use_case)]
SignupUseCaseDep = Annotated[SignupUseCase, Depends(get_signup_use_case)]
VerifyEmailUseCaseDep = Annotated[VerifyEmailUseCase, Depends(get_verify_email_use_case)]
ResendVerificationUseCaseDep = Annotated[
    ResendVerificationUseCase, Depends(get_resend_verification_use_case)
]


async def get_access_claims(request: Request) -> AccessTokenClaims:
    """Los claims del access token de la peticion, ya verificados.

    Separada de get_current_user porque hay rutas que necesitan un dato del
    token que no esta en el usuario: el cierre de sesion usa `fid` para saber
    que familia revocar. Sin esta dependencia habria que descifrar el token dos
    veces o pasear el objeto a mano.

    No consulta la base. Quien necesite al usuario pide get_current_user, que
    se construye sobre esta.

    ES `async def` AUNQUE NO ESPERE NADA, y es deliberado. FastAPI ejecuta las
    dependencias sincronas en un threadpool y las asincronas en el event loop
    directamente; quitar el `async` mandaria a un hilo aparte una funcion que
    solo lee una cookie y verifica una firma, en CADA peticion autenticada.
    Seria mas lento, no mas limpio.

    SonarQube lo marca con python:S7503. Queda excluido en
    sonar-project.properties, por regla y por archivo, junto a la misma
    excepcion que ya tenia error_handlers.py. No se silencia con `# NOSONAR`
    porque en Python ese comentario apaga TODAS las reglas de la linea,
    incluida cualquier regla de seguridad futura sobre la lectura de la
    cookie.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if token is None:
        raise UnauthorizedError("No active session")

    return decode_access_token(
        token,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


AccessClaimsDep = Annotated[AccessTokenClaims, Depends(get_access_claims)]


async def get_current_user(claims: AccessClaimsDep, session: SessionDep) -> User:
    """El usuario dueno de la peticion, a partir de la cookie de sesion.

    Es la unica puerta de entrada a una ruta protegida: quien la pide como
    dependencia recibe un User real o no llega a ejecutarse nunca.

    La verificacion del token la hace get_access_claims; aqui solo se traduce
    el sujeto a un usuario real. Los errores de token suben desde alli sin
    atraparse: `decode_access_token` distingue expirado (410) de invalido
    (400), y esa diferencia es justo la que necesita el cliente para decidir
    entre pedir un refresh o mandar al login. Envolverlos en un 401 unico la
    borraria.
    """
    try:
        user_uuid = UUID(claims.user_id)
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


def get_logout_use_case(session: SessionDep) -> LogoutUseCase:
    # SessionDep y no AuthSessionDep, igual que el refresh: solo se toca
    # refresh_tokens, que no lleva RLS. Quien autentica la ruta es la
    # dependencia de claims, no esta.
    return LogoutUseCase(
        tokens=SqlAlchemyRefreshTokenRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_logout_all_use_case(session: SessionDep) -> LogoutAllUseCase:
    return LogoutAllUseCase(
        tokens=SqlAlchemyRefreshTokenRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


LogoutUseCaseDep = Annotated[LogoutUseCase, Depends(get_logout_use_case)]
LogoutAllUseCaseDep = Annotated[LogoutAllUseCase, Depends(get_logout_all_use_case)]
