"""Tests del caso de uso de registro.

Sin Postgres, sin servidor de correo, sin app de FastAPI. Es la prueba de que
la arquitectura sirve: toda la logica del signup —sus tres ramas, su orden y
sus reglas de seguridad— se verifica con diccionarios en memoria.
"""

import pytest

from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.domain.errors import DomainValidationError
from packages.core.domain.policies.tokens import hash_opaque_token
from tests.fakes import (
    FailingEmailSender,
    InMemoryEmailSender,
    InMemoryEmailVerificationRepository,
    InMemoryProfileRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

EMAIL = "ana@ejemplo.com"
PASSWORD = "una-frase-larga-y-seguraB"


class _Contexto:
    """Agrupa los dobles para poder inspeccionarlos despues del caso de uso."""

    def __init__(self, *, emails=None) -> None:
        self.users = InMemoryUserRepository()
        self.profiles = InMemoryProfileRepository()
        self.verifications = InMemoryEmailVerificationRepository()
        self.emails = emails or InMemoryEmailSender()
        self.uow = InMemoryUnitOfWork()
        self.caso = SignupUseCase(
            users=self.users,
            profiles=self.profiles,
            verifications=self.verifications,
            emails=self.emails,
            uow=self.uow,
            verification_hours=24,
            build_link=lambda t: f"https://app.test/verify?token={t}",
            # Hasher instantaneo: los 70 ms de Argon2 no aportan nada aqui y se
            # multiplicarian por cada test.
            hasher=lambda p: f"hash::{p}",
        )


@pytest.fixture
def ctx() -> _Contexto:
    return _Contexto()


# ---- Camino feliz ----------------------------------------------------------


async def test_creates_user_profile_and_token(ctx):
    await ctx.caso.execute(EMAIL, PASSWORD)

    usuario = await ctx.users.get_by_email(EMAIL)
    assert usuario is not None
    assert usuario.email_verified is False
    assert await ctx.profiles.get_by_id(usuario.id) is not None
    assert len(ctx.verifications.por_id) == 1


async def test_the_profile_shares_the_user_id(ctx):
    await ctx.caso.execute(EMAIL, PASSWORD)

    usuario = await ctx.users.get_by_email(EMAIL)
    perfil = await ctx.profiles.get_by_id(usuario.id)

    assert perfil.id == usuario.id


async def test_only_the_token_hash_is_stored(ctx):
    """Si roban un volcado de la base, no puede servir para verificar cuentas."""
    await ctx.caso.execute(EMAIL, PASSWORD)

    _, enlace = ctx.emails.verificaciones[0]
    token = enlace.split("token=")[1]

    assert hash_opaque_token(token) in ctx.verifications.id_por_hash
    assert token not in ctx.verifications.id_por_hash


# ---- Politica de contrasena ------------------------------------------------


async def test_a_weak_password_is_rejected_before_touching_anything(ctx):
    with pytest.raises(DomainValidationError):
        await ctx.caso.execute(EMAIL, "corta")

    assert ctx.users.por_id == {}
    assert ctx.uow.commits == 0
    assert ctx.emails.verificaciones == []


# ---- Rama: el email existe y esta verificado -------------------------------


async def _usuario_verificado(ctx) -> None:
    await ctx.caso.execute(EMAIL, PASSWORD)
    usuario = await ctx.users.get_by_email(EMAIL)
    await ctx.users.mark_email_verified(usuario.id)
    ctx.emails.verificaciones.clear()
    ctx.emails.avisos_ya_registrado.clear()


async def test_an_existing_verified_account_is_never_modified(ctx):
    """SEGURIDAD: esa cuenta es de alguien. No se toca su contrasena."""
    await _usuario_verificado(ctx)
    hash_original = (await ctx.users.get_by_email(EMAIL)).password_hash

    await ctx.caso.execute(EMAIL, "otra-frase-completamente-distinta")

    assert (await ctx.users.get_by_email(EMAIL)).password_hash == hash_original
    assert len(ctx.users.por_id) == 1


async def test_an_existing_verified_account_gets_a_notice_not_a_token(ctx):
    """SEGURIDAD: mandar el correo de verificacion aqui entregaria un token
    valido de una cuenta ajena a quien solo escribio su direccion."""
    await _usuario_verificado(ctx)

    await ctx.caso.execute(EMAIL, "otra-frase-completamente-distinta")

    assert ctx.emails.avisos_ya_registrado == [EMAIL]
    assert ctx.emails.verificaciones == []


# ---- Rama: el email existe SIN verificar -----------------------------------


async def test_an_unverified_account_is_taken_over(ctx):
    """Mitigacion del account squatting: quien controla el buzon se la queda."""
    await ctx.caso.execute(EMAIL, PASSWORD)
    id_original = (await ctx.users.get_by_email(EMAIL)).id

    await ctx.caso.execute(EMAIL, "la-frase-del-dueno-real")

    usuario = await ctx.users.get_by_email(EMAIL)
    assert usuario.id == id_original                    # no se duplica la fila
    assert usuario.password_hash == "hash::la-frase-del-dueno-real"
    assert len(ctx.users.por_id) == 1


async def test_taking_over_issues_a_new_verification_token(ctx):
    await ctx.caso.execute(EMAIL, PASSWORD)
    primer_enlace = ctx.emails.verificaciones[0][1]

    await ctx.caso.execute(EMAIL, "la-frase-del-dueno-real")

    segundo_enlace = ctx.emails.verificaciones[1][1]
    assert segundo_enlace != primer_enlace


# ---- Lo indistinguible -----------------------------------------------------


@pytest.mark.parametrize("preparar", ["nuevo", "sin_verificar", "verificado"])
async def test_every_branch_returns_the_same(preparar, ctx):
    """Si una rama fallara y otra no, cualquiera podria averiguar quien tiene
    cuenta probando direcciones."""
    if preparar == "sin_verificar":
        await ctx.caso.execute(EMAIL, PASSWORD)
    elif preparar == "verificado":
        await _usuario_verificado(ctx)

    resultado = await ctx.caso.execute(EMAIL, PASSWORD)

    assert resultado is None


# ---- Orden y tolerancia a fallos -------------------------------------------


async def test_the_email_goes_out_after_the_commit(ctx):
    """Si saliera antes y el commit fallara, habriamos mandado un enlace a un
    usuario que no llego a existir."""
    momentos: list[str] = []

    commit_original = ctx.uow.commit

    async def commit_espia() -> None:
        momentos.append("commit")
        await commit_original()

    send_original = ctx.emails.send_verification

    async def send_espia(**kwargs) -> None:
        momentos.append("correo")
        await send_original(**kwargs)

    ctx.uow.commit = commit_espia
    ctx.emails.send_verification = send_espia
    ctx.caso._uow = ctx.uow
    ctx.caso._emails = ctx.emails

    await ctx.caso.execute(EMAIL, PASSWORD)

    assert momentos == ["commit", "correo"]


async def test_a_failing_email_does_not_undo_the_signup():
    """Decision B: el usuario YA existe. Devolverle un error lo dejaria
    creyendo que no se registro, y al reintentar recibiria "ya existe"."""
    ctx = _Contexto(emails=FailingEmailSender())

    await ctx.caso.execute(EMAIL, PASSWORD)      # no lanza

    assert await ctx.users.get_by_email(EMAIL) is not None
    assert ctx.uow.commits == 1
