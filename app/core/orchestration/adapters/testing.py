"""Compatibility import for the retired orchestration testing adapter.

Application testing is now owned by :mod:`app.testing.runtime.adapter`.  This
module remains only so legacy orchestration experiments can be imported while
they are being removed; it does not provide a second execution path.
"""

from app.testing.runtime.adapter import TestingAdapter

__all__ = ["TestingAdapter"]
