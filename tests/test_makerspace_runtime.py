"""Failure isolation for the trusted runner, without host mutations."""
import importlib.util
from pathlib import Path


def runtime():
    path = Path(__file__).resolve().parents[1] / 'deploy/makerspace/runtime.py'
    spec = importlib.util.spec_from_file_location('maker_runtime_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_isolation_failure_revokes_heartbeat_and_stops_workloads(monkeypatch):
    module = runtime()
    events = []
    class Stop:
        def wait(self, _seconds):
            return False
        def set(self):
            events.append('stopped')
    def broken(_config):
        raise module.RuntimeFailure('firewall changed')
    monkeypatch.setattr(module, 'check_sandbox', broken)
    monkeypatch.setattr(module, 'stop_sandboxes', lambda: events.append('containers stopped'))
    monkeypatch.setattr(module, 'call_api', lambda *args: events.append('advertised'))
    module.heartbeat({}, Stop())
    assert events == ['stopped', 'containers stopped']


def test_emergency_stop_targets_only_valid_makerspace_names(monkeypatch):
    module = runtime()
    calls = []
    valid = 'maker-public-' + 'a' * 32
    def command(args, **kwargs):
        calls.append(args)
        return valid + '\nunikorn-api\nmaker-preview-malformed' if args[1] == 'ps' else ''
    monkeypatch.setattr(module, 'command', command)
    module.stop_sandboxes()
    assert '--filter' in calls[0] and 'label=unikorn.makerspace=1' in calls[0]
    assert calls[1:] == [['docker', 'stop', '--time', '1', valid]]


def test_runtime_has_no_outbound_network_and_build_is_separate(monkeypatch):
    module = runtime()
    monkeypatch.setattr(module, 'trusted', lambda _: None)
    work = Path('/synthetic/work')
    build = module.sandbox_options('build', work, 'image', build=True)
    live = module.sandbox_options('runtime', work, 'image', data=Path('/synthetic/data'), port=20001)
    assert build[build.index('--network') + 1] == 'unikorn-makerbuild'
    assert live[live.index('--network') + 1] == 'unikorn-makerspace'
    assert not any('/synthetic/data' in arg for arg in build)
    path = Path(__file__).resolve().parents[1] / 'deploy/makerspace/firewall.py'
    spec = importlib.util.spec_from_file_location('maker_firewall_test', path)
    firewall = importlib.util.module_from_spec(spec); spec.loader.exec_module(firewall)
    assert firewall.EGRESS == [
        ['-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '--ctdir', 'REPLY', '-j', 'RETURN'],
        ['-j', 'DROP'],
    ]
    assert ['-p', 'tcp', '-m', 'multiport', '--dports', '80,443', '-j', 'RETURN'] in firewall.BUILD
