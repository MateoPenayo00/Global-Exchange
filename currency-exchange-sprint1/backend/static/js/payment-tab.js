// Pestañas de pago (compra en línea y operación de caja): cuenta regresiva del
// plazo y paneles que dependen del medio de pago elegido.
//
// Marcado que espera:
// * [data-countdown] con data-seconds-left y data-timeout, y dentro #timer,
//   #timer-value, #timer-fill y #timer-expired.
// * [data-payment-form]: el formulario con los radios del medio de pago.
// * [data-show-for="valor"]: se muestra sólo si el radio elegido tiene ese
//   data-kind (o ese value).
//
// El servidor es quien decide: aunque se manipule este reloj, una operación
// vencida se rechaza al confirmar. Acá sólo se avisa y se recarga.
(function () {
  var root = document.querySelector('[data-countdown]');
  var form = document.querySelector('[data-payment-form]');
  if (!root || !form) { return; }

  var timeout = parseInt(root.dataset.timeout, 10) || 300;
  // Se cuenta desde lo que informó el servidor, no desde el reloj del equipo,
  // que puede estar desfasado.
  var deadline = Date.now() + parseInt(root.dataset.secondsLeft, 10) * 1000;
  var value = document.getElementById('timer-value');
  var fill = document.getElementById('timer-fill');
  var timer = document.getElementById('timer');
  var panels = document.querySelectorAll('[data-show-for]');
  var finished = false;

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  function tick() {
    var left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    value.textContent = Math.floor(left / 60) + ':' + pad(left % 60);
    fill.style.width = (100 * left / timeout).toFixed(2) + '%';
    timer.classList.toggle('is-urgent', left <= 60);
    if (left === 0 && !finished) {
      finished = true;
      form.querySelectorAll('input, button, select, textarea').forEach(function (el) { el.disabled = true; });
      document.getElementById('timer-expired').hidden = false;
      // La recarga muestra el estado "vencida" que registra el servidor.
      setTimeout(function () { window.location.reload(); }, 2500);
    }
  }

  function syncPanels() {
    var checked = form.querySelector('input[type="radio"]:checked');
    var kind = checked ? (checked.dataset.kind || checked.value) : '';
    panels.forEach(function (panel) {
      panel.hidden = panel.dataset.showFor.split(' ').indexOf(kind) === -1;
    });
  }

  form.addEventListener('change', syncPanels);
  syncPanels();
  tick();
  setInterval(tick, 500);
})();
