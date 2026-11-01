"""Pure analytical ER aggregation adapted from desdeo-brb's analytical core.

Copyright (c) 2026 Giovanni Misitano

MIT License
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

Adapted equation: compute_combined_belief_degrees, commit
6744efbabeb27fe15dc52c516d86c929030b230e. See THIRD_PARTY_NOTICES.md.
Weights must already be nonnegative combination shares summing to one: normalised
attribute importance weights or externally calculated normalised rule activation
weights, supplied by the caller. The formula is shared by both uses. Rows
contain assigned grades only and may sum to less than one. No automatic weight
normalisation, averaging, extra ignorance grade or incomplete-row scaling occurs.
"""
from decimal import Decimal, InvalidOperation, localcontext
from math import prod

ENGINE_VERSION = "analytical-importance-er-v1"
METHOD_ID = "analytical_er_importance_normalized_v1"
UPSTREAM = {"repository": "https://github.com/gialmisi/desdeo-brb",
            "commit": "6744efbabeb27fe15dc52c516d86c929030b230e",
            "file": "src/desdeo_brb/inference.py", "function": "compute_combined_belief_degrees",
            "blob_sha": "e5b2cce81336709f5914d772da9bbcf470fabd8d", "license": "MIT"}


class ERInputError(ValueError):
    pass


class ERUndefinedError(ArithmeticError):
    pass


def combine_er(belief_matrix, combination_weights, *, tolerance=1e-12):
    """Return assigned_beliefs, unassigned_belief and diagnostics for one frame.

    Cleanup is limited to boundary excursions <= tolerance, recorded explicitly.
    A row exceeding one by only that amount loses its tiny excess from its largest
    component. Incomplete rows are never increased or scaled. Weights are never
    scaled; their supplied sum must already be one within tolerance. Zero-weight
    rows are ignored algebraically, but must still be valid input assessments.
    """
    with localcontext() as context:
        context.prec = 80
        def number(value, label):
            if isinstance(value, (bool, str, bytes)):
                raise ERInputError(f"{label}: a finite numeric value is required.")
            try:
                result = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise ERInputError(f"{label}: a finite numeric value is required.") from None
            if not result.is_finite():
                raise ERInputError(f"{label}: a finite numeric value is required.")
            return result
        tol = number(tolerance, "tolerance")
        if not 0 < tol <= Decimal("1e-9"):
            raise ERInputError("Floating-point cleanup tolerance must be in (0,1e-9].")
        cleanups = []
        def bounded(value, label):
            result = number(value, label)
            if result < 0 or result > 1:
                boundary = Decimal(0) if result < 0 else Decimal(1)
                if abs(result - boundary) > tol:
                    raise ERInputError(f"{label}: value outside [0,1].")
                cleanups.append({"location": label, "from": str(result), "to": str(boundary)})
                return boundary
            return result
        try:
            raw_rows = [list(row) for row in belief_matrix]
            raw_weights = list(combination_weights)
        except TypeError:
            raise ERInputError("Provide a rectangular matrix and one weight per row.") from None
        if not raw_rows or not raw_rows[0] or any(len(row) != len(raw_rows[0]) for row in raw_rows):
            raise ERInputError("The belief matrix must be non-empty and rectangular.")
        if len(raw_rows) != len(raw_weights):
            raise ERInputError("The number of weights must match the matrix rows.")
        rows = []
        for i, row in enumerate(raw_rows):
            values = [bounded(value, f"belief_matrix[{i}][{j}]") for j, value in enumerate(row)]
            total = sum(values)
            if total > 1:
                if total - 1 > tol:
                    raise ERInputError(f"Row {i} assigned total exceeds one.")
                j = max(range(len(values)), key=values.__getitem__)
                excess = total - 1
                values[j] -= excess
                cleanups.append({"location": f"row[{i}]", "assigned_excess_removed": str(excess), "component": j})
            rows.append(values)
        weights = [bounded(value, f"combination_weights[{i}]") for i, value in enumerate(raw_weights)]
        total_weight = sum(weights)
        if total_weight <= 0 or abs(total_weight - 1) > tol:
            raise ERInputError("Combination weights must already sum to one; the core does not normalise arbitrary weights.")
        active = [i for i, weight in enumerate(weights) if weight > 0]
        n = len(rows[0])
        row_sums = [sum(row) for row in rows]
        residual_terms = [1 - weights[i] * row_sums[i] for i in active]
        r = prod(residual_terms)
        q = prod(1 - weights[i] for i in active)
        c = [prod(weights[i] * rows[i][j] + residual_terms[k] for k, i in enumerate(active)) for j in range(n)]
        base = sum(c) - (n - 1) * r
        denominator = base - q
        if base <= 0 or denominator <= 0:
            raise ERUndefinedError("Undefined ER combination: nonpositive conflict/removal denominator; no epsilon was added.")
        assigned = [(value - r) / denominator for value in c]
        unassigned = (r - q) / denominator
        if any(value < -tol or value > 1 + tol for value in assigned + [unassigned]) or abs(sum(assigned) + unassigned - 1) > tol:
            raise ERUndefinedError("Combined ER mass accounting failed; no clipping or normalisation applied.")
        assigned = [bounded(value, f"output[{i}]") for i, value in enumerate(assigned)]
        unassigned = bounded(unassigned, "output.unassigned")
        return {"assigned_beliefs": [float(value) for value in assigned], "unassigned_belief": float(unassigned),
                "diagnostics": {"engine_version": ENGINE_VERSION, "method": METHOD_ID,
                                "weight_convention": "nonnegative combination shares summing to one",
                                "supplied_weight_sum": float(total_weight), "active_input_indices": active,
                                "zero_weight_indices": [i for i, weight in enumerate(weights) if weight == 0],
                                "input_assigned_totals": [float(value) for value in row_sums],
                                "products_by_grade": [float(value) for value in c], "residual_product": float(r),
                                "importance_product": float(q), "denominator": float(denominator),
                                "conflict_mass": float(bounded(1 - base, "diagnostics.conflict_mass")), "decimal_precision": 80,
                                "cleanup_tolerance": float(tol), "cleanups": cleanups}}
