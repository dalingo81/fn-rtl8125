// ============ Auth Check ============
(async function() {
    try {
        const resp = await fetch('/api/check_auth', { credentials: 'include' });
        const data = await resp.json();
        if (!data.authenticated) { window.location.href = '/login.html'; return; }
        const who = await fetch('/api/whoami', { credentials: 'include' }).then(r => r.json());
        if (who.user) document.getElementById('userBadge').textContent = '👤 ' + who.user;
        init();
    } catch(e) { window.location.href = '/login.html'; }
})();

// ============ Logout ============
async function doLogout() {
    await fetch('/api/logout', { method: 'POST', credentials: 'include' });
    window.location.href = '/login.html';
}

// ============ Status ============
let autoRefreshTimer = null;

async function refreshStatus() {
    try {
        const resp = await fetch('/api/status', { credentials: 'include' });
        if (resp.status === 401) { window.location.href = '/login.html'; return; }
        const data = await resp.json();
        updateStatusUI(data);
        document.getElementById('lastRefresh').textContent = new Date().toLocaleTimeString();
    } catch(e) {}
}

function updateStatusUI(data) {
    const s = data.status, r = data.reboot;
    const dkms = document.getElementById('sDkms');
    if (s.installed) {
        const ver = (s.dkms_status.match(/r8125\/([^,]+)/)||[])[1]||'';
        dkms.textContent = '✅ 已安装' + (ver ? ' ('+ver+')' : '');
        dkms.className = 'value ok';
    } else { dkms.textContent = '❌ 未安装'; dkms.className = 'value err'; }

    const ld = document.getElementById('sLoaded');
    if (s.loaded) { ld.textContent = '✅ 已加载'; ld.className = 'value ok'; }
    else if (r && r.dkms_installed) { ld.textContent = '⚠️ 需重启生效'; ld.className = 'value warn'; }
    else { ld.textContent = '❌ 未加载'; ld.className = 'value err'; }

    const bl = document.getElementById('sBlacklist');
    if (r && r.blacklist_exists) { bl.textContent = '✅ 已配置'; bl.className = 'value ok'; }
    else { bl.textContent = '❌ 未配置'; bl.className = 'value err'; }

    const pci = document.getElementById('sPci');
    if (s.pci_device && !s.pci_device.includes('未检测到')) { pci.textContent = s.pci_device.slice(0,60); pci.className = 'value ok'; }
    else if (s.installed) { pci.textContent = '驱动已安装，未检测到硬件'; pci.className = 'value warn'; }
    else { pci.textContent = '未检测到 RTL8125'; pci.className = 'value err'; }

    document.getElementById('btnInstall').disabled = s.installed;
    document.getElementById('btnUninstall').disabled = !s.installed;
}

// ============ SSE 流式输出 ============
function showLogPanel(title) {
    document.getElementById('logTitle').textContent = '📜 ' + title;
    document.getElementById('logCard').style.display = 'block';
    document.getElementById('logCard').scrollIntoView({ behavior: 'smooth' });
}

function closeLog() {
    document.getElementById('logCard').style.display = 'none';
}

function startSSE(endpoint, title) {
    if (window._sse) window._sse.close();

    const ce = document.getElementById('logContent');
    ce.innerHTML = '';
    showLogPanel(title);
    disableButtons(true);

    const es = new EventSource('/api' + endpoint, { withCredentials: true });
    window._sse = es;

    es.addEventListener('log', function(e) {
        try {
            const d = JSON.parse(e.data);
            const line = document.createElement('div');
            line.className = 'log-line';

            if (d.type === 'step') {
                line.classList.add('log-step');
                line.textContent = d.text;
            } else if (d.type === 'output') {
                line.classList.add('log-info');
                line.textContent = '  ' + d.text;
            } else if (d.type === 'result') {
                const icon = { success: '✅', error: '❌', info: 'ℹ️' }[d.status] || '•';
                line.textContent = icon + ' ' + d.step;
                line.classList.add('log-' + d.status);
            }
            ce.appendChild(line);
            ce.scrollTop = ce.scrollHeight;
        } catch(e) {}
    });

    es.addEventListener('done', function(e) {
        es.close();
        window._sse = null;
        disableButtons(false);
        refreshStatus();
        try {
            const d = JSON.parse(e.data);
            if (!d.success) {
                const line = document.createElement('div');
                line.className = 'log-line log-error';
                line.textContent = '❌ 操作失败';
                ce.appendChild(line);
                ce.scrollTop = ce.scrollHeight;
            }
        } catch(e) {}
    });

    es.onerror = function() {
        es.close();
        window._sse = null;
        disableButtons(false);
        refreshStatus();
    };
}

function disableButtons(disabled) {
    document.querySelectorAll('.btn-action').forEach(b => b.disabled = disabled);
}

// ============ Actions ============
function doInstall() {
    if (!confirm('即将安装 r8125 DKMS 驱动，安装后需重启系统生效。是否继续？')) return;
    startSSE('/install', '安装驱动');
}

function doUninstall() {
    if (!confirm('即将卸载 r8125 驱动，是否继续？')) return;
    startSSE('/uninstall', '卸载驱动');
}

function doOptimize() {
    if (!confirm('即将进行网络性能优化（TCP BBR/缓冲区等），是否继续？')) return;
    startSSE('/optimize', '性能优化');
}

async function doReboot() {
    if (!confirm('确定要重启系统吗？')) return;
    if (!confirm('再次确认：重启系统？')) return;

    const ce = document.getElementById('logContent');
    ce.innerHTML = '';
    showLogPanel('重启系统');
    const line = document.createElement('div');
    line.className = 'log-line log-warning';
    line.textContent = '系统将在3秒后重启...';
    ce.appendChild(line);

    await fetch('/api/reboot', { method: 'POST', credentials: 'include' });
}

// ============ Init ============
function init() {
    refreshStatus();
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(refreshStatus, 10000);
}
