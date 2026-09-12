# @emmanuelcalad0615/flash-api-contract

El contrato OpenAPI de Flash Research API, publicado como paquete npm versionado.
Es la **única fuente de verdad** de la frontera entre este repo y el frontend
(`FlashResearchPlatform-Frontend`), que genera sus tipos TypeScript a partir de
`openapi.json`.

| Archivo | Qué es |
|---|---|
| `openapi.json` | El spec, generado por FastAPI. **No se edita a mano** — lo escribe `scripts/export_openapi.py`. |
| `package.json` | El manifiesto de publicación. Su `version` debe coincidir siempre con `CONTRACT_VERSION` en `apps/api/core/contract.py`. |

Los tres números —`CONTRACT_VERSION`, `package.json:version` e
`openapi.json:info.version`— son el mismo, y `tests/test_openapi_contract.py`
falla si se separan.

Para regenerar, publicar o resolver una desincronización: **`CONTRACT.md`** en la
raíz del repo.
