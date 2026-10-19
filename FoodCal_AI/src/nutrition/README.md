# Offline Ingredient → Nutrition Mapping prototype

Contract: `docs/ingredient_nutrition_mapping_contract_v1.md`.
Types/API: `mapping_types.py`, `mapping.py`. Standard library only; no API/model calls.
This module does not change `NutritionDatabase`, portion estimation or the old pipeline.

```python
from src.recipe_parsing import MockRecipeParsingAdapter
from src.nutrition import MockNutritionMappingAdapter

parser = MockRecipeParsingAdapter()
parsed = parser.parse('case_01_clear_quantities')
# In production callers must obtain this independently of model output.
# Here the explicit design fixture is the caller-controlled source snapshot.
source_snapshot = parser.parse('case_01_clear_quantities')['source']
ingredient_id = parsed['ingredients'][0]['ingredient_id']
result = MockNutritionMappingAdapter().map(
    parsed,
    expected_source=source_snapshot,
    identity_assertions={ingredient_id: {
        'attributes': {
            'food': 'chicken', 'part': 'breast', 'state': 'raw',
            'quality': 'synthetic_unspecified_grade', 'weight_basis': 'edible_portion',
        },
        'evidence_reference': 'tests/fixtures/nutrition_mapping.json#caller-test-identity',
        'source_text_sha256': source_snapshot['text_sha256'],
    }},
)
item = result['ingredients'][0]
assert item['mapping']['status'] == 'matched'
assert item['mass']['mass_g'] == 200
assert item['qualification']['eligible_for_production'] is False
```

The raw/breast/quality assertion above is **synthetic test context**, not information
inferred from the recipe name or image. Omit assertions when identity is unknown:
even a single exact-name candidate remains ambiguous. Unknown attributes use null.
Assertions are scoped to both ingredient ID and the source text hash.

Output independently records entry mapping, declared mass and usage qualification.
`matched` selects an entry, not an energy estimate. g/克 are copied as recipe-declared
mass, without edible-fraction, state, serving or cooking conversion. Other units
and missing/vague quantities remain unavailable; quantity_raw and evidence survive.
The four mock entries contain no nutrient numbers, remain synthetic/test_only,
and cannot become production-eligible even if a caller supplies approved metadata.

Source equality only checks snapshot integrity, not source authentication. Model
output must already pass the existing Parsing Trust Boundary before this call.
Valid `partial` results remain useful for per-ingredient diagnostics, with their
original parsing status and issues preserved. A global invalid input produces
`failed` and no entities; local assertion errors preserve other ingredient results.

Tests: `python -B -m unittest tests.test_nutrition_mapping -v`.
