"""Adapter SMTP del puerto EmailSender.

Una sola clase para desarrollo y produccion: en local apunta a Mailpit
(localhost:1025, sin credenciales) y en produccion al proveedor real. Cambia la
configuracion, no la implementacion — asi el codigo que corre en produccion es
el mismo que se ejercita a diario.

Nadie fuera de este archivo importa aiosmtplib: los fallos salen como
EmailDeliveryError, igual que el adapter de Polygon hace con httpx.
"""

import logging
from email.message import EmailMessage

import aiosmtplib

from packages.core.domain.errors import EmailDeliveryError
from packages.core.domain.ports import EmailSender

logger = logging.getLogger(__name__)

_ASUNTO_VERIFICACION = "Verifica tu cuenta en Flash Research"
_ASUNTO_YA_REGISTRADO = "Intento de registro en Flash Research"

_CUERPO_VERIFICACION = """\
Bienvenido a Flash Research.

Para activar tu cuenta, abre este enlace:

{link}

El enlace caduca en {horas} horas y solo se puede usar una vez.

Si no te registraste, ignora este mensaje: sin abrir el enlace, la cuenta no
llega a activarse.
"""


_CUERPO_YA_REGISTRADO = """\
Alguien intento registrarse en Flash Research con esta direccion.

Si fuiste tu: ya tienes una cuenta activa, inicia sesion normalmente.

Si no fuiste tu, ignora este mensaje. Nadie ha accedido a tu cuenta y tu
contrasena no ha cambiado.
"""


class SmtpEmailSender(EmailSender):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        frontend_base_url: str,
        verification_hours: int,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        # El backend no puede adivinar donde vive el frontend: llega por
        # configuracion y con eso se arma el enlace.
        self._frontend_base_url = frontend_base_url.rstrip("/")
        self._verification_hours = verification_hours

    async def send_verification(self, *, to: str, link: str) -> None:
        mensaje = EmailMessage()
        mensaje["From"] = self._sender
        mensaje["To"] = to
        mensaje["Subject"] = _ASUNTO_VERIFICACION
        # Texto plano: un correo transaccional necesita una frase y un enlace.
        # El diseno HTML esta anotado como deuda en el plan de la HU-A07.
        mensaje.set_content(
            _CUERPO_VERIFICACION.format(link=link, horas=self._verification_hours)
        )

        await self._enviar(mensaje, to=to)

    async def send_already_registered(self, *, to: str) -> None:
        mensaje = EmailMessage()
        mensaje["From"] = self._sender
        mensaje["To"] = to
        mensaje["Subject"] = _ASUNTO_YA_REGISTRADO
        # Sin enlaces ni acciones: es un aviso. Un correo de este tipo con un
        # boton seria indistinguible de un phishing.
        mensaje.set_content(_CUERPO_YA_REGISTRADO)

        await self._enviar(mensaje, to=to)

    def build_verification_link(self, token: str) -> str:
        """Arma el enlace que viaja en el correo.

        Vive aqui y no en el caso de uso: este genera el token, pero donde
        vive el frontend es un detalle de entrega.
        """
        return f"{self._frontend_base_url}/verify?token={token}"

    async def _enviar(self, mensaje: EmailMessage, *, to: str) -> None:
        # Solo se autentica si hay credenciales. Mailpit no las pide; un
        # proveedor real si. La guarda de arranque impide que esta rama sin
        # autenticar llegue a produccion.
        credenciales = (
            {"username": self._username, "password": self._password}
            if self._username and self._password
            else {}
        )

        try:
            await aiosmtplib.send(
                mensaje,
                hostname=self._host,
                port=self._port,
                start_tls=bool(credenciales),
                **credenciales,
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            # SEGURIDAD: se registra el destinatario y el tipo de fallo, NUNCA
            # el enlace ni el token. El token es una credencial de un solo uso:
            # quien lo vea en un log puede verificar la cuenta ajena.
            logger.exception(
                "verification_email_failed", extra={"to": to, "error": type(exc).__name__}
            )
            raise EmailDeliveryError(
                "Could not deliver the verification email",
                details={"to": to},
            ) from exc

        logger.info("verification_email_sent", extra={"to": to})
