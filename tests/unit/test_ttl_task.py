"""Tests for TTL enforcement scheduler task."""

import time
from unittest.mock import MagicMock


class TestTtlEnforcement:
    def test_deactivates_expired_active_user(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "pk": 1,
                "username": "tempuser",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 3600,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_tak.revoke_all_user_certs.return_value = True

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_called_once_with(1)
        mock_tak.revoke_all_user_certs.assert_called_once_with("tempuser")
        mock_ak.mark_certs_revoked.assert_called_once_with(1)

    def test_reconciles_already_deactivated_user(self):
        """User deactivated but certs not yet revoked."""
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "pk": 2,
                "username": "olduser",
                "is_active": False,
                "attributes": {
                    "fastak_expires": int(time.time()) - 86400,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_tak.revoke_all_user_certs.return_value = True

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_not_called()  # Already inactive
        mock_tak.revoke_all_user_certs.assert_called_once_with("olduser")
        mock_ak.mark_certs_revoked.assert_called_once_with(2)

    def test_skips_marking_on_revocation_failure(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "pk": 3,
                "username": "failuser",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_tak.revoke_all_user_certs.return_value = False

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_called_once_with(3)
        mock_ak.mark_certs_revoked.assert_not_called()

    def test_continues_on_individual_user_error(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "pk": 4,
                "username": "error_user",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
            {
                "pk": 5,
                "username": "ok_user",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_ak.deactivate_user.side_effect = [Exception("API error"), None]
        mock_tak.revoke_all_user_certs.return_value = True

        _check_user_expiry(mock_ak, mock_tak)

        # Should still process second user
        assert mock_ak.deactivate_user.call_count == 2
        # ok_user's certs should be revoked
        mock_tak.revoke_all_user_certs.assert_called_with("ok_user")
        mock_ak.mark_certs_revoked.assert_called_once_with(5)

    def test_noop_when_no_expired_users(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = []

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_not_called()
        mock_tak.revoke_all_user_certs.assert_not_called()
