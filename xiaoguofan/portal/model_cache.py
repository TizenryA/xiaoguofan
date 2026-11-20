"""Persist each owner's model directory separately from generation admission."""
import asyncio
import json
import logging
import time


class ModelCache:
    def __init__(self, store, ttl=1800):
        self.store, self.ttl = store, ttl
        self.tasks = {}
        store.run('CREATE TABLE IF NOT EXISTS model_catalogues(owner TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, updated REAL)')

    def put(self, uid, fingerprint, payload):
        self.store.run('INSERT INTO model_catalogues VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET fingerprint=excluded.fingerprint,payload=excluded.payload,updated=excluded.updated',
                       (uid, fingerprint, json.dumps(payload), time.time()))

    async def get(self, uid, fingerprint, loader):
        row = self.store.one('SELECT * FROM model_catalogues WHERE owner=? AND fingerprint=?', (uid, fingerprint))
        task_key = (uid, fingerprint)
        if row and time.time() - row['updated'] < self.ttl:
            return json.loads(row['payload'])
        task = self.tasks.get(task_key)
        if task is None:
            async def refresh():
                try:
                    payload = await loader()
                    self.put(uid, fingerprint, payload)
                    return payload
                finally:
                    self.tasks.pop(task_key, None)
            task = asyncio.create_task(refresh())
            self.tasks[task_key] = task
            # A stale response does not await this refresh; consume failures here.
            def finished(t):
                if not t.cancelled() and t.exception() is not None:
                    logging.getLogger(__name__).warning('model directory refresh failed')
            task.add_done_callback(finished)
        if row:
            return json.loads(row['payload'])
        return await asyncio.shield(task)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
