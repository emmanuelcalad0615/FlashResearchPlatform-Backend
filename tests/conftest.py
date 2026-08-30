import os

# El rate limiting necesita Redis y los tests NO tocan la red (CLAUDE.md).
# Se apaga para la suite general; su comportamiento se prueba aparte en
# test_rate_limit.py, contra un Redis falso en memoria.
# Va antes de que cualquier test importe apps.api.config, que lee el
# entorno al importarse.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
