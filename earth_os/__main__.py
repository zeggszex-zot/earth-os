"""Entry point: ``python -m earth_os``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
