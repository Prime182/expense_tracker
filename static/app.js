/* OXY FIN+ by THE OXY - single page client */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
// Brand teal leads, then hues alternate warm/cool so neighbouring slices stay
// distinguishable -- three teals in a row read as one blob on a donut.
// Two sets: the deep wordmark teal sings on white and disappears on navy, so the
// dark theme uses lifted variants of the same hues. setTheme() swaps them.
const PALETTE_LIGHT = ['#066c7e','#c77d29','#3c9ca2','#7a5ea8','#0f8a6a','#d1495b','#1ec0e4','#e08b4c','#2a7a94','#5fd8f0'];
const PALETTE_DARK  = ['#3fb5cc','#e8a54a','#6fcdd4','#a68ad4','#34c99b','#ff8a99','#7ee0f5','#f0a76a','#5fa8c4','#a8e8f5'];
let PALETTE = PALETTE_LIGHT;
const DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];

// Sign goes before the currency symbol: -₹85,259, never ₹-85,259.
const sign = n => (Number(n) < 0 ? '-' : '');
const inr  = n => sign(n) + '₹' + Math.abs(Math.round(Number(n)||0)).toLocaleString('en-IN');
const inr2 = n => sign(n) + '₹' + Math.abs(Number(n)||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
const monthLabel = k => `${MONTH_NAMES[+k.slice(5) - 1].slice(0,3)} ${k.slice(0,4)}`;
const pct  = n => (Number(n)||0).toFixed(1) + '%';
const esc  = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const today = () => new Date().toISOString().slice(0,10);

const S = {
  user:null, boot:null, data:null, page:'overview',
  filters:{preset:'all', from:'', to:'', years:[], months:[], categories:[], subcategories:[], payments:[], weekdays:[], daytype:''},
  drill:{category:null, subcategory:null},
};

/* ---------------------------------------------------------------- plumbing */
/* Supabase issues the tokens; we keep them per-browser and send the access token
   as a Bearer header. Row Level Security on the database does the real enforcing. */
const TOKENS = {
  get(){ try { return JSON.parse(localStorage.getItem('auth') || 'null'); } catch { return null; } },
  set(t){ try { localStorage.setItem('auth', JSON.stringify(t)); } catch {} },
  clear(){ try { localStorage.removeItem('auth'); } catch {} },
};

async function request(path, method, body, token){
  const headers = {};
  if (body) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const r = await fetch(path, {method, headers, body: body ? JSON.stringify(body) : undefined});
  const text = await r.text();
  return {ok: r.ok, status: r.status, data: text ? JSON.parse(text) : {}};
}

function apiError(data){
  const d = data.detail;
  return new Error(Array.isArray(d) ? d.map(e => e.msg).join('; ') : (d || 'Something went wrong'));
}

async function api(path, method='GET', body){
  const auth = TOKENS.get();
  let r = await request(path, method, body, auth?.access_token);
  // Supabase access tokens are short-lived; swap in a fresh one and retry once.
  if (r.status === 401 && auth?.refresh_token){
    const rf = await request('/api/refresh', 'POST', {refresh_token: auth.refresh_token});
    if (rf.ok){
      TOKENS.set(rf.data);
      r = await request(path, method, body, rf.data.access_token);
    } else {
      TOKENS.clear();
      if (S.user) return location.reload();
    }
  }
  if (!r.ok) throw apiError(r.data);
  return r.data;
}

let toastTimer;
function toast(msg){
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(()=>t.classList.remove('show'), 2600);
}

/* ---------------------------------------------------------------- theme */
function setTheme(name){
  document.documentElement.dataset.theme = name;
  PALETTE = name === 'dark' ? PALETTE_DARK : PALETTE_LIGHT;
  try { localStorage.setItem('theme', name); } catch {}
  if (S.data) renderAll();
}
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------------------------------------------------------------- charts */
const charts = {};
function chart(id, cfg){
  const el = document.getElementById(id);
  if (!el) return;
  charts[id]?.destroy();
  const muted = cssVar('--muted'), grid = cssVar('--grid');
  Chart.defaults.font.family = 'Inter, system-ui, sans-serif';
  Chart.defaults.color = muted;
  cfg.options = Object.assign({
    responsive:true, maintainAspectRatio:false,
    interaction:{mode:'index', intersect:false},
    plugins:{
      legend:{display:false, labels:{usePointStyle:true, boxWidth:8, padding:14}},
      tooltip:{
        backgroundColor:cssVar('--surface'), titleColor:cssVar('--text'), bodyColor:cssVar('--text'),
        borderColor:cssVar('--border'), borderWidth:1, padding:12, cornerRadius:10,
        displayColors:true, usePointStyle:true, boxPadding:5,
      },
    },
  }, cfg.options || {});
  if (cfg.type !== 'doughnut' && cfg.type !== 'pie'){
    cfg.options.scales = Object.assign({
      x:{grid:{display:false}, border:{display:false}, ticks:{maxRotation:0, autoSkipPadding:14}},
      y:{grid:{color:grid}, border:{display:false}, ticks:{callback:v => inr(v)}, beginAtZero:true},
    }, cfg.options.scales || {});
  }
  charts[id] = new Chart(el, cfg);
}

/** Tooltip that reports Amount / Share / Transactions / Average from a parallel meta array. */
function metaTooltip(meta){
  return {
    callbacks:{
      label(ctx){
        const m = meta[ctx.dataIndex] || {};
        const out = [` ${ctx.dataset.label || 'Amount'}: ${inr2(ctx.parsed.y ?? ctx.parsed)}`];
        if (m.pct   != null) out.push(` Share: ${pct(m.pct)}`);
        if (m.count != null) out.push(` Transactions: ${m.count}`);
        if (m.avg   != null) out.push(` Average: ${inr2(m.avg)}`);
        if (m.change != null) out.push(` vs prior: ${m.change >= 0 ? '+' : ''}${inr2(m.change)}` +
                                       (m.pctChange != null ? ` (${m.pctChange >= 0 ? '+' : ''}${pct(m.pctChange)})` : ''));
        return out;
      },
    },
  };
}

const gradient = (ctx, color) => {
  const g = ctx.createLinearGradient(0, 0, 0, 300);
  g.addColorStop(0, color + '55'); g.addColorStop(1, color + '05');
  return g;
};

/* ---------------------------------------------------------------- filters */
function buildFilterOptions(){
  const cats = S.boot.categories;
  const sel = (id, opts, all) => {
    const e = $(id), keep = e.value;
    e.innerHTML = `<option value="">${all}</option>` + opts.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join('');
    e.value = keep;
  };
  sel('#fCat', cats.map(c => c.name), 'All categories');
  const chosen = cats.find(c => c.name === S.filters.categories[0]);
  sel('#fSub', chosen ? chosen.subcategories.map(s => s.name) : [], 'All sub-categories');
  sel('#fPay', S.boot.payment_methods, 'All methods');
  const years = S.data?.available?.years || [];
  sel('#fYear', years.map(String), 'All years');
  const m = $('#fMonth'), keepM = m.value;
  m.innerHTML = '<option value="">All months</option>' + MONTH_NAMES.map((n,i) => `<option value="${i+1}">${n}</option>`).join('');
  m.value = keepM;
  const w = $('#fWeekday'), keepW = w.value;
  w.innerHTML = '<option value="">All</option>' + DOW.map((n,i) => `<option value="${i}">${n}</option>`).join('');
  w.value = keepW;
}

function presetRange(p){
  const d = new Date(), iso = x => x.toISOString().slice(0,10);
  const first = (y,m) => iso(new Date(Date.UTC(y, m, 1)));
  const last  = (y,m) => iso(new Date(Date.UTC(y, m+1, 0)));
  switch(p){
    case 'today': return [today(), today()];
    case '7d':    return [iso(new Date(Date.now()-6*864e5)), today()];
    case '30d':   return [iso(new Date(Date.now()-29*864e5)), today()];
    case 'tm':    return [first(d.getFullYear(), d.getMonth()), last(d.getFullYear(), d.getMonth())];
    case 'lm':    return [first(d.getFullYear(), d.getMonth()-1), last(d.getFullYear(), d.getMonth()-1)];
    case 'ty':    return [first(d.getFullYear(), 0), last(d.getFullYear(), 11)];
    default:      return ['',''];
  }
}

function readFilters(){
  const f = S.filters;
  f.preset = $('#fPreset').value;
  if (f.preset !== 'custom'){ [f.from, f.to] = presetRange(f.preset); $('#fFrom').value = f.from; $('#fTo').value = f.to; }
  else { f.from = $('#fFrom').value; f.to = $('#fTo').value; }
  const one = (id, cast=String) => $(id).value ? [cast($(id).value)] : [];
  f.years = one('#fYear', Number); f.months = one('#fMonth', Number);
  f.categories = one('#fCat'); f.subcategories = one('#fSub');
  f.payments = one('#fPay'); f.weekdays = one('#fWeekday', Number);
  f.daytype = $('#fDayType').value;
}

function writeFilters(){
  const f = S.filters;
  $('#fPreset').value = f.preset; $('#fFrom').value = f.from; $('#fTo').value = f.to;
  $('#fYear').value = f.years[0] ?? ''; $('#fMonth').value = f.months[0] ?? '';
  $('#fCat').value = f.categories[0] ?? ''; $('#fSub').value = f.subcategories[0] ?? '';
  $('#fPay').value = f.payments[0] ?? ''; $('#fWeekday').value = f.weekdays[0] ?? '';
  $('#fDayType').value = f.daytype;
}

function filterSummary(){
  const f = S.filters, bits = [];
  if (f.from || f.to) bits.push(`${f.from || 'start'} → ${f.to || 'now'}`);
  else if (f.preset === 'all' && !f.years.length && !f.months.length) bits.push('All time');
  if (f.years.length) bits.push(f.years[0]);
  if (f.months.length) bits.push(MONTH_NAMES[f.months[0]-1]);
  if (f.categories.length) bits.push(f.categories[0]);
  if (f.subcategories.length) bits.push(f.subcategories[0]);
  if (f.payments.length) bits.push(f.payments[0]);
  if (f.daytype) bits.push(f.daytype === 'weekend' ? 'Weekends' : 'Weekdays');
  if (f.weekdays.length) bits.push(DOW[f.weekdays[0]]);
  return bits.join(' · ') || 'All time';
}

async function refresh(){
  const f = S.filters;
  S.data = await api('/api/analytics', 'POST', {
    from: f.from || null, to: f.to || null, years: f.years, months: f.months,
    categories: f.categories, subcategories: f.subcategories, payments: f.payments,
    weekdays: f.weekdays, daytype: f.daytype || null,
  });
  buildFilterOptions();
  $('#filterChip').textContent = filterSummary();
  renderAll();
}

/* ---------------------------------------------------------------- render */
const kpiCard = (label, value, foot='') =>
  `<div class="card kpi"><div class="k-label">${esc(label)}</div><div class="k-value num">${value}</div><div class="k-foot">${foot}</div></div>`;

const emptyMsg = t => `<div class="empty">${esc(t)}</div>`;

function table(cols, rows, opts={}){
  if (!rows.length) return emptyMsg(opts.empty || 'Nothing to show for these filters.');
  return `<table><thead><tr>${cols.map(c => `<th class="${c.r?'r':''}">${esc(c.t)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r,i) => `<tr class="${opts.click?'clickable':''}" ${opts.click?`data-i="${i}"`:''}>
      ${cols.map(c => `<td class="${c.r?'r':''}">${c.f(r,i)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// Bars scale against the biggest row so they stay comparable; the % column carries the true share.
const shareBar = (v, max) => `<div class="bar-cell"><i style="width:${max ? Math.min(100, v / max * 100) : 0}%"></i></div>`;
const maxOf = rows => Math.max(0, ...rows.map(r => r.amount));

function renderAll(){
  const d = S.data;
  renderOverview(d); renderDaily(d); renderMonthly(d);
  renderCategory(d); renderPayment(d); renderAllTime(d); renderLog(d); renderSettings();
}

/* ---- Overview ---- */
function renderOverview(d){
  const k = d.kpis, m = d.mom;
  const momCard = m && m.prev
    ? kpiCard('Month over month', `<span class="${m.change>=0?'up':'down'}">${m.change>=0?'▲':'▼'} ${inr(Math.abs(m.change))}</span>`,
        `${m.pct>=0?'+':''}${pct(m.pct)} vs previous month`)
    : kpiCard('Month over month', '—', 'Needs two months of data');
  $('#kpiGrid').innerHTML =
    kpiCard('Total expenses', inr(k.total), `${k.count} transactions in view`) +
    kpiCard('This month', inr(k.month_total), 'Current calendar month') +
    kpiCard("Today", inr(k.today_total), new Date().toLocaleDateString('en-IN',{weekday:'long', day:'numeric', month:'short'})) +
    kpiCard('Average per day', inr(k.avg_daily), `${k.active_days} days with spending`) +
    kpiCard('Average per month', inr(k.avg_monthly), `Avg transaction ${inr(k.avg_txn)}`) +
    momCard;

  // Monthly trend
  chart('cMonthly', {
    type:'bar',
    data:{labels: d.monthly.map(m=>m.label), datasets:[{
      label:'Spend', data: d.monthly.map(m=>m.amount),
      backgroundColor: PALETTE[0]+'cc', borderRadius:6, maxBarThickness:44,
    }]},
    options:{plugins:{tooltip: metaTooltip(d.monthly)}, onClick:(e,els)=>{
      if (!els.length) return;
      const key = d.monthly[els[0].index].key;
      $('#fPreset').value='custom'; $('#fFrom').value = key+'-01';
      const [y,mm] = key.split('-').map(Number);
      $('#fTo').value = new Date(Date.UTC(y, mm, 0)).toISOString().slice(0,10);
      applyFilters();
    }},
  });

  drawCategoryDonut('cCatDonut', d);

  // Daily area
  chart('cDaily', {
    type:'line',
    data:{labels: d.daily.map(x=>x.label), datasets:[{
      label:'Spend', data: d.daily.map(x=>x.amount), borderColor: PALETTE[1], borderWidth:2,
      pointRadius:0, pointHoverRadius:4, tension:.3, fill:true,
      backgroundColor: c => gradient(c.chart.ctx, PALETTE[1]),
    }]},
    options:{plugins:{tooltip: metaTooltip(d.daily)}},
  });

  const cats = d.categories.slice(0, 10);
  chart('cCatBar', {
    type:'bar',
    data:{labels: cats.map(c=>c.label), datasets:[{
      label:'Spend', data: cats.map(c=>c.amount), borderRadius:6,
      backgroundColor: cats.map((_,i) => PALETTE[i % PALETTE.length] + 'cc'),
    }]},
    options:{indexAxis:'y', plugins:{tooltip: metaTooltip(cats)},
      scales:{x:{grid:{color:cssVar('--grid')}, border:{display:false}, ticks:{callback:v=>inr(v)}, beginAtZero:true},
              y:{grid:{display:false}, border:{display:false}}},
      onClick:(e,els)=>{ if(els.length) filterToCategory(cats[els[0].index].label); }},
  });

  drawShare('cPayDonut', d.payments);

  $('#insights').innerHTML = d.insights.map(t => `<div class="insight"><span class="dot"></span><span>${esc(t)}</span></div>`).join('');

  $('#limitsPanel').innerHTML = d.limits.length ? d.limits.map(l => {
    const cls = l.status === 'Exceeded' ? 'bad' : l.status === 'Approaching' ? 'warn' : '';
    return `<div class="limit"><div class="l-top"><span>${esc(l.name)}</span>
      <span class="pill ${cls || 'ok'}">${esc(l.status)}</span></div>
      <div class="track"><i class="${cls}" style="width:${Math.min(100, l.pct)}%"></i></div>
      <div class="k-foot" style="margin-top:5px">${inr(l.spent)} of ${inr(l.cap)} · ${pct(l.pct)}</div></div>`;
  }).join('') : emptyMsg('No limits set. Add them under Settings — they are optional and never block an entry.');

  $('#anomalies').innerHTML = d.anomalies.length ? d.anomalies.map(a => `
    <div class="anom"><span class="pill ${a.z>=5?'bad':'warn'}">${esc(a.type)}</span>
      <div class="a-body"><div class="a-title">${esc(a.label)}</div>
      <div class="a-msg">${esc(a.message)} Outlier score ${a.z}.</div></div>
      <div class="num" style="font-weight:650">${inr(a.amount)}</div></div>`).join('')
    : emptyMsg('No anomalies detected against your historical baseline.');
}

function drawCategoryDonut(id, d){
  // FDR: automatically group minor items so the chart stays readable.
  const big = d.categories.filter(c => c.pct >= 2), small = d.categories.filter(c => c.pct < 2);
  const items = [...big];
  if (small.length > 1) items.push({
    label:'Other', key:'__other', pct: small.reduce((s,c)=>s+c.pct,0),
    amount: small.reduce((s,c)=>s+c.amount,0), count: small.reduce((s,c)=>s+c.count,0),
    avg: small.reduce((s,c)=>s+c.amount,0) / Math.max(1, small.reduce((s,c)=>s+c.count,0)),
  });
  else items.push(...small);
  drawShare(id, items, label => { if (label !== 'Other') filterToCategory(label); });
}

function drawShare(id, items, onPick){
  chart(id, {
    type:'doughnut',
    data:{labels: items.map(i=>i.label), datasets:[{
      data: items.map(i=>i.amount), borderWidth:0, hoverOffset:8,
      backgroundColor: items.map((_,i) => PALETTE[i % PALETTE.length]),
    }]},
    options:{cutout:'62%', plugins:{legend:{display:true, position:'right'}, tooltip: metaTooltip(items)},
      onClick:(e,els)=>{ if(els.length && onPick) onPick(items[els[0].index].label); }},
  });
}

/* ---- Daily & Weekly ---- */
function renderDaily(d){
  const k = d.kpis;
  const busiest = [...d.weekday].sort((a,b)=>b.amount-a.amount)[0];
  const we = d.daytype[1], wd = d.daytype[0], tot = we.amount + wd.amount;
  $('#dailyKpis').innerHTML =
    kpiCard('Days with spending', k.active_days, `Average ${inr(k.avg_daily)} per active day`) +
    kpiCard('Busiest weekday', busiest && busiest.amount ? busiest.label : '—', busiest ? `${inr(busiest.amount)} total` : '') +
    kpiCard('Weekend share', tot ? pct(we.amount/tot*100) : '—', `${inr(we.avg)} avg weekend day`) +
    kpiCard('Highest day', k.highest_day ? inr(k.highest_day.amount) : '—', k.highest_day ? esc(k.highest_day.label) : '') +
    kpiCard('Lowest day', k.lowest_day ? inr(k.lowest_day.amount) : '—', k.lowest_day ? esc(k.lowest_day.label) : '');

  drawHeatmap(d.daily);

  const avg7 = d.daily.map((_,i,a) => {
    const w = a.slice(Math.max(0,i-6), i+1);
    return +(w.reduce((s,x)=>s+x.amount,0)/w.length).toFixed(2);
  });
  chart('cDailyTrend', {
    type:'line',
    data:{labels: d.daily.map(x=>x.label), datasets:[
      {label:'Daily spend', data:d.daily.map(x=>x.amount), borderColor:PALETTE[0], borderWidth:2, tension:.3,
       pointRadius:0, pointHoverRadius:4, fill:true, backgroundColor:c=>gradient(c.chart.ctx, PALETTE[0])},
      {label:'7-day average', data:avg7, borderColor:PALETTE[2], borderWidth:2, borderDash:[5,4],
       pointRadius:0, tension:.3, fill:false},
    ]},
    options:{plugins:{legend:{display:true}}},
  });

  chart('cWeekly', {
    type:'bar',
    data:{labels: d.weekly.map(w=>w.label), datasets:[{label:'Spend', data:d.weekly.map(w=>w.amount),
      backgroundColor:PALETTE[4]+'cc', borderRadius:5, maxBarThickness:26}]},
    options:{plugins:{tooltip: metaTooltip(d.weekly)}},
  });

  chart('cWeekday', {
    type:'bar',
    data:{labels: DOW, datasets:[
      {label:'Total', data:d.weekday.map(w=>w.amount), backgroundColor:PALETTE[0]+'cc', borderRadius:5},
      {label:'Average per transaction', data:d.weekday.map(w=>w.avg), backgroundColor:PALETTE[2]+'cc', borderRadius:5, yAxisID:'y1'},
    ]},
    options:{plugins:{legend:{display:true}, tooltip: metaTooltip(d.weekday)},
      scales:{y1:{position:'right', grid:{display:false}, border:{display:false},
                  ticks:{callback:v=>inr(v)}, beginAtZero:true}},
      onClick:(e,els)=>{ if(els.length){ $('#fWeekday').value = els[0].index; applyFilters(); } }},
  });

  chart('cDayType', {
    type:'bar',
    data:{labels:d.daytype.map(x=>x.label), datasets:[
      {label:'Total spend', data:d.daytype.map(x=>x.amount), backgroundColor:[PALETTE[0]+'cc', PALETTE[3]+'cc'], borderRadius:6, maxBarThickness:80},
      {label:'Average per day', data:d.daytype.map(x=>x.avg), backgroundColor:[PALETTE[0]+'55', PALETTE[3]+'55'], borderRadius:6, maxBarThickness:80, yAxisID:'y1'},
    ]},
    options:{plugins:{legend:{display:true}},
      scales:{y1:{position:'right', grid:{display:false}, border:{display:false},
                  ticks:{callback:v=>inr(v)}, beginAtZero:true}}},
  });

  $('#topDays').innerHTML = table([
    {t:'#', f:(r,i)=>i+1},
    {t:'Day', f:r=>esc(r.label)},
    {t:'Transactions', r:true, f:r=>r.count},
    {t:'Share', f:r=>shareBar(r.amount, maxOf(d.top_days))},
    {t:'Amount', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
  ], d.top_days);
}

function drawHeatmap(daily){
  const map = Object.fromEntries(daily.map(x => [x.key, x.amount]));
  const end = new Date(); end.setHours(12,0,0,0);
  const start = new Date(end); start.setDate(start.getDate() - 363);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); // back to Monday
  const vals = Object.values(map).filter(v => v > 0).sort((a,b)=>a-b);
  const q = p => vals.length ? vals[Math.min(vals.length-1, Math.floor(vals.length * p))] : 0;
  const stops = [q(.25), q(.5), q(.75), q(.9)];
  const shade = v => !v ? 'var(--bg2)'
    : `color-mix(in srgb, var(--accent) ${v>stops[3]?100:v>stops[2]?72:v>stops[1]?48:v>stops[0]?28:15}%, var(--bg2))`;

  let html = '', col = [], cur = new Date(start);
  while (cur <= end){
    const key = cur.toISOString().slice(0,10), v = map[key] || 0;
    col.push(`<div class="cell" style="background:${shade(v)}" title="${key} · ${v ? inr2(v) : 'no spending'}"></div>`);
    if (col.length === 7){ html += `<div class="col">${col.join('')}</div>`; col = []; }
    cur.setDate(cur.getDate() + 1);
  }
  if (col.length) html += `<div class="col">${col.join('')}</div>`;
  $('#heatmap').innerHTML = html;
  $('#heatLegend').innerHTML = 'Less ' + [0, ...stops].map(v =>
    `<span class="cell" style="background:${shade(v ? v + 1 : 0)}"></span>`).join('') + ' More';
}

/* ---- Monthly ---- */
function renderMonthly(d){
  const k = d.kpis, m = d.mom, ms = d.mom_series;
  $('#monthlyKpis').innerHTML =
    kpiCard('Months in view', ms.length, `Average ${inr(k.avg_monthly)} per month`) +
    kpiCard('Highest month', k.highest_month ? inr(k.highest_month.amount) : '—', k.highest_month ? esc(k.highest_month.label) : '') +
    kpiCard('Lowest month', k.lowest_month ? inr(k.lowest_month.amount) : '—', k.lowest_month ? esc(k.lowest_month.label) : '') +
    kpiCard('Latest change', m && m.prev ? `<span class="${m.change>=0?'up':'down'}">${m.change>=0?'+':''}${inr(m.change)}</span>` : '—',
            m && m.prev ? `${m.pct>=0?'+':''}${pct(m.pct)} vs ${monthLabel(m.prev_key)}` : 'Needs two months');

  chart('cMoM', {
    data:{labels: ms.map(x=>x.label), datasets:[
      {type:'bar', label:'Spend', data:ms.map(x=>x.amount), backgroundColor:PALETTE[0]+'cc', borderRadius:6, maxBarThickness:44, order:2},
      {type:'line', label:'% change', data:ms.map(x=>x.pct), borderColor:PALETTE[2], borderWidth:2, tension:.3,
       pointRadius:3, yAxisID:'y1', order:1, fill:false},
    ]},
    options:{plugins:{legend:{display:true}, tooltip: metaTooltip(ms.map(x=>({count:x.count, change:x.change, pctChange:x.pct})))},
      scales:{y1:{position:'right', grid:{display:false}, border:{display:false}, ticks:{callback:v=>v+'%'}}}},
  });

  chart('cMonthCount', {
    type:'bar',
    data:{labels: ms.map(x=>x.label), datasets:[{label:'Transactions', data:ms.map(x=>x.count),
      backgroundColor:PALETTE[4]+'cc', borderRadius:6, maxBarThickness:44}]},
    options:{scales:{y:{grid:{color:cssVar('--grid')}, border:{display:false}, beginAtZero:true, ticks:{precision:0}}}},
  });

  $('#monthNarrative').innerHTML = d.narrative.length
    ? `<div class="scroll" style="max-height:280px">` + d.narrative.map(n =>
        `<div class="insight"><span class="dot"></span><span>${esc(n.text)}</span></div>`).join('') + '</div>'
    : emptyMsg('Log a second month to see automatic variation explanations.');

  $('#monthTable').innerHTML = table([
    {t:'Month', f:r=>esc(r.label)},
    {t:'Transactions', r:true, f:r=>r.count},
    {t:'Change', r:true, f:r=> r.change==null ? '—' : `<span class="${r.change>=0?'up':'down'}">${r.change>=0?'+':''}${inr(r.change)}</span>`},
    {t:'% change', r:true, f:r=> r.pct==null ? '—' : `<span class="${r.pct>=0?'up':'down'}">${r.pct>=0?'+':''}${pct(r.pct)}</span>`},
    {t:'Spend', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
  ], [...ms].reverse());
}

/* ---- Category ---- */
function renderCategory(d){
  drawShare('cCatShare', d.categories.slice(0,10), filterToCategory);

  chart('cCatTrend', {
    type:'line',
    data:{labels: d.cat_trend.labels, datasets: d.cat_trend.series.map((s,i)=>({
      label:s.name, data:s.data, borderColor:PALETTE[i], borderWidth:2, tension:.3,
      pointRadius:0, pointHoverRadius:4, fill:false,
    }))},
    options:{plugins:{legend:{display:true}}},
  });

  $('#catTable').innerHTML = table([
    {t:'#', f:(r,i)=>i+1},
    {t:'Category', f:r=>esc(r.label)},
    {t:'Txns', r:true, f:r=>r.count},
    {t:'Avg', r:true, f:r=>`<span class="num">${inr(r.avg)}</span>`},
    {t:'Share', f:r=>shareBar(r.amount, maxOf(d.categories))},
    {t:'%', r:true, f:r=>pct(r.pct)},
    {t:'Amount', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
  ], d.categories, {click:true});
  $('#catTable').querySelectorAll('tr.clickable').forEach(tr =>
    tr.onclick = () => { S.drill = {category: d.categories[+tr.dataset.i].label, subcategory:null}; renderCategory(S.data); });

  const cat = S.drill.category;
  $('#catCrumb').innerHTML = cat
    ? `<button data-crumb="0">All categories</button> ›  <strong>${esc(cat)}</strong>` +
      (S.drill.subcategory ? ` › <strong>${esc(S.drill.subcategory)}</strong>` : '')
    : '<span>Click any category row to drill into its sub-categories, then a sub-category for the transactions behind it.</span>';
  $('#catCrumb').querySelector('[data-crumb]')?.addEventListener('click', () => { S.drill={category:null,subcategory:null}; renderCategory(S.data); });

  if (S.drill.subcategory){
    const txns = S.data.transactions.filter(t => t.category === cat && (t.subcategory || 'Unspecified') === S.drill.subcategory);
    $('#subTitle').innerHTML = `Transactions <span class="hint">${esc(cat)} › ${esc(S.drill.subcategory)}</span>`;
    $('#subTable').innerHTML = table([
      {t:'Date', f:r=>r.date}, {t:'Description', f:r=>esc(r.description || '—')},
      {t:'Method', f:r=>`<span class="pill">${esc(r.payment)}</span>`},
      {t:'Amount', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
    ], txns);
    return;
  }

  const subs = cat ? d.subcategories.filter(s => s.category === cat) : d.subcategories.slice(0, 25);
  const subMax = maxOf(subs);
  $('#subTitle').innerHTML = cat
    ? `Sub-categories <span class="hint">${esc(cat)} · click for transactions</span>`
    : 'Sub-category breakdown <span class="hint">all categories</span>';
  $('#subTable').innerHTML = table([
    {t:'Sub-category', f:r=> cat ? esc(r.label) : `${esc(r.category)} › ${esc(r.label)}`},
    {t:'Txns', r:true, f:r=>r.count},
    {t:'Share', f:r=>shareBar(r.amount, subMax)},
    {t:'Amount', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
  ], subs, {click: !!cat});
  if (cat) $('#subTable').querySelectorAll('tr.clickable').forEach(tr =>
    tr.onclick = () => { S.drill.subcategory = subs[+tr.dataset.i].label; renderCategory(S.data); });
}

function filterToCategory(name){ $('#fCat').value = name; $('#fSub').value = ''; applyFilters(); }

/* ---- Payment ---- */
function renderPayment(d){
  const total = d.kpis.total;
  $('#payKpis').innerHTML = d.payments.length
    ? d.payments.map(p => kpiCard(p.label, inr(p.amount), `${pct(p.pct)} of spend · ${p.count} txns · avg ${inr(p.avg)}`)).join('')
    : kpiCard('Payment methods', '—', 'No transactions in view');

  drawShare('cPayShare', d.payments, name => { $('#fPay').value = name; applyFilters(); });

  chart('cPayBar', {
    data:{labels: d.payments.map(p=>p.label), datasets:[
      {type:'bar', label:'Transactions', data:d.payments.map(p=>p.count), backgroundColor:PALETTE[0]+'cc', borderRadius:6, maxBarThickness:60},
      {type:'bar', label:'Average transaction', data:d.payments.map(p=>p.avg), backgroundColor:PALETTE[2]+'cc', borderRadius:6, maxBarThickness:60, yAxisID:'y1'},
    ]},
    options:{plugins:{legend:{display:true}},
      scales:{y:{grid:{color:cssVar('--grid')}, border:{display:false}, beginAtZero:true, ticks:{precision:0}},
              y1:{position:'right', grid:{display:false}, border:{display:false}, ticks:{callback:v=>inr(v)}, beginAtZero:true}}},
  });

  $('#payTable').innerHTML = table([
    {t:'Method', f:r=>`<span class="pill">${esc(r.label)}</span>`},
    {t:'Transactions', r:true, f:r=>r.count},
    {t:'Average', r:true, f:r=>`<span class="num">${inr2(r.avg)}</span>`},
    {t:'Share', f:r=>shareBar(r.amount, maxOf(d.payments))},
    {t:'% of total', r:true, f:r=>pct(r.pct)},
    {t:'Amount', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
  ], d.payments);
  if (!total) $('#payTable').innerHTML = emptyMsg('No transactions in view.');
}

/* ---- All-time ---- */
function renderAllTime(d){
  const a = d.all_time;
  $('#atKpis').innerHTML =
    kpiCard('All-time total', inr(a.total), `${a.count} transactions logged`) +
    kpiCard('Days tracked', a.days_tracked, a.first_date ? `Since ${a.first_date}` : 'No entries yet') +
    kpiCard('Average per day', inr(a.avg_per_day), 'Across the whole period') +
    kpiCard('Months tracked', a.months_tracked, a.last_date ? `Latest entry ${a.last_date}` : '');

  chart('cCumulative', {
    type:'line',
    data:{labels: a.cumulative.map(x=>x.label), datasets:[
      {label:'Cumulative', data:a.cumulative.map(x=>x.cumulative), borderColor:PALETTE[0], borderWidth:2.5,
       pointRadius:0, pointHoverRadius:4, tension:.25, fill:true, backgroundColor:c=>gradient(c.chart.ctx, PALETTE[0])},
      {label:'Monthly', data:a.cumulative.map(x=>x.amount), borderColor:PALETTE[2], borderWidth:2,
       pointRadius:0, tension:.3, fill:false},
    ]},
    options:{plugins:{legend:{display:true}}},
  });

  chart('cYearly', {
    type:'bar',
    data:{labels:a.yearly.map(y=>y.label), datasets:[{label:'Spend', data:a.yearly.map(y=>y.amount),
      backgroundColor:a.yearly.map((_,i)=>PALETTE[i % PALETTE.length]+'cc'), borderRadius:6, maxBarThickness:70}]},
    options:{plugins:{tooltip: metaTooltip(a.yearly)},
      onClick:(e,els)=>{ if(els.length){ $('#fYear').value = a.yearly[els[0].index].label; applyFilters(); } }},
  });

  const rec = (t, r, sub) => `<div class="limit"><div class="l-top"><span>${t}</span>
    <span class="num" style="font-weight:650">${r ? inr2(r.amount) : '—'}</span></div>
    <div class="k-foot">${r ? esc(sub(r)) : 'No data yet'}</div></div>`;
  $('#records').innerHTML =
    rec('Highest single day', a.record_day, r=>r.label) +
    rec('Highest month', a.record_month, r=>r.label) +
    rec('Largest transaction', a.record_txn, r=>`${r.label} · ${r.date}`);

  chart('cAllMonths', {
    type:'bar',
    data:{labels:a.cumulative.map(x=>x.label), datasets:[{label:'Spend', data:a.cumulative.map(x=>x.amount),
      backgroundColor:PALETTE[1]+'cc', borderRadius:5, maxBarThickness:30}]},
    options:{plugins:{tooltip: metaTooltip(a.cumulative)}},
  });
}

/* ---- Log + quality ---- */
function renderLog(d){
  const t = d.transactions;
  $('#logCount').textContent = `${t.length} transactions · ${inr(d.kpis.total)}`;
  $('#logTable').innerHTML = table([
    {t:'Date', f:r=>r.date},
    {t:'Amount', r:true, f:r=>`<span class="num">${inr2(r.amount)}</span>`},
    {t:'Category', f:r=>esc(r.category)},
    {t:'Sub-category', f:r=>esc(r.subcategory || '—')},
    {t:'Description', f:r=>esc(r.description || '—')},
    {t:'Method', f:r=>`<span class="pill">${esc(r.payment)}</span>`},
    {t:'', r:true, f:(r,i)=>`<button class="btn sm" data-edit="${i}">Edit</button>
       <button class="btn sm danger" data-del="${r.id}">Delete</button>`},
  ], t, {empty:'No transactions match these filters. Hit ＋ Add expense to log one.'});

  $('#logTable').querySelectorAll('[data-edit]').forEach(b =>
    b.onclick = () => openExpenseModal(t[+b.dataset.edit]));
  $('#logTable').querySelectorAll('[data-del]').forEach(b =>
    b.onclick = async () => {
      if (!confirm('Delete this transaction?')) return;
      await api(`/api/expenses/${b.dataset.del}`, 'DELETE');
      toast('Transaction deleted'); refresh();
    });

  $('#quality').innerHTML = d.quality.length ? table([
    {t:'Check', f:r=>`<span class="pill ${r.kind==='Duplicate'?'bad':'warn'}">${esc(r.kind)}</span>`},
    {t:'Detail', f:r=>esc(r.detail)},
  ], d.quality) : emptyMsg('All clear — no duplicates or missing fields detected.');
}

/* ---- Settings ---- */
function renderSettings(){
  const cats = S.boot.categories;
  $('#catManager').innerHTML = cats.map(c => `
    <div style="margin-bottom:14px">
      <div style="display:flex;align-items:center;gap:8px;font-weight:650;font-size:13px">
        ${esc(c.name)} <button class="btn sm danger" data-delcat="${c.id}">Remove</button></div>
      <div class="tag-list">${c.subcategories.map(s =>
        `<span class="tag">${esc(s.name)}<button data-delsub="${s.id}" title="Remove">×</button></span>`).join('')
        || '<span class="k-foot">No sub-categories yet</span>'}</div>
    </div>`).join('') || emptyMsg('No categories yet.');

  const parent = $('#subParent'), keep = parent.value;
  parent.innerHTML = cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  parent.value = keep || (cats[0]?.id ?? '');
  $('#limCat').innerHTML = cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');

  $('#catManager').querySelectorAll('[data-delcat]').forEach(b => b.onclick = async () => {
    try { S.boot.categories = (await api(`/api/categories/${b.dataset.delcat}`, 'DELETE')).categories;
          toast('Category removed'); renderSettings(); buildFilterOptions(); }
    catch(e){ toast(e.message); }
  });
  $('#catManager').querySelectorAll('[data-delsub]').forEach(b => b.onclick = async () => {
    S.boot.categories = (await api(`/api/categories/${b.dataset.delsub}?sub=true`, 'DELETE')).categories;
    toast('Sub-category removed'); renderSettings(); buildFilterOptions();
  });

  const lims = S.data?.limits || [];
  $('#limManager').innerHTML = lims.length ? table([
    {t:'Limit', f:r=>esc(r.name)},
    {t:'Cap', r:true, f:r=>`<span class="num">${inr(r.cap)}</span>`},
    {t:'Status', f:r=>`<span class="pill ${r.status==='Exceeded'?'bad':r.status==='Approaching'?'warn':'ok'}">${esc(r.status)}</span>`},
    {t:'', r:true, f:r=>`<button class="btn sm danger" data-dellim="${r.id}">Remove</button>`},
  ], lims) : emptyMsg('No limits set.');
  $('#limManager').querySelectorAll('[data-dellim]').forEach(b => b.onclick = async () => {
    await api(`/api/limits/${b.dataset.dellim}`, 'DELETE'); toast('Limit removed'); refresh();
  });
}

/* ---------------------------------------------------------------- modal */
function closeModal(){ $('#modalRoot').innerHTML = ''; }

function openExpenseModal(existing){
  const cats = S.boot.categories;
  if (!cats.length) return toast('Add a category under Settings first');
  const e = existing || {};
  const curCat = cats.find(c => c.name === e.category) || cats[0];
  $('#modalRoot').innerHTML = `<div class="modal-bg"><div class="modal">
    <h3>${existing ? 'Edit transaction' : 'Add expense'}</h3>
    <p class="sub">Six fields. Everything else is derived automatically.</p>
    <form id="expForm">
      <div class="row2">
        <div><label>Date</label><input id="mDate" type="date" required value="${e.date || today()}"></div>
        <div><label>Amount (₹)</label><input id="mAmt" type="number" min="0.01" step="0.01" required
             placeholder="0.00" value="${e.amount ?? ''}"></div>
      </div>
      <div class="row2">
        <div><label>Category</label><select id="mCat">${cats.map(c =>
          `<option value="${c.id}" ${c.id===curCat.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select></div>
        <div><label>Sub-category</label><select id="mSub"></select></div>
      </div>
      <label>Description</label>
      <input id="mDesc" maxlength="280" placeholder="What was it for?" value="${esc(e.description || '')}">
      <label>Payment method</label>
      <select id="mPay">${S.boot.payment_methods.map(p =>
        `<option ${p===e.payment?'selected':''}>${esc(p)}</option>`).join('')}</select>
      <div id="mMsg" class="msg hide"></div>
      <div class="modal-actions">
        <button type="button" class="btn" id="mCancel">Cancel</button>
        <button type="submit" class="btn primary">${existing ? 'Save changes' : 'Add expense'}</button>
      </div>
    </form></div></div>`;

  const fillSubs = () => {
    const c = cats.find(x => x.id === +$('#mCat').value);
    $('#mSub').innerHTML = '<option value="">— none —</option>' + (c?.subcategories || []).map(s =>
      `<option value="${s.id}" ${s.name===e.subcategory?'selected':''}>${esc(s.name)}</option>`).join('');
  };
  fillSubs();
  $('#mCat').onchange = fillSubs;
  $('#mCancel').onclick = closeModal;
  $('.modal-bg').onclick = ev => { if (ev.target.classList.contains('modal-bg')) closeModal(); };
  $('#mAmt').focus();

  $('#expForm').onsubmit = async ev => {
    ev.preventDefault();
    const body = {
      txn_date: $('#mDate').value, amount: parseFloat($('#mAmt').value),
      category_id: +$('#mCat').value, subcategory_id: $('#mSub').value ? +$('#mSub').value : null,
      description: $('#mDesc').value.trim(), payment_method: $('#mPay').value,
    };
    try {
      if (existing) await api(`/api/expenses/${existing.id}`, 'PUT', body);
      else await api('/api/expenses', 'POST', body);
      closeModal(); toast(existing ? 'Transaction updated' : 'Expense added'); refresh();
    } catch(err){ const m = $('#mMsg'); m.textContent = err.message; m.classList.remove('hide'); }
  };
}

/* ---------------------------------------------------------------- nav */
const PAGE_META = {
  overview:['Overview','Executive summary of your spending'],
  daily:['Daily & Weekly','Granular day and week level patterns'],
  monthly:['Monthly','Month-over-month trends and variation'],
  category:['Category','Rankings, shares and sub-category drill-downs'],
  payment:['Payment','How you pay, and what it costs you'],
  alltime:['All-Time','Long-term history and cumulative spend'],
  log:['Transactions','Your raw log, plus automated data-quality checks'],
  settings:['Settings','Master lookups and optional spending limits'],
  help:['Guide','How to use this tracker'],
};

function goto(page){
  S.page = page;
  $$('.nav').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
  const [t, s] = PAGE_META[page];
  $('#pageTitle').textContent = t; $('#pageSub').textContent = s;
  $('#filters').classList.toggle('hide', page === 'settings' || page === 'help');
  $('#side').classList.remove('open');
  Object.values(charts).forEach(c => c.resize());
}

async function applyFilters(){ readFilters(); writeFilters(); await refresh(); }

function resetFilters(){
  S.filters = {preset:'all', from:'', to:'', years:[], months:[], categories:[], subcategories:[], payments:[], weekdays:[], daytype:''};
  S.drill = {category:null, subcategory:null};
  writeFilters(); refresh();
}

/* ---------------------------------------------------------------- auth ui */
let signupMode = false;

function showMsg(text, kind){
  const m = $('#authMsg');
  m.textContent = text;
  m.classList.toggle('ok', kind === 'ok');
  m.classList.remove('hide');
}

function setAuthMode(signup){
  signupMode = signup;
  $('#authTitle').textContent = signup ? 'Create your account' : 'Welcome back';
  $('#authSub').textContent = signup ? 'Start tracking in under a minute.' : 'Sign in to pick up where you left off.';
  $('#authBtn').textContent = signup ? 'Create account' : 'Sign in';
  $('#switchText').textContent = signup ? 'Already have an account?' : 'New here?';
  $('#switchBtn').textContent = signup ? 'Sign in' : 'Create an account';
  $('#inviteWrap').classList.toggle('hide', !(signup && S.inviteRequired));
  $('#password').autocomplete = signup ? 'new-password' : 'current-password';
  $('#authMsg').classList.add('hide');
}

async function startApp(email){
  S.user = email;
  $('#authView').classList.add('hide');
  $('#appView').classList.remove('hide');
  $('#userEmail').textContent = email;
  S.boot = await api('/api/bootstrap');
  buildFilterOptions();
  await refresh();
}

/* ---------------------------------------------------------------- init */
(async function init(){
  setTheme(localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

  $$('.nav').forEach(b => b.onclick = () => goto(b.dataset.page));
  $('#menuBtn').onclick = () => $('#side').classList.toggle('open');
  $('#themeBtn').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  $('#resetBtn').onclick = resetFilters;
  $('#addBtn').onclick = () => openExpenseModal();
  $('#logoutBtn').onclick = async () => {
    try { await api('/api/logout','POST'); } finally { TOKENS.clear(); location.reload(); }
  };
  $('#filters').addEventListener('change', ev => {
    if (ev.target.id === 'fPreset' && ev.target.value === 'custom') return;
    if (ev.target.id === 'fFrom' || ev.target.id === 'fTo') $('#fPreset').value = 'custom';
    applyFilters();
  });

  $('#addCatBtn').onclick = async () => {
    const name = $('#newCat').value.trim(); if (!name) return;
    S.boot.categories = (await api('/api/categories','POST',{name})).categories;
    $('#newCat').value=''; toast('Category added'); renderSettings(); buildFilterOptions();
  };
  $('#addSubBtn').onclick = async () => {
    const name = $('#newSub').value.trim(); if (!name || !$('#subParent').value) return;
    S.boot.categories = (await api('/api/categories','POST',{name, category_id:+$('#subParent').value})).categories;
    $('#newSub').value=''; toast('Sub-category added'); renderSettings(); buildFilterOptions();
  };
  $('#limKind').onchange = () => $('#limCat').classList.toggle('hide', $('#limKind').value !== 'category');
  $('#addLimBtn').onclick = async () => {
    const amount = parseFloat($('#limAmt').value); if (!(amount > 0)) return toast('Enter an amount');
    try {
      await api('/api/limits','POST',{kind:$('#limKind').value, amount,
        category_id: $('#limKind').value === 'category' ? +$('#limCat').value : null});
      $('#limAmt').value=''; toast('Limit saved'); refresh();
    } catch(e){ toast(e.message); }
  };

  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape') return closeModal();
    if (/input|select|textarea/i.test(ev.target.tagName) || $('.modal-bg')) return;
    if (ev.key === 'n' || ev.key === 'N') { ev.preventDefault(); openExpenseModal(); }
    if (ev.key === 'r' || ev.key === 'R') resetFilters();
    if (ev.key === 't' || ev.key === 'T') $('#themeBtn').click();
  });

  $('#switchBtn').onclick = () => setAuthMode(!signupMode);
  $('#authForm').onsubmit = async ev => {
    ev.preventDefault();
    const btn = $('#authBtn'), msg = $('#authMsg');
    btn.disabled = true; btn.textContent = 'Please wait…'; msg.classList.add('hide');
    try {
      const body = {email:$('#email').value, password:$('#password').value, invite_code:$('#invite').value};
      const r = await api(signupMode ? '/api/signup' : '/api/login', 'POST', body);
      if (r.pending){                       // account made, awaiting email confirmation
        setAuthMode(false);
        showMsg(r.message, 'ok');
        return;
      }
      TOKENS.set(r);
      await startApp(r.email);
    } catch(err){
      showMsg(err.message, 'error');
    } finally {
      // Do NOT call setAuthMode here: it clears #authMsg and would erase the
      // message we just displayed. Only restore the button.
      btn.disabled = false;
      btn.textContent = signupMode ? 'Create account' : 'Sign in';
    }
  };

  const cfg = await api('/api/config');
  S.inviteRequired = cfg.invite_required;
  const stored = TOKENS.get();
  if (stored?.access_token){
    try { await startApp(stored.email || ''); return; }
    catch { TOKENS.clear(); }
  }
  $('#authView').classList.remove('hide');
  $('#appView').classList.add('hide');
  setAuthMode(false);
})();
