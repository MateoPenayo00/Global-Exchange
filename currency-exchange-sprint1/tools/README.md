# tools/

Utilidades de desarrollo. **No son parte de la aplicación**: Django no las importa y
no se ejecutan en tiempo de ejecución.

## `validate_palette.py`

Comprueba que una paleta de colores para gráficos sea legible: banda de luminosidad,
piso de croma, separación entre series bajo protanopía y deuteranopía (distancia
euclídea en OKLab ×100, con las matrices de Machado, Oliveira y Fernandes de 2009),
piso de visión normal y contraste contra el fondo.

Se usó para elegir los colores de las dos series del gráfico de cotizaciones. El
resultado explica por qué el rosa pastel de la marca **no** se usa como color de línea:
queda fuera de la banda de luminosidad admisible para una marca de datos, así que el
gráfico usa los pasos intermedios de los mismos tonos.

```bash
python3 tools/validate_palette.py "#6d28d9,#ec4899" --mode light --surface "#ffffff"
```

Salida esperada para la paleta vigente: las cinco comprobaciones en `pass`.
