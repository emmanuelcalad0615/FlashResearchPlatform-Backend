from dataclasses import dataclass
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_MIN_JWT_SECRET_LENGTH = 32


@dataclass(frozen=True)
class RateLimitRule:
    """Un limite propio para las rutas que cuelgan de `prefix`.

    Cada regla lleva su PROPIO contador en Redis. Si compartieran uno, los
    cinco intentos de login se restarian del cupo general y navegar por el
    dashboard gastaria el cupo de login: ninguno de los dos limites
    significaria lo que dice.

    Vive en config y no junto al middleware por un ciclo de importacion: el
    middleware depende del logging, que depende de Settings. La regla es la
    FORMA de un ajuste de configuracion, asi que este es su sitio.
    """

    prefix: str
    limit: int
    window_seconds: int

    @property
    def scope(self) -> str:
        """Identificador de la regla dentro de la clave de Redis.

        Se deriva del prefijo y no del limite, asi que ajustar un limite en
        produccion NO reinicia los contadores en curso: quien ya estaba contado
        sigue donde estaba en vez de empezar de cero.
        """
        return self.prefix.strip("/").replace("/", ".")

    @classmethod
    def parse(cls, texto: str) -> "RateLimitRule":
        """Lee una regla escrita como "prefijo:peticiones:segundos".

        Lanza ValueError si algo no cuadra. Quien la llama es una guarda de
        arranque: mas vale que la API se niegue a levantar que enterarse de que
        un limite no se aplicaba el dia que alguien lo necesite.
        """
        partes = texto.split(":")
        if len(partes) != 3:
            raise ValueError(
                f"Rate limit rule must be 'prefix:requests:seconds', got {texto!r}"
            )

        prefix, limite, ventana = (parte.strip() for parte in partes)

        if not prefix.startswith("/"):
            raise ValueError(f"Rate limit prefix must start with '/', got {prefix!r}")

        try:
            limite_n, ventana_n = int(limite), int(ventana)
        except ValueError as exc:
            raise ValueError(
                f"Rate limit requests and seconds must be integers, got {texto!r}"
            ) from exc

        if limite_n < 1 or ventana_n < 1:
            # Un limite de 0 bloquearia la ruta entera, y es mas probable que
            # sea una errata que una intencion.
            raise ValueError(
                f"Rate limit requests and seconds must be positive, got {texto!r}"
            )

        return cls(prefix=prefix, limit=limite_n, window_seconds=ventana_n)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Sin valor por defecto a proposito: Pydantic exige que DATABASE_URL exista.
    # Un default aqui esconderia un despliegue mal configurado, que arrancaria
    # apuntando a localhost en vez de fallar.
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    debug: bool = False

    # Logging
    # log_level: severidad minima que se emite (DEBUG/INFO/WARNING/ERROR/CRITICAL).
    # log_json:  true en produccion (una linea JSON por evento, indexable por
    #            herramientas); false en desarrollo (texto coloreado, legible).
    log_level: str = "INFO"
    log_json: bool = False

    # CORS
    # Origenes exactos que el navegador puede usar para llamar a esta API.
    # NUNCA "*": con allow_credentials el navegador lo rechaza, y ademas
    # abriria la API a cualquier sitio web.
    # NoDecode desactiva el parseo JSON automatico para poder aceptar una lista
    # separada por comas, que es lo comodo en un .env.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Rate limiting (ventana fija por IP, contador en Redis)
    # rate_limit_enabled: se apaga en los tests, que no tocan la red.
    # /api/health exento: el healthcheck lo llama cada 5s y se
    # autobloquearia, haciendo que Docker reiniciara la API en bucle.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    rate_limit_exempt_paths: Annotated[list[str], NoDecode] = ["/api/health"]

    # Limites por ruta. Formato: "prefijo:peticiones:segundos", separados por
    # comas. La PRIMERA regla que casa gana, asi que el orden importa: un
    # prefijo generico escrito antes taparia a uno mas especifico.
    #
    # En configuracion y no en el codigo para poder ajustarlos en produccion
    # sin desplegar, que es justo lo que hace falta el dia que se descubre que
    # un limite era demasiado estrecho.
    #
    #   login   5/min   frena la fuerza bruta sin estorbar a quien se equivoca
    #                   de contrasena un par de veces
    #   signup  10/hora hace inviable crear cuentas en masa. NO son 3/hora a
    #                   proposito: el limite es por IP, y una oficina detras de
    #                   un NAT comparte una sola. Tres registros por hora para
    #                   un edificio entero bloquearia a usuarios legitimos.
    rate_limit_rules: Annotated[list[str], NoDecode] = [
        "/api/auth/login:5:60",
        "/api/auth/signup:10:3600",
        "/api/auth/resend-verification:5:3600",
    ]

    # Auth
    # jwt_secret es la LLAVE MAESTRA: quien la tenga puede fabricar un token
    # valido con cualquier identidad. Minimo 32 bytes aleatorios, DISTINTA en
    # cada entorno, nunca en git.
    #   generar con: python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    # Ventana que tiene un access token robado. En produccion, 15.
    access_token_minutes: int = 60
    # En produccion, 7.
    refresh_token_days: int = 30
    # Obliga a que la cookie viaje solo por HTTPS. Obligatorio en produccion.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    # Cuanto vive el enlace de verificacion de correo.
    # Segundos que deben pasar entre dos reenvios del correo de verificacion
    # PARA LA MISMA CUENTA. Protege el buzon del usuario, no el servidor: el
    # rate limit del middleware cuenta por IP y no impide que alguien escriba
    # la direccion de otra persona desde muchas IPs distintas.
    resend_verification_cooldown_seconds: int = 60

    email_verification_hours: int = 24

    # Correo saliente
    # En desarrollo apunta a Mailpit (docker compose), que atrapa los correos y
    # los muestra en http://localhost:8025 sin reenviar nada a internet.
    # user/password van vacios en desarrollo: Mailpit no pide autenticacion.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@flashresearch.local"

    # Base para armar el enlace de verificacion que viaja en el correo.
    # El backend no puede adivinar donde vive el frontend.
    frontend_base_url: str = "http://localhost:5173"

    # Solo la leen los tests de integracion (tests/conftest.py). Se declara
    # aqui porque Settings rechaza variables desconocidas del .env, y esa
    # estrictez es deseable: un typo en el nombre impide arrancar en vez de
    # caer en silencio al valor por defecto.
    test_database_url: str = ""

    # Proveedor de datos de mercado (Polygon.io)
    polygon_api_key: str = ""
    polygon_base_url: str = "https://api.polygon.io"

    @field_validator(
        "cors_origins", "rate_limit_exempt_paths", "rate_limit_rules", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Acepta 'a,b' ademas de una lista."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


    @property
    def parsed_rate_limit_rules(self) -> tuple[RateLimitRule, ...]:
        """Las reglas ya convertidas, en el orden en que se escribieron.

        El orden se conserva porque la primera que casa gana.
        """
        return tuple(RateLimitRule.parse(texto) for texto in self.rate_limit_rules)

    @model_validator(mode="after")
    def _reject_malformed_rate_limit_rules(self) -> "Settings":
        """Una regla mal escrita debe impedir el arranque.

        Sin esta guarda, una errata en la variable de entorno descartaria la
        regla en silencio y la ruta se quedaria con el limite general. El
        sintoma seria no tener sintoma: la proteccion simplemente no estaria.
        """
        # Se descarta el resultado: lo que importa es que parse() reviente aqui
        # y no en la primera peticion.
        _ = self.parsed_rate_limit_rules
        return self

    @model_validator(mode="after")
    def _reject_insecure_production_config(self) -> "Settings":
        """Una configuracion insegura en produccion debe fallar al arrancar.

        Sin credenciales SMTP no se negocia TLS, asi que el correo saldria en
        claro. En desarrollo es lo correcto —Mailpit no pide autenticacion y no
        sale nada a internet—, en produccion es un fallo grave.
        """
        if not self.debug and not (self.smtp_user and self.smtp_password):
            raise ValueError(
                "SMTP_USER y SMTP_PASSWORD son obligatorios con DEBUG=false: "
                "sin credenciales el correo viajaria sin cifrar."
            )

        # En CUALQUIER entorno: sin secreto no se puede firmar nada.
        if not self.jwt_secret:
            raise ValueError(
                "JWT_SECRET es obligatoria. Generar con: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

        # pyjwt ya avisa de esto por su cuenta: por debajo de 32 bytes, la clave
        # HMAC queda por debajo de lo recomendado por el RFC 7518 y es
        # atacable por fuerza bruta.
        if len(self.jwt_secret) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET debe tener al menos {_MIN_JWT_SECRET_LENGTH} "
                f"caracteres; tiene {len(self.jwt_secret)}."
            )

        if not self.debug and not self.cookie_secure:
            raise ValueError(
                "COOKIE_SECURE debe ser true con DEBUG=false: sin ella la "
                "cookie de sesion viajaria sin cifrar."
            )

        return self


settings = Settings()
