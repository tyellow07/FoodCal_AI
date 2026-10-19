"""Model-output boundary: compare declarations with caller-controlled context.

The context must come from the system/caller, never from the model response.
Comparison is not authentication or evidence of source licensing/semantic truth.
"""
from copy import deepcopy
from typing import cast

from .types import (
    ContractValidationError, Provenance, RecipeParsingResult, RetrievalBinding,
    Source, ValidationError, ValidationReport,
)
from .validator import decimal_literal, validate_recipe_parsing


def validate_model_recipe_parsing(
    value: object, *, expected_input_id: str, expected_source: Source | None,
    expected_provenance: Provenance, expected_binding: RetrievalBinding | None = None,
    numeric_parser=decimal_literal,
) -> ValidationReport:
    """Reject model promotion or replacement of the supplied source snapshot.

    First enforce the existing contract. Then compare full source/provenance/
    binding and input identity with independently supplied caller context. The
    general validator still accepts externally reviewed archival results; this
    entry point is for new, untrusted model output, not human-approved archives.
    """
    report = validate_recipe_parsing(value, numeric_parser=numeric_parser)
    if not report.valid:
        return report
    result = cast(RecipeParsingResult, value)
    errors = []
    if result['review_status'] == 'approved':
        errors.append(ValidationError('/review_status', 'untrusted_approval',
                                      'Model output cannot grant human approval'))
    if not isinstance(expected_provenance, dict) or expected_provenance.get('parser_kind') != 'model':
        errors.append(ValidationError('/provenance/parser_kind', 'trust_context',
                                      'Caller context must identify a model parser'))
    comparisons = (
        ('input_id', expected_input_id, 'input_identity_mismatch'),
        ('source', expected_source, 'source_mismatch'),
        ('provenance', expected_provenance, 'provenance_mismatch'),
        ('retrieval_binding', expected_binding, 'binding_mismatch'),
    )
    for field, expected, code in comparisons:
        actual = result[field]
        if isinstance(actual, dict) and isinstance(expected, dict):
            for key in sorted(actual.keys() | expected.keys(), key=str):
                if key not in actual or key not in expected or actual[key] != expected[key]:
                    token = str(key).replace('~', '~0').replace('/', '~1')
                    errors.append(ValidationError(f'/{field}/{token}', code,
                                                  'Model declaration differs from caller-controlled context'))
        elif actual != expected:
            errors.append(ValidationError('/' + field, code,
                                          'Model declaration differs from caller-controlled context'))
    return ValidationReport(tuple(errors))


def load_model_recipe_parsing(value: object, **context) -> RecipeParsingResult:
    """Validate at the model trust boundary and return a separate snapshot."""
    report = validate_model_recipe_parsing(value, **context)
    if not report.valid:
        raise ContractValidationError(report)
    return cast(RecipeParsingResult, deepcopy(value))
