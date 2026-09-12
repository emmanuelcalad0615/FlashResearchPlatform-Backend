**HU-A10 — Contrato versionado + generación de tipos**

**Description**

Como Desarrollador
Necesito que el contrato de la API se publique versionado y el frontend genere sus tipos desde él.
Para mantener una sola fuente de verdad a través de la frontera entre repos.

**Criterios de aceptación**

- El backend exporta su openapi.json y lo publica como paquete versionado desde su CI.
- El frontend consume el contrato con versión fija y genera api-types (openapi-typescript o similar).
- El cliente API del frontend usa esos tipos generados.
- El proceso de regeneración está documentado y es reproducible.

**Definición de Done**

- El código cumple los criterios de aceptación.
- Tiene pruebas (unitarias para lógica de negocio; de integración para endpoints/adapters).
- Pasa el pipeline de CI (lint + tests + build) en verde.
- Revisión visual de la UI cuando aplica.
- Smoke test manual del flujo afectado.
- No rompe las zonas protegidas (UI base y tokens) sin aprobación.
