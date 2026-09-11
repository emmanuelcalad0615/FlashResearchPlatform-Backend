"""RLS: las politicas de profiles toleran app.current_user_id en blanco

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


# PROBLEMA QUE ARREGLA
#
# Las politicas de 0001 y 0004 hacen:
#
#     current_setting('app.current_user_id', true)::uuid
#
# El segundo argumento en true evita el error cuando la variable NUNCA se
# declaro: current_setting devuelve NULL, el cast da NULL, y la comparacion
# `id = NULL` no encuentra filas. Ese camino esta bien.
#
# Lo que no esta cubierto es la variable declarada VACIA. Postgres distingue
# los dos casos:
#
#     nunca declarada                 -> NULL
#     RESET app.current_user_id       -> ''      <- no NULL
#     set_config(..., NULL, true)     -> ''
#
# Y ''::uuid no es NULL, es un error:
#
#     invalid input syntax for type uuid: ""
#
# Consecuencia: cualquier consulta a profiles sobre una conexion donde la
# variable quedo en blanco revienta con un error de Postgres —un 500— en vez
# de responder "no hay filas", que es la respuesta correcta cuando nadie se ha
# identificado. Una politica de seguridad no deberia poder tumbar la peticion.
#
# NULLIF(x, '') convierte la cadena vacia en NULL y unifica los dos caminos.
#
# Se descubrio escribiendo los tests de GET /api/auth/me: para simular una
# conexion limpia en cada peticion hay que limpiar la variable, y la unica
# forma de limpiarla produce justo el valor que rompia el cast.

_EXPRESION_NUEVA = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_EXPRESION_VIEJA = "current_setting('app.current_user_id', true)::uuid"


def _recrear_politicas(expresion: str) -> None:
    """Reescribe las cuatro politicas de profiles con la expresion dada.

    En Postgres no existe CREATE OR REPLACE POLICY, y ALTER POLICY solo admite
    cambiar USING/WITH CHECK una a una. Borrar y volver a crear es lo mas
    legible, y se hace dentro de la transaccion de la migracion: no hay una
    ventana en la que la tabla quede sin proteger.
    """
    op.execute("DROP POLICY IF EXISTS profiles_select_own ON profiles;")
    op.execute("DROP POLICY IF EXISTS profiles_update_own ON profiles;")
    op.execute("DROP POLICY IF EXISTS profiles_insert_self ON profiles;")
    op.execute("DROP POLICY IF EXISTS profiles_delete_own ON profiles;")

    op.execute(f"""
        CREATE POLICY profiles_select_own ON profiles
            FOR SELECT USING (id = {expresion});
    """)
    op.execute(f"""
        CREATE POLICY profiles_update_own ON profiles
            FOR UPDATE USING (id = {expresion});
    """)
    op.execute(f"""
        CREATE POLICY profiles_insert_self ON profiles
            FOR INSERT WITH CHECK (id = {expresion});
    """)
    op.execute(f"""
        CREATE POLICY profiles_delete_own ON profiles
            FOR DELETE USING (id = {expresion});
    """)


def upgrade() -> None:
    _recrear_politicas(_EXPRESION_NUEVA)


def downgrade() -> None:
    # Deja las politicas exactamente como las dejo 0004, error de cast incluido.
    _recrear_politicas(_EXPRESION_VIEJA)
