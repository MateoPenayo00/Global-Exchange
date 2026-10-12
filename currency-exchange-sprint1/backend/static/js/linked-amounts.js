// Montos vinculados: lo que entrega el cliente y lo que recibe, cada uno con su
// selector de divisa. Al escribir en uno se completa el otro.
//
// Repite la cuenta de core/services/pricing.py (convert): si se cambia una hay
// que cambiar la otra. El servidor recalcula siempre desde el campo que escribió
// el usuario (el "ancla"), así que esta cuenta es sólo para mostrar.
//
// Marcado que espera, dentro de un [data-linked-amounts]:
// * data-rates: id del <script type="application/json"> con las cotizaciones
//   (por id de divisa: code, buy_rate, sell_rate, decimals).
// * data-value-step="1" (opcional): redondea el valor intermedio a guaraníes
//   enteros, como la compra en línea.
// * [data-amount="in"] y [data-amount="out"]: los dos montos.
// * [data-currency="in"] y [data-currency="out"]: los dos selectores.
// * [data-anchor]: campo oculto donde se anota qué monto escribió el usuario.
//
// Cada vez que recalcula dispara "linked-amounts:change" en el contenedor, con
// el resultado en event.detail (o null si falta algún dato).
(function () {
  var EPSILON = 1e-6; // margen para el error de punto flotante al redondear

  function floorTo(x, decimals) {
    var f = Math.pow(10, decimals);
    return Math.floor(x * f + EPSILON) / f;
  }

  function ceilTo(x, decimals) {
    var f = Math.pow(10, decimals);
    return Math.ceil(x * f - EPSILON) / f;
  }

  function init(root) {
    var rates = JSON.parse(document.getElementById(root.dataset.rates).textContent);
    var roundValue = root.dataset.valueStep === '1';
    var amountIn = root.querySelector('[data-amount="in"]');
    var amountOut = root.querySelector('[data-amount="out"]');
    var currencyIn = root.querySelector('[data-currency="in"]');
    var currencyOut = root.querySelector('[data-currency="out"]');
    var anchor = root.querySelector('[data-anchor]');

    function emit(detail) {
      root.dispatchEvent(new CustomEvent('linked-amounts:change', { detail: detail }));
    }

    function recompute() {
      var cin = rates[currencyIn.value];
      var cout = rates[currencyOut.value];
      var side = anchor.value || (amountOut.value ? 'out' : 'in');
      var source = side === 'in' ? amountIn : amountOut;
      var target = side === 'in' ? amountOut : amountIn;
      var amount = parseFloat(source.value);

      if (!cin || !cout || currencyIn.value === currencyOut.value || !(amount > 0)) {
        if (source.value === '') { target.value = ''; }
        emit(null);
        return;
      }

      var rateIn = parseFloat(cin.buy_rate);
      var rateOut = parseFloat(cout.sell_rate);
      var value, inValue, outValue;
      if (side === 'in') {
        inValue = amount;
        value = inValue * rateIn;
        if (roundValue) { value = floorTo(value, 0); }
        outValue = floorTo(value / rateOut, cout.decimals);
        amountOut.value = outValue > 0 ? outValue.toFixed(cout.decimals) : '';
      } else {
        outValue = amount;
        value = outValue * rateOut;
        if (roundValue) { value = ceilTo(value, 0); }
        inValue = ceilTo(value / rateIn, cin.decimals);
        amountIn.value = inValue.toFixed(cin.decimals);
      }

      emit({
        side: side,
        amountIn: inValue,
        amountOut: outValue,
        value: value,
        currencyIn: cin,
        currencyOut: cout
      });
    }

    amountIn.addEventListener('input', function () { anchor.value = 'in'; recompute(); });
    amountOut.addEventListener('input', function () { anchor.value = 'out'; recompute(); });
    // Al cambiar una divisa se mantiene el monto que escribió el usuario y se
    // recalcula el otro.
    currencyIn.addEventListener('change', recompute);
    currencyOut.addEventListener('change', recompute);
    recompute();
  }

  document.querySelectorAll('[data-linked-amounts]').forEach(init);
})();
