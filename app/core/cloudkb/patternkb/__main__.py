"""`python -m patternkb` 진입점."""

from __future__ import annotations

import sys

from app.core.cloudkb.patternkb.cli import main

if __name__ == "__main__":
    sys.exit(main())
