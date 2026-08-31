"""Dobles en memoria de los repositorios.

Cumplen los mismos puertos que las implementaciones de SQLAlchemy, pero guardan
en diccionarios. Permiten probar un caso de uso entero sin levantar Postgres.

Un doble que no imita el comportamiento real es peor que no tenerlo: hace pasar
tests que fallarian contra la base. Los comportamientos que si se replican estan
comentados uno por uno.

Lo que NO imitan: transacciones. Un diccionario no tiene atomicidad, asi que
estos dobles no pueden demostrar que el signup sea atomico. Eso necesita un test
de integracion contra Postgres.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from packages.core.domain.entities import (
    EmailVerification,
    Profile,
    RefreshToken,
    User,
)
from packages.core.domain.repositories import (
    EmailVerificationRepository,
    ProfileRepository,
    RefreshTokenRepository,
    UserRepository,
)


def _ahora() -> datetime:
    return datetime.now(UTC)


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self.por_id: dict[UUID, User] = {}

    async def get_by_email(self, email: str) -> User | None:
        # .lower() en ambos lados: la columna real es CITEXT y compara sin
        # distinguir mayusculas. Sin esto, el test de email duplicado pasaria
        # en falso y en produccion se crearian dos cuentas para el mismo correo.
        objetivo = email.lower()
        return next(
            (u for u in self.por_id.values() if u.email.lower() == objetivo), None
        )

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.por_id.get(user_id)

    async def create(self, user_id: UUID, email: str, password_hash: str) -> User:
        ahora = _ahora()
        usuario = User(
            id=user_id,
            email=email,
            password_hash=password_hash,
            email_verified=False,
            created_at=ahora,
            updated_at=ahora,
        )
        self.por_id[user_id] = usuario
        return usuario

    async def mark_email_verified(self, user_id: UUID) -> None:
        # Las entidades son frozen: se reemplaza por una copia en vez de mutar.
        viejo = self.por_id[user_id]
        self.por_id[user_id] = replace(
            viejo, email_verified=True, updated_at=_ahora()
        )

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        viejo = self.por_id[user_id]
        self.por_id[user_id] = replace(
            viejo, password_hash=password_hash, updated_at=_ahora()
        )


class InMemoryProfileRepository(ProfileRepository):
    def __init__(self) -> None:
        self.por_id: dict[UUID, Profile] = {}

    async def create(self, user_id: UUID, display_name: str | None = None) -> Profile:
        # Sin SET LOCAL, y esta bien: la RLS es un detalle de la implementacion
        # de Postgres, no parte del contrato. Que el doble no tenga que fingirlo
        # es la senal de que la abstraccion quedo en el sitio correcto.
        ahora = _ahora()
        perfil = Profile(
            id=user_id,
            display_name=display_name,
            settings={},
            created_at=ahora,
            updated_at=ahora,
        )
        self.por_id[user_id] = perfil
        return perfil

    async def get_by_id(self, user_id: UUID) -> Profile | None:
        return self.por_id.get(user_id)


class InMemoryRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self) -> None:
        self.por_id: dict[UUID, RefreshToken] = {}
        # El hash no vive en la entidad, asi que el doble lo indexa aparte,
        # igual que la columna token_hash de la tabla real.
        self.id_por_hash: dict[str, UUID] = {}

    async def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        family_id: UUID,
        expires_at: datetime,
        user_agent: str | None,
    ) -> RefreshToken:
        token = RefreshToken(
            id=uuid4(),
            user_id=user_id,
            family_id=family_id,
            expires_at=expires_at,
            used_at=None,
            revoked_at=None,
            created_at=_ahora(),
        )
        self.por_id[token.id] = token
        self.id_por_hash[token_hash] = token.id
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        # Devuelve el token aunque este usado o revocado, igual que el real: la
        # deteccion de reutilizacion necesita encontrarlo para poder reaccionar.
        token_id = self.id_por_hash.get(token_hash)
        return self.por_id.get(token_id) if token_id else None

    async def mark_used(self, token_id: UUID) -> None:
        viejo = self.por_id[token_id]
        self.por_id[token_id] = replace(viejo, used_at=_ahora())

    async def revoke_family(self, family_id: UUID) -> None:
        ahora = _ahora()
        for token_id, token in self.por_id.items():
            # No se pisa un revoked_at existente: la fecha original es la que
            # sirve para investigar despues.
            if token.family_id == family_id and token.revoked_at is None:
                self.por_id[token_id] = replace(token, revoked_at=ahora)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        ahora = _ahora()
        for token_id, token in self.por_id.items():
            if token.user_id == user_id and token.revoked_at is None:
                self.por_id[token_id] = replace(token, revoked_at=ahora)


class InMemoryEmailVerificationRepository(EmailVerificationRepository):
    def __init__(self) -> None:
        self.por_id: dict[UUID, EmailVerification] = {}
        self.id_por_hash: dict[str, UUID] = {}

    async def create(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> EmailVerification:
        verificacion = EmailVerification(
            id=uuid4(),
            user_id=user_id,
            expires_at=expires_at,
            used_at=None,
            created_at=_ahora(),
        )
        self.por_id[verificacion.id] = verificacion
        self.id_por_hash[token_hash] = verificacion.id
        return verificacion

    async def get_by_hash(self, token_hash: str) -> EmailVerification | None:
        token_id = self.id_por_hash.get(token_hash)
        return self.por_id.get(token_id) if token_id else None

    async def mark_used(self, token_id: UUID) -> None:
        viejo = self.por_id[token_id]
        self.por_id[token_id] = replace(viejo, used_at=_ahora())
