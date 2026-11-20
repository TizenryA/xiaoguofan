"""One cleanup task owns upstream close, lease release and admission handoff."""
import asyncio
import logging


class ProxyCleanup:
    def __init__(self, workers, uid, scheduler, ticket, record):
        self.workers, self.uid = workers, uid
        self.scheduler, self.ticket, self.record = scheduler, ticket, record
        self.response = self.client = self.worker = None
        self.task = None

    async def __call__(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())
        # Repeated cancellation cannot cancel the sole cleanup owner.
        await asyncio.shield(self.task)

    async def run(self):
        try:
            # Attempt both closes even if one fails. Cleanup failure is retryable;
            # it must not permanently exclude this user from admission.
            for resource in (self.response, self.client):
                if resource is None:
                    continue
                try:
                    async with asyncio.timeout(5):
                        await resource.aclose()
                except Exception:
                    logging.getLogger(__name__).warning('upstream close unconfirmed')
            if self.worker and self.worker.get('lease'):
                try:
                    async with asyncio.timeout(5):
                        await self.workers.release(self.uid, self.worker['lease'])
                except Exception:
                    # BrokerClient retains its independent release retry task.
                    logging.getLogger(__name__).warning('lease release pending; admission remains retryable')
        finally:
            self.scheduler.finish(self.ticket, safe=True)
            self.record()
