"""Allow `python -m app.backup` invocation."""

import sys

from app.backup.cli import main

if __name__ == "__main__":
    sys.exit(main())
