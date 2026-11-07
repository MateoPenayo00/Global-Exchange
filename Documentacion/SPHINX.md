# Documentación del código con Sphinx

Sphinx genera un sitio HTML a partir de los docstrings del código de `core`.

## Estructura

```
currency-exchange-sprint1/backend/
├── docs/
│   ├── conf.py          # configuración (inicializa Django para poder importar los módulos)
│   ├── index.rst        # portada
│   ├── modelos.rst      # core.models y core.forms
│   ├── vistas.rst       # core.views y core.cashier_views
│   ├── servicios.rst    # core.services.*
│   └── utilidades.rst   # roles, autenticación, middleware, filtros
└── requirements-docs.txt
```

## Generar el HTML

Desde `currency-exchange-sprint1` (PowerShell):

```powershell
docker compose run --rm --no-deps -v "${PWD}\backend\docs:/app/docs" web sh -c "pip install -q 'sphinx>=7.0,<9.0' && sphinx-build -b html docs docs/_build/html"
```

El resultado queda en `backend/docs/_build/html/index.html` (ignorado por git).

## Agregar un módulo nuevo

En el `.rst` que corresponda, agregar:

```rst
.. automodule:: core.services.nuevo_modulo
```
