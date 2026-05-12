"""Exception types raised by the backup module."""


class BackupError(Exception):
    """Base class for backup failures."""


class BackupAlreadyRunning(BackupError):
    """Raised when an attempt to take the lock fails because another run holds it."""


class KeyNotFoundError(BackupError):
    """Raised when the age identity has not been generated yet."""
