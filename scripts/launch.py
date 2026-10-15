"""PF 環境內的啟動流程；安裝依賴前只使用標準函式庫。"""

from contextlib import closing
import hashlib
from http.client import HTTPException, HTTPSConnection
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
import webbrowser


ROOT = Path(__file__).resolve().parent.parent
DJANGO_DIR = ROOT / 'Django_Server'
PORT = 8000
DASHBOARD_URL = 'https://127.0.0.1:8000/dashboard/'
MODEL_FILES = ('ROI.pt', 'Color.pt', 'Wrinkle.pt', 'Defect.pt',
               'GradeStage1.json', 'GradeStage2.json')
LINE_CREDENTIALS = DJANGO_DIR / 'runtime_config' / 'notification_credentials.json'
FIREWALL_RULES = ('PassionFruit-Django-8000', 'PassionFruit-mDNS-5353')
STAMP_FILE = Path(sys.prefix) / 'pf_launcher_stamp.json'
LOG_FILE = ROOT / 'logs' / 'launcher.log'
TORCH_INDEXES = {
    'cu128': 'https://download.pytorch.org/whl/cu128',
    'cpu': 'https://download.pytorch.org/whl/cpu',
}
REASON_MESSAGES = {
    'python_version_mismatch': '需要 Python 3.14，請使用 PF 環境的 Python 3.14.4。',
    'torch_install_failed': 'PyTorch 安裝失敗，請確認網路連線後重新啟動。',
    'requirements_install_failed': '專案套件安裝失敗，請確認網路連線並查看紀錄後重試。',
    'model_missing': '缺少模型，請從 GitHub Release／含真實模型的 Download ZIP 重新下載並解壓縮專案。',
    'model_is_lfs_pointer': '模型只有 Git LFS 指標，請從 GitHub Release／含真實模型的 Download ZIP 重新下載並解壓縮專案。',
    'port_in_use': '連接埠 8000 已被其他程式占用，請關閉占用程式後重新啟動。',
    'django_check_failed': 'Django 啟動前檢查失敗，請將紀錄交給團隊確認設定。',
    'server_exited': '伺服器在就緒前結束，請查看紀錄及視窗中的錯誤後重試。',
    'server_timeout': '等待伺服器就緒超過 90 秒，請查看紀錄及視窗中的錯誤後重試。',
    'interrupted': '啟動已中止；需要使用時請重新雙擊啟動系統。',
    'unexpected_error': '發生非預期錯誤，請將紀錄交給團隊確認後重試。',
}


def report(message):
    print(message, flush=True)
    logging.info(message)


def reason_message(reason):
    return REASON_MESSAGES[reason]


def check_python(version=None):
    version = sys.version_info if version is None else version
    if tuple(version[:2]) != (3, 14):
        return {'ok': False, 'reason': 'python_version_mismatch'}
    return {'ok': True}


def detect_torch_variant(runner=subprocess.run, which=shutil.which):
    executable = which('nvidia-smi')
    if not executable:
        return 'cpu'
    try:
        result = runner([executable], timeout=10, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        return 'cpu'
    return 'cu128' if result.returncode == 0 else 'cpu'


def parse_torch_requirements(text):
    requirements = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*==\d+\.\d+\.\d+', line):
            raise ValueError(f'PyTorch 需求第 {number} 行格式錯誤，必須使用 name==版本：{line}')
        requirements.append(tuple(line.split('==')))
    if not requirements:
        raise ValueError('PyTorch 需求檔不可為空。')
    return requirements


def build_torch_specs(requirements, variant):
    if variant not in TORCH_INDEXES:
        raise ValueError(f'不支援的 PyTorch 版本類型：{variant}')
    return [f'{name}=={version}+{variant}' for name, version in requirements]


def compute_digest(python_version, variant, torch_bytes, requirements_bytes):
    payload = [python_version, variant, torch_bytes.hex(), requirements_bytes.hex()]
    return hashlib.sha256(json.dumps(payload).encode('utf-8')).hexdigest()


def read_stamp(path):
    try:
        stamp = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return stamp if isinstance(stamp, dict) else None


def stamp_matches(path, digest):
    stamp = read_stamp(path)
    return stamp is not None and stamp.get('digest') == digest


def write_stamp(path, digest, variant, python_version):
    stamp = {'digest': digest, 'variant': variant, 'python': python_version,
             'written_at': datetime.now(timezone.utc).isoformat()}
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(stamp, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_dependencies(variant, django_dir=DJANGO_DIR, stamp_path=STAMP_FILE,
                        runner=subprocess.run, python_version=None):
    python_version = python_version or '.'.join(map(str, sys.version_info[:3]))
    torch_bytes = (django_dir / 'requirements-torch.txt').read_bytes()
    requirements_bytes = (django_dir / 'requirements.txt').read_bytes()
    specs = build_torch_specs(parse_torch_requirements(torch_bytes.decode('utf-8')), variant)
    digest = compute_digest(python_version, variant, torch_bytes, requirements_bytes)
    if stamp_matches(stamp_path, digest):
        report('依賴已是最新。')
        return {'ok': True}

    # 更新失敗時不可留下舊 stamp，因為套件可能已被部分替換。
    stamp_path.unlink(missing_ok=True)
    commands = (
        ('torch_install_failed', '安裝 PyTorch',
         ['install', *specs, '--index-url', TORCH_INDEXES[variant]]),
        ('requirements_install_failed', '安裝專案套件',
         ['install', '-r', str(django_dir / 'requirements.txt')]),
    )
    for reason, message, arguments in commands:
        report(f'{message}……')
        try:
            result = runner([sys.executable, '-s', '-m', 'pip', *arguments], cwd=django_dir)
        except OSError as error:
            return {'ok': False, 'reason': reason, 'detail': str(error)}
        if result.returncode != 0:
            return {'ok': False, 'reason': reason, 'exit_code': result.returncode}
    check_pip(django_dir, runner)
    write_stamp(stamp_path, digest, variant, python_version)
    return {'ok': True}


def check_pip(django_dir, runner):
    # 共用的開發用 PF 可能有與本專案無關的衝突套件，不應阻擋啟動。
    report('檢查套件相依性……')
    try:
        result = runner([sys.executable, '-s', '-m', 'pip', 'check'], cwd=django_dir)
    except OSError as error:
        report(f'警告：無法執行套件相依性檢查，繼續啟動。{error}')
        return
    if result.returncode != 0:
        report('警告：pip check 回報相依性問題（見上方輸出）；若與本專案套件無關可忽略，繼續啟動。')


def check_model_file(path):
    if not path.is_file():
        return {'ok': False, 'reason': 'model_missing', 'file': str(path)}
    with path.open('rb') as model:
        header = model.read(200)
    if header.startswith(b'version https://git-lfs.github.com/spec/v1'):
        return {'ok': False, 'reason': 'model_is_lfs_pointer', 'file': str(path)}
    return {'ok': True}


def check_models(model_dir=DJANGO_DIR / 'models'):
    report('檢查部署模型……')
    for name in MODEL_FILES:
        result = check_model_file(model_dir / name)
        if not result['ok']:
            return result
    return {'ok': True}


def check_line_credentials(path=LINE_CREDENTIALS):
    report('檢查 LINE 通知憑證……')
    if not path.is_file():
        report('\n' + '=' * 60 + '\n警告：尚未設定 LINE 通知憑證！\n'
               '請從團隊 Google Drive 下載 notification_credentials.json，\n'
               f'放到以下完整路徑後重新啟動：\n{path}\n' + '=' * 60)
    return {'ok': True}


def missing_firewall_rules(output):
    present = {line.strip() for line in output.splitlines()}
    return [name for name in FIREWALL_RULES if name not in present]


def check_firewall(runner=subprocess.run, platform=os.name):
    if platform != 'nt':
        return {'ok': True}
    report('檢查 Windows 防火牆規則……')
    command = ("Get-NetFirewallRule -Name 'PassionFruit-Django-8000',"
               "'PassionFruit-mDNS-5353' -ErrorAction SilentlyContinue | "
               # 規則不存在時 Get-NetFirewallRule 仍會讓 PowerShell 結束碼為 1。
               'Select-Object -ExpandProperty Name; exit 0')
    try:
        result = runner(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                        capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            report(f'警告：無法確認防火牆規則，請雙擊「首次設定防火牆.bat」。{result.stderr or ""}')
            return {'ok': True}
        missing = missing_firewall_rules(result.stdout)
        if missing:
            report(f'警告：缺少防火牆規則 {"、".join(missing)}；請雙擊「首次設定防火牆.bat」設定一次。')
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        report(f'警告：無法確認防火牆規則，請雙擊「首次設定防火牆.bat」。{error}')
    return {'ok': True}


def port_is_open(port=PORT):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def probe_dashboard(url=DASHBOARD_URL, timeout=3):
    try:
        target = urlsplit(url)
        with closing(HTTPSConnection(target.hostname, target.port, timeout=timeout,
                                     context=ssl._create_unverified_context())) as connection:
            connection.request('GET', target.path + ('?' + target.query if target.query else ''))
            with connection.getresponse():
                return True
    except (OSError, HTTPException, ValueError):
        return False


def check_port(socket_check=port_is_open, probe=probe_dashboard, opener=webbrowser.open):
    report('檢查連接埠 8000……')
    if not socket_check():
        return {'ok': True, 'already_running': False}
    if not probe():
        return {'ok': False, 'reason': 'port_in_use'}
    report(f'伺服器已在執行，開啟 Dashboard：{DASHBOARD_URL}')
    opener(DASHBOARD_URL)
    return {'ok': True, 'already_running': True}


def check_inference_device(variant, runner=subprocess.run):
    report('檢查推論裝置……')
    try:
        result = runner([sys.executable, '-s', '-c',
                         "import torch; print('cuda' if torch.cuda.is_available() else 'cpu')"],
                        capture_output=True, text=True, timeout=120)
        device = result.stdout.strip()
        if result.returncode != 0 or device not in ('cuda', 'cpu'):
            report(f'警告：無法確認推論裝置，繼續啟動。{result.stderr or device}')
            return {'ok': True}
        report(f'推論裝置：{device}')
        if variant == 'cu128' and device == 'cpu':
            report('提示：NVIDIA 驅動程式可能需要更新；CUDA 12.8 需要 570 以上的驅動程式。')
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        report(f'警告：無法確認推論裝置，繼續啟動。{error}')
    return {'ok': True}


def check_django(django_dir=DJANGO_DIR, runner=subprocess.run):
    report('執行 Django 啟動前檢查……')
    result = runner([sys.executable, '-s', 'manage.py', 'check'], cwd=django_dir)
    if result.returncode != 0:
        return {'ok': False, 'reason': 'django_check_failed', 'exit_code': result.returncode}
    return {'ok': True}


def start_server(django_dir=DJANGO_DIR, popen=subprocess.Popen, probe=probe_dashboard,
                 opener=webbrowser.open, monotonic=time.monotonic, sleep=time.sleep):
    report('啟動 Django HTTPS 伺服器……')
    process = popen([sys.executable, '-s', 'manage.py', 'runsslserver',
                     '0.0.0.0:8000', '--noreload'], cwd=django_dir)
    try:
        deadline = monotonic() + 90
        while monotonic() < deadline:
            code = process.poll()
            if code is not None:
                return {'ok': False, 'reason': 'server_exited', 'exit_code': code}
            if probe(timeout=min(3, max(0.01, deadline - monotonic()))):
                report(f'Dashboard：{DASHBOARD_URL}\n若看到自簽憑證警告，請選「進階」→「繼續前往」。\n'
                       '關閉此視窗會停止伺服器；也可以按 Ctrl+C 停止。')
                opener(DASHBOARD_URL)
                return {'ok': True, 'exit_code': process.wait()}
            sleep(0.5)
        return {'ok': False, 'reason': 'server_timeout'}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def fail(result):
    logging.error('啟動失敗：%s', result)
    detail = result.get('file') or result.get('detail')
    message = reason_message(result['reason'])
    report(f'啟動失敗：{message}' + (f'\n{detail}' if detail else '') + f'\n紀錄檔：{LOG_FILE}')
    return 1


def main():
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=LOG_FILE, encoding='utf-8', level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
        report('開始啟動百香果辨識系統。')
        report('檢查 Python 版本……')
        result = check_python()
        if not result['ok']:
            return fail(result)
        report('偵測 PyTorch 安裝類型……')
        variant = detect_torch_variant()
        report(f'PyTorch 安裝類型：{variant}')
        result = ensure_dependencies(variant)
        if not result['ok']:
            return fail(result)
        result = check_models()
        if not result['ok']:
            return fail(result)
        check_line_credentials()
        check_firewall()
        result = check_port()
        if not result['ok']:
            return fail(result)
        if result['already_running']:
            return 0
        check_inference_device(variant)
        result = check_django()
        if not result['ok']:
            return fail(result)
        result = start_server()
        if not result['ok']:
            return fail(result)
        report(f'伺服器已結束，結束碼：{result["exit_code"]}')
        return result['exit_code']
    except KeyboardInterrupt:
        return fail({'ok': False, 'reason': 'interrupted'})
    except Exception as error:
        logging.exception('啟動程序例外')
        return fail({'ok': False, 'reason': 'unexpected_error', 'detail': str(error)})


if __name__ == '__main__':
    sys.exit(main())
