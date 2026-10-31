(() => {
  const form = document.querySelector('[data-stock-operation]');
  if (!form) return;
  const quantity = form.elements.quantity;
  const operation = form.elements.operation;
  const reason = form.elements.reason;
  const detail = form.elements.detail;
  const available = Number(form.dataset.available);
  const outstanding = Number(form.dataset.outstanding);
  const remaining = Number(form.dataset.remaining || 0);
  const maximum = Number(form.dataset.maximum);
  const preview = form.querySelector('[data-stock-preview]');
  const error = preview.querySelector('[data-preview-error]');
  function update() {
    const kind = operation ? operation.value : 'write-off';
    if (operation) {
      for (const option of reason.options) {
        if (!option.dataset.operations) continue;
        const allowed = option.dataset.operations.split(' ').includes(kind);
        option.disabled = !allowed;
        option.hidden = !allowed;
      }
      if (reason.selectedOptions[0] && reason.selectedOptions[0].disabled) reason.value = '';
      detail.required = reason.value === 'autre';
    }
    let warning = '';
    const raw = quantity.value.trim();
    const amount = Number(raw);
    let afterAvailable = available;
    let afterOut = outstanding;
    if (!/^\d+$/.test(raw) || !Number.isSafeInteger(amount) || amount < 1 || amount > maximum) {
      warning = 'Saisis une quantité entière entre 1 et 1 000 000.';
    } else if (kind === 'add') {
      afterAvailable += amount;
      if (afterAvailable + afterOut > maximum) warning = 'Le stock total dépasserait 1 000 000 unités.';
    } else if (kind === 'remove') {
      afterAvailable -= amount;
      if (afterAvailable < 0) warning = 'Le retrait dépasse le disponible.';
    } else {
      afterOut -= amount;
      if (amount > remaining) warning = 'La déclaration dépasse le restant à rendre sur cet emprunt.';
    }
    preview.querySelector('[data-preview-total]').textContent = warning ? '—' : String(afterAvailable + afterOut);
    preview.querySelector('[data-preview-available]').textContent = warning ? '—' : String(afterAvailable);
    preview.querySelector('[data-preview-out]').textContent = warning ? '—' : String(afterOut);
    const loanRemaining = preview.querySelector('[data-preview-remaining]');
    if (loanRemaining) loanRemaining.textContent = warning ? '—' : String(remaining - amount);
    error.textContent = warning;
    error.hidden = !warning;
  }
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
})();
