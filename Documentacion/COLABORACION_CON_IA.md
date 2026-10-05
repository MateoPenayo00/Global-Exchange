# Cómo la inteligencia artificial ayudó en este proyecto

Resumen de cómo se usó asistencia de IA (Claude, a través de Claude Code) durante el
desarrollo de Exchange Pro, qué aportó realmente, dónde se equivocó y qué conclusiones
saca el equipo para la materia **Ingeniería de Software II**.

Este documento existe por una razón metodológica: la materia evalúa el **proceso**, no
sólo el código. Si una parte importante del trabajo se hizo con asistencia de IA, eso
forma parte del proceso y corresponde documentarlo con el mismo criterio con que se
documenta cualquier otra herramienta del equipo.

Documentos hermanos: `DOCUMENTACION_TECNICA.md`, `MANUAL_DE_USUARIO.md` y
`SECRETOS_Y_CONFIGURACION.md`.

---

## 1. Resumen en una página

| | |
|---|---|
| **Herramienta** | Claude (modelo Opus) a través de Claude Code, un agente de línea de comandos con acceso al repositorio, a la terminal y al stack de Docker. |
| **Modalidad** | Conversacional. El equipo describe el objetivo en lenguaje natural; la IA explora el código, propone e implementa, ejecuta las pruebas y reporta. |
| **Sprints con asistencia** | Los tres. La documentación de los Sprints 1 y 2 ya registraba sesiones de trabajo asistido; el Sprint 3 se desarrolló íntegramente de esta forma. |
| **Qué aportó la IA** | Implementación, migraciones de base de datos, pruebas unitarias, documentación y depuración. |
| **Qué siguió siendo del equipo** | La definición de los objetivos del sprint, las decisiones de producto, la priorización y la validación de los resultados. |
| **Resultado del Sprint 3** | 8 objetivos cumplidos, 98 pruebas unitarias en verde, documentación reducida de 18 archivos dispersos a 4 documentos. |

La conclusión corta: **la IA aceleró mucho la ejecución, pero no reemplazó la decisión
de qué construir ni la responsabilidad de verificar que esté bien.** Cada vez que el
equipo dio una instrucción vaga, el resultado fue mediocre; cada vez que dio una
instrucción precisa, el resultado fue útil en el primer intento.

---

## 2. Antecedentes: Sprints 1 y 2

El uso de IA no empezó en el Sprint 3. La documentación anterior del proyecto dejaba
constancia de sesiones de trabajo asistido en las que se produjo:

- **Sprint 1** — el esqueleto de Docker Compose, el proyecto Django, la conexión a
  PostgreSQL, el realm de Keycloak, la integración de login por OIDC y el proxy
  inverso Nginx.
- **Sprint 2** — los modelos de divisas y billetera, el CRUD de divisas, el sistema de
  roles (`admin` / `manager` / `user`), la pestaña de gestión de usuarios contra el
  Admin API de Keycloak, y las primeras 24 pruebas unitarias.

De esos sprints también salió un problema que el Sprint 3 tuvo que resolver: **la
documentación se multiplicó sin control**. Cada sesión generaba archivos nuevos
(`CHIA.txt`, `PLA.txt`, `PUN.txt`, `FUNC.txt`, una carpeta `PDO/` con 16 archivos,
varios duplicados en inglés y español, un PDF y dos HTML). Era documentación correcta
en su contenido pero inservible en conjunto: nadie sabía cuál leer y había
contradicciones entre archivos, porque unos se actualizaban y otros no.

Es la primera lección concreta del proyecto: **generar documentación es baratísimo con
IA, y por eso es fácil generar demasiada.** El costo no está en escribirla, está en
mantenerla coherente.

---

## 3. Cómo fue el trabajo en el Sprint 3

### 3.1 El pedido inicial

El equipo planteó los seis objetivos del sprint en un solo mensaje, en lenguaje
natural y sin detalle técnico. En resumen:

1. Cambiar la divisa base de dólar a guaraní.
2. Que cada divisa tenga precio de compra y de venta separados, con el de compra más
   bajo.
3. Que el usuario pueda registrar tarjetas de crédito y elegirlas al operar.
4. Una pestaña con el historial de cada divisa, en tabla y en gráfico.
5. Un botón de administrador para adelantar un día y que las divisas varíen entre
   −5 % y +7 %.
6. Unificar la documentación en tres documentos y borrar el resto.

Y dos restricciones transversales: **todo en español** y la documentación debía partir
de la existente, no inventarse de cero.

### 3.2 La secuencia de trabajo

La IA siguió un orden que vale la pena registrar, porque es el que funcionó:

1. **Leer antes de escribir.** Primero exploró el repositorio completo: modelos,
   vistas, formularios, plantillas, migraciones, configuración de Docker, el realm de
   Keycloak y toda la documentación existente. Nada se escribió hasta tener el mapa.
2. **Cambios de esquema antes que de lógica.** Modelos y migraciones primero, después
   formularios y vistas, y las plantillas al final.
3. **Pruebas junto con el código, no después.** Cada funcionalidad nueva llegó con sus
   pruebas en la misma tanda.
4. **Verificar contra el stack real.** No alcanzó con "el código parece correcto": se
   reconstruyó la imagen de Docker, se aplicaron las migraciones sobre la base de
   datos real y se corrió la suite completa.
5. **Documentar al final**, cuando ya se sabía qué había quedado efectivamente
   construido.

### 3.3 La iteración sobre la vista previa de compra

Terminados los seis objetivos, el equipo pidió una funcionalidad más: que al comprar se
vea **cuánto se va a recibir antes de confirmar**. Se implementó como un panel con dos
cifras grandes y una flecha, más un gráfico de barras que compara lo que el usuario
paga con lo que recibiría si vendiera de inmediato — es decir, que hace visible el
margen de la casa de cambio en lugar de esconderlo.

Esa iteración dejó al descubierto un error latente que llevaba días en el código: los
formularios de compra y de venta conviven en la misma página y **los dos tenían un
campo llamado `currency`**, así que Django les asignaba el mismo `id` HTML
(`id_currency`) a los dos. El HTML era inválido y la vista previa no habría podido
distinguir a cuál de los dos selectores escuchar. Se resolvió dándole a cada formulario
su propio prefijo de `id` y se agregó una prueba que falla si el problema vuelve.

Vale la pena anotarlo: **el error no lo encontró la IA revisando su propio código, lo
encontró el intento de construir algo nuevo encima.** Las pruebas existentes pasaban
igual, porque probaban el comportamiento del servidor y no la validez del HTML.

### 3.4 El rediseño de la interfaz, y la pregunta que lo precedió

La última iteración del sprint empezó con una pregunta del equipo, no con una orden:
*"quiero una interfaz mejor, con un diseño más lindo y menos 'de IA'. ¿Qué
herramientas o plugins podés usar?"*

La respuesta fue más útil que cualquier implementación inmediata, por dos motivos.

**Primero, porque nombró el problema.** El aspecto "de IA" no era una impresión vaga:
estaba causado por decisiones concretas y enumerables, todas en el CSS que la propia
IA había escrito en las etapas anteriores — la tipografía Arial por defecto, una única
sombra difusa repetida en todas las tarjetas, la paleta azul marino y verde agua,
casi todo el texto en 14 px sin jerarquía, y un subtítulo gris debajo de cada título.
Es un patrón reconocible, y la causa no era la falta de un framework de CSS sino la
falta de decisiones: Tailwind o Pico habrían producido un aspecto genérico distinto,
igual de reconocible.

**Segundo, porque dijo qué no se podía hacer.** De las herramientas disponibles, la
más valiosa para diseñar —la que controla el navegador para sacar capturas e iterar
mirando el resultado— **no funciona en esta máquina**, porque requiere Chrome y acá
sólo hay Firefox. El intento de suplirla con capturas de Firefox en modo headless se
quedó colgado. Decirlo antes de empezar evitó la situación peor: trabajar a ciegas
dando a entender que se estaba viendo el resultado.

Con eso sobre la mesa, el equipo definió la dirección visual —morado oscuro, morado
claro y rosa pastel; el hexágono como motivo; un cohete como logotipo; la navegación
en una barra lateral; botones más legibles; menos clics por acción— y la IA la
implementó: una hoja de estilos propia con tokens de color y una escala tipográfica,
tipografías autoalojadas, el cohete dibujado como SVG, y siete flujos reorganizados
para recortar clics.

Un detalle que vale la pena registrar, porque muestra la diferencia entre seguir una
instrucción y entenderla: el rosa pastel de la marca **no se puede usar como color de
una línea de un gráfico**. Al validar la paleta con el script de medición de la
herramienta de visualización de datos (un script de Node que, al no haber Node en esta
máquina, hubo que portar a Python y comprobar contra los valores de referencia
documentados), el rosa pastel quedó fuera de la banda de luminosidad admisible y el
morado más oscuro también. La solución no fue ignorar la marca ni ignorar la
accesibilidad, sino usar los pasos intermedios de los mismos tonos para las marcas de
datos (`#6D28D9` y `#EC4899`, que pasan las cinco comprobaciones) y reservar los
pastel para superficies y fondos.

---

## 4. En qué ayudó concretamente

### 4.1 Migraciones de datos, que es donde más se pierde tiempo

El cambio de dólar a guaraní parecía un simple renombre y no lo era. Había datos reales
en la base: divisas cargadas a mano durante el Sprint 2, saldos de billetera y
movimientos, todos expresados en dólares. Borrar la base y empezar de nuevo era la
salida fácil.

En lugar de eso se escribió una migración en dos partes: una para el esquema
(renombrar `value_in_usd` a `buy_rate`, agregar `sell_rate`, renombrar `usd_balance` a
`pyg_balance`) y otra para los datos, que convierte todo lo existente usando una
cotización de referencia de 7.300 Gs./USD y además siembra 30 días de historial
sintético para que el gráfico tenga algo que mostrar desde el primer arranque.

Esa segunda parte es el tipo de trabajo donde la asistencia rinde más: es tedioso, es
fácil equivocarse y el error no se nota hasta que alguien mira un número raro semanas
después.

### 4.2 Pruebas unitarias: de 24 a 98

Las pruebas pasaron de 24 (Sprint 2) a 98, y no por acumulación: varias cubren
exactamente las reglas de negocio nuevas del sprint.

| Prueba | Qué protege |
|--------|-------------|
| `test_the_buy_price_stays_below_the_sell_price` | Simula 25 días seguidos y verifica que ninguna divisa termine comprándose más caro de lo que se vende. |
| `test_each_change_stays_within_minus_five_and_plus_seven_percent` | Corre la simulación con 25 semillas distintas y comprueba que la variación nunca se escape del rango pedido. |
| `test_only_the_last_four_digits_are_stored` | Recorre todas las columnas de la tarjeta guardada y falla si el número completo aparece en alguna. |
| `test_the_spread_is_the_cost_of_a_round_trip` | Comprar y vender de inmediato tiene que dejar menos guaraníes: así se verifica que el margen funciona. |
| `test_a_flat_series_does_not_divide_by_zero` | Una divisa que no se movió en todo el período no debe romper el gráfico. |
| `test_the_two_forms_do_not_share_field_ids` | El error de HTML descrito más arriba no puede volver sin que una prueba falle. |
| `test_advance_days_reports_the_net_change_not_the_last_day` | El resumen de la semana simulada compara contra el inicio del período, no contra el día anterior. |
| `test_the_current_page_is_marked_active_in_the_sidebar` | La barra lateral marca la pestaña correcta, que es lo único que le dice al usuario dónde está. |

El aporte acá no fue escribir más pruebas, fue **proponer casos que el equipo no había
pensado**: la serie plana del gráfico, las 25 semillas en lugar de una, el barrido de
columnas buscando el número de tarjeta.

### 4.3 Decisiones de seguridad que no estaban en el pedido

El pedido decía "que los usuarios puedan agregar tarjetas de crédito". No decía nada
sobre cómo guardarlas. La implementación resultante:

- **no guarda el número completo** en ninguna columna;
- valida el número con el algoritmo de Luhn, el mismo dígito verificador que usan las
  tarjetas reales, así que no acepta números inventados al azar;
- deduce la marca (Visa, Mastercard, Amex) de los primeros dígitos y conserva sólo los
  últimos cuatro para que el usuario reconozca la tarjeta;
- deja constancia en la interfaz de que no hay que usar una tarjeta real, y ofrece
  números de prueba.

Es un ejemplo de algo que una asistencia bien usada aporta: **el estándar de la
industria aplicado por defecto**, sin que el equipo tenga que saber de antemano que
existe una norma al respecto.

### 4.4 Eliminar dependencias en lugar de agregarlas

Para el gráfico del historial la salida obvia era instalar una librería de JavaScript
(Chart.js, D3) desde un CDN. Se resolvió distinto: un **SVG generado en el servidor**,
calculado en Python (`core/services/charts.py`) y dibujado por la plantilla.

Ventajas que se pueden defender en una presentación: no hay dependencia de CDN, el
gráfico se ve con JavaScript deshabilitado, no hay versiones de librerías que
actualizar, y la lógica del gráfico es **testeable con pruebas unitarias normales**
(hay cuatro). La vista previa de compra sí usa JavaScript, pero propio, en el archivo,
y la compra funciona igual sin él.

### 4.5 La unificación de la documentación

De 18 archivos dispersos a 4 documentos:

| Documento | Qué absorbió |
|-----------|--------------|
| `DOCUMENTACION_TECNICA.md` | Los dos README, la guía detallada de arranque, `FUNC.txt` (el recorrido botón por botón), `PUN.txt` (las pruebas) y la retrospectiva de `PLA.txt`. |
| `MANUAL_DE_USUARIO.md` | La guía de acceso en HTML y las partes de uso que estaban mezcladas en los README. |
| `SECRETOS_Y_CONFIGURACION.md` | Las dos guías de secretos (corta y detallada) y el índice de variables. |
| `COLABORACION_CON_IA.md` | Este documento. |

El balance fue de unas 1.700 líneas nuevas contra 5.300 eliminadas: **menos texto y más
cobertura**, porque buena parte de lo borrado eran traducciones duplicadas y versiones
desactualizadas del mismo contenido. La retrospectiva de los sprints anteriores no se
perdió: se incorporó como sección 9 del documento técnico, porque es justamente el tipo
de registro que la materia valora.

---

## 5. Dónde se equivocó la IA

Esta sección es la más útil de todas y por eso se deja completa.

### 5.1 Confundió el entorno de ejecución

El error más costoso del sprint. La imagen de Docker **copia** el código en lugar de
montarlo como volumen, así que el contenedor no ve los cambios del disco hasta que se
reconstruye la imagen. La IA corrió las pruebas asumiendo que sí los veía, y durante un
rato interpretó resultados que correspondían al código viejo. Se dio cuenta al ver que
un error ya corregido seguía apareciendo con el mismo número de línea.

**Lección:** una herramienta que ejecuta comandos puede equivocarse igual sobre *dónde*
se están ejecutando. Conviene desconfiar del primer resultado que no cierra.

### 5.2 Escribió pruebas con expectativas mal calculadas

Tres veces una prueba falló no porque el código estuviera mal, sino porque la
expectativa estaba mal escrita: una billetera que las vistas crean de forma diferida y
la prueba daba por existente; un `Decimal("8000")` en memoria comparado contra el
`8000.000000` que devuelve PostgreSQL. Errores menores, pero obligan a una regla:
**cuando una prueba falla, hay que mirar si el error está en el código o en la prueba, y
no asumir ninguna de las dos cosas.**

### 5.3 Dejó basura en el código al escribir rápido

En una plantilla quedó una expresión sin sentido (`{{ role_labels|default_if_none:'' }}`
pegada antes de la variable que correspondía) y en un servicio quedó un filtro absurdo
(`.exclude(code=Currency and "PYG")`, que funciona por casualidad pero no significa
nada). Ninguno rompía el funcionamiento, y ninguna prueba los habría detectado.
**Escribir archivos completos de una vez es rápido y deja ese tipo de residuo:** hay
que releer.

### 5.4 Pasó por alto el error de HTML durante días

Los `id` duplicados de los formularios de compra y venta estuvieron en el código desde
que se agregó la venta de divisas, con la suite completa en verde. Sólo aparecieron
cuando hubo que escribir código que dependía de esos `id`. **Las pruebas de Django
verifican lo que hace el servidor, no la validez del HTML que produce**, y ese punto
ciego no lo cubre ninguna cantidad de pruebas unitarias.

### 5.5 Diseñó una interfaz sin poder verla

El rediseño completo —paleta, tipografías, barra lateral, hexágonos, logotipo— se
escribió **sin que nadie viera una sola captura de pantalla**. La verificación
disponible fue estructural: que cada pantalla responda 200 con los tres roles, que
enlace la hoja de estilos, que dibuje la barra lateral y que marque la pestaña activa.
Eso descarta errores de plantilla, pero no dice nada sobre si el resultado se ve bien,
si una etiqueta se superpone con otra o si una tabla desborda en un teléfono.

La IA lo advirtió antes de empezar en lugar de después, lo que es lo correcto, pero no
cambia el hecho de fondo: **una parte del trabajo de este sprint quedó sin verificar y
depende de que una persona lo mire.**

### 5.6 Tiende a ampliar el alcance

Durante el sprint la IA agregó la **venta de divisas**, que no estaba entre los seis
objetivos. La justificación es razonable —sin ella el precio de compra nunca movía
dinero del usuario y el objetivo 2 no se podía demostrar de punta a punta— y se avisó al
equipo, pero es trabajo que nadie pidió. En un sprint con capacidad ajustada, esa
iniciativa puede ser un problema en lugar de un aporte.

---

## 6. Lo que siguió siendo del equipo

Conviene ser explícito, porque es lo que la materia evalúa:

- **Qué construir.** Los seis objetivos del sprint, la vista previa de compra, el
  rediseño de la interfaz y la decisión de unificar la documentación salieron del
  equipo. La IA no propuso ninguno.
- **La dirección visual.** La paleta morado y rosa, el hexágono, el cohete, la barra
  lateral y la consigna de reducir clics son decisiones de diseño del equipo. La IA
  aportó la ejecución y el diagnóstico de por qué el diseño anterior se veía genérico,
  pero no eligió el rumbo.
- **Las reglas del negocio.** Que la divisa base sea el guaraní, que el rango de
  variación diaria sea −5 % a +7 %, que el precio de compra sea el más bajo: todas son
  definiciones del equipo. La IA las implementó, no las eligió.
- **La decisión sobre qué documentación borrar.** La IA propuso conservar el enunciado
  de la cátedra y el export de Jira, y preguntó antes de tocarlos.
- **La validación final.** Que las pruebas pasen no prueba que el producto sea el
  pedido. Eso lo verifica una persona usando el sitio, con el recorrido de prueba de la
  sección 13 del manual de usuario.
- **La responsabilidad.** El código que se entrega es del equipo, no de la herramienta.

---

## 7. Cómo se usó bien: prácticas que funcionaron

1. **Dar el objetivo, no el procedimiento.** "Cada divisa debe tener precio de compra y
   de venta, con la compra más baja" funcionó mejor que cualquier intento de dictar los
   nombres de los campos. La instrucción en términos de negocio deja que la herramienta
   resuelva el detalle técnico, que es lo que hace bien.
2. **Pedir las pruebas en el mismo pedido.** No "implementá esto" y después "ahora
   probalo", sino las dos cosas juntas. Las pruebas escritas después tienden a
   confirmar lo que el código ya hace, en lugar de verificar lo que debería hacer.
3. **Exigir ejecución real, no plausibilidad.** La frase que más valor agregó en todo el
   sprint fue, en la práctica, "corré las pruebas contra el stack". Código que parece
   correcto y código verificado son dos cosas distintas.
4. **Revisar el diff, no sólo el resultado.** `git diff` es donde aparecen los residuos
   de la sección 5.3.
5. **Documentar al final.** Documentar mientras se construye produce documentación que
   describe intenciones; documentar al final produce documentación que describe lo que
   existe.
6. **Nombrar la restricción una sola vez y al principio.** "Todo en español" dicho al
   comienzo se respetó en todos los objetivos sin tener que repetirlo.
7. **Preguntar qué herramientas hay antes de pedir el trabajo.** La pregunta sobre
   herramientas de diseño ahorró dos cosas: un diagnóstico que el equipo no tenía
   (por qué la interfaz se veía genérica) y una expectativa falsa (que la IA podía
   iterar mirando el resultado en un navegador, cuando acá no puede).

---

## 8. Riesgos a tener presentes

- **Dependencia.** Si nadie del equipo entiende la migración `0004` o la lógica de
  `simulation.py`, el proyecto no es mantenible por más que funcione. El antídoto es
  que el código tenga comentarios que expliquen el *por qué* (los tiene) y que alguien
  los lea.
- **Documentación que se multiplica.** Ya pasó entre el Sprint 1 y el 2. Es el riesgo
  más probable de repetirse, porque escribir documentación con IA no cuesta
  prácticamente nada. La regla que se adopta de acá en adelante: **la carpeta
  `Documentacion/` tiene cuatro documentos y los cambios se hacen dentro de ellos, no
  en archivos nuevos.**
- **Falsa sensación de cobertura.** 98 pruebas en verde se sienten como garantía y no lo
  son: no cubren el login real contra Keycloak, ni las llamadas al Admin API, ni la
  validez del HTML, ni cómo se ve el sitio en un navegador. La sección 8 del documento
  técnico enumera esos huecos a propósito, y la 11 agrega el de la interfaz.
- **Desbalance en el equipo.** La retrospectiva ya señalaba que 9 de 13 tareas quedaron
  en una sola persona. Una herramienta que multiplica lo que produce un solo integrante
  **empeora** ese desbalance si no se reparte el trabajo a propósito.

---

## 9. Conclusión

Para la materia la conclusión relevante no es "la IA escribió el código". Es que **la
asistencia de IA cambió el lugar donde está el cuello de botella del proyecto.**

Antes el límite era cuánto código podía escribir el equipo por sprint. Ahora el límite
es cuán bien el equipo puede **especificar** lo que quiere y cuán rigurosamente puede
**verificar** lo que recibe. La productividad del Sprint 3 —ocho objetivos, 74 pruebas
nuevas, la documentación reorganizada y la interfaz rehecha— no habría sido alcanzable
de otra forma en el tiempo disponible. Pero cada uno de los errores de la sección 5 llegó hasta donde llegó
porque en ese punto nadie estaba verificando todavía.

Dicho de la forma más corta posible: **la IA no hizo falta menos ingeniería de software,
hizo falta distinta.** Menos tiempo escribiendo líneas, más tiempo definiendo bien el
problema, revisando diffs y desconfiando de las cosas que parecen estar bien.
