"""Aggregate-only cgroup v2 metrics for the entire Xiaoguofan slice."""
import os
import time
from pathlib import Path

LIMIT = 2 * 1024**3
HIGH = 1792 * 1024**2
WORKER_BYTES = 650 * 1024**2
BASE_RESERVE = 384 * 1024**2
HEADROOM = 96 * 1024**2


class ProjectResources:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('PORTAL_CGROUP_ROOT', '/sys/fs/cgroup/xiaoguofan.slice'))
        self.previous = None

    def snapshot(self):
        try:
            used = int((self.root/'memory.current').read_text())
            limit = int((self.root/'memory.max').read_text())
            high = int((self.root/'memory.high').read_text())
            swap = int((self.root/'memory.swap.current').read_text())
            cpu = dict(line.split() for line in (self.root/'cpu.stat').read_text().splitlines())
            usage = int(cpu['usage_usec']);now = time.monotonic();percent = None
            cores = os.cpu_count() or 1
            if self.previous and now > self.previous[0]:
                percent = min(100., max(0., (usage-self.previous[1])/(now-self.previous[0])/10000/cores))
            self.previous = (now, usage)
            # Refuse to advertise enforcement if the real parent limit is missing/wrong.
            if limit > LIMIT or limit <= 0:
                raise ValueError('project cap not enforced')
            return {'available':True,'timestamp':time.time(),
                    'memory':{'used_bytes':used,'limit_bytes':limit,'high_bytes':high,'swap_bytes':swap},
                    'cpu':{'percent':round(percent,2) if percent is not None else None,'cores':cores}}
        except (OSError, ValueError, KeyError):
            return {'available':False,'timestamp':time.time(),
                    'memory':{'used_bytes':None,'limit_bytes':LIMIT,'high_bytes':HIGH,'swap_bytes':None},
                    'cpu':{'percent':None,'cores':os.cpu_count() or 1}}

    def worker_memory(self, name):
        try:
            # Docker systemd cgroup driver scope lives directly under the project slice.
            for scope in self.root.glob('docker-*.scope'):
                # Match by container id supplied by docker run, never a user path.
                if scope.name == 'docker-'+name+'.scope':
                    return int((scope/'memory.current').read_text())
        except (OSError, ValueError):
            pass
        return 0
