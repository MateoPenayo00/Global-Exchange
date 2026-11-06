# Facturación electrónica con Factura Segura

Exchange Pro emite facturas electrónicas a través del **API ESI de Factura Segura**
(*External Systems Interoperation*), que genera y firma el XML, arma el KuDE (la
representación impresa en PDF) y lo envía al SIFEN de la DNIT. La integración usa el
**ambiente TEST**: los documentos no tienen validez fiscal.

Referencia: *API de Factura Segura para ESI v01* (manual entregado por la cátedra).

---

## 1. Qué se factura

| Operación | ¿Factura? | Por qué |
|-----------|-----------|---------|
| Compra en línea **pagada** (cualquier medio) | Sí | La casa de cambio le vende divisa al cliente. |
| Caja: el cliente **compra** divisa | Sí | Ídem. |
| Caja: el cliente **cambia** una divisa por otra | Sí | Recibe divisa extranjera. |
| Caja: el cliente **vende** divisa (recibe guaraníes) | No | La compradora es la casa de cambio; ese documento es una autofactura, que Factura Segura no soporta. Queda el comprobante interno. |
| Venta o retiro en la billetera | No | Mismo motivo que la anterior. |

Cada factura tiene **un ítem**, por el total en guaraníes de la operación, **exento de
IVA** (`iAfecIVA = 3`). Es una decisión del proyecto, a validar con el PO: si se
prefiriera facturar sólo el margen con IVA 10 %, alcanza con cambiar `ITEM_TAX` y el
ítem en `services/invoicing.py`.

La factura se emite **a pedido**: desde el comprobante de la compra (*Solicitar
factura*) o de la operación de caja (*Emitir factura*). No es automática porque el
rango habilitado en TEST es de 12 números.

## 2. Datos habilitados para el equipo 10 (TEST)

| Dato | Valor | Variable |
|------|-------|----------|
| Endpoint | `https://apitest.facturasegura.com.py/misife00/v1/esi` | `FACTURA_SEGURA_URL` (sin la ruta) |
| RUC del emisor | `2595733`, DV `3` | `FACTURA_RUC`, `FACTURA_RUC_DV` |
| Establecimiento | `001` | `FACTURA_ESTABLECIMIENTO` |
| Punto de expedición | `003` | `FACTURA_PUNTO_EXPEDICION` |
| Facturas (`iTiDE=1`) | `0000314` a `0000325` | `FACTURA_NUMERO_DESDE`, `FACTURA_NUMERO_HASTA` |
| Notas de crédito (`iTiDE=5`) | `0000109` a `0000120` | — (no se usan todavía) |
| Actividades económicas | `62010` y `74909` | fijas en `settings.INVOICE_EMITTER` |

Todos tienen esos valores por defecto en `settings.py`; sólo hace falta configurar el
token. Las **credenciales** (correo y contraseña del usuario ESI) no se versionan.

> **Timbrado.** La habilitación no indica número ni fecha de timbrado. Si Factura
> Segura los exige, completar `FACTURA_TIMBRADO` y `FACTURA_TIMBRADO_INICIO`; si
> están vacíos, `dNumTim` y `dFeIniT` no se envían.

## 3. Configuración

1. **Obtener el token** (una sola vez; vale hasta que se cambie la contraseña):

   ```bash
   docker compose exec web python manage.py factura_segura_token --email <correo ESI>
   ```

   Pide la contraseña sin mostrarla e imprime la línea `FACTURA_SEGURA_TOKEN=…`.
   Equivale al `curl` del manual contra `/login?include_auth_token`.
2. Copiar esa línea a `.env` y recrear el contenedor: `docker compose up -d web`.
3. La pestaña *Facturas* muestra **Factura Segura · TEST** en lugar de **Modo simulado**.

### Modo simulado

Sin `FACTURA_SEGURA_TOKEN` todo funciona igual, pero las facturas **no salen de la
aplicación**: `SimulatedClient` calcula los montos con las mismas fórmulas del SIFEN,
genera un CDC con el formato real y da el documento por aprobado. Las facturas
simuladas se marcan como tales en todas las pantallas, tienen **su propia numeración**
(desde `0000001`) y no consumen el rango real. La simulación de transacciones y el
comando `poblar_demo` usan siempre este modo, aunque haya token.

## 4. Flujo de una factura

```
Comprobante ─► Solicitar factura ─► formulario del receptor ─► issue_for_order / issue_for_cash
                                                                     │
          ┌──────────────────────────────────────────────────────────┘
          ▼
  Invoice con número reservado ─► calcular_de ─► generar_de ─► CDC ─► "En proceso"
                                                                         │
                       get_estado_sifen (al abrir la factura o con el botón)
                                                                         ▼
                                              Aprobada · Rechazada · Cancelada
```

1. **Receptor** (`forms.InvoiceCustomerForm`): nombre o razón social, correo (Factura
   Segura lo exige: ahí manda el KuDE y el XML cuando el SIFEN aprueba) y **RUC o
   cédula**. Con guion (`1234567-8`) es un RUC: el dígito verificador se comprueba con
   el módulo 11 de la DNIT y el receptor es contribuyente (`iNatRec=1`; persona
   jurídica si el RUC empieza en 80 y tiene 8 dígitos). Sin guion es una cédula y el
   receptor es no contribuyente (`iNatRec=2`, `iTipIDRec=1`).
2. **Número.** Se reserva el siguiente libre del rango al crear la `Invoice`. Si el
   rango se agotó, la emisión se rechaza con un mensaje que explica cómo pedir otro.
   Dos emisiones simultáneas no pueden tomar el mismo número: lo impide una
   restricción única y la segunda reintenta con el siguiente.
3. **Documento.** `build_summary()` arma el *JSON resumido de Factura Electrónica*
   del manual: emisor, receptor, condición contado, medio de pago e ítem. `calcular_de`
   lo completa con los montos del SIFEN y `generar_de` lo genera y devuelve el CDC.
4. **Estado.** El SIFEN responde en segundo plano. La pantalla de la factura consulta
   `get_estado_sifen` sola al abrirla si está en proceso y pasaron 30 segundos desde la
   última consulta; también hay un botón *Actualizar estado* y el comando
   `python manage.py actualizar_facturas` (para un cron).

### Medio de pago (`gPaConEIni`)

| Operación | `iTiPago` | Extra |
|-----------|-----------|-------|
| Compra con saldo u otra divisa de la billetera | `7` Billetera electrónica | |
| Compra con tarjeta registrada | `3` Tarjeta de crédito | `iDenTarj` según la marca (1 Visa, 2 Mastercard, 3 Amex, 99 otra), `iForProPa=2` pago electrónico |
| Compra con Stripe | `3` Tarjeta de crédito | `iDenTarj=99`, `dDesDenTarj=Stripe`, `iForProPa=2` |
| Compra con QR / caja con QR | `5` Transferencia | |
| Caja en efectivo | `1` Efectivo | |
| Caja con tarjeta | `3` Tarjeta de crédito | `iDenTarj` según la marca, `iForProPa=1` POS |

Siempre por el total, en guaraníes (`cMoneTiPag=PYG`). `iIndPres` es `2` (operación
electrónica) en línea y `1` (presencial) en caja. La fecha `dFeEmiDE` va en hora de
Paraguay, aunque el sistema guarde todo en UTC.

## 5. Estados

| Estado | Significa | Qué se puede hacer |
|--------|-----------|--------------------|
| **En proceso** | Factura Segura generó el documento; el SIFEN todavía no respondió. | Actualizar estado. |
| **Aprobada** | El SIFEN la aprobó (también *Aprobado con observación*). | Descargar KuDE y XML. Un admin puede pedir la cancelación. |
| **Rechazada** | El SIFEN la rechazó; el motivo queda en el detalle. | Corregir y reenviar **con el mismo número**. |
| **No se pudo generar** | Factura Segura rechazó el pedido (código negativo). | Corregir y reenviar con el mismo número. |
| **Sin confirmar** | Factura Segura no respondió a tiempo: el documento pudo generarse igual. | Sólo un admin puede reenviar, después de verificar con soporte (el manual pide no reenviar a ciegas). |
| **Cancelada** / **Inutilizada** | Estados finales del SIFEN. | Nada. |

`interpret_sifen()` traduce la respuesta de `get_estado_sifen` en el orden que fija el
manual: primero inutilización, después cancelación y por último aprobación
(`SOL.APROBACION` y `ENVIADO_A_SIFEN` siguen en proceso; `ERROR_SIFEN`,
`ERROR_ENVIO_LOTE` y `ERROR_CONSULTA_LOTE` se tratan como rechazo; `REINTENTAR_LOTE`
sigue en proceso porque Factura Segura reintenta sola).

**El número no se libera nunca.** Una factura que falló conserva su número y se
reenvía con él: así no se gasta el rango ni puede haber dos documentos con el mismo
número.

## 6. Pantallas y permisos

| URL | Quién | Qué |
|-----|-------|-----|
| `/facturas/` | usuario, cajero, admin | Listado, filtrable por estado. Cada uno ve lo suyo; el admin, todo. |
| `/facturas/<id>/` | quien puede verla | La factura con la estructura del KuDE (imprimible), su estado y las acciones. |
| `/trade/checkout/<id>/factura/` | el usuario dueño de la compra | Solicitar (o reenviar) la factura de una compra pagada. |
| `/caja/operaciones/<id>/factura/` | el cajero de la sesión, o un admin | Emitir la factura de una operación de caja. Nombre y cédula vienen de la operación. |
| `POST /facturas/<id>/estado/` | quien puede verla | Consultar el SIFEN. |
| `POST /facturas/<id>/reintentar/` | quien puede verla (*Sin confirmar*: sólo admin) | Reenviar con el mismo número. |
| `POST /facturas/<id>/cancelar/` | admin | Pedir al SIFEN el evento de cancelación. |
| `/facturas/<id>/kude/` y `/xml/` | quien puede verla | Descargar el KuDE (PDF) y el XML firmado desde Factura Segura. |

Una factura ajena responde 404, no 403, para no revelar que existe.

## 7. Código

| Archivo | Qué contiene |
|---------|--------------|
| `core/services/factura_segura.py` | `FacturaSeguraClient` (el protocolo del manual: `call()`, `calcular_de`, `generar_de`, `get_estado_sifen`, `sol_cancelacion`, `download`), `login()`, `SimulatedClient`, `calculate_de()` (fórmulas del SIFEN), `check_digit()` y `build_cdc()`. |
| `core/services/invoicing.py` | Qué se factura, numeración, armado del DE, emisión, reintento, estados, cancelación y descarga. |
| `core/models.py` → `Invoice` | La factura: operación de origen, número, receptor, total, CDC, estado, y el JSON enviado y recibido (para auditar y depurar rechazos). |
| `core/invoice_views.py` | Las pantallas de la tabla anterior. |
| `core/management/commands/` | `factura_segura_token` y `actualizar_facturas`. |
| `core/tests_invoicing.py` | 60 pruebas. |

## 8. Pruebas

```bash
docker compose exec web python manage.py test core.tests_invoicing
```

Nunca llaman a Factura Segura. Cubren, entre otras cosas, que `calculate_de()`
reproduzca **exactamente** los totales del ejemplo del manual (cinco ítems con IVA
5 %, 10 %, gravado parcial, exento y exonerado), que `build_cdc()` arme el CDC de
ejemplo del manual (`01800022475001003000003022024041616519033170`) con su dígito
verificador, el protocolo HTTP (cabecera, cuerpo, código negativo, 401, timeout,
descargas), el rango agotado, el reintento con el mismo número, la interpretación de
cada estado del SIFEN y los permisos de cada pantalla.

## 9. Pendiente

- **Verificar contra el ambiente TEST real.** Las pruebas reproducen el manual, pero
  hasta configurar el token no se envió ningún documento. Puntos a confirmar en la
  primera emisión real: si hace falta el timbrado, cómo acepta Factura Segura un
  receptor no contribuyente (se envían `dRucRec`, `dDVRec` e `iTiContRec` en `0`,
  igual que el manual hace con los campos de identificación que no aplican) y la
  denominación de tarjeta `99` para Stripe. Conviene probar primero con
  `calcular_de`, que no genera documento ni gasta número.
- **Notas de crédito** (`iTiDE=5`, rango `0000109`–`0000120`) para devolver una
  operación ya facturada.
- **Inutilización** de números que hayan quedado sin usar (`sol_inutilizacion`).
