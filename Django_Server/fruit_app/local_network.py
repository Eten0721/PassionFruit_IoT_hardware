"""Process-local network selection; importing this module opens no resources."""
import ipaddress
import json
import subprocess
import threading
import time
import uuid
from functools import partial, wraps

from django.http import JsonResponse


class NetworkError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def read_windows_interfaces():
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$rows = @(Get-NetAdapter -Physical | Where-Object {
    $_.Status -eq 'Up' -and $_.InterfaceType -in 6, 71
} | ForEach-Object {
    $adapter = $_
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
        Where-Object { $_.AddressState -eq 'Preferred' } | ForEach-Object {
            [pscustomobject]@{
                interface_id = [string]$adapter.ifIndex
                name = $adapter.Name
                ipv4 = $_.IPAddress
                prefix = $_.PrefixLength
            }
        }
})
ConvertTo-Json -InputObject $rows -Compress
"""
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, timeout=5, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        rows = json.loads(result.stdout.decode('utf-8-sig'))
        if not isinstance(rows, list):
            raise ValueError('Expected interface array')
        interfaces = []
        for row in rows:
            address = ipaddress.IPv4Address(row['ipv4'])
            prefix = int(row['prefix'])
            subnet = ipaddress.IPv4Network(f'{address}/{prefix}', strict=False)
            if (address.is_loopback or address.is_link_local or address.is_multicast
                    or address.is_unspecified or address.is_reserved):
                continue
            if prefix < 31 and address in (subnet.network_address, subnet.broadcast_address):
                continue
            interfaces.append({
                'interface_id': str(row['interface_id']), 'name': str(row['name']),
                'ipv4': str(address), 'prefix': prefix,
            })
        return interfaces
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise NetworkError('network_inventory_failed') from exc


class LocalNetwork:
    def __init__(self, inventory=read_windows_interfaces):
        self._inventory = inventory
        self._lock = threading.RLock()
        self._worker = None
        self._stop = threading.Event()
        self.start_run()

    def start_run(self, port=None):
        self.stop_monitor()
        with self._lock:
            self.server_run_id = str(uuid.uuid4())
            self.port = port
            self.revision = 0
            self.selection = None
            self._interfaces = []
            self._checked_at = None
            self._reason = 'network_selection_required'

    def start_monitor(self):
        if self._worker is not None:
            return
        self._stop.clear()
        self._reason = 'network_inventory_pending'
        self._worker = threading.Thread(target=self._monitor, name='network-inventory', daemon=True)
        try:
            self._worker.start()
        except RuntimeError as exc:
            self._worker = None
            self._reason = 'network_monitor_start_failed'
            raise RuntimeError(self._reason) from exc

    def stop_monitor(self):
        if self._worker is None:
            return
        self._stop.set()
        self._worker.join(timeout=6)
        if self._worker.is_alive():
            raise RuntimeError('network_monitor_shutdown_failed')
        self._worker = None

    def _read_inventory(self):
        try:
            return self._inventory(), None
        except NetworkError as exc:
            return [], exc.reason

    def _apply_inventory(self, rows, reason):
        self._interfaces = rows
        self._checked_at = time.monotonic()
        if reason:
            if self.selection:
                self.revision += 1
            self.selection = None
            self._reason = reason
            return
        if self.selection and self.selection not in rows:
            self.selection = None
            self.revision += 1
            self._reason = 'network_selection_stale'
        if not rows:
            self._reason = 'no_connected_network'
        elif self._reason in ('no_connected_network', 'network_inventory_failed', 'network_inventory_pending'):
            self._reason = 'network_selection_required'

    def _monitor(self):
        while not self._stop.is_set():
            started = time.monotonic()
            with self._lock:
                revision = self.revision
            rows, reason = self._read_inventory()
            with self._lock:
                if revision == self.revision:
                    self._apply_inventory(rows, reason)
            self._stop.wait(max(0, 1 - (time.monotonic() - started)))

    def _refresh(self, force=False):
        if not force and self._worker is not None:
            return
        if not force and self._checked_at is not None and time.monotonic() - self._checked_at < 1:
            return
        self._apply_inventory(*self._read_inventory())

    def select(self, interface_id, ipv4):
        with self._lock:
            self._refresh(force=True)
            if self._reason in ('network_inventory_failed', 'no_connected_network'):
                raise NetworkError(self._reason)
            candidate = next((row for row in self._interfaces
                              if row['interface_id'] == interface_id and row['ipv4'] == ipv4), None)
            if candidate is None:
                raise NetworkError('network_selection_stale')
            if self.port is None:
                raise NetworkError('network_server_not_serving')
            self.selection = candidate.copy()
            self.revision += 1
            self._reason = None
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            self._refresh()
            origin = f"https://{self.selection['ipv4']}:{self.port}" if self.selection else None
            return {
                'api_version': 1, 'scheme': 'https',
                'interfaces': [row.copy() for row in self._interfaces],
                'selected': self.selection is not None,
                'selection': self.selection.copy() if self.selection else None,
                'reason': self._reason,
                'server_run_id': self.server_run_id, 'network_revision': self.revision,
                'server_url': origin, 'camera_url': f'{origin}/camera/' if origin else None,
                'qr_url': '/api/network/qr.svg' if origin else None,
                'discovery': {'status': 'unavailable', 'reason': 'network_discovery_not_implemented'},
                'setup': {'status': 'unknown', 'reason': 'network_capability_missing'},
            }

    def request_reason(self, request, *, local_only=False, cleanup=False):
        snapshot = self.snapshot()
        local_only = local_only or (cleanup and not snapshot['selected'])
        try:
            remote = ipaddress.ip_address(request.META.get('REMOTE_ADDR', ''))
        except ValueError:
            return 'network_source_invalid'
        if remote.is_loopback:
            origin = f'{request.scheme}://{request.get_host()}'
            if request.headers.get('Origin', origin) != origin:
                return 'network_origin_mismatch'
            return None if local_only else (None if snapshot['selected'] else snapshot['reason'])
        if local_only:
            return 'network_local_dashboard_required'
        if not snapshot['selected']:
            return snapshot['reason']
        origin = f'{request.scheme}://{request.get_host()}'
        if origin != snapshot['server_url']:
            return 'network_origin_mismatch'
        if request.headers.get('Origin', origin) != snapshot['server_url']:
            return 'network_origin_mismatch'
        selected = snapshot['selection']
        subnet = ipaddress.IPv4Network(f"{selected['ipv4']}/{selected['prefix']}", strict=False)
        if remote not in subnet:
            return 'network_subnet_mismatch'
        return None


NETWORK = LocalNetwork()


def network_required(view=None, *, cleanup=False):
    if view is None:
        return partial(network_required, cleanup=cleanup)
    @wraps(view)
    def guarded(request, *args, **kwargs):
        reason = NETWORK.request_reason(request, cleanup=cleanup)
        if reason:
            return network_error(reason)
        return view(request, *args, **kwargs)
    return guarded


def network_error(reason, status=409):
    response = JsonResponse({'ok': False, 'reason': reason, 'message': f'網路操作被阻擋：{reason}'}, status=status)
    response['Cache-Control'] = 'no-store'
    return response
