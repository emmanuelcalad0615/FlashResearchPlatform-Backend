"""initial schema: profiles, instruments, RLS

Revision ID: 0001
Revises:
Create Date: 2026-06-19
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE EXTENSION IF NOT EXISTS timescaledb;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TABLE profiles (
            id            UUID PRIMARY KEY,
            display_name  TEXT,
            settings      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("COMMENT ON TABLE profiles IS 'Perfil de usuario, 1:1 con el usuario de auth.';")
    op.execute("COMMENT ON COLUMN profiles.settings IS 'Preferencias de app del usuario (JSONB).';")

    op.execute("""
        CREATE TRIGGER trg_profiles_updated
            BEFORE UPDATE ON profiles
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    op.execute("""
        CREATE TABLE instruments (
            ticker       TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            exchange     TEXT NOT NULL CHECK (exchange IN ('NYSE','NASDAQ','AMEX','OTHER')),
            sector       TEXT,
            industry     TEXT,
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            delisted_at  DATE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        COMMENT ON COLUMN instruments.is_active IS
            'FALSE = delistado. NUNCA se borra: evita survivorship bias en backtests futuros.';
    """)

    op.execute("CREATE INDEX idx_instruments_active   ON instruments (is_active) WHERE is_active;")
    op.execute("CREATE INDEX idx_instruments_exchange ON instruments (exchange);")
    op.execute("CREATE INDEX idx_instruments_sector   ON instruments (sector);")

    op.execute("""
        CREATE TRIGGER trg_instruments_updated
            BEFORE UPDATE ON instruments
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # RLS — profiles: cada usuario solo ve/edita su propia fila.
    # El backend setea: SET LOCAL app.current_user_id = '<uuid>';
    # Si se migra a Supabase Auth, reemplazar current_setting(...) por auth.uid().
    op.execute("ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;")

    op.execute("""
        CREATE POLICY profiles_select_own ON profiles
            FOR SELECT USING (id = current_setting('app.current_user_id', true)::uuid);
    """)
    op.execute("""
        CREATE POLICY profiles_update_own ON profiles
            FOR UPDATE USING (id = current_setting('app.current_user_id', true)::uuid);
    """)
    op.execute("""
        CREATE POLICY profiles_insert_self ON profiles
            FOR INSERT WITH CHECK (id = current_setting('app.current_user_id', true)::uuid);
    """)

    # RLS — instruments: catálogo público de lectura.
    # Escritura solo con rol de servicio (bypassa RLS).
    op.execute("ALTER TABLE instruments ENABLE ROW LEVEL SECURITY;")

    op.execute("""
        CREATE POLICY instruments_read_all ON instruments
            FOR SELECT USING (true);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS instruments_read_all ON instruments;")
    op.execute("ALTER TABLE instruments DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP POLICY IF EXISTS profiles_insert_self ON profiles;")
    op.execute("DROP POLICY IF EXISTS profiles_update_own ON profiles;")
    op.execute("DROP POLICY IF EXISTS profiles_select_own ON profiles;")
    op.execute("ALTER TABLE profiles DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP TABLE IF EXISTS instruments;")
    op.execute("DROP TABLE IF EXISTS profiles;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at;")
