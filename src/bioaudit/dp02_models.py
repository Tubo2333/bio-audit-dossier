"""DP-02 normalization decision-point vocabulary (shared ledger slice).

Scope and declaration-mapping conventions for the DP-02 vertical slice. No
public result model, no version fields, no status/lane semantics are added:
the slice reuses the DP-01 ledger, the `DP01FilteringResult` form and the
five-field version snapshot (authorization decisions 3-5).
"""

from __future__ import annotations

from types import MappingProxyType

# Public scope label for DP-02 records/results.
DP02_SCOPE = "DP-02/normalization"

# The shared declaration keeps the five-key `FilteringDeclaration` shape so
# the persisted schema stays identical to DP-01. Mapping convention for the
# normalization context:
NORMALIZATION_DECLARATION_MAPPING = MappingProxyType({
    "method": "normalization method name (e.g. cpm, tpm, tmm, quantile, median_ratio, ...)",
    "threshold": "no natural hard threshold in normalization; 0 is the documented placeholder",
    "sample_rule": "design/reference convention (e.g. reference_samples | none)",
    "unit": "gene | transcript",
    "source": "declared",
})

# Deliberately NO method whitelist: a method label is never evidence and never
# a verdict (P-01 §4.2 fail-closed: the product does not treat a method label
# as evidence and does not choose the most common method).
__all__ = ["DP02_SCOPE", "NORMALIZATION_DECLARATION_MAPPING"]