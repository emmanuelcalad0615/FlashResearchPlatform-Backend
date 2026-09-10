"""Version del contrato publico de la API (el openapi.json que consume el frontend).

Es independiente de la version del paquete Python (pyproject): esa versiona el
deployable, esta versiona la FORMA de la API. Un arreglo interno del backend no
debe obligar al frontend a subir su dependencia.

Semver del contrato:
  MAJOR - rompe: se borra o renombra un endpoint o un campo de respuesta, un
          campo opcional de request se vuelve requerido, un tipo se estrecha.
  MINOR - aditivo: endpoint nuevo, campo opcional nuevo en la respuesta.
  PATCH - solo descripciones o ejemplos; la forma no cambia.

Al cambiarla hay que regenerar el snapshot y subir la version del paquete npm:
    uv run python scripts/export_openapi.py
Ver CONTRACT.md para el procedimiento completo.
"""

from fastapi.routing import APIRoute

CONTRACT_VERSION = "0.1.0"


def operation_id_for(route: APIRoute) -> str:
    """operationId estable: el explicito de la ruta, o su `name` como fallback.

    El default de FastAPI mezcla nombre de funcion + path + metodo
    ("health_check_health_get"), asi que renombrar una funcion de router cambia
    el nombre del metodo en el cliente TypeScript generado: un breaking change
    fantasma, sin que la API haya cambiado. Aqui el nombre es un dato del
    contrato, no un detalle de implementacion.
    """
    return route.operation_id or route.name
