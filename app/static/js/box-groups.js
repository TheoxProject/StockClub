(() => {
  const form = document.querySelector('[data-group-form]');
  if (!form) return;
  const lines = Array.from(form.querySelectorAll('[data-group-line]'));
  const summary = form.querySelector('[data-group-summary]');
  function update() {
    summary.replaceChildren();
    let selected = 0;
    for (const line of lines) {
      const checkbox = line.querySelector('input[type=checkbox]');
      const quantity = line.querySelector('input[type=number]');
      const active = checkbox.checked && !checkbox.disabled;
      quantity.required = active;
      // Unselected invalid quantities must not block partial submissions.
      quantity.disabled = !active;
      if (!active) continue;
      selected++;
      const item = document.createElement('li');
      item.textContent = `${line.dataset.label} : ${quantity.value || '—'}`;
      summary.append(item);
    }
    form.querySelector('[data-group-empty]').hidden = selected > 0;
  }
  form.querySelector('[data-select-all]').addEventListener('click', () => {
    for (const line of lines) {
      const checkbox = line.querySelector('input[type=checkbox]');
      if (!checkbox.disabled) checkbox.checked = true;
    }
    update();
  });
  form.querySelector('[data-select-none]').addEventListener('click', () => {
    for (const line of lines) line.querySelector('input[type=checkbox]').checked = false;
    update();
  });
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  form.addEventListener('submit', event => {
    if (!lines.some(line => line.querySelector('input[type=checkbox]').checked && !line.querySelector('input[type=checkbox]').disabled)) {
      event.preventDefault();
      form.querySelector('[data-group-empty]').hidden = false;
    }
  });
  update();
})();
