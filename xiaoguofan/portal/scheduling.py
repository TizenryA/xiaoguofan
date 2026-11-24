"""Shared-engine admission (global FIFO + per-user cap) and disconnect ownership."""
import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException

BODY_LIMIT = 2 * 1024**2          # portal pages and management API
API_BODY_LIMIT = 64 * 1024**2     # model requests (inline images, long contexts)
BODY_BUDGET = int(os.environ.get("PORTAL_BODY_BUDGET_MB", "256")) * 1024**2  # all in-flight request bodies


class Admission:
    """Global FIFO admission with a per-user cap (shared engine).

    Replaces the per-user single-flight queue of the per-container design:
    a user may run up to ``per_user()`` requests at once, the site up to
    ``capacity()``; waiters are served in arrival order, skipping users that
    are already at their own cap so one heavy user cannot block others.
    """

    def __init__(self, capacity, per_user, timeout=300):
        self.capacity, self.per_user, self.timeout = capacity, per_user, timeout
        self.waiting = deque()
        self.running = {}
        self.total = 0
        self.peak = 0

    def snapshot(self, uid=None):
        return {'active': self.total, 'queued': len(self.waiting),
                'queued_users': len({t.uid for t in self.waiting}),
                'limit': self.capacity(), 'per_user': self.per_user(), 'peak': self.peak,
                'user_active': self.running.get(uid, 0) if uid else None,
                'user_queued': sum(t.uid == uid for t in self.waiting) if uid else None,
                'wait_timeout_seconds': self.timeout}

    def _eligible(self):
        if self.total >= self.capacity():
            return None
        limit = self.per_user()
        for ticket in self.waiting:
            if self.running.get(ticket.uid, 0) < limit:
                return ticket
        return None

    async def acquire(self, uid):
        ticket = Ticket(uid, time.monotonic()+self.timeout)
        self.waiting.append(ticket)
        try:
            while True:
                if self._eligible() is ticket:
                    self.waiting.remove(ticket)
                    self.running[uid] = self.running.get(uid, 0)+1
                    self.total += 1
                    self.peak = max(self.peak, self.total)
                    ticket.reserved = True
                    return ticket
                if ticket.deadline <= time.monotonic():
                    raise HTTPException(429, '现在排队的人太多，请稍后重试', headers={'Retry-After': '30'})
                await asyncio.sleep(.02)
        except BaseException:
            if ticket in self.waiting:
                self.waiting.remove(ticket)
            raise

    def release(self, ticket):
        if not ticket.reserved:
            return
        ticket.reserved = False
        left = self.running.get(ticket.uid, 1)-1
        if left > 0:
            self.running[ticket.uid] = left
        else:
            self.running.pop(ticket.uid, None)
        self.total = max(0, self.total-1)


@dataclass(eq=False)
class Ticket:
    uid: str
    deadline: float
    reserved: bool = False


class BodyBudget:
    def __init__(self, limit=BODY_BUDGET):
        self.limit = limit
        self.used = 0


async def _reject(send, status, message):
    import json
    raw = json.dumps({'detail': message, 'error': {'message': message}}, ensure_ascii=False).encode()
    await send({'type': 'http.response.start', 'status': status,
                'headers': [(b'content-type', b'application/json; charset=utf-8'), (b'content-length', str(len(raw)).encode()), (b'connection', b'close')]})
    await send({'type': 'http.response.body', 'body': raw})


class BoundedBodyMiddleware:
    """Buffer request bytes and give disconnect monitoring a single receive owner."""
    def __init__(self, app, budget):
        self.app, self.budget = app, budget

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        held = 0
        chunks = []
        path = scope.get('path', '')
        limit = API_BODY_LIMIT if path.startswith(('/v1/', '/v1beta/', '/v1alpha/')) or path in ('/v1', '/v1beta') else BODY_LIMIT
        try:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                part = message.get('body', b'')
                self.budget.used += len(part)
                held += len(part)
                chunks.append(part)
                if held > limit:
                    return await _reject(send, 413, '请求内容太大')
                if self.budget.used > self.budget.limit:
                    return await _reject(send, 503, '服务器正忙，请稍后重试')
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
