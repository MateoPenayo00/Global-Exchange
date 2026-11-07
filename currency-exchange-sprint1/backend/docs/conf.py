"""Configuración de Sphinx para la documentación del código de Global Exchange.

Sphinx lee los docstrings de los módulos Python y genera un sitio HTML.
Para poder importar los módulos de Django hay que inicializarlo primero.
"""

import os
import sys

# La carpeta ``backend`` (donde están ``config`` y ``core``) debe poder importarse.
sys.path.insert(0, os.path.abspath(".."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

project = "Global Exchange"
author = "Equipo Global Exchange"
language = "es"

extensions = [
    "sphinx.ext.autodoc",  # toma la documentación de los docstrings
    "sphinx.ext.viewcode",  # agrega un enlace al código fuente de cada elemento
]

autodoc_member_order = "bysource"
autodoc_default_options = {"members": True, "show-inheritance": True}

exclude_patterns = ["_build"]
html_theme = "alabaster"
html_static_path = []
