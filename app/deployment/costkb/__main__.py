"""`python -m costkb` 진입점."""

from __future__ import annotations

from app.deployment.costkb.cli import main
from app.deployment.kbcommon.console import use_utf8

use_utf8()
raise SystemExit(main())
