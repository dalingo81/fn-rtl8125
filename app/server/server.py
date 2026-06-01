#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTL8125 网卡驱动管理 Web 后端
多线程、SSE 流式输出、使用系统 /etc/shadow 验证
"""

import os
import sys
import json
import subprocess
import secrets
import time
import socket
import threading
import crypt
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# ============ 配置 ============
CONFIG_DIR = os.environ.get('TRIM_PKGETC', '/usr/local/apps/@appdata/fn-rtl8125/etc')
LOG_DIR = os.environ.get('TRIM_PKGVAR', '/usr/local/apps/@appdata/fn-rtl8125/var')
SESSION_FILE = os.path.join(LOG_DIR, '.sessions.json')

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# 端口优先级：wizard_port > port.conf > TRIM_SERVICE_PORT > 5303
_PORT_FILE = os.path.join(CONFIG_DIR, 'port.conf')
try:
    PORT = int(os.environ.get('wizard_port', ''))
except:
    try:
        if os.path.exists(_PORT_FILE):
            with open(_PORT_FILE) as _pf:
                for _line in _pf:
                    if _line.startswith('PORT='):
                        PORT = int(_line.strip().split('=',1)[1])
                        break
                else:
                    PORT = int(os.environ.get('TRIM_SERVICE_PORT', '5303'))
        else:
            PORT = int(os.environ.get('TRIM_SERVICE_PORT', '5303'))
    except:
        PORT = int(os.environ.get('TRIM_SERVICE_PORT', '5303'))


# ============ 多线程 Server ============
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ============ Session 管理（线程安全） ============
_sessions_lock = threading.Lock()

def load_sessions():
    try:
        with open(SESSION_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_sessions(sessions):
    with open(SESSION_FILE, 'w') as f:
        json.dump(sessions, f)

def verify_session(token):
    with _sessions_lock:
        sessions = load_sessions()
        if token in sessions:
            if time.time() < sessions[token].get('expiry', 0):
                return True
            del sessions[token]
            save_sessions(sessions)
        return False

def create_session(username):
    token = secrets.token_hex(32)
    with _sessions_lock:
        sessions = load_sessions()
        sessions[token] = {'expiry': time.time() + 86400, 'user': username}
        save_sessions(sessions)
    return token

def delete_session(token):
    with _sessions_lock:
        sessions = load_sessions()
        if token in sessions:
            del sessions[token]
            save_sessions(sessions)

def session_user(token):
    with _sessions_lock:
        sessions = load_sessions()
        if token in sessions:
            return sessions[token].get('user', 'unknown')
    return None


# ============ 系统登录验证 ============
def verify_shadow_login(username, password):
    if not username or not password or username == 'root':
        return False
    try:
        with open('/etc/shadow') as f:
            for line in f:
                parts = line.split(':')
                if parts[0] == username:
                    h = parts[1]
                    if not h or h in ('*', '!', '!!'):
                        return False
                    salt = '$' + '$'.join(h.split('$')[1:3])
                    return crypt.crypt(password, salt) == h
        return False
    except:
        return False


# ============ 命令执行 ============
def run_cmd(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout, r.stderr
    except:
        return False, '', ''


# ============ 状态查询 ============
def get_driver_status():
    info = {'installed': False, 'loaded': False, 'dkms_status': '', 'module_info': '', 'interfaces': []}
    ok, out, _ = run_cmd('dkms status 2>/dev/null | grep r8125 || true')
    if out.strip():
        info['installed'] = True; info['dkms_status'] = out.strip()
    ok, out, _ = run_cmd('lsmod | grep r8125 || true')
    if out.strip():
        info['loaded'] = True; info['module_info'] = out.strip()
    ok, out, _ = run_cmd("ip -o link show | grep -v lo | awk '{print $2,$3,$9}' 2>/dev/null || true")
    if out.strip():
        info['interfaces'] = [l.strip() for l in out.strip().split('\n')]
    ok, out, _ = run_cmd("lspci -nn | grep -i '8125\\|RTL8125' 2>/dev/null || true")
    info['pci_device'] = out.strip() or '未检测到RTL8125网卡'
    ok, out, _ = run_cmd("for d in /sys/class/net/*; do n=$(basename $d); [ \"$n\" = lo ]&&continue; dr=$(readlink $d/device/driver 2>/dev/null); [ -n \"$dr\" ]&&echo \"$n: $(basename $dr)\"; done")
    info['driver_map'] = out.strip() or ''
    return info

def check_reboot_required():
    _, o1, _ = run_cmd('lsmod | grep -q r8125 && echo y || echo n')
    _, o2, _ = run_cmd('dkms status 2>/dev/null | grep r8125 | grep -q installed && echo y || echo n')
    _, o3, _ = run_cmd('[ -f /etc/modprobe.d/r8125-blacklist.conf ] && echo y || echo n')
    return {'module_loaded': 'y' in o1, 'dkms_installed': 'y' in o2, 'blacklist_exists': 'y' in o3}


# ============ SSE 流式任务 ============

def sse_gen_install():
    yield 'event: log\ndata: {"type":"step","text":"📥 开始安装 r8125 驱动"}\n\n'
    def step(name, cmd, ok_if_exists=True):
        yield f'event: log\ndata: {json.dumps({"type":"step","text":name})}\n\n'
        ok, out, err = run_cmd(cmd)
        ok = ok or (ok_if_exists and 'already' in out.lower())
        text = (out or err or '')[:300]
        for line in text.split('\n'):
            if line.strip():
                yield f'event: log\ndata: {json.dumps({"type":"output","text":line.strip()})}\n\n'
        status = 'success' if ok else 'error'
        yield f'event: log\ndata: {json.dumps({"type":"result","step":name,"status":status})}\n\n'

    yield from step('安装编译依赖', 'apt-get update -qq 2>/dev/null && apt-get install -y -qq dkms build-essential linux-headers-$(uname -r) git 2>&1')
    yield from step('下载驱动源码', 'cd /tmp && rm -rf realtek-r8125-dkms && git clone --depth 1 https://github.com/awesometic/realtek-r8125-dkms 2>&1 && cd realtek-r8125-dkms && VER=$(grep PACKAGE_VERSION dkms.conf | cut -d= -f2 | tr -d \' \"\') && cp -r /tmp/realtek-r8125-dkms /usr/src/r8125-${VER} && echo "OK $VER"')
    yield from step('添加 DKMS', 'for d in /usr/src/r8125-*; do ver=$(basename $d | sed "s/r8125-//"); dkms add -m r8125 -v $ver 2>&1 || true; done')
    yield from step('编译驱动模块', 'for d in /usr/src/r8125-*; do ver=$(basename $d | sed "s/r8125-//"); dkms build -m r8125 -v $ver 2>&1 || true; done')
    yield from step('安装驱动模块', 'for d in /usr/src/r8125-*; do ver=$(basename $d | sed "s/r8125-//"); dkms install -m r8125 -v $ver 2>&1 || true; done')

    yield 'event: log\ndata: {"type":"step","text":"屏蔽冲突驱动 r8169"}\n\n'
    bl = '/etc/modprobe.d/r8125-blacklist.conf'
    if not os.path.exists(bl):
        try:
            with open(bl, 'w') as f: f.write('# 黑名单 r8169\nblacklist r8169\n')
            os.chmod(bl, 0o644)
        except:
            pass
    yield 'event: log\ndata: {"type":"result","step":"屏蔽冲突驱动 r8169","status":"success"}\n\n'
    step('更新 initramfs', 'update-initramfs -u -k all 2>&1')
    yield 'event: log\ndata: {"type":"step","text":"⚠️ 安装完成！请重启系统以使驱动生效"}\n\n'
    yield 'event: done\ndata: {"success":true}\n\n'


def sse_gen_uninstall():
    yield 'event: log\ndata: {"type":"step","text":"🗑️ 开始卸载 r8125 驱动"}\n\n'
    def step(name, cmd):
        yield f'event: log\ndata: {json.dumps({"type":"step","text":name})}\n\n'
        ok, out, err = run_cmd(cmd)
        text = (out or err or '')[:200]
        for line in text.split('\n'):
            if line.strip():
                yield f'event: log\ndata: {json.dumps({"type":"output","text":line.strip()})}\n\n'
        yield f'event: log\ndata: {json.dumps({"type":"result","step":name,"status":"success"})}\n\n'

    yield from step('卸载当前驱动模块', 'rmmod r8125 2>/dev/null || true')
    yield from step('移除 DKMS 模块', 'for d in /usr/src/r8125-*; do ver=$(basename $d | sed "s/r8125-//"); dkms remove -m r8125 -v $ver --all 2>&1 || true; done')
    yield from step('清理驱动源码', 'rm -rf /usr/src/r8125-*')
    yield from step('移除黑名单配置', 'rm -f /etc/modprobe.d/r8125-blacklist.conf')
    yield from step('更新 initramfs', 'update-initramfs -u -k all 2>&1')
    yield 'event: log\ndata: {"type":"step","text":"✅ 卸载完成"}\n\n'
    yield 'event: done\ndata: {"success":true}\n\n'


def sse_gen_optimize():
    yield 'event: log\ndata: {"type":"step","text":"🚀 开始性能优化"}\n\n'
    ok, out, _ = run_cmd("for d in /sys/class/net/*; do n=$(basename $d); [ \"$n\" = lo ]&&continue; dr=$(readlink $d/device/driver 2>/dev/null); if [ -n \"$dr\" ]&&echo \"$(basename $dr)\"|grep -qi 'r8125\\|r8169'; then echo $n; fi; done")
    ifaces = [i.strip() for i in out.strip().split('\n') if i.strip()]
    for iface in ifaces:
        for label, cmd in [
            ('TCP卸载设置', f'ethtool -K {iface} rx off tx off 2>&1 || true'),
            ('中断合并优化', f'ethtool -C {iface} rx-usecs 4 tx-usecs 4 adaptive-rx on adaptive-tx on 2>&1 || true'),
            ('环形缓冲区设置', f'ethtool -G {iface} rx 4096 tx 4096 2>&1 || true'),
            ('巨型帧 Jumbo Frame（MTU 9000）', f'ip link set {iface} mtu 9000 2>&1 || true'),
            ('节能以太网 EEE 关闭', f'ethtool --set-eee {iface} eee off 2>&1 || true'),
            ('硬件卸载加速', f'ethtool -K {iface} gro on gso on tso on 2>&1 || true'),
        ]:
            yield f'event: log\ndata: {json.dumps({"type":"step","text":f"[{iface}] {label}"})}\n\n'
            ok, o, _ = run_cmd(cmd)
            if o.strip():
                yield f'event: log\ndata: {json.dumps({"type":"output","text":o.strip()})}\n\n'
            yield f'event: log\ndata: {json.dumps({"type":"result","step":f"[{iface}] {label}","status":"success" if ok else "info"})}\n\n'

    run_cmd('modprobe tcp_bbr 2>/dev/null || true')
    for opt in ['net.core.rmem_default=262144', 'net.core.wmem_default=262144',
                'net.core.rmem_max=16777216', 'net.core.wmem_max=16777216',
                'net.core.netdev_max_backlog=5000', 'net.ipv4.tcp_congestion_control=bbr']:
        yield f'event: log\ndata: {json.dumps({"type":"step","text":f"内核参数: {opt}"})}\n\n'
        ok, o, _ = run_cmd(f'sysctl -w {opt} 2>&1 || true')
        if o.strip():
            yield f'event: log\ndata: {json.dumps({"type":"output","text":o.strip()})}\n\n'
        yield f'event: log\ndata: {json.dumps({"type":"result","step":f"内核参数: {opt}","status":"success" if ok else "info"})}\n\n'

    for param in ['rmem', 'wmem']:
        for val in ['4096', '87380', '16777216']:
            ok, _, _ = run_cmd(f'sysctl -w net.ipv4.tcp_{param}={val} 2>&1 || true')
            if ok:
                yield f'event: log\ndata: {json.dumps({"type":"step","text":f"net.ipv4.tcp_{param}={val}"})}\n\n'
                yield f'event: log\ndata: {json.dumps({"type":"result","step":f"net.ipv4.tcp_{param}={val}","status":"success"})}\n\n'

    conf = '''# RTL8125 性能优化 - 由 fn-rtl8125 管理
net.core.rmem_default = 262144
net.core.wmem_default = 262144
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.tcp_congestion_control = bbr
'''
    try:
        with open('/etc/sysctl.d/90-rtl8125-optimize.conf', 'w') as f: f.write(conf)
        yield 'event: log\ndata: {"type":"step","text":"持久化优化配置到 /etc/sysctl.d/90-rtl8125-optimize.conf"}\n\n'
        yield 'event: log\ndata: {"type":"result","step":"持久化配置","status":"success"}\n\n'
    except Exception as e:
        yield f'event: log\ndata: {json.dumps({"type":"output","text":f"持久化写入失败: {e}"})}\n\n'

    yield 'event: log\ndata: {"type":"step","text":"✅ 优化完成！"}\n\n'
    yield 'event: done\ndata: {"success":true}\n\n'


# ============ HTTP 处理器 ============
class Handler(BaseHTTPRequestHandler):

    def _set_headers(self, code=200, ct='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()

    def _send_json(self, data, code=200):
        self._set_headers(code)
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _get_token(self):
        for c in self.headers.get('Cookie', '').split(';'):
            c = c.strip()
            if c.startswith('session='):
                return c[8:]
        return None

    def _check_auth(self):
        t = self._get_token()
        return bool(t and verify_session(t))

    def _get_post_data(self):
        try:
            cl = int(self.headers.get('Content-Length', 0))
            if cl:
                return json.loads(self.rfile.read(cl).decode())
        except:
            pass
        return {}

    def _serve_static(self, rel):
        www = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'www'))
        if not rel or '..' in rel or rel.startswith('/'):
            self._set_headers(403, 'text/plain'); self.wfile.write(b'Forbidden'); return
        fp = os.path.join(www, rel)
        if not os.path.realpath(fp).startswith(www) or not os.path.isfile(fp):
            self._set_headers(404, 'text/plain'); self.wfile.write(b'Not Found'); return
        mm = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'application/javascript; charset=utf-8'}
        try:
            with open(fp, 'rb') as f:
                self._set_headers(200, mm.get(os.path.splitext(fp)[1].lower(), 'application/octet-stream'))
                self.wfile.write(f.read())
        except:
            self._set_headers(500, 'text/plain'); self.wfile.write(b'Error')

    def _sse_response(self, gen):
        """发送 SSE 响应（在独立线程运行，不阻塞服务器）"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        for chunk in gen:
            try:
                self.wfile.write(chunk.encode())
                self.wfile.flush()
            except:
                break

    def do_GET(self):
        path = urlparse(self.path).path.rstrip('/') or '/'

        if path == '/api/status':
            if not self._check_auth(): self._send_json({'error':'unauthorized'},401); return
            self._send_json({'status':get_driver_status(),'reboot':check_reboot_required(),'port':PORT})

        elif path == '/api/check_auth':
            self._send_json({'authenticated':self._check_auth()})

        elif path == '/api/whoami':
            if not self._check_auth(): self._send_json({'error':'unauthorized'},401); return
            u = session_user(self._get_token())
            self._send_json({'user':u or 'unknown'})

        # SSE 流（GET 方式，按 spec EventSource 只能用 GET）
        elif path == '/api/install':
            if not self._check_auth(): self._send_json({'error':'unauthorized'},401); return
            threading.Thread(target=self._sse_response, args=(sse_gen_install(),), daemon=True).start()

        elif path == '/api/uninstall':
            if not self._check_auth(): self._send_json({'error':'unauthorized'},401); return
            threading.Thread(target=self._sse_response, args=(sse_gen_uninstall(),), daemon=True).start()

        elif path == '/api/optimize':
            if not self._check_auth(): self._send_json({'error':'unauthorized'},401); return
            threading.Thread(target=self._sse_response, args=(sse_gen_optimize(),), daemon=True).start()

        else:
            rel = urlparse(self.path).path.lstrip('/') or 'login.html'
            self._serve_static(rel)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/') or '/'
        data = self._get_post_data()

        if path == '/api/login':
            username = data.get('username','').strip()
            password = data.get('password','')
            if verify_shadow_login(username, password):
                token = create_session(username)
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Set-Cookie',f'session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400')
                self.end_headers()
                self.wfile.write(json.dumps({'success':True}).encode())
            else:
                self._send_json({'success':False,'error':'账号或密码错误'})
            return

        # 退出登录
        if path == '/api/logout':
            token = self._get_token()
            if token:
                delete_session(token)
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('Set-Cookie','session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax')
            self.end_headers()
            self.wfile.write(json.dumps({'success':True}).encode())
            return

        if not self._check_auth():
            self._send_json({'error':'unauthorized'},401)
            return

        if path == '/api/reboot':
            self._send_json({'success':True,'message':'系统将在3秒后重启'})
            threading.Thread(target=lambda:(time.sleep(3), run_cmd('reboot')), daemon=True).start()
        else:
            self._send_json({'error':'not found'},404)

    def log_message(self, *a):
        if '/api/' in str(a[0]):
            try:
                with open(os.path.join(LOG_DIR,'access.log'),'a') as f:
                    f.write(f'{self.client_address[0]} - {a}\n')
            except:
                pass


def run_server():
    ipv6 = True
    # 手动创建 IPv6 双栈 socket（绕过 HTTPServer 的地址解析问题）
    ipv6_ok = hasattr(socket, 'AF_INET6')
    sock = None
    if ipv6_ok:
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            # 允许 IPv4 通过 IPv6 socket 接入
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('::', PORT))
            af = 'IPv4 + IPv6'
        except Exception as e:
            print(f'[fn-rtl8125] IPv6 socket bind failed: {e}')
            if sock:
                try: sock.close()
                except: pass
            sock = None

    if not sock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', PORT))
        af = 'IPv4'

    server = ThreadedHTTPServer(('0.0.0.0', PORT), Handler, bind_and_activate=False)
    server.socket = sock
    server.server_address = sock.getsockname()
    server.server_bind = lambda: None
    server.server_activate()

    print(f"[fn-rtl8125] 多线程服务器启动，监听 {af} :{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == '__main__':
    run_server()
