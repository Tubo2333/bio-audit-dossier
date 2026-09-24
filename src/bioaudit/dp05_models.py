"""DP-05 significance/effect-size threshold decision-point vocabulary.

No public result model, version fields, or status semantics are added: the
slice reuses the shared ledger, the `DP01FilteringResult` form and the
five-field version snapshot (authorization decisions 3-5).
"""

from __future__ import annotations

from types import MappingProxyType

DP05_SCOPE = "DP-05/significance_threshold"

# Paired (significance + effect-size) thresholds live inside the shared
# five-key `FilteringDeclaration` (authorization decision 7, zero new input
# fields). Mapping convention:
#   method      = threshold-family name, e.g. "significance_0.05_effect_0.5"
#   threshold   = primary threshold value (number or string), e.g. 0.05
#   sample_rule = effect-size boundary / design convention, e.g. "effect_size_0.5"
#   unit        = gene | transcript
#   source      = declared
DP05_DECLARATION_MAPPING = MappingProxyType({
    "method": "threshold-family name encoding the paired significance/effect-size choice",
    "threshold": "primary threshold value (number or string)",
    "sample_rule": "effect-size boundary / design convention",
    "unit": "gene | transcript",
    "source": "declared",
})

# No conventional threshold is ever filled in by the system (P-01 §4.5) and a
# missing declaration fails closed; the adapter reads no threshold field as
# logic input — threshold values are declared payload only.
__all__ = ["DP05_SCOPE", "DP05_DECLARATION_MAPPING"]