"""Contract v1 JSON types; annotations do not replace runtime validation."""
from dataclasses import dataclass
from typing import Any, Literal, TypedDict


class Anchor(TypedDict):
    start: int
    end: int


class Evidence(TypedDict):
    evidence_id: str
    start: int
    end: int
    evidence_text: str


class EvidenceLink(TypedDict):
    evidence_id: str
    supports: list[str]


class Source(TypedDict):
    source_id: str
    reference: str
    kind: Literal['production', 'test_fixture', 'unverified']
    review_status: Literal['approved', 'test_only', 'unreviewed']
    text: str
    text_sha256: str


class RetrievalBinding(TypedDict):
    source_id: str
    recipe_id: str
    catalog_sha256: str


class ModelIdentity(TypedDict):
    identifier: str
    digest: str | None


class Provenance(TypedDict):
    parser_id: str
    parser_kind: Literal['model', 'manual', 'legacy_import', 'design_example']
    model: ModelIdentity | None
    prompt_sha256: str | None
    raw_output_sha256: str | None
    settings: dict[str, Any]


class Ingredient(TypedDict):
    ingredient_id: str
    anchor: Anchor
    ingredient_name: str
    quantity: int | float | None
    quantity_raw: str | None
    quantity_state: Literal['exact', 'vague', 'missing', 'unparsed']
    quantity_rule: Literal['numeric_literal', 'half_literal'] | None
    unit: str | None
    optional: bool
    optional_state: Literal['explicit_optional', 'explicit_required', 'not_marked', 'unresolved']
    uncertainty: bool
    evidence_links: list[EvidenceLink]


class PreparationStep(TypedDict):
    step_id: str
    anchor: Anchor
    action_raw: str
    normalized_action: Literal['mix', 'slice', 'add'] | None
    normalization_rule: Literal['v1.zh.mix', 'v1.zh.slice', 'v1.zh.add'] | None
    ingredient_ids: list[str]
    target_status: Literal['resolved', 'partial', 'unresolved']
    uncertainty: bool
    evidence_links: list[EvidenceLink]


class Issue(TypedDict):
    code: str
    severity: Literal['info', 'warning', 'error']
    path: str
    message: str


class RecipeParsingResult(TypedDict):
    contract: Literal['foodcal.recipe_parsing']
    schema_version: Literal[1]
    input_id: str
    status: Literal['parsed', 'partial', 'unavailable', 'failed']
    review_status: Literal['unreviewed', 'needs_review', 'approved']
    source: Source | None
    retrieval_binding: RetrievalBinding | None
    provenance: Provenance
    ingredients: list[Ingredient]
    preparation_steps: list[PreparationStep]
    evidence: list[Evidence]
    issues: list[Issue]


@dataclass(frozen=True)
class ValidationError:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[ValidationError, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


class ContractValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__('; '.join(f'{e.path or "/"}: {e.code}: {e.message}' for e in report.errors))
