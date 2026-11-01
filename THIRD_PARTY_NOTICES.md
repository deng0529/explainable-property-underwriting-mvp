# Third-party notices

## desdeo-brb analytical ER equation

- Upstream: https://github.com/gialmisi/desdeo-brb
- Verified source commit: `6744efbabeb27fe15dc52c516d86c929030b230e`
- File: [src/desdeo_brb/inference.py at that commit](https://github.com/gialmisi/desdeo-brb/blob/6744efbabeb27fe15dc52c516d86c929030b230e/src/desdeo_brb/inference.py)
- Function adapted: `compute_combined_belief_degrees`, specifically its analytical
  product numerator and denominator for one output grade frame.
- Verified Git blob SHA: `e5b2cce81336709f5914d772da9bbcf470fabd8d`.
- Licence: [MIT at the same commit](https://github.com/gialmisi/desdeo-brb/blob/6744efbabeb27fe15dc52c516d86c929030b230e/LICENSE),
  licence blob `88d4850483042032f88e7c674bad5c1701c713f3`.
- Source/licence inspected on 1 November 2026. No upstream package/backends were installed.

Local adaptation is `combine_er` in `underwriting/models/er.py`. The application supplies normalized
attribute-importance shares for group aggregation. A separate BRB aggregation adapter
passes already calculated normalised rule activations into the same function;
there is no second ER formula. Only the analytical
combination equation is reused from upstream. Matching/activation is implemented
locally according to the user's explicit RIMER-style specification. No upstream
training, utility projection, optimisation, JAX or Pyomo code is included.

Modifications: standard-library 80-digit Decimal products instead of sample-batched
NumPy/log products; strict matrix/mass/weight validation; explicit sum-one combination
convention; explicit zero terms and zero-weight rows; separate unassigned output;
diagnostics/versioning; documented tolerance-only boundary cleanup; undefined-case
errors instead of tiny-value floors or a denominator epsilon. The independent exact
Fraction recursive implementation exists only in tests, not as an alternative engine.

Applicability, limitations and the derivation from importance/incompleteness mass
splitting are documented in [docs/er-aggregation.md](docs/er-aggregation.md). The function
is not claimed to implement every ER variant or the 2013 weight/reliability rule.

### Matching and activation verification

Matching and activation were also checked against `input_transform` and
`compute_activation_weights` in the same verified file/blob/commit. The local
`underwriting.models.brb.infer_brb` uses the conventional antecedent-state lookup and max-normalised
weighted-product activation. Configured per-input weights are shared across rules;
upstream also permits rule-specific attribute-weight vectors. The extended BRB
distance-matching function is not used.

Deliberate differences: complete ER input distributions bypass upstream's scalar
piecewise-linear interpolation; no output utility/scalar projection is applied;
all-zero input weights are rejected and no matching rules returns no prediction.
Upstream activation divides by total strength plus 1e-12; the local implementation
uses exact conceptual normalisation with log-scaled products and no epsilon, retaining
explicit zero/no-match handling. Tests compare a separate scalar reference of the
inspected activation equations to local equal/unequal/zero-weight cases within
2e-12 in well-conditioned examples. Upstream ER epsilon/tiny-floor differences
remain as documented above. The local numerical method was not changed by this
verification. Source metadata and full traces remain in code/records, not the UI.

### Upstream MIT licence (retained)

MIT License

Copyright (c) 2026 Giovanni Misitano

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
