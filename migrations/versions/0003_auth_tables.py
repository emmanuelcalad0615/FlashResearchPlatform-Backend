"""auth: users, refresh_tokens, email_verification_tokens

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-27
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CITEXT: texto case-insensitive. Sin esto, Ana@x.com y ana@x.com serian
    # cuentas distintas, y el usuario que se registra con mayusculas no podria
    # volver a entrar escribiendo su email en minusculas.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext;")

    # ---- users ------------------------------------------------------------
    # Identidad. Separada de `profiles`, que guarda preferencias de la app:
    # una cambia por seguridad, la otra por gusto del usuario.
    op.execute("""
        CREATE TABLE users (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email           CITEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("COMMENT ON TABLE users IS 'Credenciales de acceso. 1:1 con profiles.';")
    op.execute("""
        COMMENT ON COLUMN users.password_hash IS
            'Argon2id. NUNCA la contrasena en claro.';
    """)
    op.execute("""
        COMMENT ON COLUMN users.email_verified IS
            'FALSE hasta que el usuario abre el enlace enviado por correo.';
    """)

    op.execute("""
        CREATE TRIGGER trg_users_updated
            BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ---- refresh_tokens ---------------------------------------------------
    # Un refresh token vivo. Se guarda HASHEADO, igual que una contrasena: si
    # roban un volcado de la base, los tokens no sirven para entrar.
    op.execute("""
        CREATE TABLE refresh_tokens (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash   TEXT NOT NULL UNIQUE,
            family_id    UUID NOT NULL,
            expires_at   TIMESTAMPTZ NOT NULL,
            used_at      TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ,
            user_agent   TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        COMMENT ON COLUMN refresh_tokens.family_id IS
            'Cadena de rotacion. Cada login abre una familia; cada refresh la hereda.
             Si se reusa un token ya usado, se revoca la familia entera: el robo se
             detecta solo, sin que nadie lo denuncie.';
    """)
    op.execute("""
        COMMENT ON COLUMN refresh_tokens.used_at IS
            'NULL = sin usar. Un refresh token vale exactamente UN uso.';
    """)

    # Buscar por hash es la operacion caliente: ocurre en cada refresh.
    # El UNIQUE de token_hash ya crea su indice, no hace falta otro.
    op.execute("CREATE INDEX idx_refresh_tokens_user   ON refresh_tokens (user_id);")
    op.execute("CREATE INDEX idx_refresh_tokens_family ON refresh_tokens (family_id);")
    op.execute("""
        CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens (expires_at)
            WHERE revoked_at IS NULL;
    """)

    # ---- email_verification_tokens ---------------------------------------
    # Enlaces de verificacion. Tambien hasheados y de un solo uso.
    op.execute("""
        CREATE TABLE email_verification_tokens (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  TEXT NOT NULL UNIQUE,
            expires_at  TIMESTAMPTZ NOT NULL,
            used_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        COMMENT ON TABLE email_verification_tokens IS
            'Token aleatorio, NO un JWT: asi se puede revocar y es de un solo uso.';
    """)

    op.execute("""
        CREATE INDEX idx_email_verification_user ON email_verification_tokens (user_id);
    """)

    # ---- profiles gana su integridad referencial --------------------------
    # profiles.id existia como UUID suelto porque no habia tabla de usuarios.
    # Ahora apunta a users: no puede haber perfil sin usuario, y borrar el
    # usuario arrastra su perfil.
    #
    # NOTA: si la base ya tuviera filas en profiles sin usuario correspondiente,
    # esta migracion falla. Es lo correcto: avisa de datos huerfanos en vez de
    # ocultarlos.
    op.execute("""
        ALTER TABLE profiles
            ADD CONSTRAINT fk_profiles_user
            FOREIGN KEY (id) REFERENCES users(id) ON DELETE CASCADE;
    """)

    # ---- RLS ---------------------------------------------------------------
    # Las tres tablas de auth NO llevan RLS, a proposito.
    #
    # Solo las toca el propio servicio de autenticacion, que corre antes de que
    # exista una sesion. Ponerles RLS crearia un problema del huevo y la gallina:
    # para leer al usuario habria que saber ya quien es el usuario.
    #
    # La proteccion aqui es de aplicacion: ningun router expone estas tablas.


def downgrade() -> None:
    # Orden inverso: primero la FK, luego las tablas que dependen de users,
    # y users al final.
    op.execute("ALTER TABLE profiles DROP CONSTRAINT IF EXISTS fk_profiles_user;")

    op.execute("DROP TABLE IF EXISTS email_verification_tokens;")
    op.execute("DROP TABLE IF EXISTS refresh_tokens;")
    op.execute("DROP TABLE IF EXISTS users;")

    # La extension citext NO se borra: puede haberla creado otra cosa, o quedar
    # otras columnas usandola. Quitarla seria destructivo mas alla del alcance
    # de esta migracion.
