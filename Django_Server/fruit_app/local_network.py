"""Process-local network selection; importing this module opens no resources."""
import ipaddress
import json
import subprocess
import threading
import time
import uuid
from functools import partial, wraps

from django.http import JsonResponse


def publish_mdns(selection, port, run_id, revision):
    from zeroconf import IPVersion, ServiceInfo, Zeroconf
    service = ServiceInfo(
        '_pf-iot._tcp.local.', f'pf-{run_id}._pf-iot._tcp.local.',
        port=port, addresses=[ipaddress.IPv4Address(selection['ipv4']).packed],
        server=f'pf-{run_id}.local.',
        properties={'api_version': '1', 'scheme': 'https', 'server_run_id': run_id,
                    'network_revision': str(revision), 'info_path': '/api/network/info/'},
    )
    publisher = Zeroconf(interfaces=[selection['ipv4']], ip_version=IPVersion.V4Only)
    try:
        publisher.register_service(service)
    except Exception:
        publisher.close()
        raise
    return publisher


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
    def __init__(self, inventory=read_windows_interfaces, discovery_factory=publish_mdns):
        self._inventory = inventory
        self._discovery_factory = discovery_factory
        self._publisher = None
        self._published_revision = None
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
            self._discovery = {'status': 'waiting', 'reason': 'network_selection_required'}
            self._diagnostic = None
            self._diagnostic_report_at = None

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
        if self._worker is not None:
            self._stop.set()
            self._worker.join(timeout=12)
            if self._worker.is_alive():
                raise RuntimeError('network_monitor_shutdown_failed')
            self._worker = None
        self._close_discovery()

    def _close_discovery(self):
        if self._publisher is not None:
            self._publisher.close()
            self._publisher = None
        self._published_revision = None

    def _sync_discovery(self):
        # Only the serving monitor owns mDNS sockets; HTTP never waits for registration.
        with self._lock:
            revision = self.revision
            selection = self.selection.copy() if self.selection else None
            run_id, port = self.server_run_id, self.port
        if revision == self._published_revision:
            return
        self._close_discovery()
        publisher = None
        status = {'status': 'waiting', 'reason': 'network_selection_required'}
        if selection:
            try:
                publisher = self._discovery_factory(selection, port, run_id, revision)
                status = {'status': 'advertising', 'reason': None}
            except Exception:
                status = {'status': 'unavailable', 'reason': 'network_discovery_registration_failed'}
        with self._lock:
            stale = revision != self.revision or self._stop.is_set()
            if not stale:
                self._publisher = publisher
                self._published_revision = revision if publisher or not selection else None
                self._discovery = status
        if stale and publisher is not None:
            publisher.close()

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
                self._close_discovery()
                self.revision += 1
            self.selection = None
            self._reason = reason
            self._invalidate_diagnostics()
            return
        if self.selection and self.selection not in rows:
            replacements = [row for row in rows
                            if row['interface_id'] == self.selection['interface_id']]
            self._close_discovery()
            self.selection = replacements[0].copy() if len(replacements) == 1 else None
            self.revision += 1
            self._reason = None if self.selection else 'network_selection_stale'
            self._invalidate_diagnostics()
        if not rows:
            self._reason = 'no_connected_network'
        elif self._reason in ('no_connected_network', 'network_inventory_failed', 'network_inventory_pending'):
            self._reason = 'network_selection_required'

    def _monitor(self):
        from . import views
        while not self._stop.is_set():
            started = time.monotonic()
            with self._lock:
                revision = self.revision
            rows, reason = self._read_inventory()
            with views.STATE_LOCK:
                with self._lock:
                    if revision == self.revision:
                        if self.selection and (reason or self.selection not in rows):
                            views._lock_network('network_changed')
                        self._apply_inventory(rows, reason)
                views._check_network_safety()
            self._sync_discovery()
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
            self._close_discovery()
            self.selection = candidate.copy()
            self.revision += 1
            self._reason = None
            self._invalidate_diagnostics()
            self._discovery = {'status': 'pending', 'reason': 'network_discovery_pending'}
            return self.snapshot()

    def _invalidate_diagnostics(self):
        self._diagnostic = None
        self._diagnostic_report_at = None
        self._discovery = {'status': 'waiting', 'reason': self._reason}

    def diagnostic(self, data, *, report=False, observation=False):
        with self._lock:
            self._refresh()
            if not self.selection:
                raise NetworkError(self._reason)
            if (data.get('server_run_id') != self.server_run_id
                    or str(data.get('network_revision')) != str(self.revision)):
                raise NetworkError('network_identity_mismatch')
            boot_id = data.get('boot_id')
            if not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 96:
                raise NetworkError('network_diagnostic_invalid')
            if observation:
                if data.get('discovery_reason') != 'multiple_controllers_found':
                    raise NetworkError('network_diagnostic_invalid')
                self._diagnostic = {'boot_id': boot_id, 'source': 'mdns',
                                    'reason': 'multiple_controllers_found', 'diagnostic_id': None}
                self._diagnostic_report_at = None
            elif report:
                if (not self._diagnostic or self._diagnostic['boot_id'] != boot_id
                        or not self._diagnostic['diagnostic_id']
                        or data.get('diagnostic_id') != self._diagnostic['diagnostic_id']):
                    raise NetworkError('network_diagnostic_mismatch')
                self._diagnostic_report_at = time.monotonic()
            else:
                source, reason = data.get('discovery_source'), data.get('discovery_reason', '')
                if source not in ('mdns', 'manual') or reason not in (
                        '', 'controller_not_found', 'discovery_timeout',
                        'network_discovery_unavailable', 'network_info_invalid', 'network_info_unavailable'):
                    raise NetworkError('network_diagnostic_invalid')
                if (not self._diagnostic or self._diagnostic['boot_id'] != boot_id
                        or not self._diagnostic['diagnostic_id']):
                    self._diagnostic_report_at = None
                    self._diagnostic = {'boot_id': boot_id, 'diagnostic_id': str(uuid.uuid4())}
                self._diagnostic.update(source=source, reason=reason or None)
            return {'ok': True, 'diagnostic': True, 'command': 'none', 'command_id': 0,
                    'auto_trigger_enabled': False, 'control_allowed': False,
                    'reason': 'network_capability_missing', 'server_run_id': self.server_run_id,
                    'network_revision': self.revision, 'diagnostic_id': self._diagnostic['diagnostic_id']}

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
                'discovery': self._discovery.copy(),
                'esp32': {
                    **(self._diagnostic or {}),
                    'online': self._diagnostic_report_at is not None
                    and time.monotonic() - self._diagnostic_report_at < 6,
                    'status': 'discovery_conflict' if self._diagnostic and self._diagnostic['reason'] == 'multiple_controllers_found'
                    else 'round_trip_complete' if self._diagnostic_report_at is not None else 'waiting_for_report',
                },
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
        from . import views
        views._ensure_dataset_structure()
        with views.STATE_LOCK:
            views._check_network_safety()
            if (not cleanup and request.path != '/api/photo_capture/'
                    and not request.path.startswith('/api/esp32')
                    and views.APP_STATE.get('network_recovery')):
                return network_error(views._network_recovery_reason())
        reason = NETWORK.request_reason(request, cleanup=cleanup)
        if reason:
            return network_error(reason)
        return view(request, *args, **kwargs)
    return guarded


def network_error(reason, status=409):
    response = JsonResponse({'ok': False, 'reason': reason, 'message': f'網路操作被阻擋：{reason}'}, status=status)
    response['Cache-Control'] = 'no-store'
    return response
