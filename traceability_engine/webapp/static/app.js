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
        const e = new Error(msg);
        e.code = data && data.error;   // Fase 35: kode galat dari server
        e.hint = data && data.hint;    // Fase 35: petunjuk dari server (bila ada)
        throw e;
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

// Fase 35: pesan galat -- galat urutan tanggal (422 event_date_order_error)
// tampil dengan ikon + petunjuk dari server, lebih lama; galat lain seperti
// semula. Teks petunjuk berasal dari server (webapp/main.py), bukan aturan klien.
function toastError(err, duration = 4000) {
    if (err && err.code === 'event_date_order_error') {
        toast('📅 ' + err.message + (err.hint ? '\n' + err.hint : ''), 'error', Math.max(duration, 10000));
    } else {
        toast('❌ ' + err.message, 'error', duration);
    }
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
let currentUser = null; // Fase 47 -- {user_id, name, role, must_change_password}, diisi oleh checkAuth()

// ─── Fase 47: login gate ────────────────────────────────────────────────
// Selain daftar id di sini, `renderPicSelects()` (di bawah) mengunci 14
// dropdown "aktor" yang menegakkan role Production Manager di server
// (Fase 26/34/40/41/42/44/45/46) ke identitas sesi yang sedang login --
// tidak lagi bisa dipilih bebas dari dropdown, supaya cocok dengan
// pengecekan `require_actor_matches` di server.
const ACTOR_LOCKED_SELECT_IDS = [
    'ajPic', 'rjPic', 'ssPic', 'rtPic', 'srcPic', 'srcCancelPic', 'srBulkPic',
    'aaReviewPic', 'bncPic', 'ecDatePic', 'ecCancelPic', 'eqPic', 'enPic', 'jcPic', 'slPic',
];

function applyCurrentUserToActorFields() {
    if (!currentUser) return;
    const label = `${currentUser.name} (${currentUser.role})`;
    ACTOR_LOCKED_SELECT_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerHTML = `<option value="${currentUser.user_id}" selected>${label} — Anda</option>`;
        el.disabled = true;
    });
    const badge = document.getElementById('currentUserBadge');
    const logoutBtn = document.getElementById('logoutBtn');
    if (badge) { badge.textContent = `👤 ${currentUser.name} (${currentUser.role})`; badge.style.display = ''; }
    if (logoutBtn) logoutBtn.style.display = '';
}

function showLoginOverlay(message) {
    document.getElementById('loginOverlay').style.display = 'flex';
    document.getElementById('changePasswordOverlay').style.display = 'none';
    const errEl = document.getElementById('loginError');
    if (message) { errEl.textContent = message; errEl.style.display = ''; }
    else { errEl.style.display = 'none'; }
}

function hideLoginOverlay() {
    document.getElementById('loginOverlay').style.display = 'none';
}

function showChangePasswordOverlay() {
    document.getElementById('changePasswordOverlay').style.display = 'flex';
}

function hideChangePasswordOverlay() {
    document.getElementById('changePasswordOverlay').style.display = 'none';
}

// Mengembalikan true kalau sudah login (dan, bila perlu, sudah lewat paksa
// ganti password) -- boot() menunggu ini sebelum memuat data apa pun,
// supaya tidak ada request lain yang keburu jalan lalu gagal 401.
async function checkAuth() {
    try {
        currentUser = await api('GET', '/auth/me');
    } catch (err) {
        showLoginOverlay();
        return false;
    }
    if (currentUser.must_change_password) {
        showChangePasswordOverlay();
        return false;
    }
    hideLoginOverlay();
    applyCurrentUserToActorFields();
    return true;
}

document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    try {
        currentUser = await api('POST', '/auth/login', { username, password });
        document.getElementById('loginForm').reset();
        if (currentUser.must_change_password) {
            hideLoginOverlay();
            showChangePasswordOverlay();
        } else {
            hideLoginOverlay();
            applyCurrentUserToActorFields();
            await boot();
        }
    } catch (err) {
        showLoginOverlay(err.message || 'Username atau password salah.');
    }
});

document.getElementById('changePasswordForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const old_password = document.getElementById('cpOld').value;
    const new_password = document.getElementById('cpNew').value;
    const errEl = document.getElementById('cpError');
    try {
        await api('POST', '/auth/change-password', { old_password, new_password });
        document.getElementById('changePasswordForm').reset();
        errEl.style.display = 'none';
        hideChangePasswordOverlay();
        currentUser.must_change_password = false;
        applyCurrentUserToActorFields();
        await boot();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = '';
    }
});

document.getElementById('logoutBtn').addEventListener('click', async () => {
    try { await api('POST', '/auth/logout'); } catch (err) { /* tetap lanjut ke layar login */ }
    location.reload();
});

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
        if (page === 'adjustment') { renderAuditLog(); renderDisposition(); renderSupplierReturns(); renderAaChain(); renderEventCorrection(); renderJenisBatchOptions(); renderEventBalance(); }
        if (page === 'stock') renderStockSummary();
        if (page === 'batch-history') { renderRendemenSortation(); renderRendemenMixing(); }
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
    // Fase 47: 14 dropdown "aktor" PM-gated dikunci ke user yang login
    // (ACTOR_LOCKED_SELECT_IDS / applyCurrentUserToActorFields di atas) --
    // TIDAK diisi di sini supaya tidak menimpa kuncian itu.
    ['rPic', 'pPic', 'mPic', 'vPic', 'pkPic', 'dPic', 'sdPic']
        .forEach(id => { document.getElementById(id).innerHTML = opts; });
    applyCurrentUserToActorFields();
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
                    <button type="button" class="btn btn-secondary btn-smallall" data-alias-add="${c.customer_id}">+ Alias</button>
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
    } catch (err) { toastError(err); }
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
    } catch (err) { toastError(err); }
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
        } catch (err) { toastError(err); return; }
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
        <tr>
            <td><strong>${u.name}</strong></td>
            <td><span class="badge badge-primary">${u.role}</span></td>
            <td>
                <div style="display:flex;gap:4px;align-items:center">
                    <input class="form-input" style="padding:4px 8px;font-size:12px;width:110px" placeholder="username" data-cred-username="${u.user_id}">
                    <input class="form-input" style="padding:4px 8px;font-size:12px;width:110px" type="password" placeholder="password (min 6)" data-cred-password="${u.user_id}">
                    <button type="button" class="btn btn-secondary btn-small" data-cred-save="${u.user_id}">Set Login</button>
                </div>
            </td>
        </tr>
    `).join('') : '<tr><td colspan="3" style="text-align:center;color:var(--text-secondary)">Belum ada user</td></tr>';
}

// Fase 47: buat/reset login user (Production Manager saja -- server 403 selain itu)
document.getElementById('usersTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-cred-save]');
    if (!btn) return;
    const userId = btn.dataset.credSave;
    const username = document.querySelector(`[data-cred-username="${userId}"]`).value.trim();
    const password = document.querySelector(`[data-cred-password="${userId}"]`).value;
    if (!username || password.length < 6) {
        toast('Isi username dan password (minimal 6 karakter) dulu.', 'error');
        return;
    }
    try {
        await api('POST', `/auth/users/${userId}/credentials`, { username, password });
        toast('✅ Login user disimpan. Beri tahu username & password ini ke pemiliknya secara langsung.', 'success', 6000);
        document.querySelector(`[data-cred-username="${userId}"]`).value = '';
        document.querySelector(`[data-cred-password="${userId}"]`).value = '';
    } catch (err) { toastError(err); }
});

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
    } catch (err) { toastError(err); }
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
    } catch (err) { toastError(err); }
});

// ─── DASHBOARD ──────────────────────────────────────────────────────────
// Fase 39 -- banner pengingat retur belum diterima supplier (hanya tampilan)
async function renderSupplierReturnBanner() {
    const box = document.getElementById('supplierReturnBanner');
    if (!box) return;
    try {
        const r = await api('GET', '/supplier-returns/reminders');
        box.style.display = r.count ? '' : 'none';
        if (r.count) document.getElementById('supplierReturnBannerText').textContent =
            `${r.message} Total ${fmtQty(r.total_quantity)} kg.`;
    } catch (err) { box.style.display = 'none'; }
}
document.getElementById('supplierReturnBannerLink').addEventListener('click', (e) => {
    e.preventDefault();
    const nav = document.querySelector('.nav-item[data-page="adjustment"]');
    if (nav) nav.click();
});

async function renderDashboard() {
    renderSupplierReturnBanner();
    renderAaChainBanner();
    const data = await api('GET', '/batches?status=ACTIVE');
    const tbody = document.getElementById('dashboardTable');
    tbody.innerHTML = data.length ? data.slice(0, 15).map(b => `
        <tr>
            <td class="batch-id">${b.batch_id}</td>
            <td>${b.batch_number || '<span style="color:var(--text-secondary)">–</span>'}</td>
            <td><span class="badge badge-primary">${b.batch_type}</span></td>
            <td>${jenisCell(b)}</td>
            <td>${supplierName(b.supplier_id)}</td>
            <td>${fmtQty(b.current_quantity)} ${b.unit}</td>
            <td><span class="badge badge-success">${b.status}</span></td>
        </tr>
    `).join('') : '<tr><td colspan="7" style="text-align:center;color:var(--text-secondary)">Belum ada batch aktif</td></tr>';
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

// Fase 31: pratinjau nomor batch otomatis (Jenis + Grade + Supplier + Tanggal)
let autoNumberTimer = null;
function refreshAutoNumber() {
    clearTimeout(autoNumberTimer);
    const hint = document.getElementById('rAutoNumberPreview');
    const jenis = document.getElementById('rJenis').value;
    const manual = document.getElementById('rNomorBatch').value.trim();
    const hijau = document.getElementById('rBatchType').value === 'RAW_HIJAU';
    const gradeEl = document.getElementById('rGrade');
    if (hijau) gradeEl.value = '00';
    const grade = gradeEl.value;
    const supplier = document.getElementById('rSupplier').value;
    const tgl = document.getElementById('rTanggal').value;
    if (!jenis) { hint.textContent = ' '; return; }
    if (manual) { hint.textContent = 'Nomor batch manual terisi — nomor otomatis tidak dipakai.'; return; }
    if (!grade || !supplier || !tgl) { hint.textContent = 'Lengkapi Grade, Supplier, dan Tanggal.'; return; }
    autoNumberTimer = setTimeout(async () => {
        try {
            const q = new URLSearchParams({ jenis_code: jenis, grade_code: grade, supplier_id: supplier, event_date: tgl, batch_type: document.getElementById('rBatchType').value });
            const r = await api('GET', '/batch-number/preview?' + q.toString());
            hint.innerHTML = r.ok
                ? `Nomor batch: <code>${r.batch_number}</code>` + (r.will_merge ? ` — sudah ada (batch #${r.existing_batch_id}); stok akan DITAMBAHKAN ke batch itu.` : ' — batch baru.')
                : '⚠️ ' + r.reason;
        } catch (err) { hint.textContent = ''; }
    }, 300);
}
['rJenis', 'rGrade', 'rSupplier', 'rTanggal', 'rBatchType', 'rNomorBatch'].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener('change', refreshAutoNumber);
    el.addEventListener('input', refreshAutoNumber);
});

document.getElementById('receivingForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const netto = parseFloat(document.getElementById('rNetto').value);
    if (!netto || netto <= 0) { toast('Netto harus lebih dari 0.', 'error'); return; }
    // Pra-cek cepat saja; penegak aturan (on+off = netto) tetap server, services/receiving.py (Fase 38).
    const onS = document.getElementById('rOnSpec').value, offS = document.getElementById('rOffSpec').value;
    if (onS !== '' && offS !== '' && Math.round((Number(onS) + Number(offS)) * 1000) !== Math.round(netto * 1000)) {
        toast(`On-Spec (${onS}) + Off-Spec (${offS}) harus sama persis dengan Netto (${netto}).`, 'error', 6000); return;
    }
    const payload = {
        event_date: document.getElementById('rTanggal').value,
        pic_user_id: Number(document.getElementById('rPic').value),
        supplier_id: Number(document.getElementById('rSupplier').value),
        batch_type: document.getElementById('rBatchType').value,
        net_quantity: netto,
        batch_number: document.getElementById('rNomorBatch').value.trim() || null,
        jenis_code: document.getElementById('rJenis').value || null,
        grade_code: document.getElementById('rGrade').value || null,
        product_description: document.getElementById('rDesc').value.trim() || null,
        packaging_condition: document.getElementById('rKemasan').value.trim() || null,
        coly: document.getElementById('rColy').value ? Number(document.getElementById('rColy').value) : null,
        gross_weight: document.getElementById('rBruto').value || null,
        tare_weight: document.getElementById('rTara').value || null,
        on_spec_qty: document.getElementById('rOnSpec').value || null,
        off_spec_qty: document.getElementById('rOffSpec').value || null,
        smell_test: document.getElementById('rSmell').value.trim() || null,
        transport_no: document.getElementById('rNoAngkut').value.trim() || null,
        transport_condition: document.getElementById('rKondisiAngkut').value.trim() || null,
    };
    if (!payload.pic_user_id || !payload.supplier_id) { toast('PIC dan Supplier harus dipilih.', 'error'); return; }
    try {
        const result = await api('POST', '/receiving', payload);
        toast(`✅ Batch #${result.batch.batch_id}${result.batch.batch_number ? ' (' + result.batch.batch_number + ')' : ''} — stok kini ${fmtQty(result.batch.current_quantity)} kg`, 'success', 5000);
        document.getElementById('receivingForm').reset();
        document.getElementById('rTanggal').value = todayStr();
        document.getElementById('rNomorBatchPreview').textContent = ' ';
        await refreshBatches();
    } catch (err) { toastError(err, 6000); }
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
            { id: 'sample_received_date', label: 'Tanggal Terima Sampel', type: 'date' },
            { id: 'product_description', label: 'Deskripsi Vanilla (mis. EG / GOURMET)', type: 'text' },
            { id: 'method_temperature', label: 'Metode Suhu (°C, mis. 153)', type: 'number', step: '1' },
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
    // Hijau route only (services/curing.py). Fase 22 (2026-09-19) replaced
    // the Fase 19/20 [UNCONFIRMED] guesses with real fields from PT JAS's
    // PROSES HIJAU 2026.xlsx. Steaming above is Kering-only now -- Hijau's
    // equivalent is the dedicated 'blanching' stage below, not a reuse of
    // 'steaming' (curing.py module docstring #1). The later Sundrying step
    // in this route still reuses the 'sundrying' stage entry above
    // unchanged.
    {
        key: 'stem_removal', label: '🌿 Lepas Tangkai (Hijau)', endpoint: '/stem-removal',
        fields: [
            { id: 'starting_quantity', label: 'Berat (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty'],
    },
    {
        key: 'blanching', label: '♨️ Blanching (Hijau)', endpoint: '/blanching',
        fields: [
            { id: 'quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'temperature', label: 'Suhu (°C)', type: 'number', step: '0.1', placeholder: 'umumnya ~65°C' },
            { id: 'dip_duration_minutes', label: 'Lama Celup (menit)', type: 'number', step: '0.1', placeholder: 'umumnya ~2 menit' },
        ],
        historyCols: ['temperature', 'dip_duration_minutes'],
    },
    {
        key: 'main_curing', label: '🫙 Main Curing (Hijau)', endpoint: '/main-curing',
        fields: [
            { id: 'quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'duration_hours', label: 'Lama Pemeraman (jam)', type: 'number', step: '0.1' },
        ],
        historyCols: ['duration_hours'],
    },
    {
        key: 'first_curing', label: '🫙 1st Curing (Hijau)', endpoint: '/first-curing',
        fields: [
            { id: 'quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'duration_hours', label: 'Lama Pemeraman (jam)', type: 'number', step: '0.1' },
        ],
        historyCols: ['duration_hours'],
    },
    {
        key: 'second_curing', label: '🫙 2nd Curing (Hijau)', endpoint: '/second-curing',
        fields: [
            { id: 'quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'duration_hours', label: 'Lama Pemeraman (jam)', type: 'number', step: '0.1' },
        ],
        historyCols: ['duration_hours'],
    },
    {
        key: 'third_curing', label: '🫙 3rd Curing (Hijau)', endpoint: '/third-curing',
        fields: [
            { id: 'quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'duration_hours', label: 'Lama Pemeraman (jam)', type: 'number', step: '0.1' },
        ],
        historyCols: ['duration_hours'],
    },
    {
        key: 'airdrying', label: '🌬️ Airdrying (Hijau)', endpoint: '/airdrying',
        fields: [
            { id: 'starting_quantity', label: 'Berat Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'final_quantity', label: 'Berat Akhir (kg)', type: 'number', step: '0.001', required: true },
            { id: 'duration_days', label: 'Lama (hari)', type: 'number', step: '1' },
            { id: 'final_ka', label: 'KA Akhir (%)', type: 'number', step: '0.01' },
        ],
        historyCols: ['final_quantity', 'shrinkage_qty', 'final_ka'],
    },
    {
        key: 'sortation', label: '🧺 Sortasi', endpoint: '/sortation',
        dateLabel: 'Tanggal Mulai Sortasi (sebelum uji MD/KW pada bagian yang sudah disortir)',
        noDefaultDate: true,  // Fase 38: operator wajib memilih tanggal MULAI sendiri (bukan hari ini)
        dateHint: 'Wajib diisi manual. Ini tanggal MULAI sortasi, bukan tanggal selesai (tanggal selesai ada di kolom di bawah).',
        fields: [
            { id: 'initial_qty', label: 'Qty Awal (kg) — kosongkan = qty batch saat ini', type: 'number', step: '0.001' },
            { id: 'gourmet_qty', label: 'Gourmet (kg)', type: 'number', step: '0.001' },
            { id: 'eg_qty', label: 'EG (kg)', type: 'number', step: '0.001' },
            { id: 'ep_qty', label: 'EP (kg)', type: 'number', step: '0.001' },
            { id: 'nc_qty', label: 'NC / Non Conform (kg)', type: 'number', step: '0.001' },
            { id: 'powder_qty', label: 'Powder (kg)', type: 'number', step: '0.001' },
            { id: 'gourmet_batch_number', label: 'No. Batch Gourmet (opsional)', type: 'text' },
            { id: 'eg_batch_number', label: 'No. Batch EG (opsional)', type: 'text' },
            { id: 'ep_batch_number', label: 'No. Batch EP (opsional)', type: 'text' },
            { id: 'nc_batch_number', label: 'No. Batch NC (opsional)', type: 'text' },
            { id: 'powder_batch_number', label: 'No. Batch Powder (opsional)', type: 'text' },
            {
                id: 'process_code', label: 'Jenis Proses — kosongkan = Original', type: 'select',
                options: [{ value: '00', label: '00 — Original' }, { value: '01', label: '01 — Upgrade' }, { value: '02', label: '02 — Downgrade' }],
            },
            { id: 'end_date', label: 'Tanggal Selesai (tanggal di nomor batch)', type: 'date' },
            {
                id: 'auto_batch_number', label: 'Nomor batch hasil — hanya Upgrade/Downgrade (PP 01/02); nomor ketikan di atas tetap dipakai', type: 'select',
                options: [{ value: 'true', label: 'Buat otomatis (tanggal & supplier dari batch sumber)' }],
            },
            {
                id: 'jenis_code', label: 'Jenis hasil (opsional, ganti Jenis batch sumber)', type: 'select',
                options: [{ value: '01', label: '01 — Tahitensis' }, { value: '02', label: '02 — Planifolia' }],
            },
        ],
        derivedPreview: true,
        historyCols: ['end_date', 'shrinkage_qty', 'outputs'],
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
            {
                id: 'auto_batch_number', label: 'Nomor batch hasil (PP 04)', type: 'select',
                options: [{ value: 'true', label: 'Buat otomatis (tanggal & supplier dari batch sumber)' }],
            },
            {
                id: 'jenis_code', label: 'Jenis hasil (opsional, ganti Jenis batch sumber)', type: 'select',
                options: [{ value: '01', label: '01 — Tahitensis' }, { value: '02', label: '02 — Planifolia' }],
            },
        ],
        derivedPreview: true,
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
            `<option value="${b.batch_id}">#${b.batch_id} ${b.batch_number || ''} — ${b.batch_type}${jenisShort(b)} (${fmtQty(b.current_quantity)} kg)</option>`).join('')
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
    // Fase 44 -- semua batch (bukan hanya ACTIVE): event historis yang layak
    // dikoreksi/dibatalkan bisa ada di batch yang sudah CONSUMED/REJECTED.
    const ecSel = document.getElementById('ecBatch');
    if (ecSel) {
        const current = ecSel.value;
        ecSel.innerHTML = '<option value="">Semua batch</option>' + batches.map(b =>
            `<option value="${b.batch_id}">#${b.batch_id} ${b.batch_number || ''} — ${b.batch_type} (${b.status})</option>`).join('');
        if (current) ecSel.value = current;
    }
}

function renderProsesFields() {
    const def = stageByKey(document.getElementById('pTahap').value);
    const host = document.getElementById('prosesFields');
    if (!def) { host.innerHTML = ''; return; }
    host.innerHTML = `<div class="form-divider">${def.label}</div>
        <div class="form-group">
            <label class="form-label">${def.dateLabel || 'Tanggal'} <span style="color:var(--danger)">*</span></label>
            <input type="date" class="form-input" id="pf_event_date" value="${def.noDefaultDate ? '' : todayStr()}" required>
            ${def.dateHint ? `<div class="form-hint">${def.dateHint}</div>` : ''}
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
        }).join('') + (def.derivedPreview ? '<div class="info-box" id="pDerivedPreview">&nbsp;</div>' : '')
        + (def.key === 'sortation' ? '<div class="info-box" id="pAaInfo">&nbsp;</div>' : '');
    renderProsesHistory();
    refreshDerivedPreview();
    refreshAaInheritance();
}

// Fase 32: pratinjau nomor batch turunan (Sortasi PP 01/02, Rework PP 04)
let derivedPreviewTimer = null;
function refreshDerivedPreview() {
    clearTimeout(derivedPreviewTimer);
    const box = document.getElementById('pDerivedPreview');
    if (!box) return;
    const def = stageByKey(document.getElementById('pTahap').value);
    const auto = document.getElementById('pf_auto_batch_number');
    if (!def || !auto || auto.value !== 'true') { box.textContent = ' '; return; }
    const batchId = document.getElementById('pBatch').value;
    const pcEl = document.getElementById('pf_process_code');
    const pp = def.key === 'rework' ? '04' : (pcEl ? pcEl.value : '');
    if (!batchId) { box.textContent = 'Pilih batch sumber.'; return; }
    if (!['01', '02', '04'].includes(pp)) { box.textContent = 'Nomor otomatis hanya untuk Upgrade (01) / Downgrade (02) / Rework.'; return; }
    const slots = [['gourmet_qty', '01', 'Gourmet'], ['eg_qty', '02', 'EG'], ['ep_qty', '03', 'EP'], ['nc_qty', '04', 'NC'], ['powder_qty', '05', 'Powder']];
    const filled = slots.filter(([id]) => { const el = document.getElementById('pf_' + id); return el && Number(el.value) > 0; });
    if (!filled.length) { box.textContent = 'Isi qty grade hasil.'; return; }
    derivedPreviewTimer = setTimeout(async () => {
        const jenis = (document.getElementById('pf_jenis_code') || {}).value || '';
        const lines = [];
        for (const [id, grade, label] of filled) {
            try {
                const q = new URLSearchParams({ process_code: pp, grade_code: grade, source_batch_id: batchId, batch_type: id === 'powder_qty' ? 'POWDER' : 'PROCESSED' });
                if (jenis) q.set('jenis_code', jenis);
                const r = await api('GET', '/batch-number/preview-derived?' + q.toString());
                lines.push(r.ok
                    ? `${label}: <code>${r.batch_number}</code>` + (r.will_merge ? ` — sudah ada (batch #${r.existing_batch_id}); stok DITAMBAHKAN` : ' — batch baru')
                    : `${label}: ⚠️ ${r.reason}`);
            } catch (err) { lines.push(`${label}: –`); }
        }
        box.innerHTML = lines.join('<br>');
    }, 300);
}
// Fase 33: pewarisan AA (Sortasi) -- awalan nomor dari batch sumber + peringatan
// bila AA ketikan staf berbeda. Hanya peringatan; tidak memblokir penyimpanan.
const SORT_NUMBER_FIELDS = [['gourmet', 'pf_gourmet_batch_number'], ['eg', 'pf_eg_batch_number'], ['ep', 'pf_ep_batch_number'], ['nc', 'pf_nc_batch_number'], ['powder', 'pf_powder_batch_number']];
let aaInfo = null, aaInfoBatchId = null, aaTimer = null;
async function loadAaInfo(batchId) {
    if (!batchId) { aaInfo = null; aaInfoBatchId = null; return null; }
    if (aaInfoBatchId === batchId && aaInfo) return aaInfo;
    try { aaInfo = await api('GET', '/batch-number/inherit-aa?source_batch_id=' + encodeURIComponent(batchId)); }
    catch (err) { aaInfo = null; }
    aaInfoBatchId = batchId;
    return aaInfo;
}
function refreshAaInheritance() {
    clearTimeout(aaTimer);
    const box = document.getElementById('pAaInfo');
    if (!box) return;
    const batchId = document.getElementById('pBatch').value;
    aaTimer = setTimeout(async () => {
        const info = await loadAaInfo(batchId);
        if (!batchId || !info || !info.ok) { box.textContent = ' '; return; }
        if (!info.applies) {
            box.textContent = 'AA batch sumber (' + (info.jenis_code || '?') + ') adalah kode lama/tak dikenal — dibawa apa adanya, tanpa awalan otomatis.';
            return;
        }
        const lines = ['AA diwariskan dari batch sumber: <b>' + info.jenis_code + ' — ' + info.jenis_label + '</b>. Klik kolom No. Batch kosong untuk mengisi awalan (lanjutkan dengan tanggal-PP).'];
        for (const [name, id] of SORT_NUMBER_FIELDS) {
            const el = document.getElementById(id);
            const v = el ? el.value.trim() : '';
            if (!v) continue;
            const m = /^(\d{2})\d{4,5}-\d{6}-\d{2}$/.exec(v);
            if (m && m[1] !== info.jenis_code) lines.push('⚠️ ' + v + ': AA ' + m[1] + ' berbeda dari sumber (' + info.jenis_code + '). Jenis seharusnya tidak berubah — periksa salah input. Tetap bisa disimpan.');
        }
        box.innerHTML = lines.join('<br>');
    }, 250);
}
document.getElementById('prosesFields').addEventListener('focusin', async (e) => {
    const hit = SORT_NUMBER_FIELDS.find(([, id]) => id === e.target.id);
    if (!hit || e.target.value) return;
    const info = await loadAaInfo(document.getElementById('pBatch').value);
    const prefix = info && info.ok && info.applies ? info.prefixes[hit[0]] : null;
    if (prefix && !e.target.value) e.target.value = prefix;
});
document.getElementById('prosesFields').addEventListener('focusout', (e) => {
    // awalan saja (belum dilanjutkan tanggal-PP) dikosongkan agar tidak tersimpan setengah jadi
    if (SORT_NUMBER_FIELDS.some(([, id]) => id === e.target.id) && /^\d{7}-$/.test(e.target.value)) {
        e.target.value = '';
        refreshAaInheritance();
    }
});
document.getElementById('prosesFields').addEventListener('input', refreshAaInheritance);
document.getElementById('prosesFields').addEventListener('input', refreshDerivedPreview);
document.getElementById('prosesFields').addEventListener('change', refreshDerivedPreview);

async function renderProsesHistory() {
    const def = stageByKey(document.getElementById('pTahap').value);
    if (!def) return;
    document.getElementById('prosesRiwayatTitle').textContent = `📊 Riwayat ${def.label}`;
    const eventTypeMap = {
        qc_test: 'QC_TEST', metal_detection: 'METAL_DETECTION', steaming: 'STEAMING', sundrying: 'SUNDRYING',
        stem_removal: 'STEM_REMOVAL', blanching: 'BLANCHING',
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
            if (col === 'end_date') {
                // Fase 48: kolom resmi untuk event baru; event Sortasi lama
                // (sebelum Fase 48) masih menyimpannya di notes JSON.
                if (ev.end_date) return ev.end_date;
                const legacyNotes = ev.notes ? safeParse(ev.notes) : {};
                return legacyNotes.end_date ?? '–';
            }
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
    if (!document.getElementById('pf_event_date').value) {
        toast(def.key === 'sortation' ? 'Tanggal MULAI sortasi wajib diisi.' : 'Tanggal wajib diisi.', 'error'); return;
    }
    if (def.key === 'sortation') {
        const endEl = document.getElementById('pf_end_date');
        if (endEl && endEl.value && endEl.value < document.getElementById('pf_event_date').value) {
            toast('Tanggal selesai tidak boleh lebih awal dari tanggal MULAI sortasi.', 'error', 6000); return;
        }
    }

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
        if (result && Array.isArray(result.warnings) && result.warnings.length) {
            toast('⚠️ ' + result.warnings.join(' | '), 'info', 12000);
        }
        document.getElementById('prosesForm').reset();
        renderProsesFields();
        await refreshBatches();
    } catch (err) { toastError(err, 6000); }
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
            <button type="button" class="btn btn-secondary btn-smallall mix-src-remove">✕</button>
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

// Fase 32: pratinjau nomor batch Mixing (PP 03)
let mixPreviewTimer = null;
function refreshMixPreview() {
    clearTimeout(mixPreviewTimer);
    const box = document.getElementById('mAutoPreview');
    if (!document.getElementById('mAuto').checked) { box.textContent = ' '; return; }
    const ids = [...document.querySelectorAll('#mixSources .mix-src-batch')].map(e => e.value).filter(Boolean);
    const jenis = document.getElementById('mJenis').value.trim();
    const grade = document.getElementById('mGrade').value.trim();
    const tgl = document.getElementById('mTanggal').value;
    if (ids.length < 2 || !jenis || !grade || !tgl) { box.textContent = 'Lengkapi >=2 batch sumber, Jenis, Grade, dan Tanggal.'; return; }
    mixPreviewTimer = setTimeout(async () => {
        try {
            const q = new URLSearchParams({ process_code: '03', grade_code: grade, jenis_code: jenis, event_date: tgl, source_batch_ids: ids.join(',') });
            const r = await api('GET', '/batch-number/preview-derived?' + q.toString());
            box.innerHTML = r.ok
                ? `Nomor batch: <code>${r.batch_number}</code>` + (r.will_merge ? ` — sudah ada (batch #${r.existing_batch_id}); hasil DITAMBAHKAN ke batch itu.` : ' — batch baru.')
                : '⚠️ ' + r.reason;
        } catch (err) { box.textContent = ''; }
    }, 300);
}
['mAuto', 'mJenis', 'mGrade', 'mTanggal'].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener('change', refreshMixPreview);
    el.addEventListener('input', refreshMixPreview);
});
document.getElementById('mixSources').addEventListener('change', refreshMixPreview);

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
    if (document.getElementById('mAuto').checked) payload.auto_batch_number = true;

    try {
        const result = await api('POST', '/mixing', payload);
        toast(`✅ Mixing tersimpan — batch baru #${result.batch.batch_id} (${fmtQty(result.batch.current_quantity)} kg)`, 'success', 5000);
        document.getElementById('mixingForm').reset();
        document.getElementById('mTanggal').value = todayStr();
        initMixSources();
        await refreshBatches();
        await renderMixingHistory();
    } catch (err) { toastError(err, 6000); }
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
            <button type="button" class="btn btn-secondary btn-smallall vac-line-remove">✕</button>
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
    } catch (err) { toastError(err, 6000); }
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
            <button type="button" class="btn btn-secondary btn-smallall pk-src-remove">✕</button>
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
    } catch (err) { toastError(err, 6000); }
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
            <button type="button" class="btn btn-secondary btn-smallall ${prefix}-src-remove">✕</button>
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
    } catch (err) { toastError(err, 6000); }
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
    } catch (err) { toastError(err, 6000); }
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
    } catch (err) { toastError(err, 6000); }
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
        await renderDisposition();
    } catch (err) { toastError(err, 6000); }
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
    } catch (err) { toastError(err, 6000); }
});

// ─── DISPOSISI BATCH REJECTED (Fase 34, UI atas Fase 26) ────────────────
// Server = satu-satunya penegak role (bukan PRODUCTION_MANAGER -> 403);
// laporan hanya peringatan, tidak memblokir (services/disposition.py).
const DISPOSITION_LABELS = {
    PENDING_RETURN: ['Menunggu dikembalikan', 'badge-primary'],
    PARTIALLY_RETURNED: ['Sebagian dikembalikan', 'badge-primary'],
    RETURNED: ['Sudah dikembalikan', 'badge-success'],
    NO_STOCK: ['Tanpa stok', 'badge-gray'],
};
const DISPOSITION_OPEN = ['PENDING_RETURN', 'PARTIALLY_RETURNED'];

async function renderDisposition() {
    const tbody = document.getElementById('dispositionTable');
    const sel = document.getElementById('rtBatch');
    if (!tbody || !sel) return;
    let rows;
    try { rows = await api('GET', '/audit/disposition'); }
    catch (err) { toastError(err, 6000); return; }
    tbody.innerHTML = rows.length ? rows.map(r => {
        const [label, cls] = DISPOSITION_LABELS[r.disposition] || [r.disposition, 'badge-gray'];
        const warn = r.used_after_rejection_event_ids.length
            ? `<span class="badge badge-danger" title="${r.message}">⚠️ Dipakai setelah ditolak: event ${r.used_after_rejection_event_ids.map(i => '#' + i).join(', ')}</span>`
            : '<span style="color:var(--text-secondary)">–</span>';
        return `<tr>
            <td class="batch-id">#${r.batch_id} ${r.batch_number || ''}</td>
            <td>${r.supplier_name || '<span style="color:var(--text-secondary)">–</span>'}</td>
            <td>${r.rejected_at ? new Date(r.rejected_at).toLocaleString('id-ID') : '–'}</td>
            <td>${r.reject_reason || '–'}</td>
            <td>${fmtQty(r.quantity_on_hand)} kg</td>
            <td>${fmtQty(r.returned_quantity)} kg</td>
            <td><span class="badge ${cls}">${label}</span></td>
            <td>${warn}</td>
        </tr>`;
    }).join('') : '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary)">Belum ada batch REJECTED</td></tr>';
    const open = rows.filter(r => DISPOSITION_OPEN.includes(r.disposition));
    const current = sel.value;
    sel.innerHTML = open.length
        ? '<option value="">Pilih batch...</option>' + open.map(r =>
            `<option value="${r.batch_id}">#${r.batch_id} ${r.batch_number || ''} — sisa ${fmtQty(r.quantity_on_hand)} kg</option>`).join('')
        : '<option value="">Tidak ada batch REJECTED yang menunggu pengembalian</option>';
    if (current && open.some(r => String(r.batch_id) === current)) sel.value = current;
}

document.getElementById('rtTanggal').value = todayStr();
document.getElementById('returnForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const batchId = document.getElementById('rtBatch').value;
    const picId = document.getElementById('rtPic').value;
    const reason = document.getElementById('rtReason').value.trim();
    const qty = document.getElementById('rtQty').value;
    if (!batchId || !picId || !reason) { toast('Batch, Production Manager, dan alasan harus diisi.', 'error'); return; }
    if (qty !== '' && Number(qty) <= 0) { toast('Qty harus lebih dari 0 (atau kosongkan untuk seluruh stok).', 'error'); return; }
    try {
        await api('POST', `/batches/${batchId}/return-to-supplier`, {
            event_date: document.getElementById('rtTanggal').value,
            actor_user_id: Number(picId),
            reason,
            quantity: qty === '' ? null : qty,
        });
        toast(`✅ Batch #${batchId} dikembalikan ke supplier.`, 'success');
        document.getElementById('returnForm').reset();
        document.getElementById('rtTanggal').value = todayStr();
        await refreshBatches();
        await renderAuditLog();
        await renderDisposition();
    } catch (err) { toastError(err, 6000); }
});

// ─── STATUS RETUR KE SUPPLIER: DIKIRIM -> DITERIMA (Fase 38) ────────────
// Fase 40: konfirmasi hanya Production Manager / PIC Receiving batch itu, pembatalan hanya
// Production Manager (ditegakkan server, 403). Server juga menolak tanggal terima < tanggal kirim.
const SUPPLIER_RETURN_SOURCE = { RECEIVING_OFF_SPEC: 'Off-spec Receiving', REJECTED_BATCH: 'Batch REJECTED' };

async function renderSupplierReturns() {
    const tbody = document.getElementById('supplierReturnTable');
    const sel = document.getElementById('srcEvent');
    if (!tbody || !sel) return;
    let rows;
    try { rows = await api('GET', '/supplier-returns'); }
    catch (err) { toastError(err, 6000); return; }
    // Fase 39: yang terlambat (>= batas pengingat) di paling atas, tertua dulu
    rows.sort((a, b) => (b.overdue - a.overdue) || ((b.days_outstanding || 0) - (a.days_outstanding || 0)) || (b.event_id - a.event_id));
    tbody.innerHTML = rows.length ? rows.map(r => {
        const sent = r.status === 'DIKIRIM';
        const badge = sent
            ? (r.overdue
                ? `<span class="badge badge-danger">⏰ Terlambat — belum diterima (${r.days_outstanding} hari)</span>`
                : `<span class="badge badge-primary">Dikirim${r.days_outstanding != null ? ' (' + r.days_outstanding + ' hari)' : ''}</span>`)
            : '<span class="badge badge-success">Diterima supplier</span>';
        return `<tr>
            <td>${sent ? `<input type="checkbox" class="sr-pick" value="${r.event_id}">` : ''}</td>
            <td>#${r.event_id}</td>
            <td>${r.event_date}</td>
            <td class="batch-id">${r.batch_id ? '#' + r.batch_id + ' ' + (r.batch_number || '') : '–'}</td>
            <td>${r.supplier_name || '–'}</td>
            <td>${fmtQty(r.quantity)} ${r.unit}</td>
            <td>${SUPPLIER_RETURN_SOURCE[r.source] || r.source}</td>
            <td>${badge}</td>
            <td>${r.received_date || '–'}${r.note ? ' — ' + r.note : ''}</td>
            <td><button type="button" class="btn btn-secondary btn-small" data-hist="${r.event_id}">Riwayat</button></td>
        </tr><tr id="srHist${r.event_id}" style="display:none"><td colspan="10" class="sr-hist"></td></tr>`;
    }).join('') : '<tr><td colspan="10" style="text-align:center;color:var(--text-secondary)">Belum ada retur ke supplier</td></tr>';
    const selectAll = document.getElementById('srSelectAll');
    if (selectAll) selectAll.checked = false;
    tbody.querySelectorAll('.sr-pick').forEach(cb => cb.addEventListener('change', updateBulkButton));
    updateBulkButton();
    const open = rows.filter(r => r.status === 'DIKIRIM');
    const current = sel.value;
    sel.innerHTML = open.length
        ? '<option value="">Pilih retur...</option>' + open.map(r =>
            `<option value="${r.event_id}">#${r.event_id} — ${r.supplier_name || '?'} · ${fmtQty(r.quantity)} ${r.unit} · dikirim ${r.event_date}</option>`).join('')
        : '<option value="">Tidak ada retur yang menunggu konfirmasi</option>';
    if (current && open.some(r => String(r.event_id) === current)) sel.value = current;
    const cancelSel = document.getElementById('srcCancelEvent');
    if (cancelSel) {
        const done = rows.filter(r => r.status === 'DITERIMA');
        const cur = cancelSel.value;
        cancelSel.innerHTML = done.length
            ? '<option value="">Pilih retur...</option>' + done.map(r =>
                `<option value="${r.event_id}">#${r.event_id} — ${r.supplier_name || '?'} · ${fmtQty(r.quantity)} ${r.unit} · diterima ${r.received_date}</option>`).join('')
            : '<option value="">Tidak ada konfirmasi yang bisa dibatalkan</option>';
        if (cur && done.some(r => String(r.event_id) === cur)) cancelSel.value = cur;
    }
    tbody.querySelectorAll('button[data-hist]').forEach(btn => btn.addEventListener('click', async () => {
        const id = btn.dataset.hist;
        const row = document.getElementById('srHist' + id);
        if (row.style.display !== 'none') { row.style.display = 'none'; return; }
        try {
            const h = await api('GET', `/supplier-returns/${id}/history`);
            const label = { CONFIRMED: 'Dikonfirmasi diterima', CANCELLED: 'Konfirmasi dibatalkan' };
            row.firstElementChild.textContent = h.length
                ? h.map(x => `${(x.occurred_at || '').replace('T', ' ').slice(0, 16)} — ${label[x.action] || x.action}` +
                    `${x.received_date ? ' (tgl terima ' + x.received_date + ')' : ''} oleh ${picName(x.actor_user_id)}` +
                    `${x.note ? ' — ' + x.note : ''}`).join('  |  ')
                : 'Belum ada riwayat konfirmasi.';
            row.style.display = '';
        } catch (err) { toastError(err, 6000); }
    }));
}

document.getElementById('srcTanggal').value = todayStr();

// Fase 41: konfirmasi massal (centang di tabel; hanya Production Manager, satu tanggal, semua-atau-tidak-sama-sekali)
function pickedReturnIds() {
    return [...document.querySelectorAll('#supplierReturnTable .sr-pick:checked')].map(cb => Number(cb.value));
}
function updateBulkButton() {
    const n = pickedReturnIds().length;
    const btn = document.getElementById('srBulkBtn');
    if (btn) { btn.textContent = `Konfirmasi Terpilih (${n})`; btn.disabled = n === 0; }
}
document.getElementById('srBulkTanggal').value = todayStr();
document.getElementById('srSelectAll').addEventListener('change', (e) => {
    document.querySelectorAll('#supplierReturnTable .sr-pick').forEach(cb => { cb.checked = e.target.checked; });
    updateBulkButton();
});
document.getElementById('supplierReturnBulkForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const ids = pickedReturnIds();
    const picId = document.getElementById('srBulkPic').value;
    if (!ids.length || !picId) { toast('Pilih retur (centang) dan Production Manager.', 'error'); return; }
    try {
        const r = await api('POST', '/supplier-returns/bulk-confirm-received', {
            event_ids: ids,
            received_date: document.getElementById('srBulkTanggal').value,
            actor_user_id: Number(picId),
            note: document.getElementById('srBulkNote').value.trim() || null,
        });
        toast(`✅ ${r.length} retur dikonfirmasi diterima supplier.`, 'success');
        document.getElementById('srBulkNote').value = '';
        await renderSupplierReturns();
        renderSupplierReturnBanner();
        await renderAuditLog();
    } catch (err) {
        // galat massal: pesan server memuat SEMUA retur bermasalah; tidak ada yang tersimpan
        toastError(err, err.code === 'bulk_return_confirm_error' ? 15000 : 6000);
    }
});
document.getElementById('supplierReturnConfirmForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('srcEvent').value;
    const picId = document.getElementById('srcPic').value;
    if (!eventId || !picId) { toast('Retur dan pengonfirmasi harus dipilih.', 'error'); return; }
    try {
        await api('POST', `/supplier-returns/${eventId}/confirm-received`, {
            received_date: document.getElementById('srcTanggal').value,
            actor_user_id: Number(picId),
            note: document.getElementById('srcNote').value.trim() || null,
        });
        toast(`✅ Retur #${eventId} dikonfirmasi diterima supplier.`, 'success');
        document.getElementById('supplierReturnConfirmForm').reset();
        document.getElementById('srcTanggal').value = todayStr();
        await renderSupplierReturns();
        renderSupplierReturnBanner();
        await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});

document.getElementById('supplierReturnCancelForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('srcCancelEvent').value;
    const picId = document.getElementById('srcCancelPic').value;
    if (!eventId || !picId) { toast('Retur dan pembatal harus dipilih.', 'error'); return; }
    try {
        await api('POST', `/supplier-returns/${eventId}/cancel-confirmation`, {
            actor_user_id: Number(picId),
            reason: document.getElementById('srcCancelReason').value.trim(),
        });
        toast(`↩️ Konfirmasi retur #${eventId} dibatalkan; status kembali Dikirim.`, 'success');
        document.getElementById('supplierReturnCancelForm').reset();
        await renderSupplierReturns();
        renderSupplierReturnBanner();
        await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});

// ─── AUDIT PERUBAHAN AA RUTE HIJAU (Fase 36, laporan atas Fase 33) ──────
function aaStatusBadge(r) {
    const cls = {BARU: 'danger', DITINJAU: 'success', DIABAIKAN: 'gray', DIKOREKSI: 'primary'}[r.review_status] || 'gray';
    const note = r.review_note ? ` title="${String(r.review_note).replace(/"/g, '&quot;')}"` : '';
    return `<span class="badge badge-${cls}"${note}>${r.review_status}</span>`;
}
async function renderAaChain() {
    const tbody = document.getElementById('aaChainTable');
    if (!tbody) return;
    const hijauOnly = document.getElementById('aaChainHijauOnly').checked;
    let rows;
    try { rows = await api('GET', '/audit/aa-chain?hijau_only=' + hijauOnly); }
    catch (err) { toastError(err, 6000); return; }
    tbody.innerHTML = rows.length ? rows.map(r => `<tr title="${r.message}">
            <td>${r.event_type} #${r.event_id}</td>
            <td>${r.event_date}</td>
            <td class="batch-id">#${r.source_batch_id} ${r.source_batch_number || ''}</td>
            <td><span class="badge badge-primary">${r.source_jenis_code} ${r.source_jenis_label}</span></td>
            <td class="batch-id">#${r.result_batch_id} ${r.result_batch_number || ''}</td>
            <td><span class="badge badge-danger">⚠️ ${r.result_jenis_code} ${r.result_jenis_label}</span></td>
            <td>${r.hijau_route ? 'Hijau' : 'Lainnya'}</td>
            <td>${aaStatusBadge(r)}</td>
        </tr>`).join('')
        : '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary)">Tidak ada perubahan Jenis (AA) di rute</td></tr>';
    const sel = document.getElementById('aaReviewFinding');
    if (sel) sel.innerHTML = rows.map(r => `<option value='${JSON.stringify({e: r.event_id, s: r.source_batch_id, r: r.result_batch_id})}'>` +
        `${r.event_type} #${r.event_id}: ${r.source_batch_number || '#' + r.source_batch_id} → ${r.result_batch_number || '#' + r.result_batch_id} [${r.review_status}]</option>`).join('');
    const bsel = document.getElementById('bncBatch');
    if (bsel) {
        const seen = new Map();
        rows.forEach(r => {
            seen.set(r.source_batch_id, r.source_batch_number);
            seen.set(r.result_batch_id, r.result_batch_number);
        });
        bsel.innerHTML = [...seen].map(([id, no]) => `<option value="${id}">#${id} ${no || ''}</option>`).join('');
    }
}
async function renderAaChainBanner() {
    const box = document.getElementById('aaChainBanner');
    if (!box) return;
    try {
        const rows = await api('GET', '/audit/aa-chain?hijau_only=true&review_status=BARU');
        box.style.display = rows.length ? '' : 'none';
        if (rows.length) document.getElementById('aaChainBannerText').textContent =
            `${rows.length} temuan perubahan Jenis (AA) di rute Hijau berstatus Baru.`;
    } catch (err) { box.style.display = 'none'; }
}
document.getElementById('aaChainBannerLink').addEventListener('click', (e) => {
    e.preventDefault();
    const nav = document.querySelector('.nav-item[data-page="adjustment"]');
    if (nav) nav.click();
});
document.getElementById('aaReviewForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const raw = document.getElementById('aaReviewFinding').value;
    const picId = document.getElementById('aaReviewPic').value;
    if (!raw || !picId) { toast('Temuan dan Production Manager harus dipilih.', 'error'); return; }
    const k = JSON.parse(raw);
    try {
        await api('POST', '/audit/aa-chain/review', {
            event_id: k.e, source_batch_id: k.s, result_batch_id: k.r,
            status: document.getElementById('aaReviewStatus').value,
            actor_user_id: Number(picId), note: document.getElementById('aaReviewNote').value.trim(),
        });
        toast('✅ Status tinjau temuan disimpan.', 'success');
        document.getElementById('aaReviewNote').value = '';
        await renderAaChain(); renderAaChainBanner();
    } catch (err) { toastError(err, 6000); }
});
document.getElementById('batchNumberCorrectForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const batchId = document.getElementById('bncBatch').value;
    const picId = document.getElementById('bncPic').value;
    if (!batchId || !picId) { toast('Batch dan Production Manager harus dipilih.', 'error'); return; }
    try {
        const res = await api('POST', `/batches/${batchId}/correct-number`, {
            new_batch_number: document.getElementById('bncNewNumber').value.trim(),
            actor_user_id: Number(picId), reason: document.getElementById('bncReason').value.trim(),
        });
        toast(`✅ Nomor batch #${batchId} dikoreksi. ${res.notice || ''}`, 'success', 12000);
        document.getElementById('batchNumberCorrectForm').reset();
        await renderAaChain(); renderAaChainBanner(); await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});
document.getElementById('aaChainHijauOnly').addEventListener('change', renderAaChain);

// ─── KOREKSI TANGGAL & PEMBATALAN EVENT HISTORIS (Fase 44) ──────────────
async function renderEventCorrection() {
    const tbody = document.getElementById('ecTable');
    if (!tbody) return;
    const batchId = document.getElementById('ecBatch').value;
    let rows;
    try { rows = await api('GET', '/events/correctable' + (batchId ? `?batch_id=${batchId}` : '')); }
    catch (err) { toastError(err, 6000); return; }
    tbody.innerHTML = rows.length ? rows.map(r => `<tr>
            <td>${r.event_type} #${r.event_id}</td>
            <td>${r.event_date}</td>
            <td><span class="badge badge-success">${r.status}</span></td>
            <td>${r.batch_ids.map((id, i) => `#${id} ${r.batch_numbers[i] || ''}`).join(', ')}</td>
        </tr>`).join('')
        : '<tr><td colspan="4" style="text-align:center;color:var(--text-secondary)">Tidak ada event yang bisa dikoreksi/dibatalkan (semua sudah punya turunan, atau belum ada event)</td></tr>';
    const optHtml = rows.map(r =>
        `<option value="${r.event_id}">${r.event_type} #${r.event_id} (${r.event_date}) — ${r.batch_ids.map((id, i) => r.batch_numbers[i] || '#' + id).join(', ')}</option>`
    ).join('');
    const dsel = document.getElementById('ecDateEvent');
    if (dsel) dsel.innerHTML = optHtml;
    const csel = document.getElementById('ecCancelEvent');
    if (csel) csel.innerHTML = optHtml;
    const qsel = document.getElementById('eqEvent');
    if (qsel) { qsel.innerHTML = optHtml; await renderEventQuantityLinks(); }
    const nsel = document.getElementById('enEvent');
    if (nsel) {
        correctableNotes = new Map(rows.map(r => [String(r.event_id), r.notes || '']));
        nsel.innerHTML = optHtml; fillEventNotes();
    }
}
// ─── KOREKSI CATATAN EVENT (Fase 46) ────────────────────────────────────
let correctableNotes = new Map();
function fillEventNotes() {
    const id = document.getElementById('enEvent').value;
    document.getElementById('enNewNotes').value = correctableNotes.get(id) || '';
}
document.getElementById('enEvent').addEventListener('change', fillEventNotes);
document.getElementById('enForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('enEvent').value;
    const picId = document.getElementById('enPic').value;
    if (!eventId || !picId) { toast('Event dan Production Manager harus dipilih.', 'error'); return; }
    try {
        await api('POST', `/events/${eventId}/correct-notes`, {
            new_notes: document.getElementById('enNewNotes').value,
            actor_user_id: Number(picId), reason: document.getElementById('enReason').value.trim(),
        });
        toast(`✅ Catatan event #${eventId} dikoreksi.`, 'success');
        document.getElementById('enReason').value = '';
        await renderEventCorrection(); await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});

// ─── KOREKSI JENIS (AA) + CASCADE TURUNAN (Fase 46) ─────────────────────
async function renderJenisBatchOptions() {
    const sel = document.getElementById('jcBatch');
    if (!sel) return;
    let rows;
    try { rows = await api('GET', '/batches'); } catch (err) { return; }
    const keep = sel.value;
    sel.innerHTML = rows.filter(b => b.batch_number)
        .map(b => `<option value="${b.batch_id}">#${b.batch_id} ${b.batch_number}${b.jenis_label ? ' · ' + b.jenis_label : ''}</option>`).join('');
    if (keep) sel.value = keep;
    document.getElementById('jcPreviewStatus').innerHTML = '';
    document.getElementById('jcPreviewTable').innerHTML = '';
}
function renderJenisPlan(plan) {
    const status = document.getElementById('jcPreviewStatus');
    status.innerHTML = plan.ok
        ? `<div class="info-box" style="border-color:var(--success);color:var(--success)">✅ Jenis ${plan.old_jenis_code} → ${plan.new_jenis_code}: ${plan.changes.length} batch akan diubah, ${plan.stops.length} titik henti.</div>`
        : `<div class="info-box" style="border-color:var(--danger);color:var(--danger)">⛔ Koreksi akan DITOLAK seluruhnya: ${plan.blockers.map(b => `#${b.batch_id} ${b.batch_number || ''} — ${b.note}`).join('; ')}</div>`;
    const row = (badge, r) => `<tr>
            <td>${badge}</td>
            <td>#${r.batch_id} ${r.batch_number || ''}</td>
            <td>${r.new_batch_number || '—'}</td>
            <td>${r.via_event_id ? `${r.via_event_type} #${r.via_event_id}` : '—'}</td>
            <td>${r.note || ''}</td>
        </tr>`;
    document.getElementById('jcPreviewTable').innerHTML =
        plan.changes.map(r => row('<span class="badge badge-primary">Diubah</span>', r)).join('') +
        plan.stops.map(r => row('<span class="badge badge-warning">Berhenti</span>', r)).join('');
}
document.getElementById('jcPreviewBtn').addEventListener('click', async () => {
    const batchId = document.getElementById('jcBatch').value;
    if (!batchId) { toast('Pilih batch dulu.', 'error'); return; }
    try {
        const plan = await api('GET', `/batches/${batchId}/jenis-correction/preview?new_jenis_code=${document.getElementById('jcJenis').value}`);
        renderJenisPlan(plan);
    } catch (err) { toastError(err, 6000); }
});
document.getElementById('jenisCorrectForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const batchId = document.getElementById('jcBatch').value;
    const picId = document.getElementById('jcPic').value;
    if (!batchId || !picId) { toast('Batch dan Production Manager harus dipilih.', 'error'); return; }
    try {
        const res = await api('POST', `/batches/${batchId}/correct-jenis`, {
            new_jenis_code: document.getElementById('jcJenis').value,
            actor_user_id: Number(picId), reason: document.getElementById('jcReason').value.trim(),
        });
        toast(`✅ Jenis dikoreksi: ${res.plan.changes.length} batch diubah. ${res.notice || ''}`, 'success', 12000);
        document.getElementById('jcReason').value = '';
        await renderJenisBatchOptions(); renderJenisPlan(res.plan);
        await renderAaChain(); renderAaChainBanner(); await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});
document.getElementById('ecRefreshBtn').addEventListener('click', renderEventCorrection);
document.getElementById('ecBatch').addEventListener('change', renderEventCorrection);

document.getElementById('ecDateForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('ecDateEvent').value;
    const picId = document.getElementById('ecDatePic').value;
    if (!eventId || !picId) { toast('Event dan Production Manager harus dipilih.', 'error'); return; }
    try {
        await api('POST', `/events/${eventId}/correct-date`, {
            new_event_date: document.getElementById('ecNewDate').value,
            actor_user_id: Number(picId), reason: document.getElementById('ecDateReason').value.trim(),
        });
        toast(`✅ Tanggal event #${eventId} dikoreksi.`, 'success');
        document.getElementById('ecDateForm').reset();
        await renderEventCorrection(); await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});
document.getElementById('ecCancelForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('ecCancelEvent').value;
    const picId = document.getElementById('ecCancelPic').value;
    if (!eventId || !picId) { toast('Event dan Production Manager harus dipilih.', 'error'); return; }
    try {
        await api('POST', `/events/${eventId}/cancel`, {
            actor_user_id: Number(picId), reason: document.getElementById('ecCancelReason').value.trim(),
        });
        toast(`✅ Event #${eventId} dibatalkan (VOID). Efek stok sudah dibalik.`, 'success', 6000);
        document.getElementById('ecCancelForm').reset();
        await renderEventCorrection(); await renderAuditLog();
    } catch (err) { toastError(err, 6000); }
});
// ─── KOREKSI KUANTITAS EVENT HISTORIS (Fase 45) ─────────────────────────
async function renderEventQuantityLinks() {
    const eventId = document.getElementById('eqEvent').value;
    const lsel = document.getElementById('eqLink');
    if (!lsel) return;
    if (!eventId) { lsel.innerHTML = ''; return; }
    let links;
    try { links = await api('GET', `/events/${eventId}/links`); }
    catch (err) { toastError(err, 6000); return; }
    lsel.innerHTML = links.map(l =>
        `<option value="${l.link_id}" data-qty="${l.quantity}">${l.role} — batch #${l.batch_id}${l.batch_number ? ' ' + l.batch_number : ''} (saat ini: ${fmtQty(l.quantity)} ${l.unit})</option>`
    ).join('');
}
document.getElementById('eqEvent').addEventListener('change', renderEventQuantityLinks);

document.getElementById('eqForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('eqEvent').value;
    const linkId = document.getElementById('eqLink').value;
    const picId = document.getElementById('eqPic').value;
    if (!eventId || !linkId || !picId) { toast('Event, link, dan Production Manager harus dipilih.', 'error'); return; }
    try {
        const res = await api('POST', `/events/${eventId}/correct-quantity`, {
            link_id: Number(linkId), new_quantity: document.getElementById('eqNewQuantity').value,
            actor_user_id: Number(picId), reason: document.getElementById('eqReason').value.trim(),
        });
        const shrinkMsg = res.new_shrinkage_qty != null
            ? ` Susut ikut disesuaikan: ${fmtQty(res.old_shrinkage_qty)} → ${fmtQty(res.new_shrinkage_qty)}.` : '';
        toast(`✅ Kuantitas event #${eventId} dikoreksi. Stok & StockTransaction disesuaikan otomatis.${shrinkMsg}`, 'success', 9000);
        (res.warnings || []).forEach(w => toast(`⚠️ ${w}`, 'error', 10000));
        document.getElementById('eqForm').reset();
        await renderEventCorrection(); await renderAuditLog(); await renderEventBalance();
    } catch (err) { toastError(err, 6000); }
});

// ─── PINDAH SUSUT <-> LOSS + AUDIT KESEIMBANGAN (Fase 49) ───────────────
let slTotal = null;
async function loadShrinkageLoss(eventId) {
    const cur = document.getElementById('slCurrent');
    slTotal = null;
    let ev;
    try { ev = await api('GET', `/process-events/${eventId}`); }
    catch (err) { cur.textContent = '—'; toastError(err, 6000); return; }
    const s = Number(ev.shrinkage_qty), l = Number(ev.loss_qty);
    slTotal = Math.round((s + l) * 1000) / 1000;
    cur.textContent = `${ev.event_type} #${ev.event_id}${ev.status === 'VOID' ? ' (VOID)' : ''} — susut ${fmtQty(ev.shrinkage_qty)}, loss ${fmtQty(ev.loss_qty)} (total ${fmtQty(slTotal)})`;
    document.getElementById('slNewShrinkage').value = ev.shrinkage_qty;
    document.getElementById('slNewLoss').value = ev.loss_qty;
}
document.getElementById('slLoadBtn').addEventListener('click', () => {
    const id = document.getElementById('slEventId').value;
    if (!id) { toast('Isi nomor event dulu.', 'error'); return; }
    loadShrinkageLoss(id);
});
document.getElementById('slNewShrinkage').addEventListener('input', () => {
    if (slTotal == null) return;
    const s = Number(document.getElementById('slNewShrinkage').value || 0);
    document.getElementById('slNewLoss').value = (Math.round((slTotal - s) * 1000) / 1000).toFixed(3);
});
document.getElementById('slForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const eventId = document.getElementById('slEventId').value;
    const picId = document.getElementById('slPic').value;
    if (!eventId || !picId) { toast('Event dan Production Manager harus diisi.', 'error'); return; }
    if (slTotal == null) { toast('Klik "Muat" dulu untuk memuat susut/loss saat ini.', 'error'); return; }
    try {
        const res = await api('POST', `/events/${eventId}/transfer-shrinkage-loss`, {
            new_shrinkage_qty: document.getElementById('slNewShrinkage').value,
            new_loss_qty: document.getElementById('slNewLoss').value,
            actor_user_id: Number(picId), reason: document.getElementById('slReason').value.trim(),
        });
        toast(`✅ Event #${eventId}: susut ${fmtQty(res.old_shrinkage_qty)} → ${fmtQty(res.new_shrinkage_qty)}, loss ${fmtQty(res.old_loss_qty)} → ${fmtQty(res.new_loss_qty)}.`, 'success', 8000);
        document.getElementById('slReason').value = '';
        await loadShrinkageLoss(eventId); await renderEventBalance();
    } catch (err) { toastError(err, 6000); }
});

async function renderEventBalance() {
    const tbody = document.getElementById('ebTable');
    if (!tbody) return;
    let rows;
    try { rows = await api('GET', '/audit/event-balance'); }
    catch (err) { toastError(err, 6000); return; }
    tbody.innerHTML = rows.length ? rows.map(r => `<tr>
            <td>${r.event_type} #${r.event_id}</td>
            <td>${r.event_date}</td>
            <td>${r.batch_ids.map((id, i) => `#${id} ${r.batch_numbers[i] || ''}`).join(', ')}</td>
            <td>${fmtQty(r.sum_input)}</td>
            <td>${fmtQty(r.sum_output)}</td>
            <td>${fmtQty(r.shrinkage_qty)}</td>
            <td>${fmtQty(r.loss_qty)}</td>
            <td><span class="badge badge-warning">${Number(r.difference) > 0 ? '+' : ''}${fmtQty(r.difference)}</span></td>
            <td>${r.has_downstream
                ? `<button type="button" class="btn btn-secondary btn-small eb-chain" data-event-id="${r.event_id}">Cek Rantai Blocking</button>`
                : `<button type="button" class="btn btn-secondary btn-small eb-qty" data-event-id="${r.event_id}">Muat ke Koreksi Kuantitas</button>`}</td>
        </tr>`).join('')
        : '<tr><td colspan="9" style="text-align:center;color:var(--text-secondary)">✅ Semua event seimbang (input = output + susut + loss)</td></tr>';
    tbody.querySelectorAll('.eb-qty').forEach(btn => btn.addEventListener('click', async () => {
        const id = btn.dataset.eventId;
        const qsel = document.getElementById('eqEvent');
        if (![...qsel.options].some(o => o.value === id)) {
            qsel.insertAdjacentHTML('afterbegin', `<option value="${id}">Event #${id} (dari audit keseimbangan)</option>`);
        }
        qsel.value = id;
        await renderEventQuantityLinks();
        document.getElementById('eqNewQuantity').focus();
        qsel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
    tbody.querySelectorAll('.eb-chain').forEach(btn => btn.addEventListener('click', () => {
        document.getElementById('eqChainEventId').value = btn.dataset.eventId;
        document.getElementById('eqChainBtn').click();
        document.getElementById('eqChainEventId').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
}
document.getElementById('ebRefreshBtn').addEventListener('click', renderEventBalance);

// ─── RANTAI BLOCKING / CASCADE MANUAL BERTAHAP (Fase 45) ────────────────
document.getElementById('eqChainBtn').addEventListener('click', async () => {
    const eventId = document.getElementById('eqChainEventId').value;
    const tbody = document.getElementById('eqChainTable');
    const status = document.getElementById('eqChainStatus');
    if (!eventId) { toast('Isi nomor event dulu.', 'error'); return; }
    let result;
    try { result = await api('GET', `/events/${eventId}/blocking-chain`); }
    catch (err) { toastError(err, 6000); return; }
    if (!result.blocked) {
        status.innerHTML = `<div class="info-box" style="border-color:var(--success);color:var(--success)">✅ Event #${eventId} tidak/sudah tidak punya turunan — bisa langsung dikoreksi/dibatalkan lewat form di atas.</div>`;
        tbody.innerHTML = '';
        return;
    }
    status.innerHTML = `<div class="info-box">⚠️ Event #${eventId} diblokir oleh ${result.chain.length} event turunan. Batalkan URUT DARI BARIS PALING ATAS, lalu klik "Cek Rantai Blocking" lagi untuk menyegarkan daftar.</div>`;
    tbody.innerHTML = result.chain.map(r => `<tr>
            <td>${r.event_type} #${r.event_id}</td>
            <td>${r.event_date}</td>
            <td><span class="badge badge-success">${r.status}</span></td>
            <td>${r.batch_ids.map((id, i) => `#${id} ${r.batch_numbers[i] || ''}`).join(', ')}</td>
            <td><button type="button" class="btn btn-secondary btn-small eq-chain-load" data-event-id="${r.event_id}">Muat ke Pembatalan</button></td>
        </tr>`).join('');
    tbody.querySelectorAll('.eq-chain-load').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = btn.dataset.eventId;
            const csel = document.getElementById('ecCancelEvent');
            if (![...csel.options].some(o => o.value === id)) {
                csel.insertAdjacentHTML('afterbegin', `<option value="${id}">Event #${id} (dari rantai blocking)</option>`);
            }
            csel.value = id;
            document.getElementById('ecCancelReason').focus();
            csel.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
    });
});

document.getElementById('ecHistoryBtn').addEventListener('click', async () => {
    const eventId = document.getElementById('ecHistoryEventId').value;
    const tbody = document.getElementById('ecHistoryTable');
    if (!eventId) { toast('Isi nomor event dulu.', 'error'); return; }
    let rows;
    try { rows = await api('GET', `/events/${eventId}/history`); }
    catch (err) { toastError(err, 6000); return; }
    const kindLabel = { DATE_CORRECTION: 'Koreksi Tanggal', QUANTITY_CORRECTION: 'Koreksi Kuantitas', SHRINKAGE_CORRECTION: 'Koreksi Susut/Loss', NOTES_CORRECTION: 'Koreksi Catatan', CANCELLATION: 'Pembatalan' };
    tbody.innerHTML = rows.length ? rows.map(h => `<tr>
            <td>${new Date(h.occurred_at).toLocaleString('id-ID')}</td>
            <td><span class="badge badge-primary">${kindLabel[h.kind] || h.kind}</span></td>
            <td>${h.detail}</td>
            <td>${h.reason}</td>
            <td>${picName(h.actor_user_id)}</td>
        </tr>`).join('')
        : '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary)">Belum ada riwayat koreksi/pembatalan untuk event ini</td></tr>';
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
// Fase 35: label Jenis singkat untuk teks <option> (tanpa HTML), memakai
// BatchOut.jenis_label yang sama dengan jenisCell(); '' bila jenis kosong.
function jenisShort(b) {
    if (!b.jenis_code) return '';
    return ' · ' + (b.jenis_label ? b.jenis_label : 'AA ' + b.jenis_code + ' (tidak dikenal)');
}
// Fase 34: label Jenis (AA) dari BatchOut.jenis_label / jenis_is_legacy
// (read-side saja; 01 Tahitensis, 02 Planifolia, 03/04 = legacy, Fase 30).
function jenisCell(b) {
    if (!b.jenis_code) return '<span style="color:var(--text-secondary)">–</span>';
    if (!b.jenis_label) return `<span class="badge badge-gray" title="Kode AA tidak dikenal">${b.jenis_code}</span>`;
    return b.jenis_is_legacy
        ? `<span class="badge badge-gray" title="Kode AA lama (legacy), diterima apa adanya">${b.jenis_code} ${b.jenis_label}</span>`
        : `<span class="badge badge-primary">${b.jenis_code} ${b.jenis_label}</span>`;
}

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
            <td>${jenisCell(b)}</td>
            <td><span class="badge badge-primary">${b.batch_type}</span></td>
            <td>${supplierName(b.supplier_id)}</td>
            <td>${fmtQty(b.current_quantity)} ${b.unit}</td>
            <td><span class="badge badge-${b.status === 'ACTIVE' ? 'success' : 'gray'}">${b.status}</span></td>
            <td>${new Date(b.created_at).toLocaleString('id-ID')}</td>
        </tr>`).join('') : '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary)">Belum ada batch</td></tr>';
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
                <div><div class="form-label">Jenis (AA)</div>${jenisCell(b)}</div>
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
    if (!(await checkAuth())) return;  // Fase 47: berhenti di layar login/ganti-password
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
        toastError(err, 8000);
    }
}
boot();


// ─── RENDEMEN SORTASI (Fase 24) -- GET /rendemen/sortation, derived from
// genealogy server-side (services/rendemen.py); nothing computed here. ─────
async function renderRendemenSortation() {
    const tb = document.getElementById('rendemenTable');
    try {
        const rows = await api('GET', '/rendemen/sortation');
        const dash = '–';
        tb.innerHTML = rows.length ? rows.slice().reverse().map(r => `
            <tr>
                <td>${r.event_date}</td>
                <td>#${r.input_batch_id} ${r.input_batch_number || ''}</td>
                <td>${r.raw_weight != null ? fmtQty(r.raw_weight) : dash}</td>
                <td>${fmtQty(r.output_quantity)}</td>
                <td>${fmtQty(r.shrinkage_qty)}</td>
                <td><strong>${r.rendemen != null ? Number(r.rendemen).toFixed(2) : dash}</strong></td>
                <td>${r.yield_percent != null ? Number(r.yield_percent).toFixed(2) + '%' : dash}</td>
                <td>${r.outputs.map(o => `#${o.batch_id} ${o.batch_number || ''} (${fmtQty(o.quantity)})`).join(', ')}</td>
            </tr>`).join('')
            : '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary)">Belum ada sortasi</td></tr>';
    } catch (err) {
        tb.innerHTML = `<tr><td colspan="8" style="color:var(--danger)">Rendemen gagal dimuat: ${err.message}</td></tr>`;
    }
}

// ─── RENDEMEN MIXING (Fase 35) -- GET /rendemen/mixing; angka dihitung server. ─
async function renderRendemenMixing() {
    const tb = document.getElementById('rendemenMixTable');
    try {
        const rows = await api('GET', '/rendemen/mixing');
        const dash = '–';
        tb.innerHTML = rows.length ? rows.slice().reverse().map(r => `
            <tr>
                <td>${r.event_date}</td>
                <td>${r.output_batch_id != null ? '#' + r.output_batch_id + ' ' + (r.output_batch_number || '') : dash}</td>
                <td>${r.source_count}</td>
                <td>${fmtQty(r.input_quantity)}</td>
                <td>${fmtQty(r.output_quantity)}</td>
                <td>${fmtQty(r.shrinkage_qty)}</td>
                <td><strong>${r.rendemen != null ? Number(r.rendemen).toFixed(4) : dash}</strong></td>
                <td>${r.yield_percent != null ? Number(r.yield_percent).toFixed(2) + '%' : dash}</td>
                <td>${r.sources.map(s => `#${s.batch_id} ${s.batch_number || ''} (${fmtQty(s.quantity)})`).join(', ')}</td>
            </tr>`).join('')
            : '<tr><td colspan="9" style="text-align:center;color:var(--text-secondary)">Belum ada Mixing</td></tr>';
    } catch (err) {
        tb.innerHTML = `<tr><td colspan="9" style="color:var(--danger)">Rendemen Mixing gagal dimuat: ${err.message}</td></tr>`;
    }
}
