"""`python -m perfkb` 진입점."""

from __future__ import annotations

from app.deployment.kbcommon.console import use_utf8
from app.deployment.perfkb.cli import main

use_utf8()
raise SystemExit(main())
