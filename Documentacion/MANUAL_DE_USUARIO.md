# Exchange Pro — Manual de usuario

Guía de uso del sitio, pantalla por pantalla y botón por botón. No hace falta saber
programar para seguirla.

Para la parte técnica (cómo está construido, cómo levantarlo) ver
`DOCUMENTACION_TECNICA.md`. Para los secretos y la configuración ver
`SECRETOS_Y_CONFIGURACION.md`. Para cómo se usó asistencia de IA en el desarrollo ver
`COLABORACION_CON_IA.md`.

---

## 1. Cómo entrar

Con el proyecto levantado, abrir en el navegador:

| Qué | Dirección |
|-----|-----------|
| El sitio | `http://localhost` |
| El sitio desde otra máquina de la red | `http://<IP-de-la-máquina>` |
| Consola de administración de Keycloak | `http://localhost/admin/` |
| Panel interno de Django | `http://localhost/django-admin/` |

Si el proyecto todavía no está corriendo, levantarlo con:

```bash
cd currency-exchange-sprint1
docker compose up -d --build
```

---

## 2. Usuarios de prueba

El proyecto viene con tres cuentas ya creadas, una por cada rol. **Las tres usan la
misma contraseña.**

| Usuario | Contraseña | Rol | Qué puede hacer |
|---------|-----------|-----|-----------------|
| `demo.admin` | `ChangeMe123!` | Administrador | Todo: operar, gestionar divisas, simular días y gestionar usuarios. |
| `demo.manager` | `ChangeMe123!` | Gestor | Gestionar divisas y sus precios de compra y venta. |
| `demo.customer` | `ChangeMe123!` | Usuario | Operar: billetera, tarjetas, comprar, vender y retirar divisas. |

Correos asociados: `demo.admin@example.com`, `demo.manager@example.com`,
`demo.customer@example.com`.

También existe la cuenta de administrador de **Keycloak** (la consola de identidad,
no el sitio): usuario `admin`, contraseña `change-me`. Sirve para crear usuarios y
revisar la configuración del realm `exchange-learning`.

> Estas credenciales son de un entorno de aprendizaje local y están pensadas para
> ser públicas dentro del equipo. Antes de exponer el proyecto en cualquier red que
> no sea la propia máquina hay que cambiarlas: ver `SECRETOS_Y_CONFIGURACION.md`.

### Crear una cuenta nueva

Dos caminos:

1. **Registro propio** — botón *Registrarse* en el sitio. Abre el formulario de
   Keycloak. La cuenta recibe el rol `usuario` automáticamente al iniciar sesión por
   primera vez.
2. **Creación por un administrador** — pestaña *Usuarios* → *Nuevo usuario*. Permite
   elegir los roles desde el principio.

---

## 3. El menú

Toda la navegación está en la **barra lateral izquierda**, agrupada en tres bloques:
*General*, *Operar* y *Administración*. La pestaña en la que estás queda resaltada con
un hexágono rosa. Abajo de todo aparecen tu nombre, tus roles y el cierre de sesión.

En la **barra superior** está el título de la pantalla y, cuando hace falta, el dato
que conviene tener siempre a la vista: tu saldo en guaraníes mientras operás, o el día
simulado mientras mirás el historial.

En pantallas angostas (un teléfono) la barra lateral pasa arriba y se convierte en una
tira que se desliza con el dedo.

La barra lateral muestra solamente las pestañas que el rol permite usar:

| Pestaña | Quién la ve |
|---------|-------------|
| **Inicio** | Todos, incluso sin iniciar sesión. |
| **Cotizaciones** | Todos, incluso sin iniciar sesión. |
| **Panel** | Con sesión iniciada. |
| **Mi billetera** y **Comprar y vender** | Rol usuario. |
| **Divisas** | Roles gestor y administrador. |
| **Simulación** | Rol administrador. |
| **Usuarios** | Rol administrador. |
| **Cuenta** y **Cerrar sesión** | Con sesión iniciada (abajo de la barra lateral). |

---

## 4. Cómo funcionan los dos precios

Esto es lo más importante para entender el sitio. Cada divisa tiene **dos precios**,
igual que la pizarra de una casa de cambio real:

- **Precio de compra** — los guaraníes que la casa de cambio **te paga** por cada
  unidad. Es el más **bajo**.
- **Precio de venta** — los guaraníes que la casa de cambio **te cobra** por cada
  unidad. Es el más **alto**.

Ejemplo con el dólar a compra 7.190 y venta 7.410:

- Si **compras** 1 USD, pagas **7.410 Gs.** (se usa el precio de venta).
- Si **vendes** 1 USD, recibes **7.190 Gs.** (se usa el precio de compra).

La diferencia (220 Gs.) es la ganancia de la casa de cambio. Por eso comprar y
vender de inmediato siempre deja un poco menos de dinero: es el costo de la
operación, no un error del sistema.

El guaraní es la **divisa base** y vale 1 por definición. No se puede editar ni
eliminar.

---

## 5. Inicio

La página pública. Muestra de qué se trata el sitio y la **pizarra de hoy**: todas
las divisas activas con su precio de compra y de venta, y un enlace al historial de
cada una.

---

## 6. Cotizaciones — historial de una divisa

Pestaña **Cotizaciones**. Es pública: se puede consultar sin iniciar sesión.

**Cómo usarla:**

1. Elegir una divisa en el desplegable **Divisa**.
2. Elegir un **Período**: últimos 7, 30 o 90 días, o todo el historial.
3. Pulsar **Ver historial**.

**Qué muestra:**

- El precio de compra y de venta de hoy, y el margen en porcentaje.
- La **variación del período** en grande: en verde si subió, en rojo si bajó.
- El **gráfico**: dos líneas, azul para el precio de compra y naranja para el de
  venta. La línea naranja va siempre por encima. El eje vertical son guaraníes y el
  horizontal las fechas.
- La **tabla día por día**, de lo más reciente a lo más antiguo, con compra, venta,
  promedio y de dónde salió cada dato: *Valor inicial* (carga del proyecto), *Carga
  manual* (un gestor lo cambió) o *Simulación de día* (lo generó el botón de
  avanzar un día).

El proyecto viene con 30 días de historial ya cargados, así que el gráfico tiene
datos desde el primer momento.

---

## 7. Mi billetera (rol usuario)

Pestaña **Mi billetera**. Tiene cinco bloques.

### 7.1 Resumen

Tres cifras arriba:

- **Saldo disponible en guaraníes** — el dinero libre para comprar divisas.
- **Valor de tus divisas** — cuántos guaraníes recibirías si vendieras hoy todo lo
  que tienes (al precio de compra).
- **Total de la billetera** — la suma de los dos.

### 7.2 Mis medios de pago

Las tarjetas de crédito registradas. Se necesita **al menos una** para cargar saldo.

**Para agregar una tarjeta:** abrí el desplegable **Agregar una tarjeta** al pie de
este bloque — está en la misma página, no hace falta ir a otra pantalla — y completá:

| Campo | Qué poner |
|-------|-----------|
| Alias de la tarjeta | Un nombre para reconocerla, por ejemplo "Visa personal". |
| Nombre del titular | Como figura en la tarjeta. |
| Número de tarjeta | Uno de prueba (ver abajo). Se puede escribir con espacios. |
| Mes y año de vencimiento | Una fecha futura. |

> **No uses una tarjeta real.** Esto es un proyecto de aprendizaje. El sistema
> guarda únicamente los **últimos cuatro dígitos**, la marca y el vencimiento; el
> número completo se valida y se descarta, no queda almacenado en ninguna parte.

**Números de prueba que el sistema acepta:**

| Número | Se detecta como |
|--------|-----------------|
| `4111 1111 1111 1111` | Visa |
| `5500 0000 0000 0004` | Mastercard |
| `3400 0000 0000 009` | American Express |

El sistema rechaza números inventados al azar: valida el dígito verificador (el
mismo control que usan las tarjetas de verdad). Si aparece el error de número
inválido, usar uno de la tabla.

En la tabla, cada tarjeta aparece como `•••• •••• •••• 1111`. Si está vencida se
avisa con la palabra *(vencida)* en rojo. El botón **Eliminar** de cada fila la quita en un
clic, pidiendo confirmación al navegador; los movimientos ya hechos con ella se
conservan en el historial.

### 7.3 Cargar saldo en guaraníes

Tres formas, de la más rápida a la más flexible:

- **Botón de un clic** — *Cargar Gs. 500.000 de prueba en un clic* carga ese monto fijo
  con la primera tarjeta registrada, sin escribir nada.
- **Atajos de monto** — los botones redondeados (Gs. 100.000, 500.000, 1.000.000,
  5.000.000) rellenan el campo con un clic; después elegís la tarjeta y confirmás.
- **Formulario normal** — escribís el monto exacto, elegís la tarjeta y pulsás
  **Cargar saldo**.

Si no hay ninguna tarjeta registrada, el bloque muestra un enlace para agregar una
y la carga no se puede hacer.

### 7.4 Mis divisas

Cada divisa que se tiene, con la cantidad, el precio de compra de hoy y cuántos
guaraníes se recibirían al venderla. Desde acá se puede ir a **Vender** o usar el
botón *Retirar 1 (prueba)*.

### 7.5 Retirar divisas

Elegir divisa y cantidad, y pulsar **Retirar**.

> **Retirar no es lo mismo que vender.** *Retirar* saca la divisa de la billetera
> sin dar nada a cambio, como si se la llevara en efectivo. *Vender* la convierte en
> guaraníes que se acreditan en el saldo. Si lo que se busca es recibir guaraníes,
> hay que usar *Vender* en la pestaña **Comprar y vender**.

### 7.6 Movimientos recientes

Los últimos 20 movimientos, con fecha, tipo (*Carga de saldo*, *Compra de divisa*,
*Venta de divisa*, *Retiro de divisa*), divisa, cantidad, equivalente en guaraníes,
la cotización que se aplicó y con qué se pagó (una tarjeta o el saldo).

---

## 8. Comprar y vender (rol usuario)

Pestaña **Comprar y vender**. Arriba se ve el saldo disponible y la **pizarra de
cotizaciones** con ambos precios, el margen de cada divisa y un enlace a su
historial.

> **Atajo:** el botón **Comprar** de cada fila de la pizarra te baja al formulario con
> esa divisa ya elegida, así no tenés que buscarla de nuevo en el desplegable. Lo mismo
> hace el botón **Vender** de la billetera para el formulario de venta.

### 8.1 Comprar divisa con guaraníes

Se aplica el **precio de venta** de la divisa.

1. **Divisa a comprar** — cualquiera de las activas (ya viene elegida si llegaste con
   el botón *Comprar* de la pizarra).
2. **Monto a gastar (Gs.)** — cuántos guaraníes se quieren gastar.
3. **Pagar con** — dos opciones:
   - *Saldo en guaraníes* (muestra entre paréntesis cuánto hay disponible), o
   - una de las tarjetas registradas, que se cobra directamente **sin pasar por el
     saldo**.
4. Revisar la **vista previa de la operación** (ver abajo).
5. **Comprar**.

El sistema calcula cuánta divisa corresponde dividiendo el monto por el precio de
venta, redondeando siempre hacia abajo. El mensaje de confirmación dice cuánto se
compró, cuánto se pagó y a qué cotización.

#### La vista previa de la operación

Debajo del formulario, **antes de pulsar Comprar**, aparece un panel que se actualiza
solo cada vez que se cambia la divisa, el monto o el medio de pago. Sirve para no tener
que confirmar a ciegas.

Muestra:

- **Entregas → Recibes** — los guaraníes que se van a pagar y, al lado, exactamente
  cuántas unidades de la divisa se van a recibir.
- **La cotización aplicada** — el precio de venta que se está usando, y de dónde va a
  salir el dinero (el saldo o una tarjeta concreta).
- **Un gráfico de dos barras** — la barra naranja de arriba es lo que se paga ahora; la
  barra azul de abajo es lo que se recibiría si se vendiera la divisa de inmediato. La
  segunda es más corta, y esa diferencia es el margen de la casa de cambio. Debajo del
  gráfico se indica cuánto es esa diferencia, en guaraníes y en porcentaje.
- **Un aviso en rojo** si se eligió pagar con el saldo y el saldo no alcanza, para
  enterarse antes de enviar el formulario y no después.

La vista previa es sólo informativa: el monto que finalmente se acredita es el que
calcula el servidor al confirmar. Si el navegador tiene JavaScript deshabilitado el
panel no se muestra, pero la compra funciona igual, con el mismo precio de venta que
figura en la pizarra.

Si se elige pagar con el saldo y no alcanza, la compra se rechaza con un aviso y no
se descuenta nada.

### 8.2 Vender divisa y recibir guaraníes

Se aplica el **precio de compra** de la divisa.

1. **Divisa a vender** — sólo aparecen las que efectivamente se tienen.
2. **Cantidad a vender**.
3. **Vender**.

Los guaraníes se acreditan en el saldo al instante. Si se intenta vender más de lo
que se tiene, la operación se rechaza.

Este bloque sólo aparece si hay divisas en la billetera.

---

## 9. Divisas (roles gestor y administrador)

Pestaña **Divisas**. Lista todas las divisas con su código, nombre, símbolo, precio
de compra, precio de venta, margen y estado.

El guaraní aparece marcado con la etiqueta **base**: se puede editar su nombre y
símbolo, pero no su código ni sus cotizaciones, y no se puede eliminar.

### Nueva divisa

Botón **Nueva divisa**:

| Campo | Qué poner |
|-------|-----------|
| Código | Tres o cuatro letras, por ejemplo `JPY`. Se guarda en mayúsculas. |
| Nombre | Nombre completo, por ejemplo "Yen japonés". |
| Símbolo | Opcional, por ejemplo `¥`. |
| Precio de compra | Guaraníes que la casa de cambio paga por 1 unidad. |
| Precio de venta | Guaraníes que cobra por 1 unidad. **Debe ser mayor que el de compra.** |
| Activa | Si está desmarcada, la divisa no se puede operar ni participa de la simulación. |

Si el precio de compra es mayor o igual al de venta, el formulario no se guarda y
explica por qué: la casa de cambio tiene que comprar más barato de lo que vende.

### Editar

Enlace **Editar** de cada fila. Es la forma de subir o bajar una cotización a mano.
Cada cambio queda registrado en el historial con la fecha del día simulado actual,
así que aparece en el gráfico igual que un cambio de la simulación.

### Historial y Eliminar

**Historial** abre la pestaña de cotizaciones ya filtrada por esa divisa.
**Eliminar** pide confirmación. Una divisa con la que alguien ya operó no se puede
eliminar: el sistema lo avisa en lugar de romper los movimientos existentes. En ese
caso lo correcto es desmarcar *Activa*.

---

## 10. Simulación (rol administrador)

Pestaña **Simulación**. Sirve para ver cómo se comportan las cotizaciones a lo largo
del tiempo sin tener que esperar días reales.

**Qué muestra:**

- El **día simulado actual** del sistema.
- Cuántos **días se adelantaron** hasta ahora.
- Las cotizaciones vigentes de todas las divisas activas.

Hay **dos botones**: *Avanzar un día* y *Avanzar una semana*. El segundo hace siete
días de una sola vez y te informa la variación **neta** del período, para no tener que
pulsar siete veces cuando lo que querés es ver una tendencia.

**Avanzar un día** hace, de una vez:

1. Adelanta el reloj del sistema un día.
2. Cambia el valor de **cada divisa activa** (el guaraní queda afuera, es la base)
   un porcentaje al azar entre **−5 % y +7 %**.
3. Guarda el valor nuevo de cada divisa como un punto del historial.

Después de pulsarlo aparece la tabla **Qué cambió en el último día simulado**, con
el antes y el después de cada divisa y la variación en verde o en rojo. Ese resumen
se muestra una sola vez; al recargar la página desaparece, pero los cambios quedan
guardados.

El mismo porcentaje se aplica al precio de compra y al de venta, así que el margen
de la casa de cambio se mantiene y el precio de compra nunca termina por encima del
de venta, por muchos días que se simulen.

**Para ver el efecto:** pulsar *Avanzar una semana* dos o tres veces y después ir a
**Cotizaciones**. El gráfico va a mostrar la trayectoria completa.

> No hay botón para volver atrás: los días avanzan en una sola dirección. Para
> empezar de cero hay que reiniciar los datos del proyecto (ver
> `DOCUMENTACION_TECNICA.md`, sección 8).

---

## 11. Usuarios (rol administrador)

Pestaña **Usuarios**. Administra las cuentas directamente en Keycloak.

- **Buscar** — la caja de búsqueda filtra por nombre, usuario o correo.
- **Nuevo usuario** — crea la cuenta: usuario, correo, nombre, apellido, contraseña
  inicial (mínimo 8 caracteres), la casilla *Pedir cambio de contraseña en el primer
  inicio de sesión* y los roles a asignar.
- **Roles** — cambia los roles de una cuenta existente marcando o desmarcando
  casillas.
- **Eliminar** — borra la cuenta, con confirmación. **No se puede eliminar la propia
  cuenta desde este panel**, para no quedarse sin administrador.

---

## 12. Panel y Cuenta

**Panel** confirma que la sesión sigue activa y lista los accesos que permite el rol
actual. **Cuenta** muestra el nombre visible, el correo y el usuario, y da acceso a
**Eliminar cuenta**: ese flujo exige escribir la palabra `DELETE` en mayúsculas y
borra la cuenta en Keycloak de forma definitiva.

---

## 13. Recorrido de prueba completo

Para verificar que todo funciona de punta a punta, en unos cinco minutos:

**Como usuario (`demo.customer`):**

1. Iniciar sesión. Ir a **Mi billetera**: el saldo arranca en Gs. 0.
2. Abrir el desplegable **Agregar una tarjeta** (ya viene abierto si no tenés ninguna)
   y cargar `4111 1111 1111 1111`, vencimiento `12 / 2030`. Debe aparecer como Visa
   `•••• •••• •••• 1111`.
3. Cargar saldo: *Cargar Gs. 500.000 de prueba*. El saldo pasa a Gs. 500.000.
4. Ir a **Comprar y vender** y pulsar el botón **Comprar** de la fila del dólar:
   el formulario debe abrirse con USD ya seleccionado. Gastar Gs. 100.000 pagando con
   el saldo y verificar que la cantidad recibida sea el monto dividido por el precio de
   **venta**.
5. Comprar otra vez, ahora eligiendo la **tarjeta** como medio de pago. El saldo no
   debe cambiar, pero la tenencia de dólares sí.
6. **Vender** la mitad de los dólares. Verificar que los guaraníes recibidos
   correspondan al precio de **compra**, que es menor.
7. Volver a **Mi billetera** y revisar *Movimientos recientes*: tienen que estar la
   carga, las dos compras (una con tarjeta, otra con saldo) y la venta, cada una con
   su cotización.

**Como gestor (`demo.manager`):**

8. Ir a **Divisas**. Editar el euro y bajar su precio de compra. Intentar poner un
   precio de compra **mayor** que el de venta: el formulario debe rechazarlo.
9. Ir a **Cotizaciones**, elegir el euro: el cambio recién hecho debe aparecer como
   el último punto del gráfico, con origen *Carga manual*.

**Como administrador (`demo.admin`):**

10. Ir a **Simulación**. Anotar el día actual, pulsar **Avanzar un día** una vez y
    después **Avanzar una semana**: el reloj debe quedar ocho días adelante.
11. Verificar que cada vez aparezca la tabla de cambios, con variaciones siempre
    dentro de −5 % a +7 %, y que el precio de compra siga por debajo del de venta.
12. Ir a **Cotizaciones**: el gráfico debe mostrar los ocho días nuevos.
13. Ir a **Usuarios**, crear una cuenta de prueba con rol usuario, cambiarle los
    roles y eliminarla.

**Control de permisos:**

14. Con `demo.customer`, escribir `http://localhost/simulation/` en la barra de
    direcciones: debe responder *Acceso denegado*. Lo mismo con `/currencies/` y
    `/manage/users/`.
15. Sin iniciar sesión, abrir `http://localhost/history/`: debe funcionar, porque la
    pizarra es pública.

---

## 14. Preguntas frecuentes

**Compré y vendí lo mismo al instante y perdí dinero. ¿Es un error?**
No. Es el margen de la casa de cambio: se compra al precio de venta (más alto) y se
vende al precio de compra (más bajo). Funciona igual en una casa de cambio real.

**¿Por qué no puedo cargar saldo?**
Hace falta al menos una tarjeta registrada. Ir a *Mi billetera* → *Agregar tarjeta*.

**El sistema rechaza mi número de tarjeta.**
Valida el dígito verificador, así que no acepta números inventados. Usar uno de los
de prueba de la sección 7.2.

**¿Es seguro poner mi tarjeta?**
No hay que poner ninguna tarjeta real: es un proyecto de aprendizaje y no existe
cobro de verdad. De todos modos, el sistema sólo guarda los últimos cuatro dígitos.

**¿Cuál es la diferencia entre retirar y vender?**
*Retirar* saca la divisa de la billetera sin dar nada a cambio. *Vender* la convierte
en guaraníes que se acreditan en el saldo.

**¿Puedo deshacer un día simulado?**
No. Para volver al estado inicial hay que reiniciar los datos del proyecto.

**No veo la pestaña Divisas / Simulación / Usuarios.**
La barra lateral muestra sólo lo que permite el rol. Para verlas hay que entrar con
`demo.manager` o `demo.admin` según corresponda.

**El sitio se ve sin estilos, todo texto suelto.**
No se está cargando la hoja de estilos. Revisá que el contenedor de nginx esté
corriendo (`docker compose ps`) y que `http://localhost/static/css/app.css` responda.

**¿Funciona en el teléfono?**
Sí. La barra lateral se pasa arriba como una tira deslizable y las tablas se
desplazan de costado. La única cosa que necesita JavaScript es la vista previa de
compra y los atajos de monto; todo lo demás funciona sin él.
