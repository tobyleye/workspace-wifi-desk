import asyncio

import pytest

from app.unifi import MockUnifiClient, UnifiError


def run(coro):
    return asyncio.run(coro)


def test_builds_official_request_body():
    client = MockUnifiClient(delay_seconds=0)
    run(client.create_voucher("Ada Obi · ada_obi · 2026-11-16", 839, 2))
    assert client.requests == [{
        "count": 1,
        "name": "Ada Obi · ada_obi · 2026-11-16",
        "timeLimitMinutes": 839,
        "authorizedGuestLimit": 2,
    }]
    assert client.endpoint == "/proxy/network/integration/v1/sites/default/hotspot/vouchers"


def test_stores_official_response_fields_and_10_digit_code():
    client = MockUnifiClient(delay_seconds=0)
    result = run(client.create_voucher("x", 60, 2))
    voucher = client.vouchers[result["id"]]
    assert set(voucher) == {"id", "createdAt", "name", "code", "authorizedGuestLimit",
                            "authorizedGuestCount", "timeLimitMinutes", "expired"}
    assert voucher["code"] == result["code"]
    assert len(result["code"]) == 10 and result["code"].isdigit()
    assert voucher["authorizedGuestLimit"] == 2
    assert voucher["authorizedGuestCount"] == 0
    assert voucher["timeLimitMinutes"] == 60
    assert voucher["expired"] is False


def test_codes_are_unique():
    client = MockUnifiClient(delay_seconds=0)
    codes = {run(client.create_voucher("x", 60, 2))["code"] for _ in range(50)}
    assert len(codes) == 50


def test_outage_raises_and_stores_nothing():
    client = MockUnifiClient(delay_seconds=0)
    client.outage = True
    with pytest.raises(UnifiError):
        run(client.create_voucher("x", 60, 2))
    assert client.vouchers == {}


def test_delete_works_and_is_quiet():
    client = MockUnifiClient(delay_seconds=0)
    result = run(client.create_voucher("x", 60, 2))
    run(client.delete_voucher_quietly(result["id"]))
    assert client.vouchers == {}
    run(client.delete_voucher_quietly("does-not-exist"))  # no error
