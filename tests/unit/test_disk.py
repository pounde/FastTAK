"""Tests for app.api.health.disk — disk usage monitoring."""

from unittest.mock import MagicMock, patch


class TestGetDiskUsage:
    def _make_statvfs(self, total_gb=100, used_pct=50):
        """Create a fake statvfs result."""
        block_size = 4096
        total_blocks = int((total_gb * 1024 * 1024 * 1024) / block_size)
        avail_blocks = int(total_blocks * (1 - used_pct / 100))
        result = MagicMock()
        result.f_frsize = block_size
        result.f_blocks = total_blocks
        result.f_bavail = avail_blocks
        result.f_fsid = 12345
        return result

    @patch("app.api.health.disk.os.path.exists", return_value=True)
    @patch("app.api.health.disk.os.statvfs")
    def test_returns_usage(self, mock_statvfs, mock_exists):
        mock_statvfs.return_value = self._make_statvfs(total_gb=100, used_pct=50)

        from app.api.health.disk import get_disk_usage

        result = get_disk_usage()
        assert "items" in result
        assert len(result["items"]) >= 1
        assert "status" not in result["items"][0]
        assert result["items"][0]["percent"] > 0

    @patch("app.api.health.disk.os.path.exists", return_value=False)
    def test_skips_nonexistent_paths(self, mock_exists):
        from app.api.health.disk import get_disk_usage

        result = get_disk_usage()
        assert result == {"items": []}
