"""An explicit fixture adapter; no text extraction, model or network calls."""
import json
from pathlib import Path

from .types import ContractValidationError, RecipeParsingResult
from .validator import decimal_literal, load_recipe_parsing


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[2] / 'docs/examples/recipe_parsing_contract_v1_cases.json'


class FixtureLoadError(ValueError):
    """Mock configuration is unreadable, invalid, duplicated or not test-only."""


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f'Non-finite JSON number: {value}')


def diagnostic_result(input_id: str, *, status: str, code: str, message: str) -> RecipeParsingResult:
    """Create an empty mock diagnostic, not a sanitized version of bad output."""
    if status not in ('unavailable', 'failed'):
        raise ValueError('Diagnostic status must be unavailable or failed')
    return load_recipe_parsing({
        'contract': 'foodcal.recipe_parsing', 'schema_version': 1,
        'input_id': input_id, 'status': status, 'review_status': 'needs_review',
        'source': None, 'retrieval_binding': None,
        'provenance': {'parser_id': 'foodcal-mock-recipe-parsing-v1',
                       'parser_kind': 'design_example', 'model': None,
                       'prompt_sha256': None, 'raw_output_sha256': None, 'settings': {}},
        'ingredients': [], 'preparation_steps': [], 'evidence': [],
        'issues': [{'code': code, 'severity': 'error' if status == 'failed' else 'info',
                    'path': '', 'message': message}],
    })


class MockRecipeParsingAdapter:
    """Load once and replay exact contract design examples by complete input_id.

    Does not accept arbitrary recipe text or attach fabricated Retrieval bindings.
    Only unreviewed test-fixture design examples are permitted. Unknown input IDs
    return unavailable, which is not a claim that a recipe has no ingredients.
    """
    is_mock = True

    def __init__(self, fixture_path=DEFAULT_FIXTURE_PATH, *, numeric_parser=decimal_literal):
        self._cases = {}
        self._numeric_parser = numeric_parser
        try:
            cases = json.loads(Path(fixture_path).read_text(encoding='utf-8'),
                               object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
            if not isinstance(cases, list) or not cases:
                raise ValueError('Expected nonempty array of design examples')
            for case in cases:
                result = load_recipe_parsing(case, numeric_parser=numeric_parser)
                if (result['source'] is None or result['source']['kind'] != 'test_fixture' or
                        result['source']['review_status'] != 'test_only' or
                        result['review_status'] != 'unreviewed' or
                        result['provenance']['parser_kind'] != 'design_example' or
                        result['retrieval_binding'] is not None):
                    raise ValueError('Mock accepts only unreviewed, unbound test-fixture design examples')
                identity = result['input_id']
                if identity in self._cases:
                    raise ValueError(f'Duplicate input_id: {identity}')
                self._cases[identity] = result
        except (OSError, UnicodeError, ValueError, ContractValidationError) as error:
            raise FixtureLoadError(f'Cannot load mock fixtures: {error}') from error

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(self._cases)

    def parse(self, input_id: str) -> RecipeParsingResult:
        if not isinstance(input_id, str) or not input_id.strip():
            raise ValueError('input_id must be a nonempty string')
        if input_id not in self._cases:
            return diagnostic_result(input_id, status='unavailable', code='coverage_unknown',
                                     message='No mock fixture is configured for this input_id; no extraction was attempted.')
        return load_recipe_parsing(self._cases[input_id], numeric_parser=self._numeric_parser)
