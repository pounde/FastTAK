"""Tests for app.api.health.updates — version checking."""

from unittest.mock import MagicMock, patch

from app.api.health.updates import _extract_version


class TestExtractVersion:
    def test_strips_v_prefix(self):
        assert _extract_version("v1.2.3") == "1.2.3"

    def test_strips_version_prefix(self):
        assert _extract_version("version/2026.2.1") == "2026.2.1"

    def test_no_prefix(self):
        assert _extract_version("4.1.7") == "4.1.7"

    def test_empty_string(self):
        assert _extract_version("") == ""

    def test_only_strips_first_match(self):
        assert _extract_version("version/v1.2.3") == "v1.2.3"


class TestCheckUpdates:
    def test_returns_items_with_mocked_httpx(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.updates.settings", mock_settings)
        mock_settings.lldap_version = "0.6.1"
        mock_settings.mediamtx_version = "1.15.5"
        mock_settings.nodered_version = "4.1"
        mock_settings.tak_portal_version = "1.2.53"

        from app.api.health import updates

        monkeypatch.setattr(
            updates,
            "COMPONENTS",
            {
                "lldap": ("lldap/lldap", "0.6.1"),
            },
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tag_name": "v2026.3.0",
            "html_url": "https://example.com",
        }

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("app.api.health.updates.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = updates.check_updates()
            assert "items" in result
            assert len(result["items"]) > 0
            assert result["items"][0]["update_available"] is True

    def test_handles_api_error(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.updates.settings", mock_settings)

        from app.api.health import updates

        monkeypatch.setattr(
            updates,
            "COMPONENTS",
            {
                "lldap": ("lldap/lldap", "0.6.1"),
            },
        )

        mock_response = MagicMock()
        mock_response.status_code = 403

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("app.api.health.updates.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = updates.check_updates()
            assert "items" in result
            assert all(r.get("error") or not r["update_available"] for r in result["items"])
