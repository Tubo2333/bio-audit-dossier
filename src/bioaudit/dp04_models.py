"""DP-04 multiple-testing correction decision-point vocabulary.

No public result model, version fields, or status semantics are added: the
slice reuses the shared ledger, the `DP01FilteringResult` form and the
five-field version snapshot (authorization decisions 3-5).
"""

from __future__ import annotations

from types import MappingProxyType

DP04_SCOPE = "DP-04/multiple_testing_correction"

# Shared five-key `FilteringDeclaration` shape; mapping convention for the
# multiple-testing correction context. `none`/`no_correction` is an explicit
# declared method (authorization decision 7): no default correction is ever
# assumed, and an explicit no-correction choice is auditable when evidenced.
DP04_DECLARATION_MAPPING = MappingProxyType({
    "method": "correction method name (e.g. BH, bonferroni, storey, ...) or explicit none/no_correction",
    "threshold": "no natural hard threshold in correction choice; 0 is the documented placeholder",
    "sample_rule": "design/number-of-tests convention (e.g. many_tests | single_comparison | none)",
    "unit": "gene | transcript",
    "source": "declared",
})

__all__ = ["DP04_SCOPE", "DP04_DECLARATION_MAPPING"]