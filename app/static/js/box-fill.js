(() => {
  const root = document.querySelector('[data-box-fill]');
  if (!root) return;
  const find = selector => root.querySelector(selector);
  const form = find('[data-fill-form]');
  const results = find('[data-fill-results]');
  const summary = find('[data-fill-summary]');
  const status = find('[data-fill-status]');
  const errorBox = find('[data-fill-error]');
  const search = find('[data-fill-search]');
  const category = find('[data-fill-category]');
  const selection = new Map();
  let rows = [];
  let page = 1;
  let pages = 0;
  let requestNumber = 0;
  let controller;
  let debounce;
  let submitting = false;
  let receiptConflict = false;
  let reviewing = false;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function amount(row) {
    const raw = String(row.quantity ?? '');
    return /^[0-9]+$/.test(raw) && Number.isSafeInteger(Number(raw)) ? Number(raw) : NaN;
  }
  function problem(row) {
    const value = amount(row);
    if (!Number.isFinite(value) || value < 1 || value > 1000000) return 'Saisis un entier entre 1 et 1 000 000.';
    if (value > Number(row.available)) return `${value} demandés, seulement ${row.available} disponibles.`;
    return row.error || '';
  }
  function makeButton(text, action, id) {
    const button = node('button', text, 'button button-secondary');
    button.type = 'button';
    button.dataset.fillAction = action;
    button.dataset.itemId = id;
    return button;
  }
  function card(row, inSummary = false) {
    const id = String(row.id);
    const article = node('article', undefined, 'fill-item panel');
    if (inSummary) article.dataset.summaryItem = id;
    else article.dataset.fillItem = id;
    if (row.photo_url) {
      const image = node('img', undefined, 'fill-thumbnail');
      image.src = row.photo_url;
      image.alt = '';
      image.loading = 'lazy';
      image.decoding = 'async';
      image.width = 64;
      image.height = 64;
      article.append(image);
    } else {
      const placeholder = node('span', '▧', 'fill-thumbnail fill-placeholder');
      placeholder.setAttribute('aria-hidden', 'true');
      article.append(placeholder);
    }
    const body = node('div', undefined, 'fill-item-body');
    if (!inSummary) {
      const label = node('label', undefined, 'fill-choice');
      const check = node('input');
      check.type = 'checkbox';
      check.dataset.selectItem = id;
      check.setAttribute('aria-label', `Sélectionner ${row.name}`);
      label.append(check, node('strong', row.name));
      body.append(label);
    } else body.append(node('h3', row.name));
    if (row.category) body.append(node('p', row.category, 'field-hint'));
    body.append(node('p', `Disponibles hors boîte : ${row.available}`, 'fill-availability'));
    if (row.present) body.append(node('p', `Déjà présents dans cette boîte : ${row.present}`, 'field-hint'));
    const controls = node('div', undefined, 'fill-quantity');
    controls.dataset.itemControls = id;
    const inputId = `fill-${inSummary ? 'summary' : 'result'}-quantity-${id}`;
    const label = node('label', `Quantité à ajouter · ${row.name}`);
    label.htmlFor = inputId;
    const input = node('input');
    input.type = 'number'; input.min = '1'; input.max = String(Math.min(1000000, Number(row.available)));
    input.step = '1'; input.inputMode = 'numeric'; input.id = inputId;
    input.dataset.quantityItem = id;
    const minus = makeButton('−', 'minus', id);
    minus.setAttribute('aria-label', `Diminuer la quantité de ${row.name}`);
    const plus = makeButton('+', 'plus', id);
    plus.setAttribute('aria-label', `Augmenter la quantité de ${row.name}`);
    controls.append(label, minus, input, plus, makeButton('Tout le disponible', 'all', id));
    if (inSummary) controls.append(makeButton('Retirer de la sélection', 'remove', id));
    body.append(controls);
    const feedback = node('p', '', 'fill-line-error');
    feedback.id = inputId + '-error';
    feedback.dataset.itemError = id;
    feedback.hidden = true;
    input.setAttribute('aria-describedby', feedback.id);
    body.append(feedback);
    article.append(body);
    return article;
  }
  function sync() {
    let units = 0;
    let invalid = false;
    for (const row of selection.values()) {
      const value = amount(row);
      if (Number.isFinite(value) && value > 0) units += value;
      if (problem(row)) invalid = true;
    }
    find('[data-fill-count]').textContent = String(selection.size);
    find('[data-fill-units]').textContent = String(units);
    find('[data-fill-invalid]').hidden = !invalid;
    find('[data-fill-submit]').disabled = submitting || receiptConflict || !selection.size || invalid;
    find('[data-clear-selection]').disabled = submitting || !selection.size;
    find('[data-show-selection]').disabled = submitting || !selection.size;
    find('[data-select-visible]').disabled = submitting || !rows.length;
    for (const check of root.querySelectorAll('[data-select-item]')) {
      check.checked = selection.has(check.dataset.selectItem);
      check.disabled = submitting;
    }
    for (const controls of root.querySelectorAll('[data-item-controls]')) {
      const id = controls.dataset.itemControls;
      const row = selection.get(id);
      controls.hidden = !row;
      if (!row) continue;
      const input = controls.querySelector('input');
      if (document.activeElement !== input) input.value = row.quantity;
      input.max = String(Math.min(1000000, Number(row.available)));
      input.disabled = submitting;
      const value = amount(row);
      const valid = Number.isFinite(value) && value >= 1 && value <= row.available && value <= 1000000;
      controls.querySelector('[data-fill-action=minus]').disabled = submitting || !valid || value <= 1;
      controls.querySelector('[data-fill-action=plus]').disabled = submitting || !valid || value >= Math.min(1000000, row.available);
      controls.querySelector('[data-fill-action=all]').disabled = submitting || row.available < 1;
      const remove = controls.querySelector('[data-fill-action=remove]');
      if (remove) remove.disabled = submitting;
      const message = problem(row);
      input.setAttribute('aria-invalid', String(Boolean(message)));
    }
    for (const feedback of root.querySelectorAll('[data-item-error]')) {
      const row = selection.get(feedback.dataset.itemError);
      const message = row ? problem(row) : '';
      feedback.textContent = message;
      feedback.hidden = !message;
    }
  }
  function renderResults() {
    results.replaceChildren(...rows.map(row => card(row)));
    if (!rows.length) results.append(node('p', 'Aucun objet disponible hors boîte pour cette recherche.', 'empty-state'));
    find('[data-select-visible]').disabled = submitting || !rows.length;
    find('[data-page-previous]').disabled = page <= 1;
    find('[data-page-next]').disabled = page >= pages;
    find('[data-fill-page]').textContent = pages ? `Page ${page} sur ${pages}` : 'Aucun résultat';
    sync();
  }
  function renderSummary() {
    summary.replaceChildren(...Array.from(selection.values(), row => card(row, true)));
    if (!selection.size) summary.append(node('p', 'Aucun objet sélectionné.', 'empty-state'));
    sync();
  }
  function review(show) {
    reviewing = show;
    find('[data-results-view]').hidden = show;
    find('[data-selection-view]').hidden = !show;
    if (show) { renderSummary(); find('[data-selection-heading]').focus(); }
    else search.focus();
  }
  function select(row) {
    const id = String(row.id);
    if (!selection.has(id)) selection.set(id, {...row, quantity: '1', error: ''});
  }
  function edit(id, raw) {
    const row = selection.get(id);
    if (!row) return;
    row.quantity = raw;
    row.error = '';
    sync();
  }
  root.addEventListener('change', event => {
    const check = event.target.closest('[data-select-item]');
    if (!check || submitting) return;
    const id = check.dataset.selectItem;
    if (check.checked) select(rows.find(row => String(row.id) === id));
    else selection.delete(id);
    sync();
  });
  root.addEventListener('input', event => {
    const input = event.target.closest('[data-quantity-item]');
    if (input && !submitting) edit(input.dataset.quantityItem, input.value);
  });
  root.addEventListener('click', event => {
    const button = event.target.closest('[data-fill-action]');
    if (!button || submitting) return;
    const id = button.dataset.itemId;
    const row = selection.get(id);
    if (!row) return;
    if (button.dataset.fillAction === 'remove') {
      selection.delete(id);
      renderSummary();
      return;
    }
    const action = button.dataset.fillAction;
    const value = action === 'all' ? Math.min(1000000, row.available) : amount(row) + (action === 'plus' ? 1 : -1);
    if (Number.isSafeInteger(value) && value >= 1 && value <= Math.min(1000000, row.available)) edit(id, String(value));
  });
  find('[data-select-visible]').addEventListener('click', () => { if (!submitting) { rows.forEach(select); sync(); } });
  find('[data-clear-selection]').addEventListener('click', () => { if (!submitting) { selection.clear(); renderSummary(); } });
  find('[data-show-selection]').addEventListener('click', () => review(true));
  find('[data-back-results]').addEventListener('click', () => review(false));

  async function loadPage(nextPage = 1) {
    if (controller) controller.abort();
    controller = new AbortController();
    const current = ++requestNumber;
    const url = new URL(root.dataset.optionsUrl, window.location.origin);
    url.searchParams.set('q', search.value);
    url.searchParams.set('category', category.value);
    url.searchParams.set('page', String(nextPage));
    results.setAttribute('aria-busy', 'true');
    status.textContent = 'Chargement du matériel disponible…';
    try {
      const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin', headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (current !== requestNumber) return;
      rows = data.rows;
      page = data.page; pages = data.pages;
      for (const row of rows) {
        const chosen = selection.get(String(row.id));
        if (chosen) selection.set(String(row.id), {...chosen, ...row, error: '', quantity: chosen.quantity});
      }
      renderResults();
      status.textContent = `${data.total} objet(s) disponible(s) hors boîte. Les sélections masquées sont conservées.`;
      if (reviewing) renderSummary();
    } catch (error) {
      if (error.name !== 'AbortError' && current === requestNumber) status.textContent = 'La recherche n’a pas pu être actualisée. Les résultats précédents et ta sélection sont conservés. Réessaie.';
    } finally {
      if (current === requestNumber) results.setAttribute('aria-busy', 'false');
    }
  }
  search.addEventListener('input', () => {
    clearTimeout(debounce);
    if (controller) controller.abort();
    requestNumber++;
    debounce = setTimeout(() => loadPage(1), 180);
  });
  category.addEventListener('change', () => { clearTimeout(debounce); loadPage(1); });
  find('[data-fill-filters]').addEventListener('submit', event => { event.preventDefault(); clearTimeout(debounce); loadPage(1); });
  find('[data-page-next]').addEventListener('click', () => loadPage(page + 1));
  find('[data-page-previous]').addEventListener('click', () => loadPage(page - 1));
  find('[data-refresh-results]').addEventListener('click', () => loadPage(page));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting || receiptConflict || !selection.size || Array.from(selection.values()).some(problem)) return;
    submitting = true;
    sync();
    errorBox.hidden = true;
    const data = new FormData(form);
    for (const row of selection.values()) {
      data.append('selected', String(row.id));
      data.append(`quantity_${row.id}`, String(row.quantity));
      data.append(`label_${row.id}`, row.name);
    }
    let succeeded = false;
    try {
      // No files are sent: URL encoding avoids the multipart part-count limit
      // for a selection that spans many pages, while preserving repeated IDs.
      const response = await fetch(form.action, {method: 'POST', body: new URLSearchParams(data), credentials: 'same-origin', headers: {Accept: 'application/json'}});
      const payload = await response.json();
      if (response.ok && payload.ok) {
        succeeded = true;
        window.location.assign(payload.redirect);
        return;
      }
      for (const row of payload.rows || []) {
        const old = selection.get(String(row.id)) || {};
        selection.set(String(row.id), {...old, ...row});
      }
      receiptConflict = response.status === 409;
      errorBox.textContent = payload.message || 'L’ajout a été refusé. Vérifie les lignes sélectionnées.';
      errorBox.hidden = false;
      review(true);
    } catch (_) {
      errorBox.textContent = 'Le résultat de l’enregistrement n’a pas pu être confirmé. Consulte la boîte ou réessaie sans modifier la sélection : la même validation est protégée contre les doublons.';
      errorBox.hidden = false;
    } finally {
      if (!succeeded) { submitting = false; sync(); }
    }
  });
  // Reserve room for the measured bar, including when a mobile keyboard shrinks
  // the visual viewport. No selected row is stored in the DOM as the authority.
  function fitBar() {
    const viewport = window.visualViewport;
    const inset = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
    root.style.setProperty('--fill-keyboard-inset', `${inset}px`);
    root.style.setProperty('--fill-bar-height', `${find('[data-fill-bar]').getBoundingClientRect().height}px`);
    const input = document.activeElement;
    if (input && root.contains(input) && input.matches('[data-quantity-item], [data-fill-search]')) {
      const bounds = input.getBoundingClientRect();
      const top = (viewport ? viewport.offsetTop : 0) + 16;
      const bottom = find('[data-fill-bar]').getBoundingClientRect().top - 12;
      if (bounds.bottom > bottom) window.scrollBy(0, bounds.bottom - bottom);
      else if (bounds.top < top) window.scrollBy(0, bounds.top - top);
    }
  }
  if (window.ResizeObserver) new ResizeObserver(fitBar).observe(find('[data-fill-bar]'));
  window.addEventListener('resize', fitBar);
  root.addEventListener('focusin', event => {
    if (event.target.matches('[data-quantity-item], [data-fill-search]')) requestAnimationFrame(fitBar);
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', fitBar);
    window.visualViewport.addEventListener('scroll', fitBar);
  }
  for (const row of JSON.parse(find('[data-initial-selection]').textContent)) selection.set(String(row.id), row);
  if (selection.size) review(true);
  sync(); fitBar();
  root.dataset.ready = 'true';
  loadPage(1);
})();
