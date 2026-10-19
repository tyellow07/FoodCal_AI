"""Private pure state fold and fail-closed public boundary; no semantic review."""
from copy import deepcopy

from .review_types import AggregateDecision


def _aggregate_validated(results) -> AggregateDecision:
    """Internal only: caller must have completed full evidence validation first."""
    decisions = {}
    reasons = []
    for domain in ('identity', 'basis'):
        rows = [item for item in results if item['domain'] == domain]
        states = {item['status'] for item in rows}
        if 'mismatch' in states:
            decisions[domain] = 'incompatible'
        elif 'unresolved' in states:
            decisions[domain] = 'needs_review'
        else:
            decisions[domain] = 'compatible'
        reasons.extend(item['reason_code'] for item in rows if item['status'] in ('mismatch', 'unresolved'))
    return {'identity_decision': decisions['identity'], 'basis_decision': decisions['basis'],
            'input_integrity': 'verified', 'blocking_reasons': list(dict.fromkeys(reasons)),
            'attribute_results': deepcopy(results), 'usage': 'diagnostic/test_only', 'nutrition_eligible': False}


def aggregate_review(review_input, attribute_results, *, context=None, trust_anchor=None) -> AggregateDecision:
    """Always validate; no user-provided valid/approved flag can bypass the boundary."""
    from .review_validation import validate_review
    input_snapshot, result_snapshot = deepcopy(review_input), deepcopy(attribute_results)
    report = validate_review(input_snapshot, result_snapshot, context=context, trust_anchor=trust_anchor)
    if not report.valid:
        return {'identity_decision': 'invalid', 'basis_decision': 'invalid',
                'input_integrity': 'invalid', 'blocking_reasons': list(dict.fromkeys(i.code for i in report.issues)),
                'attribute_results': [], 'usage': 'diagnostic/test_only', 'nutrition_eligible': False}
    return _aggregate_validated(result_snapshot)
