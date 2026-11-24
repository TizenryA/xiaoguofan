"""Real TCP HTTP/SSE verification against the fake engine, NOT a Google test."""
import asyncio

import httpx
import pytest
from engine_fixture import FakeEngine, Served, cookie_payload, portal


@pytest.mark.parametrize('anti', [False, True])
def test_live_sse_disconnect_releases_user_and_global_slots(tmp_path, anti):
    fake = FakeEngine().start()
    fake.chunks, fake.chunk_delay = 500, .02
    app, store, *_ = portal(tmp_path, fake, engine_concurrency=2, user_concurrency=1)
    users = [store.discord_user(str(100000000000000011+i), 'user'+str(i)) for i in range(3)]
    keys = []
    for i, u in enumerate(users):
        store.mark_verified(u['id'])
        store.add_account(u['id'], 'own', cookie_payload(f'user{i}@gmail.com'))
        keys.append(store.create_key(u['id'], 'test')['key'])
        store.save_preferences(u['id'], anti)
    scheduler = app.state.scheduler

    async def exercise(base):
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            async def request(i):
                return await c.send(c.build_request('POST', '/v1/chat/completions', headers={'Authorization': 'Bearer '+keys[i]},
                                                    json={'model': ('[防截断]' if anti else '')+'fixture', 'stream': True, 'messages': []}), stream=True)
            a = await request(0)
            assert a.status_code == 200
            lines = a.aiter_lines()
            assert (await anext(lines)) is not None
            b = await request(1)
            assert b.status_code == 200
            # User 0 is at its own cap and the site is at its cap: both wait in line, no 429.
            pending0 = asyncio.create_task(request(0))
            pending2 = asyncio.create_task(request(2))
            for _ in range(200):
                if scheduler.snapshot()['queued'] == 2:
                    break
                await asyncio.sleep(.02)
            assert scheduler.snapshot()['queued'] == 2 and scheduler.snapshot()['active'] == 2
            await a.aclose()
            r0 = await asyncio.wait_for(pending0, 10)
            assert r0.status_code == 200, 'disconnect leaked the per-user slot'
            assert not pending2.done(), 'site cap must still hold user 2'
            await r0.aclose()
            r2 = await asyncio.wait_for(pending2, 10)
            assert r2.status_code == 200, 'disconnect leaked the global slot'
            await r2.aclose()
            await b.aclose()
            for _ in range(200):
                if scheduler.snapshot()['active'] == 0 and len(store.stats(users[0]['id'])['recent']) >= 2:
                    break
                await asyncio.sleep(.02)
            assert scheduler.snapshot()['active'] == 0
            rows = store.stats(users[0]['id'])['recent']
            assert len(rows) >= 2 and all(r['status'] == 499 for r in rows)
    try:
        with Served(app) as base:
            asyncio.run(exercise(base))
        scopes = {tuple(scope) for p, scope, _ in fake.seen if p == '/v1/chat/completions'}
        assert scopes == {('user0@gmail.com',), ('user1@gmail.com',), ('user2@gmail.com',)}
    finally:
        fake.stop()
