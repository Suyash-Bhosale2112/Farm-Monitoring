from datetime import date, timedelta


def detect_ndvi_drop(observations, threshold, baseline_days=30, minimum_observations=3):
    dated = [
        (date.fromisoformat(item["image_date"]), item)
        for item in observations
    ]
    if not dated:
        return None

    latest_date = max(item_date for item_date, _ in dated)
    latest_rows = [
        item
        for item_date, item in dated
        if item_date == latest_date and item.get("ndvi") is not None
    ]
    if not latest_rows:
        return None
    latest_ndvi = sum(item["ndvi"] for item in latest_rows) / len(latest_rows)
    baseline_start = latest_date - timedelta(days=baseline_days)
    baseline_values = [
        item["ndvi"]
        for item_date, item in dated
        if (
            baseline_start <= item_date < latest_date
            and item.get("ndvi") is not None
        )
    ]
    if len(baseline_values) < minimum_observations:
        return None

    baseline_mean = sum(baseline_values) / len(baseline_values)
    drop = baseline_mean - latest_ndvi
    return {
        "latest_date": latest_date.isoformat(),
        "latest_ndvi": latest_ndvi,
        "baseline_mean": baseline_mean,
        "baseline_count": len(baseline_values),
        "drop": drop,
        "triggered": drop >= threshold,
    }
