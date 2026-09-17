// ─── THIN CLIENT ────────────────────────────────────────────────────────
// This file intentionally contains NO business rules: no acceptance
// thresholds, no shrinkage/reconciliation math, no stage-ordering
// decisions. Every number that matters (shrinkage_qty, parsed batch-number
// components, average-of-nothing, whatever) comes back from the API
// response and is only ever *displayed* here. The two exceptions are pure
// arithmetic identities a human would do with a calculator regardless of
// any PT JAS policy (netto = bruto − tara, off-spec = netto − on-spec) —
// see README note in PROJECT_STATUS.md "Fase 15" for why that's judged
// safe to keep client-side.

const API = '/api';

async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(API + path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
        const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        throw new Error(msg);
    }
    return data;
}

function toast(msg, type = 'info', duration = 4000) {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), duration);
}

function todayStr() {
    return new Date().toISOString().slice(0, 10);
}

function fmtQty(v) {
    if (v == null) return '–';
    return Number(v).toLocaleString('id-ID', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

// ─── STATE ──────────────────────────────────────────────────────────────
let suppliers = [];
let users = [];
let batches = [];

// ─── NAVIGATION ─────────────────────────────────────────────────────────
const PAGE_TITLES = {
    dashboard: 'Dashboard', 'batch-input': 'Penerimaan Barang', proses: 'Input Proses',
    'batch-list': 'Daftar Batch', 'batch-history': 'Batch History',
    suppliers: 'Data Supplier', users: 'User Management',
};
document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
        const page = btn.dataset.page;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById(page).classList.add('active');
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('pageTitle').textContent = PAGE_TITLES[page] || page;
        if (page === 'batch-list') renderBatchList();
        if (page === 'dashboard') renderDashboard();
    });
});

// ─── MASTER DATA ────────────────────────────────────────────────────────
async function loadMasterData() {
    suppliers = await api('GET', '/suppliers');
    users = await api('GET', '/users');
    renderSupplierSelect();
    renderPicSelects();
    renderSuppliersTable();
    renderUsersTable();
}

function renderSupplierSelect() {
    const sel = document.getElementById('rSupplier');
    sel.innerHTML = '<option value="">Pilih supplier...</option>' +
        suppliers.map(s => `<option value="${s.supplier_id}">${s.supplier_code} — ${s.name}</option>`).join('');
}

function renderPicSelects() {
    const opts = '<option value="">Pilih PIC...</option>' +
        users.map(u => `<option value="${u.user_id}">${u.name} (${u.role})</option>`).join('');
    ['rPic', 'pPic'].forEach(id => { document.getElementById(id).innerHTML = opts; });
}

function renderSuppliersTable() {
    document.getElementById('suppliersTable').innerHTML = suppliers.length ? suppliers.map(s => `
        <tr><td class="batch-id">${s.supplier_code}</td><td>${s.name}</td>
        <td><span class="badge badge-${s.active ? 'success' : 'gray'}">${s.active ? 'Aktif' : 'Nonaktif'}</span></td></tr>
    `).join('') : '<tr><td colspan="3" style="text-align:center;color:var(--text-secondary)">Belum ada supplier</td></tr>';
}

function renderUsersTable() {
    document.getElementById('usersTable').innerHTML = users.length ? users.map(u => `
        <tr><td><strong>${u.name}</strong></td><td><span class="badge badge-primary">${u.role}</span></td></tr>
    `).join('') : '<tr><td colspan="2" style="text-align:center;color:var(--text-secondary)">Belum ada user</td></tr>';
}

document.getElementById('supplierForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await api('POST', '/suppliers', {
            supplier_code: document.getElementById('newSupplierCode').value.trim(),
            name: document.getElementById('newSupplierName').value.trim(),
        });
        toast('✅ Supplier ditambahkan.', 'success');
        document.getElementById('supplierForm').reset();
        await loadMasterData();
    } catch (err) { toast('❌ ' + err.message, 'error'); }
});

document.getElementById('userForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await api('POST', '/users', {
            name: document.getElementById('newUserName').value.trim(),
            role: document.getElementById('newUserRole').value,
        });
        toast('✅ User ditambahkan.', 'success');
        document.getElementById('userForm').reset();
        await loadMasterData();
    } catch (err) { toast('❌ ' + err.message, 'error'); }
});

// ─── DASHBOARD ──────────────────────────────────────────────────────────
async function renderDashboard() {
    const data = await api('GET', '/batches?status=ACTIVE');
    const tbody = document.getElementById('dashboardTable');
    tbody.innerHTML = data.length ? data.slice(0, 15).map(b => `
        <tr>
            <td class="batch-id">${b.batch_id}</td>
            <td>${b.batch_number || '<span style="color:var(--text-secondary)">–</span>'}</td>
            <td><span class="badge badge-primary">${b.batch_type}</span></td>
            <td>${supplierName(b.supplier_id)}</td>
            <td>${fmtQty(b.current_quantity)} ${b.unit}</td>
            <td><span class="badge badge-success">${b.status}</span></td>
        </tr>
    `).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary)">Belum ada batch aktif</td></tr>';
}

function supplierName(id) {
    const s = suppliers.find(x => x.supplier_id === id);
    return s ? `${s.supplier_code} — ${s.name}` : '–';
}

// ─── RECEIVING ──────────────────────────────────────────────────────────
function recalcWeights() {
    const bruto = parseFloat(document.getElementById('rBruto').value) || 0;
    const tara = parseFloat(document.getElementById('rTara').value) || 0;
    const netto = Math.max(0, Math.round((bruto - tara) * 1000) / 1000);
    const nettoEl = document.getElementById('rNetto');
    if (bruto || tara) nettoEl.value = String(netto);
    const onSpecEl = document.getElementById('rOnSpec');
    if (onSpecEl.value === '') onSpecEl.value = nettoEl.value;
    recalcOffSpec();
}
function recalcOffSpec() {
    const netto = parseFloat(document.getElementById('rNetto').value) || 0;
    const onSpec = parseFloat(document.getElementById('rOnSpec').value) || 0;
    const off = Math.max(0, Math.round((netto - onSpec) * 1000) / 1000);
    document.getElementById('rOffSpec').value = String(off);
}
['rBruto', 'rTara'].forEach(id => document.getElementById(id).addEventListener('input', recalcWeights));
['rNetto', 'rOnSpec'].forEach(id => document.getElementById(id).addEventListener('input', recalcOffSpec));
document.getElementById('rTanggal').value = todayStr();

let batchNumberTimer = null;
document.getElementById('rNomorBatch').addEventListener('input', (e) => {
    clearTimeout(batchNumberTimer);
    const val = e.target.value.trim();
    const hint = document.getElementById('rNomorBatchPreview');
    if (!val) { hint.textContent = 'Kosongkan jika belum ada nomor batch.'; return; }
    batchNumberTimer = setTimeout(async () => {
        try {
            const r = await api('GET', '/batch-number/parse?value=' + encodeURIComponent(val));
            hint.innerHTML = r.ok
                ? `Terbaca: jenis <code>${r.jenis_code}</code>, grade <code>${r.grade_code}</code>, supplier <code>${r.supplier_code}</code>, tanggal ${r.receiving_date}, proses <code>${r.process_code}</code> (${r.process_code_label || '–'})`
                : 'Format tidak dikenali — tetap bisa disimpan sebagai teks bebas.';
        } catch (err) { hint.textContent = ''; }
    }, 350);
});

document.getElementById('receivingForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const netto = parseFloat(document.getElementById('rNetto').value);
    if (!netto || netto <= 0) { toast('Netto harus lebih dari 0.', 'error'); return; }
    const payload = {
        event_date: document.getElementById('rTanggal').value,
        pic_user_id: Number(document.getElementById('rPic').value),
        supplier_id: Number(document.getElementById('rSupplier').value),
        batch_type: document.getElementById('rBatchType').value,
        net_quantity: netto,
        batch_number: document.getElementById('rNomorBatch').value.trim() || null,
        product_description: document.getElementById('rDesc').value.trim() || null,
        packaging_condition: document.getElementById('rKemasan').value.trim() || null,
        coly: document.getElementById('rColy').value ? Number(document.getElementById('rColy').value) : null,
        gross_weight: document.getElementById('rBruto').value || null,
        tare_weight: document.getElementById('rTara').value || null,
        on_spec_qty: document.getElementById('rOnSpec').value || null,
        off_spec_qty: document.getElementById('rOffSpec').value || null,
        smell_test: document.getElementById('rSmell').value.trim() || null,
    };
    if (!payload.pic_user_id || !payload.supplier_id) { toast('PIC dan Supplier harus dipilih.', 'error'); return; }
    try {
        const result = await api('POST', '/receiving', payload);
        toast(`✅ Batch #${result.batch.batch_id} dibuat — ${fmtQty(result.batch.current_quantity)} kg`, 'success', 5000);
        document.getElementById('receivingForm').reset();
        document.getElementById('rTanggal').value = todayStr();
        document.getElementById('rNomorBatchPreview').textContent = ' ';
        await refreshBatches();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

// ─── PROSES (schema-driven) ─────────────────────────────────────────────
// UI-only field metadata (labels/inputs) -- not business rules. Adding a
// future stage (Sortation, Mixing, ...) means adding one entry here plus
// its router, never touching the engine.
const STAGE_DEFS = [
    {
        key: 'qc_test', label: '⚗️ QC Test (KA/AW)', endpoint: '/qc-tests',
        fields: [
            { id: 'stage', label: 'Status Sampel', type: 'select', required: true, options: ['RM', 'IP', 'FP'] },
            { id: 'quantity', label: 'Quantity (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'sample_weight', label: 'Berat Sampel (g)', type: 'number', step: '0.01' },
            { id: 'ka_1', label: 'KA 1 (%)', type: 'number', step: '0.01' },
            { id: 'ka_2', label: 'KA 2 (%)', type: 'number', step: '0.01' },
            { id: 'ka_3', label: 'KA 3 (%) — opsional', type: 'number', step: '0.01' },
            { id: 'aw', label: 'Kadar AW', type: 'number', step: '0.001' },
            { id: 'finding', label: 'Catatan / Hasil PIC', type: 'text' },
        ],
        historyCols: ['stage', 'ka_1', 'ka_2', 'ka_3', 'aw', 'finding'],
    },
    {
        key: 'metal_detection', label: '🧲 Metal Detection', endpoint: '/metal-detections',
        fields: [
            { id: 'stage', label: 'Status Sampel', type: 'select', required: true, options: ['RM', 'IP', 'FP'] },
            { id: 'quantity', label: 'Quantity (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'product_status', label: 'Status Produk', type: 'text' },
            { id: 'finding', label: 'Temuan Logam', type: 'text', placeholder: 'mis. Tidak Ada / serpihan besi' },
            { id: 'description', label: 'Deskripsi', type: 'text' },
        ],
        historyCols: ['stage', 'finding', 'product_status'],
    },
    {
        key: 'steaming', label: '♨️ Steaming', endpoint: '/steaming',
        fields: [
            { id: 'quantity', label: 'Quantity (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'pan_count', label: 'Jumlah Panci', type: 'number', step: '1' },
            { id: 'water_condition', label: 'Kondisi Air', type: 'select', options: ['Baik', 'Perlu Ganti'] },
            { id: 'pan_condition', label: 'Kondisi Panci', type: 'select', options: ['Baik', 'Perlu Perhatian'] },
            { id: 'steam_temperature', label: 'Suhu (°C)', type: 'number', step: '0.1' },
            { id: 'verification_reading_1', label: 'Verifikasi 1', type: 'number', step: '0.1' },
            { id: 'verification_reading_2', label: 'Verifikasi 2', type: 'number', step: '0.1' },
            { id: 'verification_reading_3', label: 'Verifikasi 3', type: 'number', step: '0.1' },
            { id: 'end_time', label: 'Jam Selesai', type: 'time' },
        ],
        historyCols: ['pan_count', 'steam_temperature', 'water_condition'],
    },
    {
        key: 'sundrying', label: '☀️ Sundrying', endpoint: '/sundrying',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'starting_ka', label: 'KA Awal (%)', type: 'number', step: '0.01' },
            { id: 'drying_duration', label: 'Durasi Penjemuran', type: 'text', placeholder: 'mis. 60 menit x 3, atau 14 hari' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'drying_duration'],
    },
];

function stageByKey(k) { return STAGE_DEFS.find(s => s.key === k); }

function renderStageSelect() {
    document.getElementById('pTahap').innerHTML = STAGE_DEFS.map(s => `<option value="${s.key}">${s.label}</option>`).join('');
}

function renderBatchSelect() {
    const sel = document.getElementById('pBatch');
    const active = batches.filter(b => b.status === 'ACTIVE');
    sel.innerHTML = active.length
        ? '<option value="">Pilih batch...</option>' + active.map(b =>
            `<option value="${b.batch_id}">#${b.batch_id} ${b.batch_number || ''} — ${b.batch_type} (${fmtQty(b.current_quantity)} kg)</option>`).join('')
        : '<option value="">Tidak ada batch aktif</option>';
}

function renderProsesFields() {
    const def = stageByKey(document.getElementById('pTahap').value);
    const host = document.getElementById('prosesFields');
    if (!def) { host.innerHTML = ''; return; }
    host.innerHTML = `<div class="form-divider">${def.label}</div>
        <div class="form-group">
            <label class="form-label">Tanggal <span style="color:var(--danger)">*</span></label>
            <input type="date" class="form-input" id="pf_event_date" value="${todayStr()}" required>
        </div>` +
        def.fields.map(f => {
            const req = f.required ? ' <span style="color:var(--danger)">*</span>' : '';
            let input;
            if (f.type === 'select') {
                input = `<select class="form-select" id="pf_${f.id}"${f.required ? ' required' : ''}>
                    <option value="">Pilih...</option>${f.options.map(o => `<option value="${o}">${o}</option>`).join('')}</select>`;
            } else {
                input = `<input type="${f.type}" class="form-input" id="pf_${f.id}" step="${f.step || 'any'}"
                    placeholder="${f.placeholder || ''}"${f.required ? ' required' : ''}>`;
            }
            return `<div class="form-group"><label class="form-label">${f.label}${req}</label>${input}</div>`;
        }).join('');
    renderProsesHistory();
}

async function renderProsesHistory() {
    const def = stageByKey(document.getElementById('pTahap').value);
    if (!def) return;
    document.getElementById('prosesRiwayatTitle').textContent = `📊 Riwayat ${def.label}`;
    const eventTypeMap = { qc_test: 'QC_TEST', metal_detection: 'METAL_DETECTION', steaming: 'STEAMING', sundrying: 'SUNDRYING' };
    const events = await api('GET', `/process-events?event_type=${eventTypeMap[def.key]}`);
    document.getElementById('prosesTableHead').innerHTML =
        '<th>Tanggal</th><th>Batch</th>' + def.historyCols.map(c => `<th>${c}</th>`).join('') + '<th>PIC</th>';
    document.getElementById('prosesTable').innerHTML = events.length ? events.slice(0, 30).map(ev => {
        const batchId = (ev.links.find(l => l.role === 'OUTPUT') || ev.links[0] || {}).batch_id;
        const cellFor = (col) => {
            if (col === 'shrinkage_qty') return fmtQty(ev.shrinkage_qty);
            if (col === 'final_quantity') { const l = ev.links.find(l => l.role === 'OUTPUT'); return l ? fmtQty(l.quantity) : '–'; }
            if (ev.quality_test && col in ev.quality_test) return ev.quality_test[col] ?? '–';
            const notes = ev.notes ? safeParse(ev.notes) : {};
            return notes[col] ?? '–';
        };
        return `<tr><td>${ev.event_date}</td><td class="batch-id">#${batchId}</td>` +
            def.historyCols.map(c => `<td>${cellFor(c)}</td>`).join('') +
            `<td>${picName(ev.pic_user_id)}</td></tr>`;
    }).join('') : `<tr><td colspan="${def.historyCols.length + 3}" style="text-align:center;color:var(--text-secondary)">Belum ada data</td></tr>`;
}

function safeParse(s) { try { return JSON.parse(s); } catch (e) { return {}; } }
function picName(id) { const u = users.find(x => x.user_id === id); return u ? u.name : '–'; }

document.getElementById('pTahap').addEventListener('change', renderProsesFields);
document.getElementById('pBatch').addEventListener('change', renderProsesFields);

document.getElementById('prosesForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const def = stageByKey(document.getElementById('pTahap').value);
    const batchId = document.getElementById('pBatch').value;
    const picId = document.getElementById('pPic').value;
    if (!def || !batchId) { toast('Tahap dan batch harus dipilih.', 'error'); return; }
    if (!picId) { toast('PIC harus dipilih.', 'error'); return; }

    const payload = {
        event_date: document.getElementById('pf_event_date').value,
        pic_user_id: Number(picId),
        batch_id: Number(batchId),
    };
    for (const f of def.fields) {
        const el = document.getElementById('pf_' + f.id);
        if (!el) continue;
        const raw = el.value;
        if (raw === '') continue;
        payload[f.id] = (f.type === 'number') ? raw : raw;
    }
    try {
        const result = await api('POST', def.endpoint, payload);
        toast(`✅ ${def.label} untuk batch #${batchId} tersimpan.`, 'success');
        document.getElementById('prosesForm').reset();
        renderProsesFields();
        await refreshBatches();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

// ─── BATCH LIST ─────────────────────────────────────────────────────────
async function renderBatchList() {
    const status = document.getElementById('blStatus').value;
    const type = document.getElementById('blType').value;
    const qs = new URLSearchParams();
    if (status) qs.set('status', status);
    if (type) qs.set('batch_type', type);
    const data = await api('GET', '/batches' + (qs.toString() ? '?' + qs.toString() : ''));
    document.getElementById('batchListTable').innerHTML = data.length ? data.map(b => `
        <tr>
            <td class="batch-id">${b.batch_id}</td>
            <td>${b.batch_number || '<span style="color:var(--text-secondary)">–</span>'}</td>
            <td><span class="badge badge-primary">${b.batch_type}</span></td>
            <td>${supplierName(b.supplier_id)}</td>
            <td>${fmtQty(b.current_quantity)} ${b.unit}</td>
            <td><span class="badge badge-${b.status === 'ACTIVE' ? 'success' : 'gray'}">${b.status}</span></td>
            <td>${new Date(b.created_at).toLocaleString('id-ID')}</td>
        </tr>`).join('') : '<tr><td colspan="7" style="text-align:center;color:var(--text-secondary)">Belum ada batch</td></tr>';
}
['blStatus', 'blType'].forEach(id => document.getElementById(id).addEventListener('change', renderBatchList));

// ─── BATCH HISTORY ──────────────────────────────────────────────────────
async function findBatch() {
    const id = document.getElementById('historySearch').value.trim();
    const el = document.getElementById('historyDetails');
    if (!id) return;
    try {
        const b = await api('GET', `/batches/${id}`);
        el.innerHTML = `
            <div class="form-divider" style="margin-top:0">📋 Batch #${b.batch_id}</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;font-size:13px;margin-bottom:14px">
                <div><div class="form-label">Nomor Batch</div>${b.batch_number || '–'}</div>
                <div><div class="form-label">Alur</div><span class="badge badge-primary">${b.batch_type}</span></div>
                <div><div class="form-label">Supplier</div>${b.supplier_name || '–'}</div>
                <div><div class="form-label">Qty Saat Ini</div><strong>${fmtQty(b.current_quantity)} ${b.unit}</strong></div>
                <div><div class="form-label">Status</div><span class="badge badge-success">${b.status}</span></div>
            </div>
            <div class="form-divider">🗓️ Riwayat Proses (kronologis)</div>
            ${b.events.map(ev => `
                <div style="background:var(--surface-dark);border-radius:6px;padding:10px 12px;margin-bottom:8px">
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
                        <span class="badge badge-primary">${ev.event_type}</span>
                        <span style="font-size:12.5px">${ev.event_date}</span>
                        <span style="font-size:12px;color:var(--text-secondary)">PIC ${picName(ev.pic_user_id)}</span>
                    </div>
                    <div style="font-size:12.5px">
                        ${ev.links.map(l => `<span class="badge badge-gray">${l.role} ${fmtQty(l.quantity)} ${l.unit}</span>`).join(' ')}
                        ${Number(ev.shrinkage_qty) > 0 ? ` <span class="badge badge-warning">shrinkage ${fmtQty(ev.shrinkage_qty)}</span>` : ''}
                    </div>
                    ${ev.quality_test ? `<div style="font-size:12px;margin-top:6px;color:var(--text-secondary)">
                        QualityTest: stage=${ev.quality_test.stage}
                        ${ev.quality_test.ka_1 != null ? ` KA1=${ev.quality_test.ka_1}` : ''}
                        ${ev.quality_test.ka_2 != null ? ` KA2=${ev.quality_test.ka_2}` : ''}
                        ${ev.quality_test.aw != null ? ` AW=${ev.quality_test.aw}` : ''}
                        ${ev.quality_test.finding ? ` finding="${ev.quality_test.finding}"` : ''}
                        ${ev.quality_test.metal_detection_finding ? ` MD finding="${ev.quality_test.metal_detection_finding}"` : ''}
                    </div>` : ''}
                    ${ev.notes ? `<div style="font-size:11.5px;margin-top:4px;color:var(--text-secondary)">📝 ${ev.notes}</div>` : ''}
                </div>
            `).join('') || '<div style="color:var(--text-secondary);font-size:13px">Belum ada event.</div>'}
        `;
    } catch (err) {
        el.innerHTML = `<p style="color:var(--danger)">${err.message}</p>`;
    }
}
document.getElementById('historySearch').addEventListener('keydown', e => { if (e.key === 'Enter') findBatch(); });

// ─── SHARED REFRESH ─────────────────────────────────────────────────────
async function refreshBatches() {
    batches = await api('GET', '/batches');
    renderBatchSelect();
}

// ─── BOOT ───────────────────────────────────────────────────────────────
async function boot() {
    const statusEl = document.getElementById('apiStatus');
    try {
        renderStageSelect();
        await loadMasterData();
        await refreshBatches();
        await renderDashboard();
        renderProsesFields();
        statusEl.textContent = 'Terhubung'; statusEl.className = 'api-status ok';
    } catch (err) {
        statusEl.textContent = 'Gagal terhubung ke API'; statusEl.className = 'api-status err';
        toast('❌ ' + err.message, 'error', 8000);
    }
}
boot();
