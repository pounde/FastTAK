"""Disk usage monitoring via os.statvfs on mounted volumes."""

import os

# Key mount points inside the monitor container
MOUNT_POINTS = {
    "root": "/",
    "tak-certs": "/opt/tak/certs",
}


def get_disk_usage() -> dict:
    """Return disk usage for key mount points."""
    results = []
    seen_devices = set()

    for label, path in MOUNT_POINTS.items():
        if not os.path.exists(path):
            continue
        try:
            st = os.statvfs(path)
            # Skip duplicate filesystems (same device)
            device_id = st.f_fsid if hasattr(st, "f_fsid") else 0
            if device_id in seen_devices and device_id != 0:
                continue
            seen_devices.add(device_id)

            total = st.f_frsize * st.f_blocks
            free = st.f_frsize * st.f_bavail
            used = total - free
            pct = round((used / total) * 100, 1) if total > 0 else 0

            results.append(
                {
                    "mount": label,
                    "path": path,
                    "total_gb": round(total / 1024 / 1024 / 1024, 1),
                    "used_gb": round(used / 1024 / 1024 / 1024, 1),
                    "free_gb": round(free / 1024 / 1024 / 1024, 1),
                    "percent": pct,
                }
            )
        except OSError:
            continue

    return {"items": results}
