#!/usr/bin/env python3
"""Compatibility wrapper for the full Paper 2 analysis."""
from __future__ import annotations

import pandas as pd

# pandas Series exposes ``between`` but DatetimeIndex does not on every supported
# release.  The analysis uses the method only for transparent date-window masks.
if not hasattr(pd.Index, "between"):
    def _index_between(self, left, right, inclusive="both"):
        if inclusive == "both":
            return (self >= left) & (self <= right)
        if inclusive == "left":
            return (self >= left) & (self < right)
        if inclusive == "right":
            return (self > left) & (self <= right)
        return (self > left) & (self < right)
    pd.Index.between = _index_between

import full_analysis

if __name__ == "__main__":
    full_analysis.main()
