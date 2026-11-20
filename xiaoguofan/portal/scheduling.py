"""Per-owner FIFO admission and disconnect ownership."""
import asyncio
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.responses import JSONResponse

BODY_LIMIT = 2 * 1024**2
BODY_BUDGET = 32 * 1024**2
BUSY_DETAILS = frozenset({
    '项目内存预算已满，请稍后重试', '项目资源保护未就绪，请稍后重试',
    '账号池正在启动或维护，请稍后重试', '自己的账号池正在处理请求，请稍后重试',
})


@dataclass(eq=False)
class Ticket:
    uid: str
    deadline: float
    retry_at: float = 0
    reserved: bool = False
    committed: bool = False


class FairQueue:
    def __init__(self, capacity, locks, timeout=900, per_user=5, total=100):
        self.capacity = capacity  # Synchronous cached value only; never network I/O.
        self.locks = locks
        self.timeout, self.per_user, self.total = timeout, per_user, total
        self.queues = {}
        self.turns = deque()
        self.running = {}
        self.quarantined = set()

    @property
    def waiting(self):
        return sum(sum(not t.reserved for t in q) for q in self.queues.values())

    def snapshot(self, uid=None):
        return {'active': len(self.running), 'queued': self.waiting,
                'queued_users': sum(any(not t.reserved for t in q) for q in self.queues.values()),
                'limit': self.capacity(), 'user_queued': sum(not t.reserved for t in self.queues.get(uid, ())),
                'queue_limit': None, 'user_queue_limit': None,
                'wait_timeout_seconds': self.timeout, 'quarantined': len(self.quarantined)}

    def enqueue(self, uid):
        self.expire()
        ticket = Ticket(uid, time.monotonic() + self.timeout)
        if uid not in self.queues:
            self.queues[uid] = deque()
            self.turns.append(uid)
        self.queues[uid].append(ticket)
        return ticket

    def expire(self):
        now = time.monotonic()
        for q in list(self.queues.values()):
            for ticket in list(q):
                if not ticket.reserved and ticket.deadline <= now:
                    self.remove(ticket)

    def eligible(self):
        now = time.monotonic()
        for uid in self.turns:
            q = self.queues[uid]
            if (q and uid not in self.running
                    and not self.locks[uid].locked() and q[0].retry_at <= now
                    and q[0].deadline > now):
                return q[0]
        return None

    async def acquire(self, ticket, request):
        try:
            while True:
                if ticket.deadline <= time.monotonic():
                    raise HTTPException(504, '排队等待超时，请稍后重试')
                if len(self.running) < self.capacity() and self.eligible() is ticket:
                    # asyncio's unlocked Lock.acquire completes without yielding.
                    await self.locks[ticket.uid].acquire()
                    self.running[ticket.uid] = ticket
                    ticket.reserved = True
                    return
                await asyncio.sleep(.05)
        except BaseException:
            self.finish(ticket)
            raise

    def commit(self, ticket):
        self.remove(ticket)
        ticket.committed = True
        # Rotate this user's remaining FIFO behind other users only on successful start.
        if ticket.uid in self.turns:
            self.turns.remove(ticket.uid)
            self.turns.append(ticket.uid)

    def retry(self, ticket):
        # Keep the exact head/turn and monotonic deadline; release only the reservation.
        if self.running.get(ticket.uid) is ticket:
            del self.running[ticket.uid]
            self.locks[ticket.uid].release()
        ticket.reserved = False
        ticket.retry_at = time.monotonic() + .2

    def remove(self, ticket):
        q = self.queues.get(ticket.uid)
        if q is not None and ticket in q:
            q.remove(ticket)
            if not q:
                del self.queues[ticket.uid]
                self.turns.remove(ticket.uid)

    def finish(self, ticket, safe=True):
        self.remove(ticket)
        if self.running.get(ticket.uid) is ticket:
            del self.running[ticket.uid]
            self.locks[ticket.uid].release()
            if ticket.uid in self.turns:
                self.turns.remove(ticket.uid)
                self.turns.append(ticket.uid)
        ticket.reserved = False


class BodyBudget:
    def __init__(self, limit=BODY_BUDGET):
        self.limit = limit
        self.used = 0


class BoundedBodyMiddleware:
    """Buffer request bytes and give disconnect monitoring a single receive owner."""
    def __init__(self, app, budget):
        self.app, self.budget = app, budget

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        held = 0
        chunks = []
        try:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                part = message.get('body', b'')
                self.budget.used += len(part)
                held += len(part)
                chunks.append(part)
                if not message.get('more_body', False):
                    break
            body = b''.join(chunks)
            chunks.clear()
            disconnected = asyncio.Event()
            scope['portal.disconnect'] = disconnected
            async def monitor():
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        disconnected.set()
                        return
            monitor_task = asyncio.create_task(monitor())
            delivered = False
            async def buffered_receive():
                nonlocal delivered, body
                if not delivered:
                    delivered = True
                    result = {'type': 'http.request', 'body': body, 'more_body': False}
                    body = b''
                    return result
                await disconnected.wait()
                return {'type': 'http.disconnect'}
            try:
                await self.app(scope, buffered_receive, send)
            finally:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
        finally:
            self.budget.used -= held
