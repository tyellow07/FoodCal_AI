# Recipe Retrieval Adapter

`retrieve_recipes(detection_result)` and `RecipeRetriever.retrieve(...)` consume
the schema-1 Food Detection result. They return traceable **recipe priors** for
each detected object. No LLM, network service, supplier SDK, new dependency,
model inference, portion estimate or calorie calculation is involved.

## Usage

Run from the project root in the existing Python environment:

```python
from pathlib import Path
from src.detection import detect_food
from src.recipes import retrieve_recipes, RecipeRetriever

image = Path('data/roboflow_taiwanese_food_detection/valid/images') / (
    '2583_jpg.rf.adb6bef6999f8e2de5eb51fb3daa225b.jpg'
)
detected = detect_food(image)
recipes = retrieve_recipes(detected)
# No production catalog is configured: objects are unavailable, not invented.

# Explicit test-only example; these are synthetic placeholders, not recipes.
retriever = RecipeRetriever('tests/fixtures/recipe_catalog.json',
                            include_test_fixtures=True)
example = retriever.retrieve(detected, limit=5)
for obj in example['objects']:
    print(obj['detection']['class_name'], obj['status'], obj['candidates'])
```

For repeated calls retain a `RecipeRetriever` instance: it reads, validates and
hashes one catalog snapshot at construction. Reconstruct it to reload changed
catalog data. The convenience function constructs a fresh adapter per call.
Catalog paths are local only; the adapter never downloads a URL.

## Catalog contract

A UTF JSON file contains `schema_version: 1` and a `recipes` list. Each record
must contain:

| Field | Required value |
| --- | --- |
| `recipe_id`, `name` | Nonempty strings, copied without rewriting |
| `food_class` | `{class_id, class_name}` matching the dataset's 15-class YAML |
| `source.source_id` | Nonempty source identifier |
| `source.reference` | Nonempty source document/path/URL and record locator |
| `source.kind` | `production` or `test_fixture` |
| `source.review_status` | `approved` for production, `test_only` for fixtures |
| `ingredients`, `cooking_methods` | Optional lists of nonempty strings |

Recipe identity is `(source_id, recipe_id)`; duplicates invalidate the catalog.
The authoritative mapping is the existing `foodcal_data.yaml`. Missing optional
content stays absent, rather than being generated. Other record fields are not
used or emitted. The included fixtures deliberately use `TEST_ONLY` tokens and
cover cold_noodle and turkey_rice; they contain no real cooking instructions.

Production data must be curated and approved by the caller before supplying the
catalog. `approved` is a supplied review declaration, not independent factual
verification. The adapter validates provenance metadata and structure; it cannot
authenticate an external reference, inspect licenses, or establish recipe truth.
A source URL is retained as text and never fetched. There is currently **no
configured or verified production recipe source in this project**. Neither the
YOLO class labels nor saved detector predictions constitute recipe data.

Fail-closed policy: a malformed record, invalid class mapping, duplicate recipe
identity or invalid/unapproved source makes the entire catalog unavailable.
No partly validated catalog is used. Test fixtures are excluded by default and
require explicit `include_test_fixtures=True`, even if production records coexist.

## Output and matching

Top-level result: `schema_version`, `status`, original `image`, `detection_model`,
`detection_inference_settings`, `detection_limitations`, `catalog` path/SHA-256/
load status/reason, `include_test_fixtures`, `objects`, and retrieval `limitations`.

Each object preserves its original `detection` (including detection_id,
confidence, bounding_box and model_identifier), has an `available`/`unavailable`
status and reason, and contains a `candidates` list. Repeated classes and
overlapping boxes remain separate objects. The input is not modified.

Candidates contain recipe ID/name/class/source, optional ingredients and cooking
methods, `retrieval_score: 1.0`, `score_method: exact_class_id_and_name_match`, and
`interpretation: recipe_prior`. This score encodes exact taxonomy agreement
only. It is independent of detection confidence, recipe quality, actual scene
ingredients and recipe probability. Candidates tie on score and are ordered
deterministically by `(source_id, recipe_id)`, limited separately per object.
There is no semantic ranking, confidence tuning or implicit relabeling.

| Aggregate status | Meaning |
| --- | --- |
| `available` | Every detected object has at least one eligible candidate |
| `partial` | Some objects have candidates and some are unavailable |
| `unavailable` | Objects exist but none has eligible candidates |
| `no_detections` | Input detection list is empty; objects is an empty list |

Unavailable reasons include `no_catalog_configured`, `catalog_unreadable`,
`invalid_catalog_json`, `invalid_catalog_schema`,
`invalid_recipe_identity_or_class`, `invalid_or_unapproved_source`,
`duplicate_recipe_identity`, `invalid_recipe_optional_fields`,
`test_fixtures_disabled` and `no_matching_recipe`.
Malformed Detection input raises `InvalidDetectionResultError`; an invalid
positive-integer limit or class configuration raises an explicit configuration
error. Invalid Detection input is not disguised as absent recipe data.

No detections does not prove absence of food; unavailable does not prove no recipe
exists. Recipe candidates do not establish actual ingredients, cooking methods,
grams, portions or calories. Overlapping dish/component detections must not be
summed automatically. Known baseline misclassifications remain visible and can
retrieve the wrong class's recipe priors; retrieval does not correct the model.

## Tests and next extension

```powershell
python -B -m unittest tests.test_recipe_retrieval -v
python -B -m unittest discover -s tests -t . -p "test*.py" -v
```

Tests use explicit synthetic fixtures, an injected detector backend, and replay
of the existing real validation smoke results; no new model inference is needed.
Production-metadata tests use ephemeral synthetic records to check the contract,
not to supply a real source. Existing reports and review records stay unchanged.

Next, source-approved recipe documents can be parsed in a separate Gemma
experiment and reviewed before conversion to this catalog format. Preserve
source locator and recipe identity throughout parsing. Retrieval stays independent
of the parser/LLM and accepts no unsupported factual completion. Provider or
ranking changes can be made within the same documented candidate contract when
real requirements/data justify them; no provider hierarchy is introduced now.
