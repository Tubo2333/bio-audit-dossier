"""DP-03 differential-analysis method decision-point vocabulary.

No public result model, version fields, or status semantics are added: the
slice reuses the shared ledger, the `DP01FilteringResult` form and the
five-field version snapshot (authorization decisions 3-5).
"""

from __future__ import annotations

from types import MappingProxyType

DP03_SCOPE = "DP-03/differential_method"

# Shared five-key `FilteringDeclaration` shape; mapping convention for the
# differential-analysis method context:
DP03_DECLARATION_MAPPING = MappingProxyType({
    "method": "differential-analysis method name (e.g. deseq2, edger, limma-voom, ...)",
    "threshold": "no natural hard threshold in method choice; 0 is the documented placeholder",
    "sample_rule": "design/reference convention (e.g. paired | unpaired | none)",
    "unit": "gene | transcript",
    "source": "declared",
})

# Competing method declarations travel in evidence_observations as declared
# items whose value is another method name; the adapter preserves them as a
# conflicted result with no arbitrary winner (T-01 §7 / P-01 §4.3).
__all__ = ["DP03_SCOPE", "DP03_DECLARATION_MAPPING"]