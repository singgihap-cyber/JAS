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
let customers = [];
let customerAliases = []; // Fase 21 lanjutan -- flat list from GET /customer-aliases, {alias_id, customer_id, alias}

// ─── NAVIGATION ─────────────────────────────────────────────────────────
const PAGE_TITLES = {
    dashboard: 'Dashboard', 'batch-input': 'Penerimaan Barang', proses: 'Input Proses',
    mixing: 'Mixing', 'vacuum-packing': 'Vacuum & Packing', delivery: 'Pengiriman', adjustment: 'Penyesuaian & Rejeksi',
    'batch-list': 'Daftar Batch', 'batch-history': 'Batch History',
    stock: 'Stock Monitoring', suppliers: 'Data Supplier', users: 'User Management',
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
        if (page === 'mixing') renderMixingHistory();
        if (page === 'vacuum-packing') { renderVacuumHistory(); renderPackingHistory(); }
        if (page === 'delivery') { renderDeliveryHistory(); renderSampleDeliveryHistory(); renderCustomersTable(); }
        if (page === 'adjustment') renderAuditLog();
        if (page === 'stock') renderStockSummary();
    });
});

// ─── MASTER DATA ────────────────────────────────────────────────────────
async function loadMasterData() {
    suppliers = await api('GET', '/suppliers');
    users = await api('GET', '/users');
    customers = await api('GET', '/customers');
    customerAliases = await api('GET', '/customer-aliases');
    renderSupplierSelect();
    renderPicSelects();
    renderCustomerSelects();
    renderSuppliersTable();
    renderUsersTable();
    renderCustomersTable();
}

function renderSupplierSelect() {
    const opts = suppliers.map(s => `<option value="${s.supplier_id}">${s.supplier_code} — ${s.name}</option>`).join('');
    document.getElementById('rSupplier').innerHTML = '<option value="">Pilih supplier...</option>' + opts;
    document.getElementById('mSupplier').innerHTML = '<option value="">Tidak diatribusikan (default)</option>' + opts;
    document.getElementById('pkSupplier').innerHTML = '<option value="">Tidak diisi (warisi dari sumber jika 1)</option>' + opts;
}

function renderPicSelects() {
    const opts = '<option value="">Pilih PIC...</option>' +
        users.map(u => `<option value="${u.user_id}">${u.name} (${u.role})</option>`).join('');
    ['rPic', 'pPic', 'mPic', 'vPic', 'pkPic', 'dPic', 'sdPic', 'ajPic', 'rjPic', 'ssPic']
        .forEach(id => { document.getElementById(id).innerHTML = opts; });
}

function renderCustomerSelects() {
    const opts = customers.map(c => `<option value="${c.customer_id}">${c.name}</option>`).join('');
    ['dCustomer', 'sdCustomer'].forEach(id => {
        document.getElementById(id).innerHTML = '<option value="">Tidak diatribusikan</option>' + opts;
    });
}

function renderCustomersTable() {
    document.getElementById('customersTable').innerHTML = customers.length ? customers.map(c => {
        const aliasesForCustomer = customerAliases.filter(a => a.customer_id === c.customer_id);
        const aliasBadges = aliasesForCustomer.map(a => `<span class="badge badge-gray">${a.alias}</span>`).join(' ');
        return `<tr>
            <td class="batch-id">#${c.customer_id}</td>
            <td>${c.name}</td>
            <td>
                <div class="customer-suggest" style="margin:0 0 6px 0">${aliasBadges}</div>
                <div style="display:flex;gap:4px">
                    <input class="form-input" style="padding:4px 8px;font-size:12px" placeholder="Tambah alias — mis. MALIK S/RUSIA" data-alias-input="${c.customer_id}">
                    <button type="button" class="btn btn-secondary btn-small" data-alias-add="${c.customer_id}">+ Alias</button>
                </div>
            </td>
        </tr>`;
    }).join('') : '<tr><td colspan="3" style="text-align:center;color:var(--text-secondary)">Belum ada customer</td></tr>';
}

document.getElementById('customersTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-alias-add]');
    if (!btn) return;
    const customerId = btn.dataset.aliasAdd;
    const input = document.querySelector(`[data-alias-input="${customerId}"]`);
    const alias = input.value.trim();
    if (!alias) { toast('Isi teks alias dulu.', 'error'); return; }
    try {
        await api('POST', `/customers/${customerId}/aliases`, { alias });
        toast('✅ Alias ditambahkan — kini dianggap cocok pasti (exact) saat dipakai di form Delivery.', 'success', 5000);
        customerAliases = await api('GET', '/customer-aliases');
        renderCustomersTable();
    } catch (err) { toast('❌ ' + err.message, 'error'); }
});

document.getElementById('customerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await api('POST', '/customers', { name: document.getElementById('newCustomerName').value.trim() });
        toast('✅ Customer ditambahkan.', 'success');
        document.getElementById('customerForm').reset();
        customers = await api('GET', '/customers');
        renderCustomerSelects();
        renderCustomersTable();
    } catch (err) { toast('❌ ' + err.message, 'error'); }
});

// ─── CUSTOMER MATCHING (Fase 21, resolves Fase 12 poin 7) ─────────────────
// Suggestion-only UI assist over GET /customers/match
// (services/customer_matching.py). It NEVER writes customer_id itself --
// clicking a suggestion chip just sets the existing
// <select id="{prefix}Customer"> to that customer_id, exactly as if the
// operator had picked it manually from the dropdown (customer_matching.py
// module docstring #1: matching assists, never decides). A failed match
// call is swallowed (best-effort UI assist) rather than blocking the form.
let _customerMatchTimers = {};

function setupCustomerMatch(prefix) {
    const nameInput = document.getElementById(`${prefix}Perusahaan`);
    const suggestBox = document.getElementById(`${prefix}CustomerSuggest`);
    if (!nameInput || !suggestBox) return;

    nameInput.addEventListener('input', () => {
        clearTimeout(_customerMatchTimers[prefix]);
        const q = nameInput.value.trim();
        if (!q) { suggestBox.innerHTML = ''; return; }
        _customerMatchTimers[prefix] = setTimeout(async () => {
            let candidates = [];
            try { candidates = await api('GET', `/customers/match?q=${encodeURIComponent(q)}&limit=5`); }
            catch (e) { return; }
            renderCustomerSuggestions(prefix, q, candidates);
        }, 350);
    });
}

function renderCustomerSuggestions(prefix, query, candidates) {
    const suggestBox = document.getElementById(`${prefix}CustomerSuggest`);
    if (!suggestBox) return;
    const chips = candidates.map(c => {
        const pct = Math.round(c.score * 100);
        let label;
        if (c.matched_alias) label = `✓ ${c.name} (alias: "${c.matched_alias}")`;
        else if (c.exact) label = `✓ ${c.name}`;
        else label = `${c.name} (~${pct}%)`;
        return `<button type="button" class="badge ${c.exact ? 'badge-success' : 'badge-primary'} suggest-chip" data-prefix="${prefix}" data-customer-id="${c.customer_id}">${label}</button>`;
    }).join('');
    const noneHint = chips ? '' : '<span class="form-hint" style="margin:0">Tidak ada customer serupa.</span>';
    const createChip = `<button type="button" class="badge badge-gray suggest-chip" data-prefix="${prefix}" data-create="${encodeURIComponent(query)}">+ Buat customer baru "${query}"</button>`;
    suggestBox.innerHTML = chips + noneHint + createChip;
}

document.body.addEventListener('click', async (e) => {
    const chip = e.target.closest('.suggest-chip');
    if (!chip) return;
    const prefix = chip.dataset.prefix;
    const select = document.getElementById(`${prefix}Customer`);
    const suggestBox = document.getElementById(`${prefix}CustomerSuggest`);
    if (!select) return;

    if (chip.dataset.customerId) {
        select.value = chip.dataset.customerId;
        toast('Customer dipilih dari saran — masih bisa diganti manual di dropdown.', 'info', 3000);
    } else if (chip.dataset.create !== undefined) {
        const name = decodeURIComponent(chip.dataset.create);
        try {
            const created = await api('POST', '/customers', { name });
            customers = await api('GET', '/customers');
            renderCustomerSelects();
            renderCustomersTable();
            select.value = created.customer_id;
            toast(`✅ Customer "${name}" dibuat & dipilih.`, 'success');
        } catch (err) { toast('❌ ' + err.message, 'error'); return; }
    }
    if (suggestBox) suggestBox.innerHTML = '';
});

setupCustomerMatch('d');
setupCustomerMatch('sd');

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
    // Hijau route only: Main/1st/2nd/3rd Curing, Airdrying (Fase 19 engine,
    // services/curing.py). No source document defines field-level detail
    // for these five stages -- fields are modeled on Sundrying's
    // starting/final-quantity-with-derived-shrinkage shape and marked
    // [UNCONFIRMED] (curing.py module docstring #2-5), same as the router
    // schemas (webapp/schemas.py). Steaming/blanching and the later
    // Sundrying step in this route reuse the existing 'steaming'/'sundrying'
    // stage entries above unchanged (curing.py #1) -- not repeated here.
    {
        key: 'main_curing', label: '🫙 Main Curing (Hijau)', endpoint: '/main-curing',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'duration', label: 'Durasi — [UNCONFIRMED]', type: 'text', placeholder: 'mis. 5 hari' },
            { id: 'condition_notes', label: 'Kondisi / Catatan — [UNCONFIRMED]', type: 'text' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'duration'],
    },
    {
        key: 'first_curing', label: '🫙 1st Curing (Hijau)', endpoint: '/first-curing',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'duration', label: 'Durasi — [UNCONFIRMED]', type: 'text', placeholder: 'mis. 5 hari' },
            { id: 'condition_notes', label: 'Kondisi / Catatan — [UNCONFIRMED]', type: 'text' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'duration'],
    },
    {
        key: 'second_curing', label: '🫙 2nd Curing (Hijau)', endpoint: '/second-curing',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'duration', label: 'Durasi — [UNCONFIRMED]', type: 'text', placeholder: 'mis. 5 hari' },
            { id: 'condition_notes', label: 'Kondisi / Catatan — [UNCONFIRMED]', type: 'text' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'duration'],
    },
    {
        key: 'third_curing', label: '🫙 3rd Curing (Hijau)', endpoint: '/third-curing',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'duration', label: 'Durasi — [UNCONFIRMED]', type: 'text', placeholder: 'mis. 5 hari' },
            { id: 'condition_notes', label: 'Kondisi / Catatan — [UNCONFIRMED]', type: 'text' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'duration'],
    },
    {
        key: 'airdrying', label: '🌬️ Airdrying (Hijau)', endpoint: '/airdrying',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'duration', label: 'Durasi — [UNCONFIRMED]', type: 'text', placeholder: 'mis. 5 hari' },
            { id: 'condition_notes', label: 'Kondisi / Catatan — [UNCONFIRMED]', type: 'text' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'duration'],
    },
    {
        key: 'sortation', label: '🧺 Sortasi', endpoint: '/sortation',
        fields: [
            { id: 'initial_qty', label: 'Qty Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'gourmet_qty', label: 'Gourmet (kg)', type: 'number', step: '0.001' },
            { id: 'eg_qty', label: 'EG (kg)', type: 'number', step: '0.001' },
            { id: 'ep_qty', label: 'EP (kg)', type: 'number', step: '0.001' },
            { id: 'nc_qty', label: 'NC / Non Conform (kg)', type: 'number', step: '0.001' },
            { id: 'powder_qty', label: 'Powder (kg)', type: 'number', step: '0.001' },
            {
                id: 'process_code', label: 'Jenis Proses — kosongkan = Original', type: 'select',
                options: [{ value: '00', label: '00 — Original' }, { value: '01', label: '01 — Upgrade' }, { value: '02', label: '02 — Downgrade' }],
            },
            { id: 'end_date', label: 'Tanggal Selesai', type: 'date' },
        ],
        historyCols: ['shrinkage_qty', 'outputs'],
    },
    {
        key: 'grinding', label: '⚙️ Grinding & Sieving (NC → Powder)', endpoint: '/grinding',
        fields: [
            { id: 'starting_qty', label: 'Qty Awal NC (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_qty', label: 'Qty Akhir Powder (kg)', type: 'number', step: '0.001', required: true },
            { id: 'result_date', label: 'Tanggal Hasil', type: 'date' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'outputs'],
    },
    {
        key: 'magnetization', label: '🧲 Magnetization (MG)', endpoint: '/magnetization',
        fields: [
            { id: 'quantity', label: 'Quantity (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'finding', label: 'Temuan (TEMUAN)', type: 'text' },
            { id: 'notes', label: 'Keterangan', type: 'text' },
        ],
        historyCols: ['finding', 'notes'],
    },
    {
        key: 'md_powder', label: '🧲 Metal Detection Powder (MDPW)', endpoint: '/md-powder',
        fields: [
            { id: 'quantity', label: 'Quantity (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'finding', label: 'Temuan (TEMUAN)', type: 'text' },
            { id: 'notes', label: 'Keterangan', type: 'text' },
        ],
        historyCols: ['finding', 'notes'],
    },
    {
        key: 'rework', label: '🔁 Rework (Olah Ulang)', endpoint: '/rework',
        fields: [
            { id: 'starting_qty', label: 'Qty Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'gourmet_qty', label: 'Gourmet (kg)', type: 'number', step: '0.001' },
            { id: 'eg_qty', label: 'EG (kg)', type: 'number', step: '0.001' },
            { id: 'ep_qty', label: 'EP (kg)', type: 'number', step: '0.001' },
            { id: 'nc_qty', label: 'NC / Non Conform (kg)', type: 'number', step: '0.001' },
            { id: 'process_description', label: 'Keterangan Proses', type: 'text' },
        ],
        historyCols: ['shrinkage_qty', 'outputs'],
    },
];

function stageByKey(k) { return STAGE_DEFS.find(s => s.key === k); }

function renderStageSelect() {
    document.getElementById('pTahap').innerHTML = STAGE_DEFS.map(s => `<option value="${s.key}">${s.label}</option>`).join('');
}

function activeBatchOptionsHtml() {
    const active = batches.filter(b => b.status === 'ACTIVE');
    return active.length
        ? active.map(b =>
            `<option value="${b.batch_id}">#${b.batch_id} ${b.batch_number || ''} — ${b.batch_type} (${fmtQty(b.current_quantity)} kg)</option>`).join('')
        : '';
}

function renderBatchSelect() {
    const sel = document.getElementById('pBatch');
    const opts = activeBatchOptionsHtml();
    const html = opts ? '<option value="">Pilih batch...</option>' + opts : '<option value="">Tidak ada batch aktif</option>';
    sel.innerHTML = html;
    const vSel = document.getElementById('vBatch');
    if (vSel) vSel.innerHTML = html;
    ['ajBatch', 'rjBatch', 'ssBatch'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { const current = el.value; el.innerHTML = html; if (current) el.value = current; }
    });
    updateAdjustmentCurrentQty();
    refreshMixSourceOptions();
    refreshPackingSourceOptions();
    refreshDeliverySourceOptions();
    refreshSampleDeliverySourceOptions();
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
                const optHtml = f.options.map(o => typeof o === 'string'
                    ? `<option value="${o}">${o}</option>`
                    : `<option value="${o.value}">${o.label}</option>`).join('');
                input = `<select class="form-select" id="pf_${f.id}"${f.required ? ' required' : ''}>
                    <option value="">Pilih...</option>${optHtml}</select>`;
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
    const eventTypeMap = {
        qc_test: 'QC_TEST', metal_detection: 'METAL_DETECTION', steaming: 'STEAMING', sundrying: 'SUNDRYING',
        main_curing: 'MAIN_CURING', first_curing: 'FIRST_CURING', second_curing: 'SECOND_CURING',
        third_curing: 'THIRD_CURING', airdrying: 'AIRDRYING',
        sortation: 'SORTATION', grinding: 'GRINDING', magnetization: 'MAGNETIZATION', md_powder: 'MD_POWDER',
        rework: 'REWORK',
    };
    const events = await api('GET', `/process-events?event_type=${eventTypeMap[def.key]}`);
    document.getElementById('prosesTableHead').innerHTML =
        '<th>Tanggal</th><th>Batch</th>' + def.historyCols.map(c => `<th>${c}</th>`).join('') + '<th>PIC</th>';
    document.getElementById('prosesTable').innerHTML = events.length ? events.slice(0, 30).map(ev => {
        // Prefer the INPUT link's batch (the batch the stage was performed
        // on) as the row's identifying "Batch" column; self-loop stages
        // (QC/MD/Steam/Dry/Magnetization/MDPW) have INPUT===OUTPUT so this
        // is unchanged for them, while Sortation/Grinding now show the
        // source batch rather than an arbitrary first OUTPUT.
        const batchId = (ev.links.find(l => l.role === 'INPUT') || ev.links.find(l => l.role === 'OUTPUT') || {}).batch_id;
        const cellFor = (col) => {
            if (col === 'shrinkage_qty') return fmtQty(ev.shrinkage_qty);
            if (col === 'final_quantity') { const l = ev.links.find(l => l.role === 'OUTPUT'); return l ? fmtQty(l.quantity) : '–'; }
            if (col === 'outputs') {
                const outs = ev.links.filter(l => l.role === 'OUTPUT');
                return outs.length ? outs.map(l => `#${l.batch_id} (${fmtQty(l.quantity)})`).join(', ') : '–';
            }
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
        let extra = '';
        if (result && Array.isArray(result.batches) && result.batches.length) {
            extra = ' → batch baru ' + result.batches.map(b => `#${b.batch_id}`).join(', ');
        } else if (result && result.batch && result.batch.batch_id) {
            extra = ` → batch baru #${result.batch.batch_id}`;
        }
        toast(`✅ ${def.label} untuk batch #${batchId} tersimpan.${extra}`, 'success');
        document.getElementById('prosesForm').reset();
        renderProsesFields();
        await refreshBatches();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

// ─── MIXING (MANY->ONE, own form -- see services/mixing.py) ────────────
// Doesn't fit the single-batch "Input Proses" selector (STAGE_DEFS above):
// Mixing takes N (>=2) source batches with a per-source quantity. This
// section is still thin -- `cp_qty` and `shrinkage_qty` are never computed
// here, only ever displayed from the API response (mixing.py #1/#2).
let mixSourceSeq = 0;

function mixSourceRowHtml(rowId) {
    return `<div class="form-row mix-source-row" data-row-id="${rowId}" style="align-items:end">
        <div class="form-group">
            <label class="form-label">Batch Sumber</label>
            <select class="form-select mix-src-batch" id="mixSrcBatch${rowId}"><option value="">Pilih batch...</option></select>
        </div>
        <div class="form-group" style="display:flex;gap:8px;align-items:end">
            <div style="flex:1">
                <label class="form-label">Qty (kg)</label>
                <input type="number" class="form-input mix-src-qty" id="mixSrcQty${rowId}" step="0.001" min="0">
            </div>
            <button type="button" class="btn btn-secondary btn-small mix-src-remove">✕</button>
        </div>
    </div>`;
}

function addMixSourceRow() {
    mixSourceSeq += 1;
    const host = document.getElementById('mixSources');
    host.insertAdjacentHTML('beforeend', mixSourceRowHtml(mixSourceSeq));
    const row = host.lastElementChild;
    row.querySelector('.mix-src-batch').innerHTML = '<option value="">Pilih batch...</option>' + activeBatchOptionsHtml();
    row.querySelector('.mix-src-remove').addEventListener('click', () => {
        if (host.children.length <= 2) { toast('Mixing butuh minimal 2 batch sumber.', 'error'); return; }
        row.remove();
    });
}

function initMixSources() {
    const host = document.getElementById('mixSources');
    host.innerHTML = '';
    mixSourceSeq = 0;
    addMixSourceRow();
    addMixSourceRow();
}

function refreshMixSourceOptions() {
    const host = document.getElementById('mixSources');
    if (!host) return;
    const opts = activeBatchOptionsHtml();
    host.querySelectorAll('.mix-src-batch').forEach(sel => {
        const current = sel.value;
        sel.innerHTML = '<option value="">Pilih batch...</option>' + opts;
        if (current) sel.value = current;
    });
}

document.getElementById('mixAddSource').addEventListener('click', addMixSourceRow);
document.getElementById('mixingForm').addEventListener('reset', () => setTimeout(initMixSources, 0));
document.getElementById('mTanggal').value = todayStr();

document.getElementById('mixingForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const picId = document.getElementById('mPic').value;
    if (!picId) { toast('PIC harus dipilih.', 'error'); return; }

    const sources = [];
    document.querySelectorAll('#mixSources .mix-source-row').forEach(row => {
        const batchId = row.querySelector('.mix-src-batch').value;
        const qty = row.querySelector('.mix-src-qty').value;
        if (batchId && qty) sources.push({ batch_id: Number(batchId), quantity: qty });
    });
    if (sources.length < 2) { toast('Isi minimal 2 batch sumber beserta qty-nya.', 'error'); return; }
    const batchIds = sources.map(s => s.batch_id);
    if (new Set(batchIds).size !== batchIds.length) { toast('Batch sumber tidak boleh duplikat.', 'error'); return; }

    const finalQty = document.getElementById('mFinalQty').value;
    if (!finalQty || Number(finalQty) <= 0) { toast('Qty akhir harus lebih dari 0.', 'error'); return; }

    const payload = {
        event_date: document.getElementById('mTanggal').value,
        pic_user_id: Number(picId),
        sources,
        final_qty: finalQty,
        product_description: document.getElementById('mDesc').value.trim(),
    };
    const batchType = document.getElementById('mBatchType').value;
    if (batchType) payload.batch_type = batchType;
    const grade = document.getElementById('mGrade').value.trim();
    if (grade) payload.grade_code = grade;
    const jenis = document.getElementById('mJenis').value.trim();
    if (jenis) payload.jenis_code = jenis;
    const supplierId = document.getElementById('mSupplier').value;
    if (supplierId) payload.supplier_id = Number(supplierId);

    try {
        const result = await api('POST', '/mixing', payload);
        toast(`✅ Mixing tersimpan — batch baru #${result.batch.batch_id} (${fmtQty(result.batch.current_quantity)} kg)`, 'success', 5000);
        document.getElementById('mixingForm').reset();
        document.getElementById('mTanggal').value = todayStr();
        initMixSources();
        await refreshBatches();
        await renderMixingHistory();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

async function renderMixingHistory() {
    const events = await api('GET', '/process-events?event_type=MIXING');
    document.getElementById('mixingTable').innerHTML = events.length ? events.slice(0, 30).map(ev => {
        const sourcesTxt = ev.links.filter(l => l.role === 'INPUT').map(l => `#${l.batch_id} (${fmtQty(l.quantity)})`).join(', ');
        const outputLink = ev.links.find(l => l.role === 'OUTPUT');
        const notes = ev.notes ? safeParse(ev.notes) : {};
        return `<tr>
            <td>${ev.event_date}</td>
            <td>${sourcesTxt || '–'}</td>
            <td class="batch-id">${outputLink ? '#' + outputLink.batch_id : '–'}</td>
            <td>${notes.cp_qty != null ? fmtQty(notes.cp_qty) : '–'}</td>
            <td>${outputLink ? fmtQty(outputLink.quantity) : '–'}</td>
            <td>${fmtQty(ev.shrinkage_qty)}</td>
            <td>${notes.product_description || '–'}</td>
            <td>${picName(ev.pic_user_id)}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary)">Belum ada data Mixing</td></tr>';
}

// ─── VACUUM (self-loop ONE->ONE, own form -- dynamic plastic-line list,
// see services/vacuum_packing.py #1/#2/#3) ─────────────────────────────
let vacLineSeq = 0;

function vacLineRowHtml(rowId) {
    return `<div class="form-row vac-line-row" data-row-id="${rowId}" style="align-items:end">
        <div class="form-group">
            <label class="form-label">Ukuran Plastik</label>
            <input class="form-input vac-line-size" id="vacLineSize${rowId}" placeholder="mis. 25x37.5">
        </div>
        <div class="form-group">
            <label class="form-label">Lot Plastik</label>
            <input class="form-input vac-line-lot" id="vacLineLot${rowId}">
        </div>
        <div class="form-group">
            <label class="form-label">Qty Plastik (pcs)</label>
            <input type="number" class="form-input vac-line-qty" id="vacLineQty${rowId}" step="1" min="0">
        </div>
        <div class="form-group">
            <label class="form-label">Berat/Pack (kg)</label>
            <input type="number" class="form-input vac-line-perpack" id="vacLinePerPack${rowId}" step="0.001" min="0">
        </div>
        <div class="form-group" style="display:flex;gap:8px;align-items:end">
            <div style="flex:1">
                <label class="form-label">Berat Total (kg) <span style="color:var(--danger)">*</span></label>
                <input type="number" class="form-input vac-line-total" id="vacLineTotal${rowId}" step="0.001" min="0" required>
            </div>
            <button type="button" class="btn btn-secondary btn-small vac-line-remove">✕</button>
        </div>
    </div>`;
}

function addVacLine() {
    vacLineSeq += 1;
    const host = document.getElementById('vacLines');
    host.insertAdjacentHTML('beforeend', vacLineRowHtml(vacLineSeq));
    const row = host.lastElementChild;
    row.querySelector('.vac-line-remove').addEventListener('click', () => {
        if (host.children.length <= 1) { toast('Vacuum butuh minimal 1 baris plastik.', 'error'); return; }
        row.remove();
    });
}

function initVacLines() {
    const host = document.getElementById('vacLines');
    host.innerHTML = '';
    vacLineSeq = 0;
    addVacLine();
}

document.getElementById('vacAddLine').addEventListener('click', addVacLine);
document.getElementById('vacuumForm').addEventListener('reset', () => setTimeout(initVacLines, 0));
document.getElementById('vTanggal').value = todayStr();

document.getElementById('vacuumForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const picId = document.getElementById('vPic').value;
    const batchId = document.getElementById('vBatch').value;
    if (!picId || !batchId) { toast('Batch dan PIC harus dipilih.', 'error'); return; }

    const plastic_lines = [];
    document.querySelectorAll('#vacLines .vac-line-row').forEach(row => {
        const total = row.querySelector('.vac-line-total').value;
        if (!total) return;
        plastic_lines.push({
            total_weight: total,
            plastic_size: row.querySelector('.vac-line-size').value.trim() || null,
            plastic_lot: row.querySelector('.vac-line-lot').value.trim() || null,
            plastic_qty: row.querySelector('.vac-line-qty').value || null,
            weight_per_pack: row.querySelector('.vac-line-perpack').value || null,
        });
    });
    if (!plastic_lines.length) { toast('Isi minimal 1 baris plastik dengan Berat Total.', 'error'); return; }

    const payload = {
        event_date: document.getElementById('vTanggal').value,
        pic_user_id: Number(picId),
        batch_id: Number(batchId),
        plastic_lines,
        product_description: document.getElementById('vDesc').value.trim() || null,
        buyer: document.getElementById('vBuyer').value.trim() || null,
    };
    try {
        const result = await api('POST', '/vacuum', payload);
        const outLink = result.links.find(l => l.role === 'OUTPUT');
        toast(`✅ Vacuum tersimpan — total ${outLink ? fmtQty(outLink.quantity) : ''} kg`, 'success', 5000);
        document.getElementById('vacuumForm').reset();
        document.getElementById('vTanggal').value = todayStr();
        initVacLines();
        await refreshBatches();
        await renderVacuumHistory();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

async function renderVacuumHistory() {
    const events = await api('GET', '/process-events?event_type=VACUUM');
    document.getElementById('vacuumTable').innerHTML = events.length ? events.slice(0, 30).map(ev => {
        const link = ev.links.find(l => l.role === 'OUTPUT') || ev.links.find(l => l.role === 'INPUT');
        const notes = ev.notes ? safeParse(ev.notes) : {};
        return `<tr>
            <td>${ev.event_date}</td>
            <td class="batch-id">${link ? '#' + link.batch_id : '–'}</td>
            <td>${link ? fmtQty(link.quantity) : '–'}</td>
            <td>${notes.product_description || '–'}</td>
            <td>${notes.buyer || '–'}</td>
            <td>${picName(ev.pic_user_id)}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary)">Belum ada data Vacuum</td></tr>';
}

// ─── PACKING (ONE-or-MANY->ONE, own form -- dynamic source-batch list,
// same reasoning as Mixing but min 1 source not 2, see
// services/vacuum_packing.py "Packing" section) ────────────────────────
let pkSourceSeq = 0;

function pkSourceRowHtml(rowId) {
    return `<div class="form-row pk-source-row" data-row-id="${rowId}" style="align-items:end">
        <div class="form-group">
            <label class="form-label">Batch Sumber</label>
            <select class="form-select pk-src-batch" id="pkSrcBatch${rowId}"><option value="">Pilih batch...</option></select>
        </div>
        <div class="form-group" style="display:flex;gap:8px;align-items:end">
            <div style="flex:1">
                <label class="form-label">Berat (kg)</label>
                <input type="number" class="form-input pk-src-qty" id="pkSrcQty${rowId}" step="0.001" min="0">
            </div>
            <button type="button" class="btn btn-secondary btn-small pk-src-remove">✕</button>
        </div>
    </div>`;
}

function addPkSourceRow() {
    pkSourceSeq += 1;
    const host = document.getElementById('pkSources');
    host.insertAdjacentHTML('beforeend', pkSourceRowHtml(pkSourceSeq));
    const row = host.lastElementChild;
    row.querySelector('.pk-src-batch').innerHTML = '<option value="">Pilih batch...</option>' + activeBatchOptionsHtml();
    row.querySelector('.pk-src-remove').addEventListener('click', () => {
        if (host.children.length <= 1) { toast('Packing butuh minimal 1 batch sumber.', 'error'); return; }
        row.remove();
    });
}

function initPkSources() {
    const host = document.getElementById('pkSources');
    host.innerHTML = '';
    pkSourceSeq = 0;
    addPkSourceRow();
}

function refreshPackingSourceOptions() {
    const host = document.getElementById('pkSources');
    if (!host) return;
    const opts = activeBatchOptionsHtml();
    host.querySelectorAll('.pk-src-batch').forEach(sel => {
        const current = sel.value;
        sel.innerHTML = '<option value="">Pilih batch...</option>' + opts;
        if (current) sel.value = current;
    });
}

document.getElementById('pkAddSource').addEventListener('click', addPkSourceRow);
document.getElementById('packingForm').addEventListener('reset', () => setTimeout(initPkSources, 0));
document.getElementById('pkTanggal').value = todayStr();

document.getElementById('packingForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const picId = document.getElementById('pkPic').value;
    if (!picId) { toast('PIC harus dipilih.', 'error'); return; }

    const sources = [];
    document.querySelectorAll('#pkSources .pk-source-row').forEach(row => {
        const batchId = row.querySelector('.pk-src-batch').value;
        const qty = row.querySelector('.pk-src-qty').value;
        if (batchId && qty) sources.push({ batch_id: Number(batchId), quantity: qty });
    });
    if (!sources.length) { toast('Isi minimal 1 batch sumber beserta beratnya.', 'error'); return; }
    const batchIds = sources.map(s => s.batch_id);
    if (new Set(batchIds).size !== batchIds.length) { toast('Batch sumber tidak boleh duplikat.', 'error'); return; }

    const bruto = document.getElementById('pkBruto').value;
    if (!bruto || Number(bruto) <= 0) { toast('Bruto harus lebih dari 0.', 'error'); return; }

    const payload = {
        event_date: document.getElementById('pkTanggal').value,
        pic_user_id: Number(picId),
        sources,
        gross_weight: bruto,
        plastic_size: document.getElementById('pkPlastikUkuran').value.trim() || null,
        plastic_lot: document.getElementById('pkPlastikLot').value.trim() || null,
        plastic_qty: document.getElementById('pkPlastikQty').value || null,
        carton_lot: document.getElementById('pkKartonLot').value.trim() || null,
        carton_qty: document.getElementById('pkKartonQty').value || null,
        envelope_qty: document.getElementById('pkAmplop').value || null,
        shipping_number: document.getElementById('pkNomorKirim').value.trim() || null,
        destination: document.getElementById('pkTujuan').value.trim() || null,
        product_description: document.getElementById('pkDesc').value.trim() || null,
        buyer: document.getElementById('pkBuyer').value.trim() || null,
    };
    const batchType = document.getElementById('pkBatchType').value;
    if (batchType) payload.batch_type = batchType;
    const grade = document.getElementById('pkGrade').value.trim();
    if (grade) payload.grade_code = grade;
    const jenis = document.getElementById('pkJenis').value.trim();
    if (jenis) payload.jenis_code = jenis;
    const supplierId = document.getElementById('pkSupplier').value;
    if (supplierId) payload.supplier_id = Number(supplierId);

    try {
        const result = await api('POST', '/packing', payload);
        toast(`✅ Packing tersimpan — batch baru #${result.batch.batch_id} (netto ${fmtQty(result.batch.net_weight)} kg)`, 'success', 5000);
        document.getElementById('packingForm').reset();
        document.getElementById('pkTanggal').value = todayStr();
        initPkSources();
        await refreshBatches();
        await renderPackingHistory();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

async function renderPackingHistory() {
    const events = await api('GET', '/process-events?event_type=PACKING');
    document.getElementById('packingTable').innerHTML = events.length ? events.slice(0, 30).map(ev => {
        const sourcesTxt = ev.links.filter(l => l.role === 'INPUT').map(l => `#${l.batch_id} (${fmtQty(l.quantity)})`).join(', ');
        const outputLink = ev.links.find(l => l.role === 'OUTPUT');
        const outBatch = outputLink ? batches.find(b => b.batch_id === outputLink.batch_id) : null;
        const notes = ev.notes ? safeParse(ev.notes) : {};
        return `<tr>
            <td>${ev.event_date}</td>
            <td>${sourcesTxt || '–'}</td>
            <td class="batch-id">${outputLink ? '#' + outputLink.batch_id : '–'}</td>
            <td>${outBatch && outBatch.gross_weight != null ? fmtQty(outBatch.gross_weight) : '–'}</td>
            <td>${outBatch && outBatch.net_weight != null ? fmtQty(outBatch.net_weight) : (outputLink ? fmtQty(outputLink.quantity) : '–')}</td>
            <td>${outBatch && outBatch.tare_weight != null ? fmtQty(outBatch.tare_weight) : '–'}</td>
            <td>${notes.product_description || '–'}</td>
            <td>${picName(ev.pic_user_id)}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary)">Belum ada data Packing</td></tr>';
}

// ─── DELIVERY (PD) + SAMPLE DELIVERY (SmpD) -- both STOCK-OUT, no output
// batch (services/delivery.py) -- own forms with dynamic source-batch
// lists, same pattern as Packing's (min 1 source, not 2 like Mixing).
// net_weight/tare_weight are never computed here, only ever displayed from
// the API response (delivery.py #3). ───────────────────────────────────
let dSourceSeq = 0;
let sdSourceSeq = 0;

function deliverySourceRowHtml(prefix, rowId) {
    return `<div class="form-row ${prefix}-source-row" data-row-id="${rowId}" style="align-items:end">
        <div class="form-group">
            <label class="form-label">Batch Sumber</label>
            <select class="form-select ${prefix}-src-batch" id="${prefix}SrcBatch${rowId}"><option value="">Pilih batch...</option></select>
        </div>
        <div class="form-group" style="display:flex;gap:8px;align-items:end">
            <div style="flex:1">
                <label class="form-label">Netto (kg)</label>
                <input type="number" class="form-input ${prefix}-src-qty" id="${prefix}SrcQty${rowId}" step="0.001" min="0">
            </div>
            <button type="button" class="btn btn-secondary btn-small ${prefix}-src-remove">✕</button>
        </div>
    </div>`;
}

function addDeliverySourceRow() {
    dSourceSeq += 1;
    const host = document.getElementById('dSources');
    host.insertAdjacentHTML('beforeend', deliverySourceRowHtml('d', dSourceSeq));
    const row = host.lastElementChild;
    row.querySelector('.d-src-batch').innerHTML = '<option value="">Pilih batch...</option>' + activeBatchOptionsHtml();
    row.querySelector('.d-src-remove').addEventListener('click', () => {
        if (host.children.length <= 1) { toast('Delivery butuh minimal 1 batch sumber.', 'error'); return; }
        row.remove();
    });
}

function initDSources() {
    const host = document.getElementById('dSources');
    host.innerHTML = '';
    dSourceSeq = 0;
    addDeliverySourceRow();
}

function refreshDeliverySourceOptions() {
    const host = document.getElementById('dSources');
    if (!host) return;
    const opts = activeBatchOptionsHtml();
    host.querySelectorAll('.d-src-batch').forEach(sel => {
        const current = sel.value;
        sel.innerHTML = '<option value="">Pilih batch...</option>' + opts;
        if (current) sel.value = current;
    });
}

function addSampleDeliverySourceRow() {
    sdSourceSeq += 1;
    const host = document.getElementById('sdSources');
    host.insertAdjacentHTML('beforeend', deliverySourceRowHtml('sd', sdSourceSeq));
    const row = host.lastElementChild;
    row.querySelector('.sd-src-batch').innerHTML = '<option value="">Pilih batch...</option>' + activeBatchOptionsHtml();
    row.querySelector('.sd-src-remove').addEventListener('click', () => {
        if (host.children.length <= 1) { toast('Sample Delivery butuh minimal 1 batch sumber.', 'error'); return; }
        row.remove();
    });
}

function initSdSources() {
    const host = document.getElementById('sdSources');
    host.innerHTML = '';
    sdSourceSeq = 0;
    addSampleDeliverySourceRow();
}

function refreshSampleDeliverySourceOptions() {
    const host = document.getElementById('sdSources');
    if (!host) return;
    const opts = activeBatchOptionsHtml();
    host.querySelectorAll('.sd-src-batch').forEach(sel => {
        const current = sel.value;
        sel.innerHTML = '<option value="">Pilih batch...</option>' + opts;
        if (current) sel.value = current;
    });
}

document.getElementById('dAddSource').addEventListener('click', addDeliverySourceRow);
document.getElementById('deliveryForm').addEventListener('reset', () => {
    setTimeout(initDSources, 0);
    document.getElementById('dCustomerSuggest').innerHTML = '';
});
document.getElementById('dTanggal').value = todayStr();

document.getElementById('sdAddSource').addEventListener('click', addSampleDeliverySourceRow);
document.getElementById('sampleDeliveryForm').addEventListener('reset', () => {
    setTimeout(initSdSources, 0);
    document.getElementById('sdCustomerSuggest').innerHTML = '';
});
document.getElementById('sdTanggal').value = todayStr();

function collectDeliverySources(prefix) {
    const sources = [];
    document.querySelectorAll(`#${prefix}Sources .${prefix}-source-row`).forEach(row => {
        const batchId = row.querySelector(`.${prefix}-src-batch`).value;
        const qty = row.querySelector(`.${prefix}-src-qty`).value;
        if (batchId && qty) sources.push({ batch_id: Number(batchId), quantity: qty });
    });
    return sources;
}

document.getElementById('deliveryForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const picId = document.getElementById('dPic').value;
    if (!picId) { toast('PIC harus dipilih.', 'error'); return; }

    const sources = collectDeliverySources('d');
    if (!sources.length) { toast('Isi minimal 1 batch sumber beserta netto-nya.', 'error'); return; }
    const batchIds = sources.map(s => s.batch_id);
    if (new Set(batchIds).size !== batchIds.length) { toast('Batch sumber tidak boleh duplikat.', 'error'); return; }

    const bruto = document.getElementById('dBruto').value;
    if (!bruto || Number(bruto) <= 0) { toast('Bruto harus lebih dari 0.', 'error'); return; }

    const payload = {
        event_date: document.getElementById('dTanggal').value,
        pic_user_id: Number(picId),
        sources,
        gross_weight: bruto,
        shipping_number: document.getElementById('dNomorKirim').value.trim() || null,
        destination: document.getElementById('dTujuan').value.trim() || null,
        recipient: document.getElementById('dPerusahaan').value.trim() || null,
        expedition: document.getElementById('dEkspedisi').value.trim() || null,
        transport_condition: document.getElementById('dKondisiAngkut').value.trim() || null,
        packaging_condition: document.getElementById('dKondisiKemasan').value.trim() || null,
        coly: document.getElementById('dColy').value || null,
    };
    const customerId = document.getElementById('dCustomer').value;
    if (customerId) payload.customer_id = Number(customerId);

    try {
        const result = await api('POST', '/delivery', payload);
        toast(`✅ Delivery tersimpan — netto ${fmtQty(result.shipment.net_weight)} kg`, 'success', 5000);
        document.getElementById('deliveryForm').reset();
        document.getElementById('dTanggal').value = todayStr();
        initDSources();
        await refreshBatches();
        await renderDeliveryHistory();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

document.getElementById('sampleDeliveryForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const picId = document.getElementById('sdPic').value;
    if (!picId) { toast('PIC harus dipilih.', 'error'); return; }

    const sources = collectDeliverySources('sd');
    if (!sources.length) { toast('Isi minimal 1 batch sumber beserta netto-nya.', 'error'); return; }
    const batchIds = sources.map(s => s.batch_id);
    if (new Set(batchIds).size !== batchIds.length) { toast('Batch sumber tidak boleh duplikat.', 'error'); return; }

    const bruto = document.getElementById('sdBruto').value;
    if (!bruto || Number(bruto) <= 0) { toast('Bruto harus lebih dari 0.', 'error'); return; }

    const payload = {
        event_date: document.getElementById('sdTanggal').value,
        pic_user_id: Number(picId),
        sources,
        gross_weight: bruto,
        shipping_number: document.getElementById('sdNomorKirim').value.trim() || null,
        destination: document.getElementById('sdTujuan').value.trim() || null,
        recipient: document.getElementById('sdPerusahaan').value.trim() || null,
        description: document.getElementById('sdDesc').value.trim() || null,
        expedition: document.getElementById('sdEkspedisi').value.trim() || null,
        transport_condition: document.getElementById('sdKondisiAngkut').value.trim() || null,
        packaging_condition: document.getElementById('sdKondisiKemasan').value.trim() || null,
        coly: document.getElementById('sdColy').value || null,
    };
    const customerId = document.getElementById('sdCustomer').value;
    if (customerId) payload.customer_id = Number(customerId);

    try {
        const result = await api('POST', '/sample-delivery', payload);
        toast(`✅ Sample Delivery tersimpan — netto ${fmtQty(result.shipment.net_weight)} kg`, 'success', 5000);
        document.getElementById('sampleDeliveryForm').reset();
        document.getElementById('sdTanggal').value = todayStr();
        initSdSources();
        await refreshBatches();
        await renderSampleDeliveryHistory();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

async function renderDeliveryHistory() {
    const rows = await api('GET', '/deliveries?event_type=DELIVERY');
    document.getElementById('deliveryTable').innerHTML = rows.length ? rows.map(r => {
        const sourcesTxt = r.event.links.filter(l => l.role === 'INPUT').map(l => `#${l.batch_id} (${fmtQty(l.quantity)})`).join(', ');
        return `<tr>
            <td>${r.event.event_date}</td>
            <td>${sourcesTxt || '–'}</td>
            <td>${r.shipment.shipping_number || '–'}</td>
            <td>${r.shipment.recipient || r.shipment.destination || '–'}</td>
            <td>${fmtQty(r.shipment.net_weight)}</td>
            <td>${fmtQty(r.shipment.tare_weight)}</td>
            <td>${picName(r.event.pic_user_id)}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="7" style="text-align:center;color:var(--text-secondary)">Belum ada data Delivery</td></tr>';
}

async function renderSampleDeliveryHistory() {
    const rows = await api('GET', '/deliveries?event_type=SAMPLE_DELIVERY');
    document.getElementById('sampleDeliveryTable').innerHTML = rows.length ? rows.map(r => {
        const sourcesTxt = r.event.links.filter(l => l.role === 'INPUT').map(l => `#${l.batch_id} (${fmtQty(l.quantity)})`).join(', ');
        const notes = r.event.notes ? safeParse(r.event.notes) : {};
        return `<tr>
            <td>${r.event.event_date}</td>
            <td>${sourcesTxt || '–'}</td>
            <td>${r.shipment.shipping_number || '–'}</td>
            <td>${notes.description || '–'}</td>
            <td>${fmtQty(r.shipment.net_weight)}</td>
            <td>${picName(r.event.pic_user_id)}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary)">Belum ada data Sample Delivery</td></tr>';
}

// ─── ADJUSTMENT (stock opname, gated PRODUCTION_MANAGER) + manual
// REJECTED/SUPERSEDED marking -- services/adjustment.py. None of the three
// go through record_process_event()/the genealogy graph (GENEALOGY.md
// §3.2/§5.2) -- server is the sole enforcer of the role gate on Adjustment,
// this form never filters the PIC dropdown by role (see routers/adjustment.py
// module docstring). ─────────────────────────────────────────────────────
function updateAdjustmentCurrentQty() {
    const el = document.getElementById('ajCurrentQty');
    if (!el) return;
    const batchId = Number(document.getElementById('ajBatch').value);
    const batch = batches.find(b => b.batch_id === batchId);
    el.textContent = batch ? `Qty saat ini: ${fmtQty(batch.current_quantity)} ${batch.unit}` : 'Qty saat ini: –';
}
document.getElementById('ajBatch').addEventListener('change', updateAdjustmentCurrentQty);
document.getElementById('ajTanggal').value = todayStr();

document.getElementById('adjustmentForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const batchId = document.getElementById('ajBatch').value;
    const picId = document.getElementById('ajPic').value;
    const newQty = document.getElementById('ajNewQty').value;
    const notes = document.getElementById('ajNotes').value.trim();
    if (!batchId || !picId) { toast('Batch dan PIC harus dipilih.', 'error'); return; }
    if (newQty === '' || Number(newQty) < 0) { toast('Qty baru harus diisi (boleh 0).', 'error'); return; }
    if (!notes) { toast('Alasan/catatan wajib diisi.', 'error'); return; }
    try {
        await api('POST', '/adjustment', {
            event_date: document.getElementById('ajTanggal').value,
            batch_id: Number(batchId),
            new_quantity: newQty,
            actor_user_id: Number(picId),
            notes,
        });
        toast('✅ Adjustment tersimpan.', 'success');
        document.getElementById('adjustmentForm').reset();
        document.getElementById('ajTanggal').value = todayStr();
        await refreshBatches();
        await renderAuditLog();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

document.getElementById('rejectForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const batchId = document.getElementById('rjBatch').value;
    const picId = document.getElementById('rjPic').value;
    const reason = document.getElementById('rjReason').value.trim();
    if (!batchId || !picId || !reason) { toast('Batch, PIC, dan alasan harus diisi.', 'error'); return; }
    try {
        await api('POST', `/batches/${batchId}/reject`, { actor_user_id: Number(picId), reason });
        toast(`✅ Batch #${batchId} ditandai REJECTED.`, 'success');
        document.getElementById('rejectForm').reset();
        await refreshBatches();
        await renderAuditLog();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

document.getElementById('supersedeForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const batchId = document.getElementById('ssBatch').value;
    const picId = document.getElementById('ssPic').value;
    const reason = document.getElementById('ssReason').value.trim();
    if (!batchId || !picId || !reason) { toast('Batch, PIC, dan alasan harus diisi.', 'error'); return; }
    try {
        await api('POST', `/batches/${batchId}/supersede`, { actor_user_id: Number(picId), reason });
        toast(`✅ Batch #${batchId} ditandai SUPERSEDED.`, 'success');
        document.getElementById('supersedeForm').reset();
        await refreshBatches();
        await renderAuditLog();
    } catch (err) { toast('❌ ' + err.message, 'error', 6000); }
});

async function renderAuditLog() {
    const logs = await api('GET', '/audit-logs?entity_type=Batch');
    document.getElementById('auditLogTable').innerHTML = logs.length ? logs.slice(0, 30).map(l => `
        <tr>
            <td>${new Date(l.timestamp).toLocaleString('id-ID')}</td>
            <td class="batch-id">#${l.entity_id}</td>
            <td><span class="badge badge-primary">${l.action}</span></td>
            <td>${l.before_value ?? '–'}</td>
            <td>${l.after_value ?? '–'}</td>
            <td>${picName(l.actor_user_id)}</td>
        </tr>`).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary)">Belum ada audit log</td></tr>';
}

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
    const traceEl = document.getElementById('historyTrace');
    if (!id) return;
    traceEl.innerHTML = '';
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
        await renderChainOfCustody(id, traceEl);
    } catch (err) {
        el.innerHTML = `<p style="color:var(--danger)">${err.message}</p>`;
    }
}
document.getElementById('historySearch').addEventListener('keydown', e => { if (e.key === 'Enter') findBatch(); });

// ─── CHAIN OF CUSTODY (Fase 14 report layer, over the Batch History page --
// services/traceability.py chain_of_custody_report(), see GET
// /batches/{id}/trace). This is the forward+backward end-to-end picture,
// distinct from the linear per-batch event list `findBatch()` already
// renders above: resolved suppliers at the backward root, resolved
// shipments/customers at the forward leaves, and any leaves still
// in-process (not yet SHIPPED/REJECTED) -- all fields the engine already
// computed, nothing derived client-side. ──────────────────────────────
function traceEventRowsHtml(events) {
    return events.length ? events.map(ev => `
        <tr>
            <td>${ev.event_date}</td>
            <td><span class="badge badge-primary">${ev.event_type}</span></td>
            <td>${ev.pic || '–'}</td>
            <td>${ev.total_input_quantity != null ? fmtQty(ev.total_input_quantity) : '–'}</td>
            <td>${ev.total_output_quantity != null ? fmtQty(ev.total_output_quantity) : '–'}</td>
            <td>${ev.notes || '–'}</td>
        </tr>`).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary)">–</td></tr>';
}

async function renderChainOfCustody(batchId, traceEl) {
    try {
        const t = await api('GET', `/batches/${batchId}/trace`);
        const suppliersTxt = t.suppliers.length
            ? t.suppliers.map(s => `<span class="badge badge-gray">${s.supplier_code} — ${s.name}</span>`).join(' ')
            : '<span style="color:var(--text-secondary)">Tidak ada supplier di ujung backward (batch ini sendiri hasil proses, bukan langsung dari penerimaan)</span>';
        const shipmentsTxt = t.shipments.length
            ? t.shipments.map(s => `<span class="badge badge-gray">${s.shipping_number || '(tanpa nomor)'} → ${s.recipient || s.customer_name || s.destination || '–'}</span>`).join(' ')
            : '<span style="color:var(--text-secondary)">Belum ada pengiriman tercatat di ujung forward</span>';
        const incompleteTxt = t.incomplete_leaves.length
            ? t.incomplete_leaves.map(b => `<span class="badge badge-warning">#${b.batch_id} (${b.status}, ${fmtQty(b.current_quantity)} kg)</span>`).join(' ')
            : '<span class="badge badge-success">Tidak ada — semua ujung forward sudah SHIPPED/REJECTED</span>';
        traceEl.innerHTML = `
            <div class="form-divider">🔗 Chain of Custody (Forward + Backward)</div>
            <div style="font-size:13px;margin-bottom:8px"><div class="form-label">Supplier Asal (backward root)</div>${suppliersTxt}</div>
            <div style="font-size:13px;margin-bottom:8px"><div class="form-label">Pengiriman (forward leaves)</div>${shipmentsTxt}</div>
            <div style="font-size:13px;margin-bottom:12px"><div class="form-label">Batch Masih Dalam Proses (belum SHIPPED/REJECTED)</div>${incompleteTxt}</div>
            <div class="form-divider">⬅️ Riwayat Upstream (menuju supplier)</div>
            <div class="table-container">
                <table>
                    <thead><tr><th>Tanggal</th><th>Event</th><th>PIC</th><th>Qty In</th><th>Qty Out</th><th>Catatan</th></tr></thead>
                    <tbody>${traceEventRowsHtml(t.upstream_events)}</tbody>
                </table>
            </div>
            <div class="form-divider">➡️ Riwayat Downstream (menuju shipment)</div>
            <div class="table-container">
                <table>
                    <thead><tr><th>Tanggal</th><th>Event</th><th>PIC</th><th>Qty In</th><th>Qty Out</th><th>Catatan</th></tr></thead>
                    <tbody>${traceEventRowsHtml(t.downstream_events)}</tbody>
                </table>
            </div>
        `;
    } catch (err) {
        traceEl.innerHTML = `<p style="color:var(--danger)">Chain of custody gagal dimuat: ${err.message}</p>`;
    }
}

// ─── STOCK MONITORING (Fase 13 report layer -- services/stock.py, all
// read-only: no function there ever assigns to Batch.current_quantity, so
// every call below is a GET) ────────────────────────────────────────────
async function renderStockSummary() {
    const [rows, total] = await Promise.all([
        api('GET', '/stock/summary'),
        api('GET', '/stock/total'),
    ]);
    document.getElementById('stockTotal').textContent = fmtQty(total.total_quantity) + ' kg';
    document.getElementById('stockSummaryTable').innerHTML = rows.length ? rows.map(r => `
        <tr>
            <td>${r.supplier_id ? `${r.supplier_code} — ${r.supplier_name}` : '<span style="color:var(--text-secondary)">–</span>'}</td>
            <td>${r.jenis_code || '–'}</td>
            <td>${r.grade_code || '–'}</td>
            <td>${r.batch_count}</td>
            <td>${fmtQty(r.total_quantity)} kg</td>
        </tr>`).join('') : '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary)">Belum ada stok aktif</td></tr>';
}

async function runStockReconciliation() {
    const el = document.getElementById('reconcileResult');
    el.innerHTML = '<p style="color:var(--text-secondary)">Menjalankan...</p>';
    try {
        const mismatches = await api('GET', '/stock/reconcile');
        if (!mismatches.length) {
            el.innerHTML = '<span class="badge badge-success">✅ Semua batch cocok — tidak ada selisih antara cache dan ledger.</span>';
            return;
        }
        el.innerHTML = `<span class="badge badge-danger">⚠️ ${mismatches.length} batch tidak cocok</span>
            <div class="table-container" style="margin-top:8px">
                <table>
                    <thead><tr><th>Batch ID</th><th>Status</th><th>Cache</th><th>Ledger</th></tr></thead>
                    <tbody>${mismatches.map(m => `<tr><td class="batch-id">#${m.batch_id}</td><td>${m.status}</td>
                        <td>${fmtQty(m.cached_quantity)}</td><td>${fmtQty(m.ledger_quantity)}</td></tr>`).join('')}</tbody>
                </table>
            </div>`;
    } catch (err) {
        el.innerHTML = `<p style="color:var(--danger)">${err.message}</p>`;
    }
}

async function findBatchStock() {
    const id = document.getElementById('stockBatchSearch').value.trim();
    const el = document.getElementById('stockBatchResult');
    if (!id) return;
    el.innerHTML = '<p style="color:var(--text-secondary)">Memuat...</p>';
    try {
        const [balance, txns] = await Promise.all([
            api('GET', `/batches/${id}/stock`),
            api('GET', `/batches/${id}/transactions`),
        ]);
        const matchBadge = balance.matches
            ? '<span class="badge badge-success">✅ Cocok</span>'
            : '<span class="badge badge-danger">⚠️ Tidak cocok</span>';
        el.innerHTML = `
            <div style="font-size:13px;margin-bottom:10px">
                Batch #${balance.batch_id} (${balance.status}) — Cache: <strong>${fmtQty(balance.cached_quantity)}</strong> kg,
                Ledger: <strong>${fmtQty(balance.ledger_quantity)}</strong> kg — ${matchBadge}
            </div>
            <div class="table-container">
                <table>
                    <thead><tr><th>Tanggal</th><th>Event ID</th><th>Arah</th><th>Qty</th><th>Saldo Setelah</th><th>Sampel?</th></tr></thead>
                    <tbody>${txns.length ? txns.map(t => `
                        <tr>
                            <td>${new Date(t.created_at).toLocaleString('id-ID')}</td>
                            <td>#${t.event_id}</td>
                            <td><span class="badge badge-${t.direction === 'IN' ? 'success' : 'gray'}">${t.direction}</span></td>
                            <td>${fmtQty(t.quantity)}</td>
                            <td>${fmtQty(t.balance_after)}</td>
                            <td>${t.is_sample ? 'Ya' : 'Tidak'}</td>
                        </tr>`).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary)">Belum ada transaksi</td></tr>'}</tbody>
                </table>
            </div>`;
    } catch (err) {
        el.innerHTML = `<p style="color:var(--danger)">${err.message}</p>`;
    }
}
document.getElementById('stockBatchSearch').addEventListener('keydown', e => { if (e.key === 'Enter') findBatchStock(); });

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
        initMixSources();
        initVacLines();
        initPkSources();
        initDSources();
        initSdSources();
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
