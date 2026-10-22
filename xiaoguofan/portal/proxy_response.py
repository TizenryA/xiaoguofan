"""Own proxy resources across the entire ASGI response, including header-send failure."""
from starlette.responses import StreamingResponse


class ManagedStreamingResponse(StreamingResponse):
    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.cleanup()
