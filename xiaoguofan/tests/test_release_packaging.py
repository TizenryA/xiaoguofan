"""Release topology contracts, before touching the selected build host."""
import importlib.util
from pathlib import Path

import pytest

P=Path(__file__).resolve().parents[1]/'packaging/install.py'
spec=importlib.util.spec_from_file_location('xgf_installer',P);installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def test_installer_refuses_insecure_or_overlapping_paths():
    for origin,root,data in [('http://public.example','/opt/app','/var/data'),('https://good.example/a/b','/opt/app','/var/data'),('https://good.example','/opt/app','/opt/app/data'),('https://good.example','/opt/a/../b','/var/data')]:
        with pytest.raises(ValueError):installer.validate(origin,root,data,8890)
    installer.validate('https://example.com/lite','/opt/app','/var/data',8890)


def test_broker_only_docker_socket_and_shared_slice():
    c={'root':'/opt/app','data':'/var/data','socket_dir':'/run/xiaoguofan-release','portal_image':'xiaoguofan-portal:0.9.11','docker_gid':999}
    d=installer.compose(c,123,124)['services']
    for v in d.values():assert v['cgroup_parent']=='xiaoguofan.slice' and v['network_mode']=='host'
    assert not any('docker.sock' in v for v in d['portal']['volumes'])
    assert any('docker.sock' in v for v in d['broker']['volumes'])
    assert d['portal']['user']=='123:124' and d['portal']['read_only']
    assert d['broker']['memswap_limit']==d['broker']['mem_limit']


def test_linux_units_retain_least_privilege_and_budget():
    c={'root':'/opt/app','data':'/var/data','socket_dir':'/run/xiaoguofan-release','python':'/opt/app/app/.venv/bin/python','mode':'linux'}
    u=installer.units(c)
    assert 'User=xiaoguofan' in u['xiaoguofan-portal.service']
    assert 'Slice=xiaoguofan.slice' in u['xiaoguofan-portal.service']
    assert 'cleanup' in u['xiaoguofan-broker.service'] and 'MemoryMax=150M' in u['xiaoguofan-broker.service']
