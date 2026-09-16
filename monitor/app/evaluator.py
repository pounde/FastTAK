"""Stateless health threshold evaluation.

Takes raw data and thresholds, returns status and optional message.
Three evaluation patterns determined by threshold config shape:
  1. Numeric — warning/critical levels, direction inferred from values
  2. Boolean — true/false mapped to status via config
  3. State mapping — string states mapped to status (containers)
"""

from app.status import Status


def evaluate(service: str, data: dict, service_config: dict) -> dict:
    """Compare raw data against thresholds, return status + message + should_alert.

    Accepts the full service config (thresholds, alert_min_level, etc.).
    Returns should_alert based on whether the status meets alert_min_level.
    The evaluator is stateless — it decides, the scheduler acts.
    """
    thresholds = service_config.get("thresholds", {})
    alert_min_level = service_config.get("alert_min_level", "warning")

    if "items" in data:
        if "health" in thresholds and "status" in thresholds:
            result = _evaluate_containers(data["items"], thresholds)
        else:
            result = _evaluate_list(service, data["items"], thresholds)
    else:
        result = _evaluate_single(service, data, thresholds)

    result["should_alert"] = Status[result["status"]] >= Status[alert_min_level]
    return result


def _evaluate_single(service: str, data: dict, thresholds: dict) -> dict:
    """Evaluate a single dict of values against thresholds."""
    worst = Status.ok
    message = None

    for key, levels in thresholds.items():
        if key in ("min_dead_tuples", "error_status"):
            continue  # filter/error-severity params, not thresholds
        if key not in data:
            continue

        value = data[key]
        status, msg = _check_threshold(key, value, levels, data)
        if Status[status] > worst:
            worst = Status[status]
            message = msg

    result = {"status": worst.name}
    if message:
        result["message"] = message
    return result


def _item_name(item: dict) -> str:
    return (
        item.get("name")
        or item.get("file")
        or item.get("mount")
        or item.get("table")
        or item.get("domain", "")
    )


def item_status(item: dict, thresholds: dict) -> tuple[str, str | None]:
    """One item's status and message. An item carrying `error` is a warning
    with the reason (a probe that could not read it must not read as fine);
    otherwise the worst of its numeric thresholds. The dashboard rows and the
    service status both come from here, so they cannot disagree.
    """
    name = _item_name(item)
    if item.get("error"):
        error_status = thresholds.get("error_status", "warning")
        return error_status, f"{name}: {item['error']}" if name else str(item["error"])

    worst = Status.ok
    message = None
    for key, levels in thresholds.items():
        if key in ("min_dead_tuples", "error_status"):
            continue
        if key not in item:
            continue
        status, msg = _check_threshold(key, item[key], levels, item)
        if Status[status] > worst:
            worst = Status[status]
            message = f"{name}: {msg}" if name else msg
    return worst.name, message


def _evaluate_list(service: str, items: list, thresholds: dict) -> dict:
    """Evaluate a list of items, return worst status."""
    worst = Status.ok
    message = None
    min_dead = thresholds.get("min_dead_tuples")

    for item in items:
        # Apply min_dead_tuples filter for autovacuum — but never on an error
        # item; a probe that could not read a table must not be filtered
        # away by a threshold about the data it could not read.
        if "error" not in item and min_dead is not None and item.get("dead_tuples", 0) < min_dead:
            continue
        status, msg = item_status(item, thresholds)
        if Status[status] > worst:
            worst = Status[status]
            message = msg

    result = {"status": worst.name}
    if message:
        result["message"] = message
    return result


def _evaluate_containers(items: list, thresholds: dict) -> dict:
    """Evaluate containers with two-map fallback: health → status.

    Logic: check health map first. If health is "unknown" (no healthcheck),
    fall back to the status map. If neither map has a match, use default_status.
    """
    health_map = thresholds.get("health", {})
    status_map = thresholds.get("status", {})
    default = thresholds.get("default_status", "warning")
    worst = Status.ok
    message = None

    for item in items:
        health = item.get("health", "unknown")
        docker_status = item.get("status", "unknown")
        name = item.get("name", "")

        if health != "unknown" and health in health_map:
            # Health is known and mapped — use it
            mapped = health_map[health]
        elif docker_status in status_map:
            # Health is unknown or unmapped — fall back to Docker status
            mapped = status_map[docker_status]
        else:
            # Neither map has a match
            mapped = default

        level = Status[mapped]
        if level > worst:
            worst = level
            if level > Status.ok:
                message = f"{name}: {health}/{docker_status}"

    result = {"status": worst.name}
    if message:
        result["message"] = message
    return result


def _check_threshold(key: str, value, levels: dict, context: dict) -> tuple[str, str | None]:
    """Check a single value against its threshold levels.

    Returns (status_name, message_or_none).
    """
    # Boolean/state mapping: levels map values to status strings
    if isinstance(levels, dict) and not _is_numeric_threshold(levels):
        mapped = levels.get(str(value).lower())
        if mapped and Status[mapped] > Status.ok:
            return mapped, f"{key} is {value}"
        if mapped:
            return mapped, None
        # Unmapped value
        return "warning", f"{key} has unmapped value: {value}"

    # Numeric threshold: infer direction from warning vs critical
    warning = levels.get("warning")
    critical = levels.get("critical")
    if warning is None or critical is None:
        return "ok", None

    if warning < critical:
        # Higher is worse (size_bytes, percent, dead_pct)
        if value >= critical:
            return "critical", f"{key} is {value} (threshold: {critical})"
        if value >= warning:
            return "warning", f"{key} is {value} (threshold: {warning})"
    else:
        # Lower is worse (days_left)
        if value <= critical:
            return "critical", f"{key} is {value} (threshold: {critical})"
        if value <= warning:
            return "warning", f"{key} is {value} (threshold: {warning})"

    return "ok", None


def _is_numeric_threshold(levels: dict) -> bool:
    """Check if a threshold dict has numeric warning/critical values."""
    return (
        "warning" in levels and "critical" in levels and isinstance(levels["warning"], int | float)
    )
