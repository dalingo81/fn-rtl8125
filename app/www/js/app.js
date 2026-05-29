/* ============================================================
   fn-r8125 前端逻辑
   RTL8125 驱动管理工具
   ============================================================ */

// 当前活跃的 EventSource 连接
let currentSSE = null;

/**
 * 判断是否在 iframe 内
 */
function inIframe() {
  try { return window.self !== window.top; } catch { return true; }
}

/**
 * 切换页面
 */
function switchPage(page) {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`.nav-btn[data-page="${page}"]`).classList.add('active');
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(`page-${page}`).classList.add('active');
}

/**
 * 切换输出面板
 */
function toggleOutput() {
  const panel = document.getElementById('outputPanel');
  panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
}

/**
 * 追加日志到输出面板
 */
function appendOutput(msg, cls = '') {
  const el = document.getElementById('outputContent');
  const text = document.createTextNode(msg);
  if (cls) {
    const span = document.createElement('span');
    span.className = cls;
    span.appendChild(text);
    el.appendChild(span);
  } else {
    el.appendChild(text);
  }
  el.scrollTop = el.scrollHeight;
}

/**
 * 清空输出
 */
function clearOutput() {
  document.getElementById('outputContent').innerHTML = '';
}

/**
 * 复制输出
 */
function copyOutput() {
  const el = document.getElementById('outputContent');
  const text = el.textContent;
  // 选中输出内容
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  appendOutput('\n[📋 输出内容已选中，按 Ctrl+C 复制]\n', 'log-warn');
}

/**
 * 更新步骤状态徽章
 */
function setBadge(step, status, text) {
  const badge = document.getElementById(`badge-${step}`);
  if (!badge) return;
  badge.className = 'step-badge ' + status;
  badge.textContent = text;
}

/**
 * 切换按钮状态
 */
function setButtonLoading(step, loading) {
  const btn = document.getElementById(`btn-${step}`);
  if (!btn) return;
  btn.disabled = loading;
  if (loading) {
    btn.innerHTML = '<span class="btn-icon">⏳</span> 执行中...';
  } else {
    btn.innerHTML = '<span class="btn-icon">▶</span> 执行';
  }
}

/**
 * 执行某一步骤 (SSE)
 */
function runStep(step) {
  if (currentSSE) {
    currentSSE.close();
    currentSSE = null;
  }

  // 展开输出面板
  const panel = document.getElementById('outputPanel');
  panel.style.display = 'flex';
  clearOutput();

  const stepMap = {
    'step1': { name: '第一步：安装编译环境', badge: 'step1' },
    'step2': { name: '第二步：编译安装驱动', badge: 'step2' },
    'step3': { name: '第三步：持久化配置', badge: 'step3' },
    'step4': { name: '驱动验证', badge: '' },
    'step5': { name: '节能优化', badge: '' },
  };

  const info = stepMap[step] || { name: step, badge: '' };
  appendOutput(`$ 开始执行：${info.name}\n`, 'prompt');
  appendOutput('─'.repeat(50) + '\n', '');

  // 更新徽章
  if (info.badge) {
    setBadge(info.badge, 'running', '运行中');
    setButtonLoading(info.badge, true);
  }

  const btnId = `btn-${step}`;
  const btn = document.getElementById(btnId);
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> 执行中...';
  }

  let iface = '';
  if (step === 'step5') {
    iface = document.getElementById('iface-select').value;
  }

  const protocol = window.location.protocol === 'https:' ? 'https' : 'http';
  const url = `${protocol}://${window.location.host}/api/run-step?step=${step}&iface=${encodeURIComponent(iface)}`;

  const es = new EventSource(url);
  currentSSE = es;

  es.addEventListener('log', (e) => {
    try {
      const data = JSON.parse(e.data);
      let cls = '';
      if (data.message.includes('[完成]')) cls = 'log-done';
      else if (data.message.includes('[错误]')) cls = 'log-error';
      else if (data.message.includes('[警告]')) cls = 'log-warn';
      else if (data.message.includes('[信息]')) cls = 'log-info';
      else if (data.message.startsWith('  ')) cls = '';
      appendOutput(data.message, cls);
    } catch {}
  });

  es.addEventListener('status', (e) => {
    try {
      const data = JSON.parse(e.data);
      document.getElementById('statusText').textContent = '运行中';
      document.getElementById('statusDot').style.background = '#fdcb6e';
    } catch {}
  });

  es.addEventListener('done', (e) => {
    try {
      const data = JSON.parse(e.data);
      es.close();
      currentSSE = null;
      document.getElementById('statusText').textContent = '就绪';
      document.getElementById('statusDot').style.background = 'var(--accent)';

      if (info.badge) {
        if (data.success) {
          setBadge(info.badge, 'done', '✓ 已完成');
          setButtonLoading(info.badge, false);
        } else {
          setBadge(info.badge, 'error', '✗ 失败');
          setButtonLoading(info.badge, false);
        }
      }

      const b = document.getElementById(btnId);
      if (b) {
        b.disabled = false;
        b.innerHTML = '<span class="btn-icon">▶</span> 执行';
      }

      appendOutput('\n' + '─'.repeat(50) + '\n', '');
      if (data.success) {
        appendOutput('✅ 步骤完成\n', 'log-done');
      } else {
        appendOutput('❌ 步骤执行出错\n', 'log-error');
      }
    } catch {}
  });

  es.addEventListener('error', (e) => {
    try {
      const data = JSON.parse(e.data);
      appendOutput(`[错误] ${data.message}\n`, 'log-error');
    } catch {
      appendOutput('[错误] 连接异常，请重试\n', 'log-error');
    }
    es.close();
    currentSSE = null;
    document.getElementById('statusText').textContent = '就绪';
    document.getElementById('statusDot').style.background = 'var(--accent)';

    if (info.badge) {
      setBadge(info.badge, 'error', '✗ 失败');
      setButtonLoading(info.badge, false);
    }

    const b = document.getElementById(btnId);
    if (b) {
      b.disabled = false;
      b.innerHTML = '<span class="btn-icon">▶</span> 执行';
    }
  });
}

/**
 * 执行节能优化
 */
function runEnergySaving() {
  const iface = document.getElementById('iface-select').value;
  if (!iface) {
    alert('请先选择一个网卡接口');
    return;
  }
  runStep('step5');
}

/**
 * 刷新网卡列表
 */
function refreshInterfaces() {
  const select = document.getElementById('iface-select');
  select.disabled = true;
  select.innerHTML = '<option value="">刷新中...</option>';

  const protocol = window.location.protocol === 'https:' ? 'https' : 'http';
  fetch(`${protocol}://${window.location.host}/api/list-interfaces`)
    .then(r => r.json())
    .then(data => {
      select.innerHTML = '';
      if (data.success && data.interfaces.length > 0) {
        data.interfaces.forEach(name => {
          const opt = document.createElement('option');
          opt.value = name;
          opt.textContent = name;
          select.appendChild(opt);
        });
        // 默认选中第一个非 lo 接口
        select.value = data.interfaces[0];
      } else {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '未检测到网卡';
        select.appendChild(opt);
      }
      select.disabled = false;
    })
    .catch(() => {
      select.innerHTML = '<option value="">加载失败</option>';
      select.disabled = false;
    });
}

/**
 * 初始化
 */
document.addEventListener('DOMContentLoaded', () => {
  refreshInterfaces();

  // 如果是 iframe，调整一些样式
  if (inIframe()) {
    document.getElementById('app').style.height = '100vh';
  }
});
