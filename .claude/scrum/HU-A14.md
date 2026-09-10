**HU-A14 — Chequeo de sincronización del contrato en CI**

**Description**

Como Desarrollador
Necesito que el CI detecte cuando el frontend quedó desactualizado respecto al contrato del backend.
Para que la desincronización falle pronto en CI y no en tiempo de ejecución.

**Criterios de aceptación**

- El CI del frontend verifica que los tipos generados correspondan a la versión declarada del contrato.
- Si el contrato cambió y los tipos no se regeneraron, el build falla con un mensaje claro.
- El procedimiento para resolver una desincronización está documentado.

**Definición de Done**

- El código cumple los criterios de aceptación.
- Tiene pruebas (unitarias para lógica de negocio; de integración para endpoints/adapters).
- Pasa el pipeline de CI (lint + tests + build) en verde.
- Revisión visual de la UI cuando aplica.
- Smoke test manual del flujo afectado.
- No rompe las zonas protegidas (UI base y tokens) sin aprobación.