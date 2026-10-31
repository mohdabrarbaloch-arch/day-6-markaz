/* ============================================================================
   Markaz — SPA
   No framework, no build step. One render function per view, one fetch helper,
   and every number on screen comes from the API rather than a fixture.
   ========================================================================= */
'use strict';

// ─────────────────────────────────────────────────────────────── state ─────
const state = {
  token: localStorage.getItem('markaz_token') || null,
  user: null,
  view: 'regions',
  catalog: null,
  planVerdict: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ─────────────────────────────────────────────────────────────── utils ─────
const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );

const money = (n) =>
  '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });

const pct = (n) => Math.round(Number(n || 0) * 100) + '%';

let toastTimer = null;
function toast(msg, kind = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast' + (kind ? ' is-' + kind : '');
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

// ─────────────────────────────────────────────────────────────── api ──────
async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let payload = null;
  if (res.status !== 204) {
    const text = await res.text();
    try { payload = text ? JSON.parse(text) : null; } catch { payload = { detail: text }; }
  }

  if (res.status === 401 && auth && state.token) {
    signOut(true);
    throw new Error('Your session expired. Please sign in again.');
  }

  if (!res.ok) {
    const detail = payload && payload.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : detail && detail.message
        ? detail.message
        : `Request failed (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

// ─────────────────────────────────────────────────────────────── auth ─────
function setAuthTab(tab) {
  $$('[data-auth-tab]').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.authTab === tab)
  );
  $$('[data-only]').forEach((el) => {
    el.hidden = el.dataset.only !== tab;
  });
  $('#auth-submit .btn-label').textContent = tab === 'login' ? 'Sign in' : 'Create account';
  $('#auth-error').hidden = true;
}

let authMode = 'login';

$('#auth-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = $('#auth-error');
  errEl.hidden = true;

  const fd = new FormData(e.target);
  const btn = $('#auth-submit');
  btn.disabled = true;

  try {
    const body =
      authMode === 'login'
        ? { email: fd.get('email'), password: fd.get('password') }
        : {
            email: fd.get('email'),
            password: fd.get('password'),
            display_name: fd.get('display_name') || 'Operator',
          };

    const data = await api(`/api/auth/${authMode}`, { method: 'POST', body, auth: false });
    state.token = data.access_token;
    state.user = data.user;
    localStorage.setItem('markaz_token', state.token);
    await boot();
  } catch (err) {
    // Pydantic's 422 comes back as a list of field errors, not a string.
    let msg = err.message;
    if (err.payload && Array.isArray(err.payload.detail) && err.payload.detail.length) {
      msg = err.payload.detail.map((d) => d.msg || d.message || 'Invalid value').join('; ');
    }
    errEl.textContent = msg;
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

$$('[data-auth-tab]').forEach((btn) =>
  btn.addEventListener('click', () => {
    authMode = btn.dataset.authTab;
    setAuthTab(authMode);
  })
);

function signOut(silent = false) {
  state.token = null;
  state.user = null;
  state.catalog = null;
  localStorage.removeItem('markaz_token');
  $('#app-shell').hidden = true;
  $('#auth-screen').hidden = false;
  if (!silent) toast('Signed out.');
}

$('#sign-out').addEventListener('click', () => signOut());

// ─────────────────────────────────────────────────────────────── nav ──────
$('#nav').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-view]');
  if (!btn) return;
  showView(btn.dataset.view);
});

function showView(name) {
  state.view = name;
  $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.dataset.view === name));

  if (name === 'regions') loadRegions();
  if (name === 'deployments') loadDeployments();
  if (name === 'pipelines') loadPipelines();
  if (name === 'drift') loadDrift();
}

// ─────────────────────────────────────────────────────────────── regions ──
async function loadRegions() {
  const grid = $('#regions-grid');
  try {
    const [regions, catalog] = await Promise.all([
      api('/api/regions'),
      state.catalog ? Promise.resolve(state.catalog) : api('/api/regions/catalog'),
    ]);
    state.catalog = catalog;

    grid.innerHTML = regions.map(regionCard).join('');
    renderCatalog(catalog);
    populateSelects(catalog);
  } catch (err) {
    grid.innerHTML = `<div class="empty"><strong>Could not load regions</strong>${esc(err.message)}</div>`;
  }
}

function regionCard(r) {
  const used = r.gpu_capacity ? r.committed_gpus / r.gpu_capacity : 0;
  const cls = used >= 1 ? 'is-full' : used >= 0.8 ? 'is-tight' : '';
  return `
    <article class="region-card">
      <div class="region-top">
        <span class="region-name">${esc(r.display_name)}</span>
        <span class="region-code">${esc(r.code)}</span>
      </div>
      <div class="region-sub">${esc(r.skus.length)} GPU shapes · ${pct(r.utilisation)} committed</div>

      <div class="meter" role="img" aria-label="${r.committed_gpus} of ${r.gpu_capacity} GPU-equivalents committed">
        <div class="meter-fill ${cls}" style="width:${Math.min(used * 100, 100)}%"></div>
      </div>

      <div class="region-stats">
        <span><strong>${r.committed_gpus}</strong>/${r.gpu_capacity} GPUs</span>
        <span><strong>${r.headroom_gpus}</strong> free</span>
      </div>

      <div class="region-stats" style="margin-top:6px">
        <span>${r.active_deployments} deployment${r.active_deployments === 1 ? '' : 's'}</span>
        <span>${money(r.monthly_cost_usd)}/mo</span>
      </div>

      <div class="region-tags">
        ${r.data_residency.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}
      </div>
    </article>`;
}

function renderCatalog(cat) {
  const body = $('#catalog-body');
  if (!cat) { body.innerHTML = ''; return; }

  body.innerHTML = `
    <div class="catalog-col">
      <h4>Models (${cat.models.length})</h4>
      ${cat.models
        .map(
          (m) => `
        <div class="catalog-row">
          <span class="name">${esc(m.display_name)}</span>
          <span class="val">${m.min_vram_gb}GB</span>
        </div>`
        )
        .join('')}
    </div>
    <div class="catalog-col">
      <h4>GPU shapes (${cat.skus.length})</h4>
      ${cat.skus
        .map(
          (s) => `
        <div class="catalog-row">
          <span class="name">${esc(s.display_name)}</span>
          <span class="val">$${s.hourly_usd.toFixed(2)}/hr</span>
        </div>`
        )
        .join('')}
    </div>
    <div class="catalog-col">
      <h4>Regions (${cat.regions.length})</h4>
      ${cat.regions
        .map(
          (r) => `
        <div class="catalog-row">
          <span class="name">${esc(r.code)}</span>
          <span class="val">${r.gpu_capacity} GPU</span>
        </div>`
        )
        .join('')}
    </div>`;
}

function populateSelects(cat) {
  if (!cat) return;
  const model = $('#sel-model');
  const region = $('#sel-region');
  const sku = $('#sel-sku');
  if (!model || model.dataset.ready === '1') return;

  model.innerHTML = cat.models
    .map((m) => `<option value="${esc(m.code)}">${esc(m.display_name)} · ${m.min_vram_gb}GB</option>`)
    .join('');

  region.innerHTML = cat.regions
    .map((r) => `<option value="${esc(r.code)}">${esc(r.display_name)}</option>`)
    .join('');

  sku.innerHTML = cat.skus
    .map((s) => `<option value="${esc(s.code)}">${esc(s.display_name)} · $${s.hourly_usd.toFixed(2)}/hr</option>`)
    .join('');

  [model, region, sku].forEach((el) => (el.dataset.ready = '1'));

  // Default to the region with the most headroom so the first plan is not a rejection.
  model.value = 'llama-3-8b';
  const roomiest = [...cat.regions].sort((a, b) => b.gpu_capacity - a.gpu_capacity)[0];
  if (roomiest) region.value = roomiest.code;
  sku.value = 'l4-24';
}

// ─────────────────────────────────────────────────────────────── plan ─────
$('#plan-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#deploy-btn');
  btn.disabled = true;
  try {
    const body = readPlanForm();
    if (!body) return;
    const dep = await api('/api/deployments', { method: 'POST', body });
    toast(`Deployed ${dep.name} (${dep.required_gpus} GPU-equivalents).`, 'ok');
    hidePlanForm();
    await loadDeployments();
  } catch (err) {
    const reasons =
      err.payload && err.payload.detail && err.payload.detail.reasons
        ? err.payload.detail.reasons.map((r) => r.detail).join(' ')
        : err.message;
    toast(reasons, 'error');
    await loadDeployments();
  } finally {
    btn.disabled = !state.planVerdict || !state.planVerdict.verdict.accepted;
  }
});

function readPlanForm() {
  const f = $('#plan-form');
  const fd = new FormData(f);
  const replicas = parseInt(fd.get('replicas'), 10);
  const budgetRaw = fd.get('monthly_budget_usd');

  if (!fd.get('name') || !String(fd.get('name')).trim()) {
    toast('Give the deployment a name first.', 'error');
    return null;
  }
  if (!replicas || replicas < 1) {
    toast('Replicas must be at least 1.', 'error');
    return null;
  }

  return {
    name: String(fd.get('name')).trim(),
    model_code: fd.get('model_code'),
    region_code: fd.get('region_code'),
    sku_code: fd.get('sku_code'),
    replicas,
    monthly_budget_usd: budgetRaw === '' || budgetRaw === null ? null : Number(budgetRaw),
  };
}

async function runPlan() {
  const body = readPlanForm();
  if (!body) return;

  const out = $('#plan-result');
  out.innerHTML = '<div class="metric"><div class="metric-label">Evaluating</div><div class="metric-value">…</div></div>';
  out.hidden = false;

  try {
    const plan = await api('/api/deployments/plan', { method: 'POST', body });
    state.planVerdict = plan;
    out.innerHTML = renderPlan(plan);
    $('#deploy-btn').disabled = !plan.verdict.accepted;
  } catch (err) {
    state.planVerdict = null;
    $('#deploy-btn').disabled = true;
    out.innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
  }
}

function renderPlan(plan) {
  const v = plan.verdict;
  const c = plan.cost;
  const ok = v.accepted;

  const headroomCls = v.headroom_after < 0 ? 'is-bad' : v.headroom_after === 0 ? 'is-warn' : 'is-ok';

  return `
    <div class="verdict-banner ${ok ? 'is-accepted' : 'is-rejected'}">
      <span class="icon">${ok ? '✓' : '✕'}</span>
      <span>${ok ? 'Accepted — this fits.' : 'Rejected — this does not fit.'}</span>
    </div>

    <div class="verdict-metrics">
      <div class="metric">
        <div class="metric-label">GPUs needed</div>
        <div class="metric-value">${v.required_gpus}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Headroom now</div>
        <div class="metric-value">${v.headroom_before}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Headroom after</div>
        <div class="metric-value ${headroomCls}">${v.headroom_after}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Utilisation</div>
        <div class="metric-value ${v.utilisation_after >= 1 ? 'is-bad' : v.utilisation_after >= 0.8 ? 'is-warn' : ''}">${pct(v.utilisation_after)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Monthly</div>
        <div class="metric-value">${money(c.monthly_usd)}</div>
      </div>
      ${
        c.monthly_budget_usd !== null
          ? `<div class="metric">
               <div class="metric-label">Over budget</div>
               <div class="metric-value ${c.within_budget ? 'is-ok' : 'is-bad'}">${c.within_budget ? 'none' : money(c.overage_usd)}</div>
             </div>`
          : ''
      }
    </div>

    ${
      v.reasons.length
        ? `<ul class="reason-list">
             ${v.reasons
               .map(
                 (r) => `<li class="reason is-error">
                   <span class="reason-code">${esc(r.code.replace(/_/g, ' '))}</span>
                   <span>${esc(r.detail)}</span>
                 </li>`
               )
               .join('')}
           </ul>`
        : ''
    }

    ${
      v.warnings.length
        ? `<ul class="reason-list">
             ${v.warnings
               .map(
                 (w) => `<li class="reason is-warn">
                   <span class="reason-code">${esc(w.code.replace(/_/g, ' '))}</span>
                   <span>${esc(w.detail)}</span>
                 </li>`
               )
               .join('')}
           </ul>`
        : ''
    }

    <p class="panel-sub">
      ${esc(plan.model.display_name)} on ${esc(plan.sku.display_name)}
      in ${esc(plan.region.display_name)} · ${v.replicas} replica${v.replicas === 1 ? '' : 's'}
      ${plan.cost.monthly_budget_usd !== null ? ` · budget ${money(plan.cost.monthly_budget_usd)}/mo` : ''}
    </p>`;
}

function showPlanForm() {
  $('#plan-form').hidden = false;
  $('#deploy-btn').disabled = true;
  state.planVerdict = null;
  $('#plan-result').hidden = true;
  if (!state.catalog) loadRegions();
  $('#plan-form [name="name"]').focus();
}

function hidePlanForm() {
  $('#plan-form').hidden = true;
  $('#plan-form').reset();
  $('#plan-result').hidden = true;
  state.planVerdict = null;
  $('#deploy-btn').disabled = true;
}

// ─────────────────────────────────────────────────────────────── deployments
async function loadDeployments() {
  const list = $('#deployments-list');
  try {
    const deps = await api('/api/deployments');
    if (!deps.length) {
      list.innerHTML = `<div class="empty"><strong>No deployments yet</strong>Plan one to see the admission check in action.</div>`;
      return;
    }
    list.innerHTML = deps.map(deploymentRow).join('');
  } catch (err) {
    list.innerHTML = `<div class="empty"><strong>Could not load deployments</strong>${esc(err.message)}</div>`;
  }
}

function deploymentRow(d) {
  const stopped = d.status !== 'active';
  const pill = stopped
    ? '<span class="pill pill-muted">stopped</span>'
    : '<span class="pill pill-ok">active</span>';

  const driftPill =
    d.observed_replicas === null
      ? '<span class="pill pill-muted">unreported</span>'
      : d.observed_replicas === d.replicas
      ? '<span class="pill pill-ok">in sync</span>'
      : '<span class="pill pill-bad">drifted</span>';

  return `
    <article class="row-card" data-id="${d.id}">
      <div class="row-main">
        <div class="row-title">${esc(d.name)} ${pill} ${driftPill}</div>
        <div class="row-meta">
          <code>${esc(d.model_code)}</code>
          <code>${esc(d.region_code)}</code>
          <code>${esc(d.sku_code)}</code>
          <span>${d.required_gpus} GPUs</span>
          <span>${money(d.monthly_cost_usd)}/mo</span>
          <span>observed: ${d.observed_replicas === null ? '—' : d.observed_replicas}</span>
        </div>
      </div>
      <div class="row-actions">
        <div class="step-ctl">
          <button class="step-btn" data-scale="${d.id}" data-delta="-1" ${d.replicas <= 0 ? 'disabled' : ''} aria-label="Decrease replicas">−</button>
          <span class="step-val">${d.replicas}</span>
          <button class="step-btn" data-scale="${d.id}" data-delta="1" ${d.replicas >= 64 ? 'disabled' : ''} aria-label="Increase replicas">+</button>
        </div>
        <button class="btn btn-ghost btn-sm" data-observe="${d.id}" title="Report the observed replica count from the cluster">Report</button>
        <button class="btn btn-danger btn-sm" data-delete="${d.id}">Delete</button>
      </div>
      <form class="observe-bar" data-observe-form="${d.id}" hidden>
        <label class="observe-label" for="obs-${d.id}">Cluster reports</label>
        <input id="obs-${d.id}" name="observed" type="number" min="0" max="512" step="1"
               value="${d.observed_replicas === null ? d.replicas : d.observed_replicas}" required />
        <span class="observe-unit">replica(s)</span>
        <button type="submit" class="btn btn-primary btn-sm">Record</button>
        <button type="button" class="btn btn-ghost btn-sm" data-observe-cancel="${d.id}">Cancel</button>
      </form>
    </article>`;
}

$('#deployments-list').addEventListener('click', async (e) => {
  const scaleBtn = e.target.closest('[data-scale]');
  const delBtn = e.target.closest('[data-delete]');
  const obsBtn = e.target.closest('[data-observe]');
  const obsCancel = e.target.closest('[data-observe-cancel]');

  // An inline bar instead of window.prompt(): prompt() blocks the whole page, cannot be
  // styled or validated in place, and looks nothing like the rest of the app.
  if (obsBtn) {
    const id = obsBtn.dataset.observe;
    const bar = $(`[data-observe-form="${id}"]`);
    const willShow = bar.hidden;
    $$('[data-observe-form]').forEach((b) => { b.hidden = true; });
    bar.hidden = !willShow;
    if (willShow) $(`#obs-${id}`).focus();
    return;
  }

  if (obsCancel) {
    $(`[data-observe-form="${obsCancel.dataset.observeCancel}"]`).hidden = true;
    return;
  }

  try {
    if (scaleBtn) {
      const id = scaleBtn.dataset.scale;
      const delta = parseInt(scaleBtn.dataset.delta, 10);
      const card = scaleBtn.closest('[data-id]');
      const current = parseInt($('.step-val', card).textContent, 10);
      const next = Math.max(0, current + delta);
      const updated = await api(`/api/deployments/${id}/scale`, {
        method: 'PATCH',
        body: { replicas: next },
      });
      toast(`Scaled to ${updated.replicas} replica(s) · ${money(updated.monthly_cost_usd)}/mo`, 'ok');
      await loadDeployments();
    }

    if (delBtn) {
      const id = delBtn.dataset.delete;
      if (!confirm('Delete this deployment? Capacity is released immediately.')) return;
      await api(`/api/deployments/${id}`, { method: 'DELETE' });
      toast('Deployment deleted.', 'ok');
      await loadDeployments();
    }
  } catch (err) {
    const detail =
      err.payload && err.payload.detail && err.payload.detail.reasons
        ? err.payload.detail.reasons.map((r) => r.detail).join(' ')
        : err.message;
    toast(detail, 'error');
  }
});

// Submitting the inline observe bar. Separate listener because a form submit is not a click.
$('#deployments-list').addEventListener('submit', async (e) => {
  const form = e.target.closest('[data-observe-form]');
  if (!form) return;
  e.preventDefault();
  const id = form.dataset.observeForm;
  const n = parseInt(new FormData(form).get('observed'), 10);
  if (!Number.isFinite(n) || n < 0) { toast('Enter a whole number ≥ 0.', 'error'); return; }
  try {
    await api(`/api/deployments/${id}/observe`, { method: 'POST', body: { observed_replicas: n } });
    toast(`Recorded ${n} observed replica(s).`, 'ok');
    await loadDeployments();
  } catch (err) {
    toast(err.message, 'error');
  }
});

// ─────────────────────────────────────────────────────────────── pipelines ─
function parseLines(text) {
  return String(text || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
}

// Editing the form invalidates whatever error it was showing. Without this the alert
// keeps claiming the graph is broken while the operator is fixing it, which reads as
// "my fix did not help" and is worse than no message at all.
$('#pipeline-form').addEventListener('input', () => {
  const errEl = $('#pipeline-error');
  if (!errEl.hidden) {
    errEl.hidden = true;
    errEl.textContent = '';
  }
});

$('#pipeline-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = $('#pipeline-error');
  errEl.hidden = true;

  const fd = new FormData(e.target);

  const steps = parseLines(fd.get('steps')).map((line) => {
    const [key, operation, seconds] = line.split(',').map((p) => p.trim());
    return { key, operation, duration_seconds: parseInt(seconds, 10) };
  });

  // The label reads "before, after" and that is what gets parsed. Saying "before -> after"
  // while splitting on ',' is how this shipped a bug: every edge became a one-field object,
  // the server rejected it, and the operator saw "Field required" three times with no clue why.
  const edges = parseLines(fd.get('edges')).map((line) => {
    const parts = line.split(',').map((p) => p.trim());
    return { before: parts[0], after: parts[1] };
  });

  // Validate here rather than letting the server answer with loc paths. A client that
  // can name the offending line makes a 422 actionable instead of mysterious.
  const name = String(fd.get('name') || '').trim();
  const problems = [];
  if (!name) problems.push('Give the pipeline a name.');
  if (!steps.length) problems.push('Add at least one step.');

  steps.forEach((s, i) => {
    if (!s.key || !s.operation || !Number.isFinite(s.duration_seconds) || s.duration_seconds < 1) {
      problems.push(`Step ${i + 1}: expected "key, operation, seconds".`);
    }
  });

  edges.forEach((e, i) => {
    if (!e.before || !e.after) {
      problems.push(`Edge ${i + 1}: expected "before, after".`);
    }
  });

  const keys = new Set(steps.map((s) => s.key));
  edges.forEach((e, i) => {
    if (e.before && !keys.has(e.before)) problems.push(`Edge ${i + 1}: unknown step "${e.before}".`);
    if (e.after && !keys.has(e.after)) problems.push(`Edge ${i + 1}: unknown step "${e.after}".`);
  });

  if (problems.length) {
    errEl.textContent = problems.join(' ');
    errEl.hidden = false;
    return;
  }

  try {
    await api('/api/pipelines', {
      method: 'POST',
      body: {
        name,
        description: String(fd.get('description') || '').trim() || null,
        steps,
        edges,
      },
    });
    toast('Pipeline validated and saved.', 'ok');
    $('#pipeline-form').hidden = true;
    e.target.reset();
    await loadPipelines();
  } catch (err) {
    let msg = err.message;
    if (err.payload && err.payload.detail && Array.isArray(err.payload.detail.issues)) {
      msg = err.payload.detail.issues.map((i) => i.detail).join(' ');
    } else if (err.payload && err.payload.detail && typeof err.payload.detail === 'object') {
      // Validation errors from FastAPI arrive as {detail: {...}} or {detail: [{loc, msg}]}.
      // Falling through to err.message loses the one sentence that says what is wrong.
      const d = err.payload.detail;
      if (Array.isArray(d)) {
        msg = d.map((x) => `${(x.loc || []).slice(-1)[0] || 'field'}: ${x.msg}`).join('; ');
      } else if (d.message) {
        msg = d.message;
      }
    } else if (err.payload && Array.isArray(err.payload.detail)) {
      msg = err.payload.detail.map((d) => `${(d.loc || []).slice(-1)[0] || 'field'}: ${d.msg}`).join('; ');
    }
    errEl.textContent = msg;
    errEl.hidden = false;
  }
});

async function loadPipelines() {
  const list = $('#pipelines-list');
  try {
    const pipes = await api('/api/pipelines');
    if (!pipes.length) {
      list.innerHTML = `<div class="empty"><strong>No pipelines yet</strong>Define a graph and Markaz will refuse it if it cannot be ordered.</div>`;
      return;
    }

    // Fetch detail for each so the run order and critical path are visible.
    const details = await Promise.all(pipes.map((p) => api(`/api/pipelines/${p.id}`)));

    list.innerHTML = details
      .map((d) => {
        const order = d.validation.order || [];
        const cp = d.validation.critical_path || [];
        return `
        <article class="row-card" data-pid="${d.id}">
          <div class="row-main">
            <div class="row-title">${esc(d.name)}
              <span class="pill ${d.is_valid ? 'pill-ok' : 'pill-bad'}">${d.is_valid ? 'valid' : 'invalid'}</span>
            </div>
            <div class="row-meta">
              <span>${order.length} steps</span>
              <span>critical path ${d.critical_path_seconds}s</span>
              <span>total ${d.validation.total_seconds}s</span>
            </div>
            <div class="row-meta" style="margin-top:7px">
              <span>run order: <code>${esc(order.join(' → '))}</code></span>
            </div>
            <div class="row-meta" style="margin-top:4px">
              <span>longest path: <code>${esc(cp.join(' → '))}</code></span>
            </div>
          </div>
          <div class="row-actions">
            <button class="btn btn-danger btn-sm" data-del-pipe="${d.id}">Delete</button>
          </div>
        </article>`;
      })
      .join('');
  } catch (err) {
    list.innerHTML = `<div class="empty"><strong>Could not load pipelines</strong>${esc(err.message)}</div>`;
  }
}

$('#pipelines-list').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-del-pipe]');
  if (!btn) return;
  if (!confirm('Delete this pipeline?')) return;
  try {
    await api(`/api/pipelines/${btn.dataset.delPipe}`, { method: 'DELETE' });
    toast('Pipeline deleted.', 'ok');
    await loadPipelines();
  } catch (err) {
    toast(err.message, 'error');
  }
});

// ─────────────────────────────────────────────────────────────── drift ─────
async function loadDrift() {
  try {
    const report = await api('/api/deployments/drift/report');
    const s = report.summary;

    $('#drift-summary').innerHTML = `
      <div class="stat">
        <div class="stat-label">Deployments</div>
        <div class="stat-value">${s.total}</div>
      </div>
      <div class="stat">
        <div class="stat-label">In sync</div>
        <div class="stat-value ${s.in_sync === s.total && s.total > 0 ? 'is-ok' : ''}">${s.in_sync}</div>
      </div>
      <div class="stat">
        <div class="stat-label">Drifted</div>
        <div class="stat-value ${s.drifted > 0 ? 'is-warn' : 'is-ok'}">${s.drifted}</div>
      </div>
      <div class="stat">
        <div class="stat-label">Critical</div>
        <div class="stat-value ${s.critical > 0 ? 'is-bad' : 'is-ok'}">${s.critical}</div>
      </div>`;

    const list = $('#drift-list');
    if (!report.findings.length) {
      list.innerHTML = `<div class="empty"><strong>Nothing to check</strong>Deploy something first, then report its observed state.</div>`;
      return;
    }

    list.innerHTML = report.findings
      .map((f) => {
        const cls = f.severity === 'critical' ? 'pill-bad' : f.severity === 'warning' ? 'pill-warn' : 'pill-ok';
        return `
        <article class="row-card">
          <div class="row-main">
            <div class="row-title">${esc(f.name)} <span class="pill ${cls}">${esc(f.state.replace(/_/g, ' '))}</span></div>
            <div class="row-meta"><span>${esc(f.detail)}</span></div>
            <div class="row-meta" style="margin-top:5px">
              <code>${esc(f.region)}</code>
              <span>desired ${f.desired_replicas}</span>
              <span>observed ${f.observed_replicas === null ? '—' : f.observed_replicas}</span>
            </div>
          </div>
        </article>`;
      })
      .join('');
  } catch (err) {
    $('#drift-list').innerHTML = `<div class="empty"><strong>Could not load drift</strong>${esc(err.message)}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────── actions ──
document.addEventListener('click', (e) => {
  const planBtn = e.target.closest('[data-action="plan"]');
  const togglePlan = e.target.closest('[data-action="toggle-plan"]');
  const togglePipe = e.target.closest('[data-action="toggle-pipeline"]');
  const refreshRegions = e.target.closest('[data-action="refresh-regions"]');
  const refreshDrift = e.target.closest('[data-action="refresh-drift"]');

  if (planBtn) runPlan();
  if (togglePlan) {
    const form = $('#plan-form');
    if (form.hidden) showPlanForm();
    else hidePlanForm();
  }
  if (togglePipe) {
    const form = $('#pipeline-form');
    form.hidden = !form.hidden;
    if (!form.hidden) $('#pipeline-form [name="name"]').focus();
  }
  if (refreshRegions) loadRegions();
  if (refreshDrift) loadDrift();
});

// Re-plan whenever the form changes, so the verdict is never stale against the
// inputs it was computed from.
$('#plan-form').addEventListener('change', () => {
  if (!$('#plan-result').hidden) runPlan();
  else $('#deploy-btn').disabled = true;
});

// ─────────────────────────────────────────────────────────────── boot ──────
async function boot() {
  if (!state.token) {
    $('#auth-screen').hidden = false;
    $('#app-shell').hidden = true;
    setAuthTab('login');
    return;
  }

  try {
    state.user = await api('/api/auth/me');
  } catch {
    signOut(true);
    return;
  }

  $('#user-name').textContent = state.user.display_name;
  $('#auth-screen').hidden = true;
  $('#app-shell').hidden = false;

  await loadRegions();
  showView('regions');
}

boot();
