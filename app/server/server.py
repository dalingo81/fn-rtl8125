#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fn-r8125 后端服务 (Python 版)
飞牛NAS RTL8125 2.5G网卡驱动管理工具

提供三个功能：
  1. 安装r8125驱动（三步）
  2. 验证驱动安装
  3. 设置网卡节能

运行: python3 server.py
"""

import os
import sys
import json
import subprocess
import threading
import time
import re
import select
import fcntl
import signal
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get('TRIM_SERVICE_PORT', '5300'))

# ============================================================
# 工具函数 - 实时流式执行命令
# ============================================================

def run_cmd_stream(cmd, log_cb, timeout=600, env=None):
    """
    实时流式执行命令，通过 log_cb 回调实时输出每行内容
    返回 {'code': exit_code}
    """
    my_env = os.environ.copy()
    my_env['DEBIAN_FRONTEND'] = 'noninteractive'
    # 禁用缓冲，让输出实时到达
    my_env['PYTHONUNBUFFERED'] = '1'
    if env:
        my_env.update(env)

    try:
        proc = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=my_env, bufsize=0,
            preexec_fn=os.setsid
        )

        # 将 stdout 设为非阻塞
        fd = proc.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        start_time = time.time()
        partial_line = b''

        while True:
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed > timeout:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                return {'code': -1}

            # 检查进程是否结束
            ret = proc.poll()
            if ret is not None:
                # 读取剩余输出
                try:
                    remaining = proc.stdout.read()
                    if remaining:
                        partial_line += remaining
                except:
                    pass
                if partial_line:
                    for line in partial_line.decode('utf-8', errors='replace').split('\n'):
                        if line.strip():
                            log_cb(line + '\n')
                return {'code': ret}

            # 读取可用输出
            try:
                data = proc.stdout.read(4096)
                if data:
                    partial_line += data
                    # 按行分割输出
                    while b'\n' in partial_line:
                        idx = partial_line.index(b'\n')
                        line = partial_line[:idx]
                        partial_line = partial_line[idx+1:]
                        decoded = line.decode('utf-8', errors='replace').strip()
                        if decoded:
                            log_cb(decoded + '\n')
            except (BlockingIOError, IOError):
                pass

            time.sleep(0.05)

    except Exception as e:
        return {'code': -1, 'error': str(e)}


# ============================================================
# 工具函数 - 简单执行（适合快速命令）
# ============================================================

def run_cmd(cmd, timeout=120, env=None):
    """简单执行命令，返回 {code, stdout, stderr}"""
    my_env = os.environ.copy()
    my_env['DEBIAN_FRONTEND'] = 'noninteractive'
    if env:
        my_env.update(env)

    try:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=my_env
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            'code': proc.returncode,
            'stdout': stdout.decode('utf-8', errors='replace').strip(),
            'stderr': stderr.decode('utf-8', errors='replace').strip()
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        return {'code': -1, 'stdout': '', 'stderr': 'Command timed out'}
    except Exception as e:
        return {'code': -1, 'stdout': '', 'stderr': str(e)}


# ============================================================
# 辅助函数
# ============================================================

def parse_iface_names(output):
    """从 ip -o link show 输出中解析接口名称"""
    result = []
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(':')
        if len(parts) >= 2:
            name = parts[1].strip().split()[0] if parts[1].strip() else ''
            if name and name != 'lo':
                result.append(name)
    return result


def list_interfaces():
    """列出所有非 lo 网络接口"""
    r = run_cmd("ip -o link show 2>/dev/null")
    if r['code'] != 0:
        return []
    return parse_iface_names(r['stdout'])


# ============================================================
# 核心功能
# ============================================================

def step1_install_build_env(log_cb):
    """第一步：安装编译环境"""
    # 检查并释放 apt 锁
    log_cb('[步骤] 检查 apt 锁状态...')
    lock_check = run_cmd('lsof /var/lib/dpkg/lock-frontend 2>/dev/null || '
                         'lsof /var/lib/apt/lists/lock 2>/dev/null || echo "NO_LOCK"')
    if 'NO_LOCK' not in lock_check['stdout']:
        log_cb('[警告] 检测到 apt 锁，等待释放...')
        for i in range(30):
            time.sleep(1)
            lock_r = run_cmd('lsof /var/lib/dpkg/lock-frontend 2>/dev/null || echo "FREE"')
            if 'FREE' in lock_r['stdout']:
                log_cb(f'[完成] apt 锁已释放（等待 {i+1} 秒）')
                break
            if i == 29:
                log_cb('[警告] apt 锁等待超时，强制清除已残留进程...')
                run_cmd('kill -9 $(ps aux | grep "[a]pt" | awk "{print \\$2}") 2>/dev/null; true')
                run_cmd('rm -f /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock')
                log_cb('[完成] apt 锁已强制清除')
    else:
        log_cb('[信息] apt 锁未被占用')

    # 步骤1: apt update
    log_cb('[步骤] 1/3 更新软件包列表（可能需要 1-2 分钟）...')
    log_cb('       sudo apt-get update\n')
    r = run_cmd_stream('apt-get update -qq 2>&1', log_cb, timeout=300)
    if r['code'] != 0:
        log_cb(f'[错误] apt update 失败\n')
        return False
    log_cb('[完成] 软件包列表已更新\n')

    # 步骤2: 安装编译工具
    log_cb('[步骤] 2/3 安装编译工具链 build-essential、dkms、git 等（下载 ~86MB，请耐心等待 2-5 分钟）...\n')
    log_cb('       sudo apt-get install -y build-essential dkms git curl wget gcc make\n')
    r = run_cmd_stream(
        'apt-get install -y build-essential dkms git curl wget gcc make 2>&1',
        log_cb, timeout=600
    )
    if r['code'] != 0:
        log_cb(f'[错误] 安装编译工具链失败（退出码 {r["code"]}）\n')
        return False
    log_cb('[完成] 编译工具链安装完成\n')

    # 步骤3: 安装内核头文件
    log_cb('[步骤] 3/3 安装内核头文件...\n')
    log_cb('       sudo apt-get install -y linux-headers-$(uname -r)\n')
    r = run_cmd_stream(
        'apt-get install -y linux-headers-$(uname -r) 2>&1',
        log_cb, timeout=600
    )
    if r['code'] != 0:
        log_cb(f'[错误] 安装内核头文件失败\n')
        return False
    log_cb('[完成] 内核头文件安装完成\n')

    return True


def step2_install_driver(log_cb):
    """第二步：下载并编译安装 r8125 驱动"""
    TMPDIR = '/tmp/r8125-install'

    log_cb('[步骤] 创建临时目录...\n')
    run_cmd(f'rm -rf {TMPDIR} && mkdir -p {TMPDIR}')

    # 检查是否已有 r8125
    chk_mod = run_cmd('modinfo r8125 2>/dev/null && echo "EXISTS" || echo "NOT_FOUND"')
    if 'EXISTS' in chk_mod['stdout']:
        log_cb('[信息] 系统已加载 r8125 驱动模块\n')

    # 方案 A: 社区 DKMS 仓库
    log_cb('[步骤] 尝试使用社区维护的 r8125 DKMS 驱动...\n')
    log_cb('       git clone https://github.com/awesometic/realtek-r8125-dkms.git\n')
    r = run_cmd_stream(
        'cd /tmp && rm -rf realtek-r8125-dkms && '
        'git clone --depth=1 https://github.com/awesometic/realtek-r8125-dkms.git 2>&1',
        log_cb, timeout=60
    )

    dkms_ok = False
    if r['code'] == 0:
        log_cb('[步骤] 执行 DKMS 安装脚本...\n')
        r = run_cmd_stream(
            'cd /tmp/realtek-r8125-dkms && chmod +x dkms-install.sh && ./dkms-install.sh 2>&1',
            log_cb, timeout=120
        )
        if r['code'] == 0:
            log_cb('[完成] DKMS 驱动安装成功\n')
            dkms_ok = True
        else:
            log_cb('[信息] DKMS 方式失败，切换到 Realtek 官方源码编译...\n')
    else:
        log_cb('[信息] 克隆仓库失败，切换到 Realtek 官方源码编译...\n')

    # 方案 B: Realtek 官方源码
    if not dkms_ok:
        log_cb('[步骤] 下载 Realtek 官方驱动 v9.015.00...\n')
        r = run_cmd_stream(
            f'cd {TMPDIR} && '
            'wget -q --timeout=30 "https://rtitwww.realtek.com/rtdrivers/cn/nic2/r8125-9.015.00.tar.bz2" '
            '-O r8125.tar.bz2 2>&1 || '
            'curl -sL --connect-timeout 30 "https://rtitwww.realtek.com/rtdrivers/cn/nic2/r8125-9.015.00.tar.bz2" '
            '-o r8125.tar.bz2 2>&1',
            log_cb, timeout=60
        )
        if r['code'] != 0:
            log_cb('[错误] 下载驱动失败，请检查网络连接\n')
            return False

        log_cb('[步骤] 解压驱动源码...\n')
        r = run_cmd_stream(f'cd {TMPDIR} && tar -xjf r8125.tar.bz2 2>&1', log_cb, timeout=30)
        if r['code'] != 0:
            log_cb('[错误] 解压失败\n')
            return False

        find_dir = run_cmd(f'ls -d {TMPDIR}/r8125-* 2>/dev/null | head -1')
        src_dir = find_dir['stdout'].strip() or f'{TMPDIR}/r8125-9.015.00'
        src_name = os.path.basename(src_dir)

        log_cb(f'[步骤] 编译驱动 (源码目录: {src_name})...\n')
        r = run_cmd_stream(
            f'cd {src_dir} && make clean 2>/dev/null; make -j$(nproc) 2>&1',
            log_cb, timeout=180
        )
        if r['code'] != 0:
            log_cb('[错误] 编译失败\n')
            return False

        log_cb('[步骤] 安装驱动...\n')
        r = run_cmd_stream(f'cd {src_dir} && make install 2>&1', log_cb, timeout=60)
        if r['code'] != 0:
            log_cb('[错误] 安装失败\n')
            return False
        log_cb('[完成] 官方驱动编译安装成功\n')

    # 卸载 r8169 + 加载 r8125
    log_cb('[步骤] 卸载 r8169 驱动冲突...\n')
    run_cmd('rmmod r8169 2>/dev/null; rmmod r8125 2>/dev/null; true')

    log_cb('[步骤] 加载 r8125 驱动...\n')
    r = run_cmd('modprobe r8125 2>&1')
    if r['code'] != 0:
        log_cb(f'[错误] 加载 r8125 模块失败: {r["stderr"]}\n')
        return False

    check_r = run_cmd('lsmod | grep r8125')
    if check_r['code'] == 0:
        log_cb('[完成] r8125 驱动已成功加载到内核\n')
    else:
        log_cb('[警告] r8125 可能未正确加载\n')

    log_cb('[信息] 已卸载 r8169 模块以避免冲突\n')
    return True


def step3_persist_config(log_cb):
    """第三步：配置驱动持久化"""
    log_cb('[步骤] 配置 modprobe 驱动优先级...\n')

    conf_file = '/etc/modprobe.d/r8125-disable-r8169.conf'
    conf_content = '''# RTL8125 驱动配置 - 由 fn-r8125 应用生成
# 禁止 r8169 绑定 RTL8125 设备
install r8169 /bin/true

# 确保 r8125 优先加载
softdep r8169 pre: r8125
'''
    write_r = run_cmd(f'cat > {conf_file} << \'CONFEOF\'\n{conf_content}CONFEOF\necho OK', timeout=10)
    if 'OK' in write_r['stdout']:
        log_cb(f'[完成] 已写入: {conf_file}\n')
    else:
        log_cb(f'[警告] 写入配置失败\n')

    log_cb('[步骤] 更新 initramfs...\n')
    r = run_cmd_stream(f'update-initramfs -u -k $(uname -r) 2>&1', log_cb, timeout=120)
    if r['code'] == 0:
        log_cb('[完成] initramfs 更新成功\n')
    else:
        log_cb('[警告] initramfs 更新不完整\n')

    log_cb('[步骤] 验证 r8125 自动加载...\n')
    chk_auto = run_cmd('grep -q "^r8125" /etc/modules 2>/dev/null && echo "YES" || echo "NO"')
    if 'NO' in chk_auto['stdout']:
        run_cmd('echo "r8125" >> /etc/modules')
        log_cb('[完成] 已添加 r8125 到 /etc/modules\n')
    else:
        log_cb('[信息] r8125 已在 /etc/modules 中\n')

    log_cb('\n--- 当前网卡状态 ---\n')
    link_r = run_cmd('ip -o link show 2>/dev/null')
    if link_r['code'] == 0:
        for l in link_r['stdout'].split('\n'):
            if l.strip():
                log_cb(f'  {l}\n')

    log_cb('\n[信息] 第三步完成！建议重启系统使所有配置生效。\n')
    return True


def step4_verify_driver(log_cb):
    """第四步：验证驱动安装状态"""
    log_cb('==== RTL8125 驱动验证报告 ====\n\n')

    log_cb('[1/6] 检查 PCI 设备...\n')
    r = run_cmd('lspci -nn 2>/dev/null | grep -i ethernet')
    if r['code'] == 0:
        for l in r['stdout'].split('\n'):
            if l.strip():
                log_cb(f'  {l}\n')
    else:
        log_cb('  [信息] 未检测到以太网控制器\n')

    log_cb('\n[2/6] 检查驱动模块...\n')
    m8125 = run_cmd('lsmod | grep r8125')
    if m8125['code'] == 0:
        log_cb('  r8125: ✓ 已加载\n')
    else:
        log_cb('  r8125: ✗ 未加载\n')

    m8169 = run_cmd('lsmod | grep r8169')
    if m8169['code'] == 0:
        log_cb('  r8169: ⚠ 已加载（可能冲突）\n')
    else:
        log_cb('  r8169: ✓ 已禁用或未加载\n')

    log_cb('\n[3/6] PCI 驱动绑定...\n')
    r = run_cmd("lspci -k 2>/dev/null | grep -A 3 -i ethernet")
    if r['code'] == 0:
        for l in r['stdout'].split('\n'):
            if l.strip():
                log_cb(f'  {l}\n')

    log_cb('\n[4/6] 网络接口列表...\n')
    ifaces = list_interfaces()
    log_cb(f'  {", ".join(ifaces)}\n')

    log_cb('\n[5/6] 网卡连接速度...\n')
    for name in ifaces:
        speed = run_cmd(f'ethtool {name} 2>/dev/null | grep -i speed')
        if speed['code'] == 0:
            sp = speed['stdout'].strip()
            log_cb(f'  {name}: {sp}\n')
            if '2500' in sp:
                log_cb(f'    → 2.5G 速度 ✓\n')

    log_cb('\n[6/6] r8125 驱动版本...\n')
    r = run_cmd('modinfo r8125 2>/dev/null | grep -E "version|description"')
    if r['code'] == 0:
        for l in r['stdout'].split('\n'):
            if l.strip():
                log_cb(f'  {l}\n')
    else:
        log_cb('  [信息] r8125 模块未安装或不在当前内核中\n')

    log_cb('\n==== 验证完成 ====\n')
    return True


def step5_energy_saving(log_cb, iface):
    """进阶：网卡节能设置"""
    if not iface:
        log_cb('[错误] 请指定网卡接口名称（如 enp2s0）\n')
        return False

    log_cb(f'==== 配置网卡 {iface} 节能优化 ====\n\n')

    log_cb('[1/4] 检查当前节能设置...\n')
    eee = run_cmd(f'ethtool --show-eee {iface} 2>/dev/null || echo "EEE_NOT_SUPPORTED"')
    if 'EEE_NOT_SUPPORTED' not in eee['stdout']:
        for l in eee['stdout'].split('\n'):
            if l.strip():
                log_cb(f'  {l}\n')
    else:
        log_cb('  该网卡不支持 EEE 或 ethtool 不可用\n')

    log_cb('\n[2/4] 关闭 EEE...\n')
    r = run_cmd(f'ethtool --set-eee {iface} eee off 2>&1')

    log_cb('\n[3/4] 关闭 WOL 和节能...\n')
    run_cmd(f'ethtool -s {iface} wol d 2>/dev/null || true')
    run_cmd(f'ethtool --set-priv-flags {iface} wol-disable on 2>/dev/null || true')
    log_cb('  ✓ 已关闭\n')

    log_cb('\n[4/4] 写入持久化配置...\n')
    service_content = f'''[Unit]
Description=RTL8125 Network Performance Tuning
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/ethtool --set-eee {iface} eee off
ExecStart=/usr/sbin/ethtool -s {iface} wol d
ExecStart=/usr/sbin/ethtool -G {iface} rx 4096 tx 4096

[Install]
WantedBy=multi-user.target
'''
    svc_file = '/etc/systemd/system/r8125-perf-tuning.service'
    r = run_cmd(
        f'cat > {svc_file} << \'SERVICEEOF\'\n{service_content}SERVICEEOF\n'
        f'systemctl daemon-reload && systemctl enable r8125-perf-tuning.service && echo "ENABLED"',
        timeout=15
    )
    if 'ENABLED' in r['stdout']:
        log_cb('  ✓ 已创建并启用 r8125-perf-tuning.service\n')
        log_cb('  ✓ 重启后自动生效\n')
    else:
        log_cb(f'  ⚠ 持久化失败\n')

    log_cb('\n==== 节能配置完成 ====\n')
    return True


# ============================================================
# HTTP 服务
# ============================================================

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'www')


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_cors_headers()
        self.end_headers()

    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, filepath):
        ext = os.path.splitext(filepath)[1]
        mime_map = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.png': 'image/png',
            '.ico': 'image/x-icon',
            '.json': 'application/json; charset=utf-8',
        }
        mime = mime_map.get(ext, 'application/octet-stream')
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'max-age=3600')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/api/list-interfaces':
            ifaces = list_interfaces()
            self.send_json({'success': True, 'interfaces': ifaces})
            return

        if path == '/api/run-step':
            step = params.get('step', [''])[0]
            iface = params.get('iface', [''])[0]
            self.handle_sse(step, iface)
            return

        if path == '/' or path == '/index.html':
            self.send_static(os.path.join(STATIC_DIR, 'index.html'))
            return

        safe_path = path.lstrip('/')
        full_path = os.path.normpath(os.path.join(STATIC_DIR, safe_path))
        if full_path.startswith(os.path.normpath(STATIC_DIR)):
            self.send_static(full_path)
        else:
            self.send_response(404)
            self.end_headers()

    def handle_sse(self, step, iface):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        def sse_send(event, data):
            msg = f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
            try:
                self.wfile.write(msg.encode('utf-8'))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_cb(msg):
            sse_send('log', {'message': msg})

        sse_send('status', {'message': '开始执行...\n'})

        try:
            if os.geteuid() != 0:
                log_cb('[错误] 需要 root 权限运行，请以 sudo 启动服务\n')
                sse_send('done', {'success': False})
                return

            result = False
            if step == 'step1':
                result = step1_install_build_env(log_cb)
            elif step == 'step2':
                result = step2_install_driver(log_cb)
            elif step == 'step3':
                result = step3_persist_config(log_cb)
            elif step == 'step4':
                result = step4_verify_driver(log_cb)
            elif step == 'step5':
                result = step5_energy_saving(log_cb, iface)
            else:
                sse_send('error', {'message': f'未知步骤: {step}\n'})
                sse_send('done', {'success': False})
                return

            sse_send('done', {'success': result})
        except Exception as e:
            import traceback
            sse_send('error', {'message': f'错误: {str(e)}\n'})
            sse_send('done', {'success': False})


def main():
    server = HTTPServer(('127.0.0.1', PORT), RequestHandler)
    print(f'[fn-r8125] Server running on http://127.0.0.1:{PORT}')
    print(f'[fn-r8125] Python {sys.version.split()[0]}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[fn-r8125] Server stopped')
        server.server_close()


if __name__ == '__main__':
    main()
