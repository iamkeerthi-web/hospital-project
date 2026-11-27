const API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:5000/api' : '/api';

const sqmsState = {
    departments: [],
    doctors: [],
    patientToken: localStorage.getItem('sqms_patient_token') || '',
    patientUser: JSON.parse(localStorage.getItem('sqms_patient_user') || 'null'),
    staffToken: localStorage.getItem('sqms_staff_token') || '',
    staffUser: JSON.parse(localStorage.getItem('sqms_staff_user') || 'null'),
};

async function apiRequest(path, options = {}, token = '') {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.error || `Request failed (${res.status})`);
    }
    return data;
}

function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function setStatusMessage(elId, msg, isError = false) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.textContent = msg;
    el.className = `status-msg ${isError ? 'status-err' : 'status-ok'}`;
}

async function loadDepartmentsAndDoctors() {
    try {
        const [deptRes, docRes] = await Promise.all([
            apiRequest('/departments'),
            apiRequest('/doctors'),
        ]);
        sqmsState.departments = deptRes.departments || [];
        sqmsState.doctors = docRes.doctors || [];
        populateDepartmentDropdowns();
        filterEntryDoctors();
    } catch (err) {
        console.warn('Could not load departments/doctors from backend:', err.message);
    }
}

function populateDepartmentDropdowns() {
    const selectIds = ['departmentSelect', 'queueDeptSelect', 'apptDeptSelect', 'staffDeptSelect', 'adminDocDept'];
    for (const id of selectIds) {
        const sel = document.getElementById(id);
        if (!sel) continue;
        const previous = sel.value;
        sel.innerHTML = sqmsState.departments
            .map(d => `<option value="${d.id}">${escapeHtml(d.name)} (${escapeHtml(d.code)})</option>`)
            .join('');
        if (previous && sqmsState.departments.some(d => String(d.id) === String(previous))) {
            sel.value = previous;
        }
    }
}

function filterEntryDoctors() {
    const deptId = document.getElementById('departmentSelect')?.value;
    const docSel = document.getElementById('doctorSelect');
    if (!docSel) return;
    const matching = sqmsState.doctors.filter(d => String(d.department_id) === String(deptId) && d.is_available);
    docSel.innerHTML = '<option value="">Auto-assign available doctor</option>' +
        matching.map(d => `<option value="${d.id}">${escapeHtml(d.name)} (${escapeHtml(d.specialization)})</option>`).join('');
}

function filterApptDoctors() {
    const deptId = document.getElementById('apptDeptSelect')?.value;
    const docSel = document.getElementById('apptDoctorSelect');
    if (!docSel) return;
    const matching = sqmsState.doctors.filter(d => String(d.department_id) === String(deptId) && d.is_available);
    docSel.innerHTML = '<option value="">Any available doctor</option>' +
        matching.map(d => `<option value="${d.id}">${escapeHtml(d.name)} (${escapeHtml(d.specialization)})</option>`).join('');
}

// ---------------- Live Queue View ----------------

async function refreshLiveQueue() {
    const overviewEl = document.getElementById('queueOverviewGrid');
    const deptSelect = document.getElementById('queueDeptSelect');
    const detailEl = document.getElementById('queueDetailPanel');
    if (!overviewEl || !deptSelect || !detailEl) return;

    try {
        const overview = await apiRequest('/queue/overview');
        overviewEl.innerHTML = (overview.departments || []).map(d => `
            <div class="stat-box" style="cursor:pointer" onclick="selectQueueDept(${d.department_id})">
                <div class="stat-val">${escapeHtml(d.current_serving_token || '—')}</div>
                <div class="stat-label">${escapeHtml(d.department_name)} (${escapeHtml(d.department_code)})</div>
                <div style="font-size:12px; margin-top:6px; color:#334155;">
                    Waiting: <b>${d.waiting_count}</b> | Doctors: <b>${d.available_doctor_count}</b>
                    ${d.emergency_count > 0 ? `<br><span class="badge badge-emergency">${d.emergency_count} Emergency</span>` : ''}
                </div>
            </div>
        `).join('');

        const selectedDeptId = deptSelect.value || (overview.departments[0] && overview.departments[0].department_id);
        if (selectedDeptId) {
            deptSelect.value = selectedDeptId;
            const snap = await apiRequest(`/queue/department/${selectedDeptId}`);
            renderDepartmentQueueDetail(snap, detailEl);
        }
    } catch (err) {
        detailEl.innerHTML = `<p class="status-err">${escapeHtml(err.message)}</p>`;
    }
}

function selectQueueDept(deptId) {
    const sel = document.getElementById('queueDeptSelect');
    if (sel) {
        sel.value = String(deptId);
        refreshLiveQueue();
    }
}

function renderDepartmentQueueDetail(snap, container) {
    const servingHtml = snap.current_serving.length
        ? snap.current_serving.map(s => `
            <div class="result" style="margin-bottom:10px;">
                <p><b>Now Serving:</b> <span class="badge badge-serving">${escapeHtml(s.token_number)}</span> — ${escapeHtml(s.patient_name)}</p>
                <p><b>Doctor:</b> ${escapeHtml(s.doctor_name || 'Assigned Doctor')}</p>
            </div>
        `).join('')
        : '<p style="color:#475569; margin:8px 0;">No token currently serving.</p>';

    const rowsHtml = snap.waiting_list.length
        ? `<table class="data-table">
            <thead>
                <tr><th>Pos</th><th>Token</th><th>Patient</th><th>Type</th><th>Priority</th><th>Est. Wait</th><th>Status</th></tr>
            </thead>
            <tbody>
                ${snap.waiting_list.map(w => `
                    <tr>
                        <td><b>#${w.position}</b></td>
                        <td><b>${escapeHtml(w.token_number)}</b></td>
                        <td>${escapeHtml(w.patient_name)}</td>
                        <td>${escapeHtml(w.token_type)}</td>
                        <td>${w.is_emergency ? '<span class="badge badge-emergency">EMERGENCY</span>' : 'Normal'}</td>
                        <td>~${w.estimated_wait_mins} mins</td>
                        <td><span class="badge badge-${escapeHtml(w.token_status)}">${escapeHtml(w.token_status)}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`
        : '<p style="color:#475569; margin-top:10px;">Waiting queue is empty.</p>';

    container.innerHTML = `
        <h3>${escapeHtml(snap.department.name)} Queue (${snap.waiting_count} waiting, ${snap.available_doctor_count} doctor(s) available)</h3>
        ${servingHtml}
        ${rowsHtml}
    `;
}

async function trackTokenStatus() {
    const tokenInput = document.getElementById('trackTokenInput')?.value.trim().toUpperCase();
    const resultEl = document.getElementById('trackTokenResult');
    if (!tokenInput) {
        resultEl.innerHTML = '<p class="status-err">Enter a token number (e.g., GEN-001)</p>';
        return;
    }
    try {
        const data = await apiRequest(`/queue/position/${encodeURIComponent(tokenInput)}`);
        resultEl.innerHTML = `
            <div class="result">
                <p><b>Token:</b> ${escapeHtml(data.token_number)} (${escapeHtml(data.department_name)})</p>
                <p><b>Status:</b> <span class="badge badge-${escapeHtml(data.token_status)}">${escapeHtml(data.token_status)}</span>
                   ${data.is_emergency ? '<span class="badge badge-emergency">EMERGENCY</span>' : ''}</p>
                <p><b>Queue Position:</b> ${data.position > 0 ? `#${data.position} (${data.patients_ahead} ahead)` : 'Not in waiting queue'}</p>
                <p><b>Estimated Wait:</b> ~${data.estimated_wait_mins} mins</p>
                <p><b>Current Serving in Dept:</b> ${escapeHtml(data.current_serving_token || 'None')}</p>
                <p><b>Checked In:</b> ${data.checked_in ? 'Yes' : 'No'}</p>
            </div>
        `;
    } catch (err) {
        resultEl.innerHTML = `<p class="status-err">${escapeHtml(err.message)}</p>`;
    }
}

// ---------------- Patient Portal ----------------

function renderPatientPortalState() {
    const banner = document.getElementById('patientAuthBanner');
    const authForms = document.getElementById('patientAuthForms');
    const dashboard = document.getElementById('patientDashboard');
    if (!banner || !authForms || !dashboard) return;

    if (sqmsState.patientToken && sqmsState.patientUser) {
        banner.classList.remove('hidden');
        document.getElementById('patientWelcomeText').textContent =
            `Signed in as ${sqmsState.patientUser.name} (${sqmsState.patientUser.phone})`;
        authForms.classList.add('hidden');
        dashboard.classList.remove('hidden');
        document.getElementById('profName').value = sqmsState.patientUser.name || '';
        document.getElementById('profAge').value = sqmsState.patientUser.age ?? '';
        document.getElementById('profEmail').value = sqmsState.patientUser.email || '';
        document.getElementById('profLang').value = sqmsState.patientUser.preferred_lang || 'en';
        loadPatientHistory();
    } else {
        banner.classList.add('hidden');
        authForms.classList.remove('hidden');
        dashboard.classList.add('hidden');
    }
}

async function handlePatientRegister() {
    try {
        const res = await apiRequest('/patients/register', {
            method: 'POST',
            body: JSON.stringify({
                name: document.getElementById('regName').value,
                age: document.getElementById('regAge').value,
                phone: document.getElementById('regPhone').value,
                email: document.getElementById('regEmail').value,
                preferred_lang: document.getElementById('regLang').value,
                password: document.getElementById('regPassword').value,
            }),
        });
        sqmsState.patientToken = res.auth_token;
        sqmsState.patientUser = res.patient;
        localStorage.setItem('sqms_patient_token', res.auth_token);
        localStorage.setItem('sqms_patient_user', JSON.stringify(res.patient));
        setStatusMessage('patientAuthStatus', 'Account created and signed in!');
        renderPatientPortalState();
    } catch (err) {
        setStatusMessage('patientAuthStatus', err.message, true);
    }
}

async function handlePatientLogin() {
    try {
        const res = await apiRequest('/patients/login', {
            method: 'POST',
            body: JSON.stringify({
                identifier: document.getElementById('loginIdentifier').value,
                password: document.getElementById('loginPassword').value,
            }),
        });
        sqmsState.patientToken = res.auth_token;
        sqmsState.patientUser = res.patient;
        localStorage.setItem('sqms_patient_token', res.auth_token);
        localStorage.setItem('sqms_patient_user', JSON.stringify(res.patient));
        setStatusMessage('patientAuthStatus', 'Logged in successfully!');
        renderPatientPortalState();
    } catch (err) {
        setStatusMessage('patientAuthStatus', err.message, true);
    }
}

function logoutPatient() {
    sqmsState.patientToken = '';
    sqmsState.patientUser = null;
    localStorage.removeItem('sqms_patient_token');
    localStorage.removeItem('sqms_patient_user');
    renderPatientPortalState();
}

async function updatePatientProfile() {
    try {
        const res = await apiRequest('/patients/me', {
            method: 'PUT',
            body: JSON.stringify({
                name: document.getElementById('profName').value,
                age: document.getElementById('profAge').value,
                email: document.getElementById('profEmail').value,
                preferred_lang: document.getElementById('profLang').value,
            }),
        }, sqmsState.patientToken);
        sqmsState.patientUser = res.patient;
        localStorage.setItem('sqms_patient_user', JSON.stringify(res.patient));
        setStatusMessage('patientProfileStatus', 'Profile updated successfully!');
        renderPatientPortalState();
    } catch (err) {
        setStatusMessage('patientProfileStatus', err.message, true);
    }
}

async function bookAppointment() {
    try {
        const res = await apiRequest('/appointments', {
            method: 'POST',
            body: JSON.stringify({
                department_id: document.getElementById('apptDeptSelect').value,
                doctor_id: document.getElementById('apptDoctorSelect').value || null,
                appointment_time: document.getElementById('apptTime').value,
                symptoms: document.getElementById('apptSymptoms').value,
                spoken_lang: sqmsState.patientUser?.preferred_lang || 'en',
            }),
        }, sqmsState.patientToken);
        setStatusMessage('apptStatus', `Appointment #${res.appointment.id} booked!`);
        loadPatientHistory();
    } catch (err) {
        setStatusMessage('apptStatus', err.message, true);
    }
}

async function checkinAppointment(apptId) {
    try {
        const res = await apiRequest(`/appointments/${apptId}/checkin`, { method: 'POST' }, sqmsState.patientToken);
        setStatusMessage('apptStatus', `Checked in! Token: ${res.token.token_number}`);
        loadPatientHistory();
        refreshLiveQueue();
    } catch (err) {
        setStatusMessage('apptStatus', err.message, true);
    }
}

async function loadPatientHistory() {
    const el = document.getElementById('patientHistoryPanel');
    if (!el || !sqmsState.patientToken) return;
    try {
        const data = await apiRequest('/patients/me/history', {}, sqmsState.patientToken);
        const tokensHtml = (data.tokens || []).length
            ? `<table class="data-table">
                <thead><tr><th>Token</th><th>Dept</th><th>Type</th><th>Pos</th><th>Wait</th><th>Status</th></tr></thead>
                <tbody>
                    ${data.tokens.map(t => `
                        <tr>
                            <td><b>${escapeHtml(t.token_number)}</b></td>
                            <td>${escapeHtml(t.department_name)}</td>
                            <td>${escapeHtml(t.token_type)}</td>
                            <td>${t.position || '—'}</td>
                            <td>${t.estimated_wait_mins ? `~${t.estimated_wait_mins}m` : '—'}</td>
                            <td><span class="badge badge-${escapeHtml(t.status)}">${escapeHtml(t.status)}</span></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>`
            : '<p style="color:#475569;">No tokens issued yet.</p>';

        const apptsHtml = (data.appointments || []).length
            ? `<table class="data-table" style="margin-top:12px;">
                <thead><tr><th>ID</th><th>Dept</th><th>Time</th><th>Status</th><th>Action</th></tr></thead>
                <tbody>
                    ${data.appointments.map(a => `
                        <tr>
                            <td>#${a.id}</td>
                            <td>${escapeHtml(a.department_name)}</td>
                            <td>${escapeHtml(a.appointment_time)}</td>
                            <td><span class="badge badge-${escapeHtml(a.status)}">${escapeHtml(a.status)}</span></td>
                            <td>
                                ${a.status === 'scheduled'
                                    ? `<button class="btn btn-green btn-sm" onclick="checkinAppointment(${a.id})">Check-In &amp; Get Token</button>`
                                    : '—'}
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>`
            : '<p style="color:#475569; margin-top:8px;">No appointments booked yet.</p>';

        el.innerHTML = `<h4>My Tokens</h4>${tokensHtml}<h4 style="margin-top:15px;">My Appointments</h4>${apptsHtml}`;
    } catch (err) {
        if (err.message.toLowerCase().includes('expired') || err.message.toLowerCase().includes('invalid')) {
            logoutPatient();
        }
    }
}

// ---------------- Staff / Doctor / Admin Portal ----------------

function renderStaffPortalState() {
    const loginCard = document.getElementById('staffLoginCard');
    const dashboard = document.getElementById('staffDashboard');
    const banner = document.getElementById('staffAuthBanner');
    if (!loginCard || !dashboard || !banner) return;

    if (sqmsState.staffToken && sqmsState.staffUser) {
        loginCard.classList.add('hidden');
        dashboard.classList.remove('hidden');
        banner.classList.remove('hidden');
        document.getElementById('staffWelcomeText').textContent =
            `Signed in: ${sqmsState.staffUser.full_name || sqmsState.staffUser.name || sqmsState.staffUser.username} (${sqmsState.staffUser.role.toUpperCase()})`;
        refreshStaffDashboard();
    } else {
        loginCard.classList.remove('hidden');
        dashboard.classList.add('hidden');
        banner.classList.add('hidden');
    }
}

async function handleStaffLogin() {
    const roleType = document.getElementById('staffLoginType').value;
    const identifier = document.getElementById('staffUsername').value;
    const password = document.getElementById('staffPassword').value;
    try {
        if (roleType === 'doctor') {
            const res = await apiRequest('/doctors/login', {
                method: 'POST',
                body: JSON.stringify({ identifier, password }),
            });
            sqmsState.staffToken = res.auth_token;
            sqmsState.staffUser = { ...res.doctor, role: 'doctor' };
        } else {
            const res = await apiRequest('/admin/login', {
                method: 'POST',
                body: JSON.stringify({ username: identifier, password }),
            });
            sqmsState.staffToken = res.auth_token;
            sqmsState.staffUser = res.user;
        }
        localStorage.setItem('sqms_staff_token', sqmsState.staffToken);
        localStorage.setItem('sqms_staff_user', JSON.stringify(sqmsState.staffUser));
        setStatusMessage('staffAuthStatus', 'Authenticated!');
        renderStaffPortalState();
    } catch (err) {
        setStatusMessage('staffAuthStatus', err.message, true);
    }
}

function logoutStaff() {
    sqmsState.staffToken = '';
    sqmsState.staffUser = null;
    localStorage.removeItem('sqms_staff_token');
    localStorage.removeItem('sqms_staff_user');
    renderStaffPortalState();
}

async function refreshStaffDashboard() {
    if (!sqmsState.staffToken) return;
    const role = sqmsState.staffUser?.role;
    const isAdminOrStaff = role === 'admin' || role === 'staff';
    const adminOnlyEl = document.getElementById('adminManagementPanels');
    if (adminOnlyEl) {
        adminOnlyEl.classList.toggle('hidden', !isAdminOrStaff);
    }

    await loadDepartmentsAndDoctors();
    await loadStaffQueueControl();
    await loadStaffDoctorsList();

    if (isAdminOrStaff) {
        await Promise.all([loadAdminStats(), loadAdminPatients()]);
    }
}

async function loadStaffQueueControl() {
    const deptId = document.getElementById('staffDeptSelect')?.value || 1;
    const container = document.getElementById('staffQueueTable');
    if (!container) return;

    try {
        const snap = await apiRequest(`/queue/department/${deptId}`);
        const servingHtml = snap.current_serving.length
            ? snap.current_serving.map(s => `
                <div class="result" style="margin-bottom:10px;">
                    <b>Serving:</b> <span class="badge badge-serving">${escapeHtml(s.token_number)}</span>
                    ${escapeHtml(s.patient_name)} (${escapeHtml(s.translated_symptoms)})
                    <button class="btn btn-sm btn-green" onclick="updateQueueTokenStatus(${s.token_id}, 'completed')">Complete</button>
                </div>
            `).join('')
            : '<p style="color:#475569; margin-bottom:8px;">No patient currently serving.</p>';

        const waitingHtml = snap.waiting_list.length
            ? `<table class="data-table">
                <thead><tr><th>Pos</th><th>Token</th><th>Patient</th><th>Symptoms</th><th>Priority</th><th>Actions</th></tr></thead>
                <tbody>
                    ${snap.waiting_list.map(w => `
                        <tr>
                            <td>#${w.position}</td>
                            <td><b>${escapeHtml(w.token_number)}</b></td>
                            <td>${escapeHtml(w.patient_name)}<br><small>${escapeHtml(w.patient_phone)}</small></td>
                            <td>${escapeHtml(w.translated_symptoms)}</td>
                            <td>${w.is_emergency ? `<span class="badge badge-emergency">EMG (${w.priority_level})</span>` : 'Normal'}</td>
                            <td>
                                ${!w.is_emergency
                                    ? `<button class="btn btn-sm btn-danger" onclick="escalateEmergency(${w.token_id}, true)">🚨 Emergency</button>`
                                    : `<button class="btn btn-sm btn-secondary" onclick="escalateEmergency(${w.token_id}, false)">Normal</button>`}
                                <button class="btn btn-sm btn-warning" onclick="updateQueueTokenStatus(${w.token_id}, 'skipped')">Skip</button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>`
            : '<p style="color:#475569;">No waiting patients in this department.</p>';

        container.innerHTML = servingHtml + waitingHtml;
    } catch (err) {
        container.innerHTML = `<p class="status-err">${escapeHtml(err.message)}</p>`;
    }
}

async function callNextInDepartment() {
    const deptId = document.getElementById('staffDeptSelect')?.value || 1;
    try {
        const res = await apiRequest(`/queue/department/${deptId}/next`, { method: 'POST' }, sqmsState.staffToken);
        setStatusMessage('staffQueueMsg', res.message);
        await refreshStaffDashboard();
    } catch (err) {
        setStatusMessage('staffQueueMsg', err.message, true);
    }
}

async function escalateEmergency(tokenId, makeEmergency) {
    try {
        const res = await apiRequest(`/queue/${tokenId}/priority`, {
            method: 'PATCH',
            body: JSON.stringify({
                is_emergency: makeEmergency,
                priority_level: makeEmergency ? 15 : 0,
            }),
        }, sqmsState.staffToken);
        setStatusMessage('staffQueueMsg', res.message);
        await loadStaffQueueControl();
    } catch (err) {
        setStatusMessage('staffQueueMsg', err.message, true);
    }
}

async function updateQueueTokenStatus(tokenId, status) {
    try {
        const res = await apiRequest(`/queue/${tokenId}/status`, {
            method: 'PATCH',
            body: JSON.stringify({ status }),
        }, sqmsState.staffToken);
        setStatusMessage('staffQueueMsg', res.message);
        await refreshStaffDashboard();
    } catch (err) {
        setStatusMessage('staffQueueMsg', err.message, true);
    }
}

async function loadStaffDoctorsList() {
    const container = document.getElementById('staffDoctorsList');
    if (!container) return;
    const role = sqmsState.staffUser?.role;
    const userId = sqmsState.staffUser?.id;

    container.innerHTML = `<table class="data-table">
        <thead><tr><th>Doctor</th><th>Department</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>
            ${sqmsState.doctors.map(d => {
                const canToggle = role === 'admin' || role === 'staff' || (role === 'doctor' && d.id === userId);
                return `<tr>
                    <td><b>${escapeHtml(d.name)}</b><br><small>${escapeHtml(d.specialization)}</small></td>
                    <td>${escapeHtml(d.department_name)}</td>
                    <td>${d.is_available ? '<span class="badge badge-serving">Available</span>' : '<span class="badge badge-cancelled">Unavailable</span>'}</td>
                    <td>
                        ${canToggle
                            ? `<button class="btn btn-sm btn-secondary" onclick="toggleDoctorAvailability(${d.id}, ${!d.is_available})">${d.is_available ? 'Set Offline' : 'Set Available'}</button>`
                            : '—'}
                    </td>
                </tr>`;
            }).join('')}
        </tbody>
    </table>`;
}

async function toggleDoctorAvailability(doctorId, isAvailable) {
    try {
        await apiRequest(`/doctors/${doctorId}/availability`, {
            method: 'PATCH',
            body: JSON.stringify({ is_available: isAvailable }),
        }, sqmsState.staffToken);
        await refreshStaffDashboard();
    } catch (err) {
        setStatusMessage('staffQueueMsg', err.message, true);
    }
}

async function loadAdminStats() {
    const el = document.getElementById('adminStatsGrid');
    if (!el) return;
    try {
        const data = await apiRequest('/admin/stats', {}, sqmsState.staffToken);
        const s = data.summary;
        el.innerHTML = `
            <div class="stat-box"><div class="stat-val">${s.total_patients}</div><div class="stat-label">Total Patients</div></div>
            <div class="stat-box"><div class="stat-val">${s.available_doctors}/${s.total_doctors}</div><div class="stat-label">Available Doctors</div></div>
            <div class="stat-box"><div class="stat-val">${s.waiting_tokens}</div><div class="stat-label">Waiting Tokens</div></div>
            <div class="stat-box"><div class="stat-val">${s.serving_tokens}</div><div class="stat-label">Now Serving</div></div>
            <div class="stat-box"><div class="stat-val">${s.completed_tokens}</div><div class="stat-label">Completed</div></div>
            <div class="stat-box"><div class="stat-val">${s.active_emergencies}</div><div class="stat-label">Active Emergencies</div></div>
        `;
    } catch (err) {
        el.innerHTML = `<p class="status-err">${escapeHtml(err.message)}</p>`;
    }
}

async function loadAdminPatients() {
    const el = document.getElementById('adminPatientsTable');
    if (!el) return;
    const q = document.getElementById('adminPatientSearch')?.value.trim() || '';
    try {
        const data = await apiRequest(`/admin/patients?q=${encodeURIComponent(q)}`, {}, sqmsState.staffToken);
        el.innerHTML = `<table class="data-table">
            <thead><tr><th>Name</th><th>Phone</th><th>Age</th><th>Type</th><th>Tokens</th></tr></thead>
            <tbody>
                ${data.patients.map(p => `
                    <tr>
                        <td>${escapeHtml(p.name)}</td>
                        <td>${escapeHtml(p.phone)}</td>
                        <td>${p.age ?? '—'}</td>
                        <td>${p.is_walkin_only ? 'Walk-In' : 'Registered'}</td>
                        <td>${p.total_tokens}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
    } catch (err) {
        el.innerHTML = `<p class="status-err">${escapeHtml(err.message)}</p>`;
    }
}

async function adminAddDepartment() {
    try {
        await apiRequest('/departments', {
            method: 'POST',
            body: JSON.stringify({
                name: document.getElementById('adminDeptName').value,
                code: document.getElementById('adminDeptCode').value,
                avg_consult_mins: document.getElementById('adminDeptMins').value || 7,
            }),
        }, sqmsState.staffToken);
        setStatusMessage('adminManageMsg', 'Department created!');
        await refreshStaffDashboard();
    } catch (err) {
        setStatusMessage('adminManageMsg', err.message, true);
    }
}

async function adminAddDoctor() {
    try {
        await apiRequest('/admin/doctors', {
            method: 'POST',
            body: JSON.stringify({
                name: document.getElementById('adminDocName').value,
                specialization: document.getElementById('adminDocSpec').value,
                department_id: document.getElementById('adminDocDept').value,
                phone: document.getElementById('adminDocPhone').value,
                email: document.getElementById('adminDocEmail').value,
                password: document.getElementById('adminDocPass').value || 'Doctor@123',
            }),
        }, sqmsState.staffToken);
        setStatusMessage('adminManageMsg', 'Doctor added!');
        await refreshStaffDashboard();
    } catch (err) {
        setStatusMessage('adminManageMsg', err.message, true);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadDepartmentsAndDoctors();
    renderPatientPortalState();
    renderStaffPortalState();
});
