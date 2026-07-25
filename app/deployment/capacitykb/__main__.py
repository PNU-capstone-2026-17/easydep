"""`python -m capacitykb` 진입점."""

from __future__ import annotations

from capacitykb.cli import main
from kbcommon.console import use_utf8

use_utf8()
raise SystemExit(main())
