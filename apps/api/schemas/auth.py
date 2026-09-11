"""Modelos de entrada y salida de los endpoints de autenticacion.

Un schema NO es una entidad. La entidad describe lo que el sistema SABE; el
schema, lo que DICE y ESCUCHA. Se parecen en el email y en nada mas: aqui la
contrasena viaja en claro y no existe id, mientras que la entidad guarda el
hash y jamas puede salir de aqui.

Estos schemas validan la FORMA de la peticion. Las reglas de negocio —longitud
minima de la contrasena, lista negra— viven en packages/core/domain/policies y
NO se repiten aqui: dos fuentes de verdad acabarian divergiendo.
"""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# Tope muy holgado, a proposito. No es la politica de contrasenas: es una
# defensa contra denegacion de servicio. Sin el, alguien podria mandar
# megabytes de "contrasena" y ocupar la CPU del servidor hasheandolos con
# Argon2 antes de que el dominio llegue a rechazarlos.
_MAX_PASSWORD_BYTES = 1024


class SignupRequest(BaseModel):
    # EmailStr valida el FORMATO segun el RFC, no que el buzon exista. Eso solo
    # lo demuestra que alguien abra el enlace de verificacion.
    email: EmailStr
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_BYTES)


class LoginRequest(BaseModel):
    # Sin EmailStr a proposito: al iniciar sesion no se valida el formato. Si
    # se rechazara aqui un email malformado, la respuesta seria un 422 distinto
    # del 401 generico, y eso delataria informacion. Cualquier cosa que no
    # exista en la base sale como invalid_credentials.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_BYTES)


class VerifyEmailRequest(BaseModel):
    # 256 sobra: el token son 43 caracteres. Cualquier cosa mas larga es basura
    # o un intento de sobrecargar la busqueda.
    token: str = Field(min_length=1, max_length=256)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class MessageResponse(BaseModel):
    """Respuesta generica de los endpoints que no devuelven datos.

    Declarada como response_model, FastAPI RECORTA la salida a estos campos.
    Si alguien devolviera de mas —la entidad completa, por ejemplo— el
    password_hash no llegaria al cliente.

    No lleva email, ni id, ni si la cuenta era nueva: el signup responde igual
    en sus tres ramas, y devolver el email confirmaria que se proceso esa
    direccion.
    """

    message: str


class MeResponse(BaseModel):
    """Identidad del usuario de la sesion actual.

    Es lo que el frontend pregunta al cargar cada pagina. No puede deducirlo
    solo: la cookie de sesion es HttpOnly, asi que su JavaScript no la ve.

    Declarada como response_model, recorta la salida a estos cuatro campos. El
    password_hash del User no puede escaparse aunque alguien lo devolviera por
    error.
    """

    id: UUID
    email: EmailStr
    email_verified: bool
    # None mientras el usuario no elija nombre: el registro crea el perfil vacio.
    display_name: str | None
