#!/usr/bin/env python3
"""Build the small public data contract consumed by VPush."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_TZ = timezone(timedelta(hours=8))


def read_json(name):
    path = ROOT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_js(name, variable):
    text = (ROOT / name).read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(variable)}\s*=\s*(\{{.*?\}})\s*;", text, re.S)
    if not match:
        return {}
    return json.loads(match.group(1))


def read_page_data():
    text = (ROOT / "index.html").read_text(encoding="utf-8")
    match = re.search(r"window\.__A500_PAGE_DATA\s*=\s*(\{.*?\})\s*;", text, re.S)
    return json.loads(match.group(1)) if match else {}


def source_generated_at(page, realtime, dividend, transition):
    values = [
        page.get("pageCreatedAt"),
        realtime.get("ts"),
        dividend.get("update_time"),
        transition.get("date"),
    ]
    parsed = []
    for value in values:
        if not value:
            continue
        try:
            text = str(value).replace(" ", "T")
            if text.endswith("Z"):
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=LOCAL_TZ)
            parsed.append(dt.astimezone(timezone.utc))
        except ValueError:
            continue
    return max(parsed).isoformat() if parsed else None


def main():
    page = read_page_data()
    base = read_js("a500_baseline.js", "window.__A500_BASE")
    realtime = read_js("realtime_data.js", "window.__RT")
    dividend = read_json("dividend_data.json")
    transition = read_json("transition_data.json")
    temp_history = read_json("temp_history.json")
    pe_history = read_json("pe_history.json")

    a500 = {
        **{key: page.get(key) for key in (
            "date", "stockYield", "bondYield", "dividendYield", "premium",
            "dcaPct", "dcaLabel", "priceStale", "priceFallback", "pageCreatedAt",
        ) if key in page},
        **realtime,
        "temperature_history": temp_history,
        "pe_history": pe_history,
        "pe_last_calibrated": base.get("peLastCalibrated"),
        "price_window": base.get("priceWindow"),
    }

    payload = {
        "schema_version": 1,
        "source": "a500",
        "generated_at": source_generated_at(page, realtime, dividend, transition),
        "a500": a500,
        "dividend": dividend,
        "transition": transition,
    }
    (ROOT / "vpush_data.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print("wrote vpush_data.json")


if __name__ == "__main__":
    main()
