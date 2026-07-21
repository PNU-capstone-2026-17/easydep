"""`python -m perfkb` 진입점."""

from __future__ import annotations

from perfkb.cli import main
from kbcommon.console import use_utf8

use_utf8()
raise SystemExit(main())
