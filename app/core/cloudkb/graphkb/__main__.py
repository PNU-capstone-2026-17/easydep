"""`python -m graphkb` 진입점."""

from __future__ import annotations

from app.core.cloudkb.graphkb.cli import main
from app.core.cloudkb.kbcommon.console import use_utf8

use_utf8()
raise SystemExit(main())
