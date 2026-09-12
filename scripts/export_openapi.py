"""Exporta el openapi.json de la API al snapshot commiteado en contract/.

Dos modos:
    uv run python scripts/export_openapi.py            # escribe el snapshot
    uv run python scripts/export_openapi.py --check    # solo compara (CI)

El snapshot se commitea a proposito: es lo que hace que un cambio de forma de la
API aparezca como diff en el PR, y lo que permite al CI detectar que alguien
cambio la API sin regenerarlo. Ver CONTRACT.md.
"""

import argparse
import json
import sys
from pathlib import Path

# El proyecto no se instala como paquete (pyproject no tiene build-system), asi
# que ejecutar "python scripts/x.py" deja scripts/ en sys.path pero no la raiz
# del repo, y "import apps" falla. pytest no lo sufre porque inserta el rootdir
# por su cuenta. Se agrega aqui para que el comando documentado funcione tal
# cual, sin PYTHONPATH ni -m.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from apps.api.main import app  # noqa: E402  (despues del sys.path de arriba, a proposito)

CONTRACT_PATH = REPO_ROOT / "contract" / "openapi.json"

STALE_MESSAGE = """El contrato exportado no coincide con contract/openapi.json.
Cambiaste la forma de la API sin regenerar el snapshot.
  1. Ejecuta: uv run python scripts/export_openapi.py
  2. Revisa el diff de contract/openapi.json (que cambio, y si rompe).
  3. Sube CONTRACT_VERSION en apps/api/core/contract.py segun semver.
  4. Commitea ambos archivos."""


def render_spec() -> str:
    """Serializa el openapi de la app de forma DETERMINISTA.

    sort_keys ordena las claves siempre igual, asi el diff del PR muestra el
    cambio real del contrato y no un reordenamiento de dicts. El salto de linea
    final es para que git no marque "No newline at end of file".
    """
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_spec() -> None:
    """Escribe el snapshot, creando contract/ si hace falta."""
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(render_spec(), encoding="utf-8")


def check_spec() -> bool:
    """True si el snapshot commiteado corresponde al codigo actual."""
    if not CONTRACT_PATH.exists():
        return False
    return CONTRACT_PATH.read_text(encoding="utf-8") == render_spec()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="No escribe: falla si el snapshot esta desactualizado.",
    )
    args = parser.parse_args()

    if not args.check:
        write_spec()
        print(f"Snapshot escrito en {CONTRACT_PATH.relative_to(Path.cwd())}")
        return 0

    if check_spec():
        print("Contrato sincronizado.")
        return 0

    if not CONTRACT_PATH.exists():
        print("No existe contract/openapi.json. Ejecuta el script sin --check.", file=sys.stderr)
        return 1

    print(STALE_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
