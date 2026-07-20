/* INAD Analytics — estado, filtros, fetch e gráficos (Chart.js v4) */
'use strict';

// ─── SANITIZAÇÃO (previne XSS armazenado a partir de dados do PDF/rede) ────
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ─── ESTADO ──────────────────────────────────────────────────────────────────
const state = {
  from: null,        // YYYY-MM-DD ou null
  to: null,
  reports: null,     // Set de ids selecionados, ou null = todos
  segment: 'all',    // all | novo | antigo
  cutoffMode: 'last_n',
  cutoffLastN: 1,
  cutoffDate: null,
};

let allReports = [];        // [{id, report_name, report_date}] p/ o seletor
let lastData = null;        // última resposta de /api/kpis/analytics
let lastVersion = null;     // meta.data_version conhecido
let abortCtrl = null;
let debounceTimer = null;
let lastFetchTime = null;

const COLORS = {
  novo:   '#4f8dff',
  antigo: '#a855f7',
  total:  '#636b85',
  value:  '#6c63ff',
  green:  '#22c55e',
};

const fmtBRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const fmtBRLShort = v => v >= 1e6 ? `R$ ${(v / 1e6).toFixed(1)}M`
  : v >= 1e3 ? `R$ ${(v / 1e3).toFixed(0)}k` : fmtBRL.format(v);
const fmtDate = iso => iso ? iso.split('-').reverse().join('/') : '—';
// Data local (não UTC) em AAAA-MM-DD — toISOString() converteria para UTC e
// deslocaria 1 dia perto da meia-noite em fusos atrás de UTC (ex.: Brasil).
const toLocalISODate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// ─── URL ⇆ ESTADO ────────────────────────────────────────────────────────────
function stateFromURL() {
  const p = new URLSearchParams(location.search);
  state.from = p.get('from') || null;
  state.to = p.get('to') || null;
  state.segment = ['all', 'novo', 'antigo'].includes(p.get('segment')) ? p.get('segment') : 'all';
  const rep = p.get('reports');
  state.reports = rep ? new Set(rep.split(',').map(Number).filter(Number.isFinite)) : null;
  if (p.get('cutoff')) {
    state.cutoffMode = 'date';
    state.cutoffDate = p.get('cutoff');
  } else if (p.get('cutoff_last_n')) {
    state.cutoffMode = 'last_n';
    state.cutoffLastN = parseInt(p.get('cutoff_last_n'), 10) || 1;
  }
}

function urlFromState() {
  const p = new URLSearchParams();
  if (state.from) p.set('from', state.from);
  if (state.to) p.set('to', state.to);
  if (state.segment !== 'all') p.set('segment', state.segment);
  if (state.reports) p.set('reports', [...state.reports].join(','));
  if (state.cutoffMode === 'date' && state.cutoffDate) p.set('cutoff', state.cutoffDate);
  else if (state.cutoffLastN !== 1) p.set('cutoff_last_n', String(state.cutoffLastN));
  const qs = p.toString();
  history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
}

function apiQuery() {
  const p = new URLSearchParams();
  if (state.from) p.set('start', state.from);
  if (state.to) p.set('end', state.to);
  if (state.reports) p.set('reports', [...state.reports].join(','));
  p.set('segment', state.segment);
  if (state.cutoffMode === 'date' && state.cutoffDate) p.set('cutoff', state.cutoffDate);
  else p.set('cutoff_last_n', String(state.cutoffLastN));
  return p.toString();
}

// ─── FETCH ───────────────────────────────────────────────────────────────────
function scheduleRefresh() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(refresh, 300);
}

async function refresh() {
  urlFromState();
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = new AbortController();
  document.body.classList.add('loading');
  try {
    const res = await fetch(`/api/kpis/analytics?${apiQuery()}`, { signal: abortCtrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    lastData = await res.json();
    lastVersion = lastData.meta.data_version;
    lastFetchTime = Date.now();
    document.getElementById('fresh-pill').classList.remove('visible');
    render(lastData);
  } catch (e) {
    if (e.name !== 'AbortError') console.error('Erro ao carregar analytics:', e);
  } finally {
    document.body.classList.remove('loading');
  }
}

// Polling leve: compara a "versão" derivada de /api/reports com a última vista
async function pollVersion() {
  try {
    const res = await fetch('/api/reports');
    if (!res.ok) return;
    const rows = await res.json();
    const maxId = rows.length ? Math.max(...rows.map(r => r.id)) : 0;
    const maxImp = rows.length ? rows.map(r => r.imported_at).sort().at(-1) : '';
    const version = `${rows.length}:${maxId}:${maxImp}`;
    if (lastVersion !== null && version !== lastVersion) {
      document.getElementById('fresh-pill').classList.add('visible');
    }
  } catch (e) { /* servidor pode estar reiniciando; silencioso */ }
}
setInterval(pollVersion, 45000);
setInterval(() => {
  if (!lastFetchTime) return;
  const s = Math.round((Date.now() - lastFetchTime) / 1000);
  document.getElementById('updated-at').textContent =
    s < 60 ? `atualizado há ${s}s` : `atualizado há ${Math.floor(s / 60)}min`;
}, 5000);

// ─── GRÁFICOS ────────────────────────────────────────────────────────────────
Chart.defaults.color = '#9ba3bb';
Chart.defaults.borderColor = 'rgba(37,43,59,.8)';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 11.5;
Chart.defaults.plugins.tooltip.backgroundColor = '#181c26';
Chart.defaults.plugins.tooltip.borderColor = '#2e3650';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.titleColor = '#e8eaf0';
Chart.defaults.plugins.tooltip.bodyColor = '#9ba3bb';
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.legend.labels.boxHeight = 12;

const charts = {};

function upsertChart(id, config) {
  if (charts[id]) {
    charts[id].data = config.data;
    charts[id].options = config.options;
    charts[id].update();
  } else {
    charts[id] = new Chart(document.getElementById(id), config);
  }
}

function segDatasets(series, key, opts = {}) {
  // Monta datasets respeitando o filtro de segmento ativo
  const mk = (seg, color, extra = {}) => ({
    label: seg === 'novo' ? 'Novos' : seg === 'antigo' ? 'Antigos' : 'Total',
    data: series.map(s => s[seg][key]),
    borderColor: color, backgroundColor: color + '33',
    pointBackgroundColor: color, tension: .3, borderWidth: 2,
    pointRadius: 3, ...extra, ...opts,
  });
  if (state.segment === 'novo') return [mk('novo', COLORS.novo)];
  if (state.segment === 'antigo') return [mk('antigo', COLORS.antigo)];
  return [
    mk('novo', COLORS.novo),
    mk('antigo', COLORS.antigo),
    mk('total', COLORS.total, { borderDash: [6, 4], pointRadius: 0, backgroundColor: 'transparent', fill: false }),
  ];
}

function render(data) {
  const { series, transitions, meta } = data;
  renderTiles(series, transitions);
  renderDetailTable(series);
  document.getElementById('cutoff-info').textContent =
    `Corte "novo": estreia a partir de ${fmtDate(meta.cutoff_date)}`;

  // limites dos inputs de data
  if (meta.available_date_range.min) {
    for (const id of ['f-from', 'f-to']) {
      const el = document.getElementById(id);
      el.min = meta.available_date_range.min;
      el.max = meta.available_date_range.max;
    }
  }

  const labels = series.map(s => fmtDate(s.report_date));
  const hasData = series.length > 0;
  for (const box of document.querySelectorAll('.chart-box')) {
    box.querySelector('.empty-msg')?.remove();
    if (!hasData) {
      const wrap = box.querySelector('.chart-canvas-wrap');
      if (wrap) {
        const msg = document.createElement('p');
        msg.className = 'empty-msg';
        msg.textContent = 'Sem dados no período selecionado.';
        wrap.before(msg);
      }
    }
  }

  // Evolução de clientes
  upsertChart('chart-evolution', {
    type: 'line',
    data: { labels, datasets: segDatasets(series, 'clients') },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });

  // Tendência de valor (R$)
  upsertChart('chart-value', {
    type: 'line',
    data: { labels, datasets: segDatasets(series, 'total_value', { fill: state.segment !== 'all' }) },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmtBRLShort(v) } } },
      plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmtBRL.format(c.parsed.y)}` } } } },
  });

  // Taxa de recuperação por transição
  const tLabels = transitions.map(t => fmtDate(t.to_date));
  const tSets = [];
  if (state.segment !== 'antigo') tSets.push({ label: 'Novos', data: transitions.map(t => t.recovery_rate_novo), backgroundColor: COLORS.novo });
  if (state.segment !== 'novo') tSets.push({ label: 'Antigos', data: transitions.map(t => t.recovery_rate_antigo), backgroundColor: COLORS.antigo });
  if (state.segment === 'all') tSets.push({ label: 'Geral', data: transitions.map(t => t.recovery_rate), backgroundColor: COLORS.green });
  upsertChart('chart-recovery', {
    type: 'bar',
    data: { labels: tLabels, datasets: tSets },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { callback: v => v + '%' } } },
      plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y}%` } } } },
  });

  // Composição do último relatório do período
  const last = series.at(-1);
  upsertChart('chart-mix', {
    type: 'doughnut',
    data: {
      labels: ['Novos', 'Antigos'],
      datasets: [{
        data: last ? [last.novo.clients, last.antigo.clients] : [],
        backgroundColor: [COLORS.novo, COLORS.antigo],
        borderColor: '#181c26', borderWidth: 3,
      }],
    },
    options: { responsive: true, maintainAspectRatio: false, cutout: '62%',
      plugins: { tooltip: { callbacks: {
        label: c => ` ${c.label}: ${c.parsed} cliente(s)` } } } },
  });

  // Mix ao longo do tempo (empilhado)
  upsertChart('chart-mix-time', {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Novos', data: series.map(s => s.novo.clients), backgroundColor: COLORS.novo, stack: 's' },
      { label: 'Antigos', data: series.map(s => s.antigo.clients), backgroundColor: COLORS.antigo, stack: 's' },
    ] },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } },
  });
}

function renderTiles(series, transitions) {
  const seg = state.segment === 'all' ? 'total' : state.segment;
  const first = series[0], last = series.at(-1);
  const val = (s, k) => s ? s[seg][k] : 0;

  const delta = (cur, prev, fmt) => {
    if (!series.length || series.length < 2) return ['—', 'neutral'];
    const d = cur - prev;
    const sign = d > 0 ? '+' : '';
    return [`${sign}${fmt ? fmt(d) : d} no período`, d > 0 ? 'up' : d < 0 ? 'down' : 'neutral'];
  };

  const setTile = (id, value, [dTxt, dCls]) => {
    document.getElementById(`${id}-v`).textContent = value;
    const el = document.getElementById(`${id}-d`);
    el.textContent = dTxt;
    el.className = `tile-delta ${dCls}`;
  };

  setTile('tile-clients', val(last, 'clients'), delta(val(last, 'clients'), val(first, 'clients')));
  setTile('tile-parcels', val(last, 'parcels'), delta(val(last, 'parcels'), val(first, 'parcels')));
  setTile('tile-value', fmtBRLShort(val(last, 'total_value')),
    delta(val(last, 'total_value'), val(first, 'total_value'), d => fmtBRLShort(Math.abs(d)).replace('R$', d < 0 ? '-R$' : 'R$')));

  const rateKey = state.segment === 'novo' ? 'recovery_rate_novo'
    : state.segment === 'antigo' ? 'recovery_rate_antigo' : 'recovery_rate';
  const rates = transitions.map(t => t[rateKey]).filter(r => r > 0 || transitions.length);
  const avg = rates.length ? (rates.reduce((a, b) => a + b, 0) / rates.length).toFixed(1) : '—';
  setTile('tile-recovery', avg === '—' ? '—' : `${avg}%`, ['média das transições', 'neutral']);
}

function renderDetailTable(series) {
  const tbody = document.getElementById('detail-tbody');
  if (!series.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text3)">Sem dados no período.</td></tr>';
    return;
  }
  tbody.innerHTML = series.map(s => {
    const pctNovo = s.total.clients ? Math.round(s.novo.clients / s.total.clients * 100) : 0;
    return `<tr>
      <td>${s.report_id}</td>
      <td>${escapeHtml(s.report_name)}</td>
      <td>${fmtDate(s.report_date)}</td>
      <td class="num">${s.total.clients}</td>
      <td class="num">${s.total.parcels}</td>
      <td class="num">${fmtBRL.format(s.total.total_value)}</td>
      <td class="num">${pctNovo}%</td>
    </tr>`;
  }).join('');
}

// ─── CONTROLES ───────────────────────────────────────────────────────────────
function bindControls() {
  const fFrom = document.getElementById('f-from');
  const fTo = document.getElementById('f-to');
  fFrom.value = state.from || '';
  fTo.value = state.to || '';
  fFrom.onchange = () => { state.from = fFrom.value || null; clearChips(); scheduleRefresh(); };
  fTo.onchange = () => { state.to = fTo.value || null; clearChips(); scheduleRefresh(); };

  // atalhos de período
  const clearChips = () => document.querySelectorAll('.chip[data-days]').forEach(c => c.classList.remove('active'));
  document.querySelectorAll('.chip[data-days]').forEach(chip => {
    chip.onclick = () => {
      clearChips();
      chip.classList.add('active');
      const days = parseInt(chip.dataset.days, 10);
      if (!days) { state.from = state.to = null; }
      else {
        const to = new Date();
        const from = new Date(to.getTime() - days * 864e5);
        state.from = toLocalISODate(from);
        state.to = toLocalISODate(to);
      }
      fFrom.value = state.from || '';
      fTo.value = state.to || '';
      scheduleRefresh();
    };
  });

  // segmento
  document.querySelectorAll('.seg-toggle button').forEach(btn => {
    if (btn.dataset.seg === state.segment) {
      document.querySelectorAll('.seg-toggle button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    }
    btn.onclick = () => {
      document.querySelectorAll('.seg-toggle button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.segment = btn.dataset.seg;
      scheduleRefresh();
    };
  });

  // corte novo/antigo
  const cutSel = document.getElementById('f-cutoff');
  const cutDate = document.getElementById('f-cutoff-date');
  cutSel.value = state.cutoffMode === 'date' ? 'date' : String(state.cutoffLastN);
  cutDate.style.display = state.cutoffMode === 'date' ? '' : 'none';
  cutDate.value = state.cutoffDate || '';
  cutSel.onchange = () => {
    if (cutSel.value === 'date') {
      state.cutoffMode = 'date';
      cutDate.style.display = '';
      if (cutDate.value) { state.cutoffDate = cutDate.value; scheduleRefresh(); }
    } else {
      state.cutoffMode = 'last_n';
      state.cutoffLastN = parseInt(cutSel.value, 10);
      cutDate.style.display = 'none';
      scheduleRefresh();
    }
  };
  cutDate.onchange = () => {
    if (cutDate.value) { state.cutoffDate = cutDate.value; scheduleRefresh(); }
  };

  // dropdown de relatórios
  const dd = document.getElementById('dd-reports');
  document.getElementById('dd-reports-btn').onclick = e => {
    e.stopPropagation();
    dd.classList.toggle('open');
  };
  document.addEventListener('click', e => {
    if (!dd.contains(e.target)) dd.classList.remove('open');
  });
  document.getElementById('dd-all').onclick = () => setAllReports(true);
  document.getElementById('dd-none').onclick = () => setAllReports(false);

  document.getElementById('btn-refresh').onclick = refresh;
  document.getElementById('fresh-pill').onclick = refresh;
}

function setAllReports(checked) {
  document.querySelectorAll('#dd-list input').forEach(cb => { cb.checked = checked; });
  syncReportsFromCheckboxes();
}

function syncReportsFromCheckboxes() {
  const boxes = [...document.querySelectorAll('#dd-list input')];
  const chosen = boxes.filter(b => b.checked).map(b => Number(b.value));
  state.reports = chosen.length === boxes.length ? null : new Set(chosen);
  document.getElementById('dd-count').textContent =
    state.reports ? `${chosen.length}/${boxes.length}` : 'todos';
  scheduleRefresh();
}

async function loadReportList() {
  try {
    const res = await fetch('/api/reports');
    if (!res.ok) return;
    allReports = await res.json();
    const list = document.getElementById('dd-list');
    list.innerHTML = allReports.map(r => `
      <label class="dd-item">
        <input type="checkbox" value="${r.id}" ${!state.reports || state.reports.has(r.id) ? 'checked' : ''}>
        <span>${escapeHtml(r.report_name)}</span>
        <span class="di-date">${fmtDate(r.report_date)}</span>
      </label>`).join('');
    list.querySelectorAll('input').forEach(cb => { cb.onchange = syncReportsFromCheckboxes; });
    if (state.reports) {
      document.getElementById('dd-count').textContent = `${state.reports.size}/${allReports.length}`;
    }
  } catch (e) { console.error('Erro ao listar relatórios:', e); }
}

async function checkDemoMode() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) return;
    const h = await res.json();
    if (h.demo) {
      document.getElementById('demo-banner').classList.add('visible');
      const btn = document.getElementById('btn-demo');
      if (btn) btn.style.display = 'none'; // já estamos numa instância demo
    }
  } catch (e) { /* sem servidor */ }
}

async function launchDemo() {
  const btn = document.getElementById('btn-demo');
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Iniciando...';
  try {
    const res = await fetch('/api/demo/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Falha ao iniciar o servidor demo.');

    btn.textContent = data.already_running ? '⏳ Abrindo...' : '⏳ Gerando dados...';

    let ready = false;
    for (let i = 0; i < 20 && !ready; i++) {
      await new Promise(r => setTimeout(r, 400));
      try {
        const h = await fetch(data.health_url, { cache: 'no-store' });
        ready = h.ok;
      } catch (e) { /* ainda subindo */ }
    }
    if (!ready) throw new Error('O servidor demo demorou demais para responder.');

    window.open(data.url, '_blank');
  } catch (e) {
    alert('Não foi possível iniciar o modo demo: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// ─── BOOT ────────────────────────────────────────────────────────────────────
stateFromURL();
bindControls();
checkDemoMode();
loadReportList();
refresh();
