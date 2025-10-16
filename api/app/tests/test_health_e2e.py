import pytest

@pytest.mark.anyio
async def test_health(async_client):
    r = await async_client.get("/api/v1/health")
    assert r.status_code == 200