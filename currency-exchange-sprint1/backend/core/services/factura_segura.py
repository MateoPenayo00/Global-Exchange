"""Cliente del API ESI de Factura Segura (``/misife00/v1/esi``).

Factura Segura recibe el documento electrónico (DE) en JSON, genera y firma el
XML, arma el KuDE (la representación impresa en PDF) y lo envía al SIFEN de la
DNIT. Este módulo sólo sabe hablar con Factura Segura; qué se factura y cuándo lo
decide ``services.invoicing``.

Protocolo (manual "API de Factura Segura para ESI", v01):

* Petición: ``POST {"operation": "<nombre>", "params": {...}}`` con la cabecera
  ``Authentication-Token``.
* Respuesta: ``{"code", "description", "operation_info": {"id"}, "results": [...]}``.
  ``code`` 0 o positivo es éxito; negativo, error (el motivo va en ``description``).
* ``dwn_kude`` y ``dwn_xml`` son ``GET /esi/<operación>/<RUC>/<CDC>`` y devuelven
  el archivo binario.

Hay dos clientes con la misma interfaz:

* ``FacturaSeguraClient`` — llama al API real (ambiente TEST o producción).
* ``SimulatedClient`` — no sale de la aplicación: calcula los montos con las
  mismas fórmulas del SIFEN, inventa un CDC con el formato correcto y da el
  documento por aprobado. Se usa cuando no hay token configurado, en las pruebas
  y al poblar la base con datos de demostración, para no gastar el rango de
  numeración real (12 facturas en TEST).
"""

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_PATH = "/misife00/v1/esi"
LOGIN_PATH = "/login?include_auth_token"

DOWNLOAD_KUDE = "dwn_kude"
DOWNLOAD_XML = "dwn_xml"


class FacturaSeguraError(Exception):
    """Factura Segura rechazó la petición o no respondió.

    ``uncertain`` indica que no se sabe si la operación se ejecutó (timeout o
    conexión cortada): el manual pide no reenviarla sin verificar antes.
    """

    def __init__(self, message, *, code=None, operation_id="", results=None, uncertain=False):
        super().__init__(message)
        self.code = code
        self.operation_id = operation_id
        self.results = results or []
        self.uncertain = uncertain


@dataclass
class ApiResponse:
    code: int
    description: str
    operation_id: str
    results: list = field(default_factory=list)

    @property
    def first(self) -> dict:
        return self.results[0] if self.results else {}


def _ruc_without_dv(ruc: str) -> str:
    return str(ruc).split("-")[0].strip()


# --- Cliente real ---------------------------------------------------------


class FacturaSeguraClient:
    """Llama al API ESI de Factura Segura con el token del usuario ESI."""

    simulated = False

    def __init__(self, base_url=None, token=None, timeout=None):
        self.base_url = (base_url or settings.FACTURA_SEGURA_URL).rstrip("/")
        self.token = token if token is not None else settings.FACTURA_SEGURA_TOKEN
        self.timeout = timeout or settings.FACTURA_SEGURA_TIMEOUT

    def _headers(self) -> dict:
        return {"accept": "application/json", "Authentication-Token": self.token}

    def call(self, operation: str, params: dict) -> ApiResponse:
        """Ejecuta ``operation`` y devuelve la respuesta. Lanza ``FacturaSeguraError`` si ``code`` < 0."""
        try:
            response = requests.post(
                f"{self.base_url}{API_PATH}",
                json={"operation": operation, "params": params},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # El servidor pudo haber procesado la petición: el resultado queda sin confirmar.
            logger.warning("Factura Segura no respondió a %s: %s", operation, exc)
            raise FacturaSeguraError(
                "Factura Segura no respondió a tiempo; el resultado quedó sin confirmar.", uncertain=True
            ) from exc
        except requests.RequestException as exc:
            raise FacturaSeguraError(f"No se pudo contactar a Factura Segura: {exc}") from exc

        if response.status_code in (401, 403):
            raise FacturaSeguraError("Factura Segura rechazó el token de autenticación (revisá FACTURA_SEGURA_TOKEN).")
        try:
            body = response.json()
        except ValueError as exc:
            raise FacturaSeguraError(
                f"Factura Segura respondió algo que no es JSON (HTTP {response.status_code})."
            ) from exc

        result = ApiResponse(
            code=int(body.get("code", -1)),
            description=str(body.get("description", "")),
            operation_id=str((body.get("operation_info") or {}).get("id", "")),
            results=body.get("results") or [],
        )
        if result.code < 0:
            detail = "; ".join(str(item.get("Exception", item)) for item in result.results if item)
            message = f"{result.description} (código {result.code})" + (f": {detail}" if detail else "")
            raise FacturaSeguraError(
                message, code=result.code, operation_id=result.operation_id, results=result.results
            )
        return result

    # --- Operaciones ---

    def calcular_de(self, de: dict) -> dict:
        """Completa los montos del DE resumido según el SIFEN. No genera ningún documento."""
        return self.call("calcular_de", {"DE": de}).first.get("DE") or {}

    def generar_de(self, de: dict) -> ApiResponse:
        """Genera el DE. ``first["CDC"]`` trae el CDC; la aprobación del SIFEN llega después."""
        return self.call("generar_de", {"DE": de})

    def get_estado_sifen(self, cdc: str, ruc: str) -> dict:
        return self.call("get_estado_sifen", {"CDC": cdc, "dRucEm": _ruc_without_dv(ruc)}).first

    def sol_cancelacion(self, cdc: str, ruc: str) -> ApiResponse:
        return self.call("sol_cancelacion", {"CDC": cdc, "dRucEm": _ruc_without_dv(ruc)})

    def download(self, kind: str, ruc: str, cdc: str) -> bytes:
        """Descarga el KuDE en PDF (``dwn_kude``) o el XML firmado (``dwn_xml``)."""
        url = f"{self.base_url}{API_PATH}/{kind}/{_ruc_without_dv(ruc)}/{cdc}"
        try:
            response = requests.get(url, headers={"Authentication-Token": self.token}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FacturaSeguraError(f"No se pudo descargar el archivo de Factura Segura: {exc}") from exc
        content_type = response.headers.get("Content-Type", "")
        if response.status_code != 200 or "json" in content_type:
            # Ante un error el API responde con su JSON estándar en lugar del archivo.
            try:
                description = response.json().get("description", "")
            except ValueError:
                description = ""
            raise FacturaSeguraError(
                description or f"Factura Segura no entregó el archivo (HTTP {response.status_code})."
            )
        return response.content


def login(email: str, password: str, base_url=None, timeout=None) -> str:
    """Pide el token de autenticación del usuario ESI. El token vale hasta que cambie la contraseña."""
    base = (base_url or settings.FACTURA_SEGURA_URL).rstrip("/")
    try:
        response = requests.post(
            f"{base}{LOGIN_PATH}",
            json={"email": email, "password": password},
            timeout=timeout or settings.FACTURA_SEGURA_TIMEOUT,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise FacturaSeguraError(f"No se pudo iniciar sesión en Factura Segura: {exc}") from exc
    token = ((body.get("response") or {}).get("user") or {}).get("authentication_token")
    if not token:
        errors = (body.get("response") or {}).get("errors") or body.get("meta")
        raise FacturaSeguraError(f"Factura Segura no devolvió un token: {errors}")
    return token


# --- Cálculo de montos del SIFEN ------------------------------------------
#
# Mismas fórmulas que aplica ``calcular_de`` (manual de Factura Segura y Manual
# Técnico del SIFEN v150). Las usa el cliente simulado; las pruebas verifican que
# reproducen exactamente el ejemplo del manual.

PYG_DECIMALS = Decimal("1")
OTHER_DECIMALS = Decimal("0.00000001")


def _dec(value) -> Decimal:
    return Decimal(str(value or "0").replace(",", "."))


def _fmt(value: Decimal, quantum: Decimal) -> str:
    text = f"{value.quantize(quantum, rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def calculate_de(summary: dict) -> dict:
    """Devuelve una copia de ``summary`` con los campos calculados de ítems y totales."""
    de = dict(summary)
    quantum = PYG_DECIMALS if de.get("cMoneOpe", "PYG") == "PYG" else OTHER_DECIMALS
    totals = {key: Decimal("0") for key in (
        "dSubExe", "dSubExo", "dSub5", "dSub10", "dTotDesc", "dIVA5", "dIVA10", "dBaseGrav5", "dBaseGrav10"
    )}

    items = []
    for raw in summary.get("gCamItem", []):
        item = dict(raw)
        quantity, price, discount = _dec(item["dCantProSer"]), _dec(item["dPUniProSer"]), _dec(item.get("dDescItem"))
        affect, proportion, tax_rate = str(item["iAfecIVA"]), _dec(item.get("dPropIVA")), _dec(item.get("dTasaIVA"))

        total_item = (price - discount) * quantity
        gross = price * quantity
        denominator = Decimal("10000") + tax_rate * proportion
        if affect in ("1", "4"):
            base = Decimal("100") * total_item * proportion / denominator
            vat = base * tax_rate / Decimal("100")
        else:
            base = vat = Decimal("0")
        exempt_base = (
            Decimal("100") * total_item * (Decimal("100") - proportion) / denominator if affect == "4" else Decimal("0")
        )
        # Los totales suman los valores ya redondeados de cada ítem, igual que el SIFEN.
        total_item, base, vat, exempt_base = (v.quantize(quantum, rounding=ROUND_HALF_UP) for v in (total_item, base, vat, exempt_base))

        item.update(
            {
                "dTiCamIt": "",
                "dTotBruOpeItem": _fmt(gross, quantum),
                "dPorcDesIt": _fmt(discount / price * 100, OTHER_DECIMALS).replace(".", ",") if price else "0",
                "dTotOpeItem": _fmt(total_item, quantum),
                "dTotOpeGs": "",
                "dBasGravIVA": _fmt(base, quantum),
                "dLiqIVAItem": _fmt(vat, quantum),
                "dBasExe": _fmt(exempt_base, quantum),
            }
        )
        items.append(item)

        totals["dTotDesc"] += discount * quantity
        if affect == "3":
            totals["dSubExe"] += total_item
        elif affect == "2":
            totals["dSubExo"] += total_item
        elif affect == "4":
            totals["dSubExe"] += exempt_base
        if affect in ("1", "4"):
            gravado = total_item if affect == "1" else base + vat
            if tax_rate == 5:
                totals["dSub5"] += gravado
                totals["dIVA5"] += vat
                totals["dBaseGrav5"] += base
            elif tax_rate == 10:
                totals["dSub10"] += gravado
                totals["dIVA10"] += vat
                totals["dBaseGrav10"] += base

    total_operation = totals["dSubExe"] + totals["dSubExo"] + totals["dSub5"] + totals["dSub10"]
    total_vat = totals["dIVA5"] + totals["dIVA10"]
    de["gCamItem"] = items
    de.update({key: _fmt(value, quantum) for key, value in totals.items()})
    de.update(
        {
            "dTotOpe": _fmt(total_operation, quantum),
            "dTotDescGlotem": "0",
            "dTotAntItem": "0",
            "dTotAnt": "0",
            "dPorcDescTotal": "0",
            "dDescTotal": _fmt(totals["dTotDesc"], quantum),
            "dAnticipo": "0",
            "dRedon": "0",
            "dComi": "0",
            "dTotGralOpe": _fmt(total_operation, quantum),
            "dLiqTotIVA5": "0",
            "dLiqTotIVA10": "0",
            "dIVAComi": "0",
            "dTotIVA": _fmt(total_vat, quantum),
            "dTBasGraIVA": _fmt(totals["dBaseGrav5"] + totals["dBaseGrav10"], quantum),
            "dTotalGs": "" if quantum == PYG_DECIMALS else _fmt(total_operation * _dec(de.get("dTiCam")), PYG_DECIMALS),
        }
    )
    return de


# --- CDC ------------------------------------------------------------------


def check_digit(number: str, base_max: int = 11) -> int:
    """Dígito verificador módulo 11 de la DNIT (el del RUC y el del CDC)."""
    total, factor = 0, 2
    for digit in reversed(str(number)):
        if factor > base_max:
            factor = 2
        total += int(digit) * factor
        factor += 1
    remainder = total % 11
    return 11 - remainder if remainder > 1 else 0


def build_cdc(de: dict, security_code: str) -> str:
    """Arma el CDC de 44 dígitos: tipo, RUC, DV, establecimiento, punto, número,
    tipo de contribuyente, fecha, tipo de emisión, código de seguridad y su DV."""
    issued = datetime.fromisoformat(de["dFeEmiDE"])
    body = (
        f"{int(de['iTiDE']):02d}"
        f"{_ruc_without_dv(de['dRucEm']).zfill(8)}"
        f"{de['dDVEmi']}"
        f"{de['dEst']}{de['dPunExp']}{de['dNumDoc']}"
        f"{de['iTipCont']}"
        f"{issued:%Y%m%d}"
        f"{de['iTipEmi']}"
        f"{security_code}"
    )
    return f"{body}{check_digit(body)}"


# --- Cliente simulado -----------------------------------------------------


class SimulatedClient:
    """Imita a Factura Segura sin salir de la aplicación. Todo documento queda aprobado."""

    simulated = True

    @staticmethod
    def _response(results=None) -> ApiResponse:
        return ApiResponse(code=0, description="OK (simulado)", operation_id=f"sim-{secrets.token_hex(8)}", results=results or [])

    def calcular_de(self, de: dict) -> dict:
        return calculate_de(de)

    def generar_de(self, de: dict) -> ApiResponse:
        security_code = f"{secrets.randbelow(10**9):09d}"
        return self._response([{"CDC": build_cdc(de, security_code)}])

    def get_estado_sifen(self, cdc: str, ruc: str) -> dict:
        return {
            "estado_sifen": "Aprobado",
            "desc_sifen": "0260 - Aprobado (simulado)",
            "error_sifen": "",
            "fch_sifen": "",
            "estado_can": "",
            "desc_can": "",
            "error_can": "",
            "fch_can": "",
            "estado_inu": "",
            "desc_inu": "",
            "error_inu": "",
            "fch_inu": "",
        }

    def sol_cancelacion(self, cdc: str, ruc: str) -> ApiResponse:
        return self._response()

    def download(self, kind: str, ruc: str, cdc: str) -> bytes:
        raise FacturaSeguraError("Las facturas simuladas no tienen KuDE ni XML de Factura Segura.")
