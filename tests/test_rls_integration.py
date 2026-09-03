"""Verifica que la Row Level Security filtre de verdad.

Estos tests SOLO valen conectados con un rol sin privilegios de superusuario.
Con `flash` pasarian en falso: los superusuarios se saltan la RLS
incondicionalmente, asi que cualquier asercion sobre "la RLS protege" seria
verde sin demostrar nada. Por eso usan la fixture rls_session.

OJO: que estos tests pasen NO significa que la aplicacion este protegida. La
API sigue conectandose como `flash`. Cierran la pregunta "¿la RLS funciona?",
no "¿la app la usa?" — eso necesita el rol flash_app de produccion, anotado en
deployment-decisions.md.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.conftest import requiere_bd

pytestmark = [pytest.mark.integration, requiere_bd]


async def _crear_usuario(sesion, email: str) -> uuid.UUID:
    """users no tiene RLS: el rol restringido puede insertar sin declarar nada."""
    user_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (:i, :e, 'x')"),
        {"i": user_id, "e": email},
    )
    return user_id


async def _declarar(sesion, user_id: uuid.UUID) -> None:
    # set_config y no SET LOCAL: SET no admite parametros enlazados.
    await sesion.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )


async def test_without_declaring_a_user_nothing_is_visible(rls_session):
    """La politica compara contra app.current_user_id. Sin declararla, no hay
    con que comparar y no se devuelve ninguna fila."""
    ana = await _crear_usuario(rls_session, f"ana-{uuid.uuid4()}@ejemplo.com")
    await _declarar(rls_session, ana)
    await rls_session.execute(text("INSERT INTO profiles (id) VALUES (:i)"), {"i": ana})
    await rls_session.commit()

    try:
        # Transaccion nueva: SET LOCAL murio con el commit anterior.
        visibles = await rls_session.scalar(text("SELECT count(*) FROM profiles"))

        assert visibles == 0
    finally:
        await rls_session.rollback()
        await _declarar(rls_session, ana)
        await rls_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": ana})
        await rls_session.commit()


async def test_only_the_own_profile_is_visible(rls_session):
    ana = await _crear_usuario(rls_session, f"ana-{uuid.uuid4()}@ejemplo.com")
    carlos = await _crear_usuario(rls_session, f"carlos-{uuid.uuid4()}@ejemplo.com")

    for quien in (ana, carlos):
        await _declarar(rls_session, quien)
        await rls_session.execute(
            text("INSERT INTO profiles (id) VALUES (:i)"), {"i": quien}
        )
    await rls_session.commit()

    try:
        await _declarar(rls_session, ana)
        filas = (await rls_session.execute(text("SELECT id FROM profiles"))).scalars().all()

        # Existen dos perfiles en la tabla; Ana solo puede ver el suyo.
        assert filas == [ana]
    finally:
        await rls_session.rollback()
        for quien in (ana, carlos):
            await _declarar(rls_session, quien)
            await rls_session.execute(
                text("DELETE FROM users WHERE id = :i"), {"i": quien}
            )
        await rls_session.commit()


async def test_cannot_create_a_profile_for_someone_else(rls_session):
    """EL test que importa.

    Declarandose como Ana, intentar crear el perfil de Carlos. Conectado como
    `flash` esto FUNCIONA —y es justo el agujero que la RLS deberia tapar—;
    con el rol restringido, Postgres lo rechaza.
    """
    ana = await _crear_usuario(rls_session, f"ana-{uuid.uuid4()}@ejemplo.com")
    carlos = await _crear_usuario(rls_session, f"carlos-{uuid.uuid4()}@ejemplo.com")
    await rls_session.commit()

    try:
        await _declarar(rls_session, ana)

        with pytest.raises((DBAPIError, ProgrammingError)) as capturado:
            await rls_session.execute(
                text("INSERT INTO profiles (id) VALUES (:i)"), {"i": carlos}
            )
            await rls_session.flush()

        assert "policy" in str(capturado.value).lower()
    finally:
        await rls_session.rollback()
        for quien in (ana, carlos):
            await _declarar(rls_session, quien)
            await rls_session.execute(
                text("DELETE FROM users WHERE id = :i"), {"i": quien}
            )
        await rls_session.commit()
