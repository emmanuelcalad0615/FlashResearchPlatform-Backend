"""force RLS on profiles so it also applies to the table owner

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-27
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PROBLEMA QUE ARREGLA
    #
    # En PostgreSQL, el DUENO de una tabla se salta la RLS por defecto. La
    # migracion 0001 habilito RLS en profiles y escribio sus politicas, pero
    # alembic crea las tablas como `flash` y la API se conecta como el mismo
    # `flash`. Al ser dueña, la aplicacion nunca queda sujeta a las politicas:
    # estaban escritas y habilitadas, pero no filtraban nada.
    #
    # FORCE hace que la RLS se aplique tambien al dueno, sin excepciones.
    op.execute("ALTER TABLE profiles FORCE ROW LEVEL SECURITY;")

    # Politica de DELETE, que faltaba. Sin ella, con FORCE activo nadie podria
    # borrar un perfil: RLS deniega por defecto toda operacion sin politica
    # que la permita. Hace falta para el borrado de cuenta.
    op.execute("""
        CREATE POLICY profiles_delete_own ON profiles
            FOR DELETE USING (id = current_setting('app.current_user_id', true)::uuid);
    """)

    # instruments NO lleva FORCE, a proposito.
    #
    # Su unica politica es `instruments_read_all` (SELECT). Con FORCE, el dueno
    # perderia INSERT y UPDATE, y el job de sincronizacion del catalogo
    # (HU-B02) no podria escribir. El diseno de 0001 ya era ese: lectura
    # publica, escritura solo con el rol de servicio.
    #
    # Su RLS queda como preparacion para cuando exista un rol de aplicacion
    # separado del dueno (ver deployment-decisions.md, decisiones pendientes).


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS profiles_delete_own ON profiles;")
    op.execute("ALTER TABLE profiles NO FORCE ROW LEVEL SECURITY;")
