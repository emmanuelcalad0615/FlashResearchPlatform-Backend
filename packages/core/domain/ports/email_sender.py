"""Puerto de envio de correo.

Un metodo por intencion, no un send() generico. Con un generico, el caso de uso
tendria que redactar el mensaje —asunto, cuerpo, formato— y eso es presentacion,
no dominio. Asi solo declara la intencion y el adapter decide como se ve.

Cuando llegue el reset de contrasena se anade send_password_reset(...).
"""

from abc import ABC, abstractmethod


class EmailSender(ABC):
    @abstractmethod
    async def send_verification(self, *, to: str, link: str) -> None:
        """Manda el enlace de verificacion de cuenta.

        Puede lanzar EmailDeliveryError. Quien llama decide que hacer: el
        signup lo registra y sigue adelante, porque el usuario YA existe y
        devolverle un error lo dejaria creyendo que no se registro, sin poder
        reintentar. La salida para el es /auth/resend-verification.
        """

    @abstractmethod
    async def send_already_registered(self, *, to: str) -> None:
        """Avisa a quien YA tiene cuenta de que alguien intento registrarse.

        El signup responde 201 exista o no el email, para no delatar quien tiene
        cuenta. Pero el dueno del buzon si merece enterarse, asi que la rama
        "ya existe y esta verificado" manda ESTE correo en vez del de
        verificacion: no crea nada, no cambia contrasenas, solo avisa.

        Quien recibe cada correo distingue las ramas; el cliente HTTP no.
        """
