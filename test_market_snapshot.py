import datetime
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("realtime", Path(__file__).with_name("realtime.py"))
realtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(realtime)


def test_snapshot_contract():
    payload = realtime.snapshot_payload(
        {
            "price": 5370.3,
            "change": 0.19,
            "pe": 16.31,
            "pePercentile": 17.2,
            "pricePercentile": 68.4,
            "temperature": 41,
            "temperatureRaw": 38,
            "live": False,
            "fresh": True,
            "delayed": False,
            "market": False,
            "ts": "2026-09-30 22:07:26",
        },
        now=datetime.datetime(2026, 9, 30, 14, 8, tzinfo=datetime.timezone.utc),
    )
    assert payload["temperature"] == 41
    assert payload["temperature_status"] == "normal"
    assert payload["market_status"] == "closed"
    assert payload["updated_at"] == "2026-09-30T22:07:26+08:00"
    assert payload["source"] == "a500"


if __name__ == "__main__":
    test_snapshot_contract()
    print("ok")
