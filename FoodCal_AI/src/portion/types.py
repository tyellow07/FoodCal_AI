"""Photo Portion Estimation Contract v0 JSON types; constructing them confers no trust."""
from typing import Literal, TypedDict

from src.recipe_parsing.types import Issue


class MassInterval(TypedDict):
    low: str  # exact non-negative decimal strings, grams
    high: str
    coverage: str  # nominal probability in (0, 1]


class EvidenceRef(TypedDict):
    evidence_type: Literal['ingredient_annotation', 'reviewed_prediction']
    evidence_id: str  # annotation_id or review_id of the bound visual evidence item
    ingredient_candidate: str
    evidence_sha256: str  # canonical JSON hash of the whole evidence item


class PortionMethod(TypedDict):
    kind: Literal['direct_weighing', 'calibrated_visual_estimate']
    method_id: str
    method_version: str


class Measurement(TypedDict):
    instrument_resolution_g: str
    evidence_ref: str
    measured_by: str


class CalibrationRef(TypedDict):
    calibration_id: str
    record_sha256: str


class Producer(TypedDict):
    kind: Literal['human', 'calibrated_method']
    ref: str


class PortionReview(TypedDict):
    status: Literal['not_requested', 'pending', 'accepted', 'rejected']
    reviewer_ref: str | None
    reviewed_at: str | None


class PortionEstimate(TypedDict):
    contract: Literal['foodcal.photo_portion_estimate']
    schema_version: Literal[0]
    estimate_id: str
    evidence_ref: EvidenceRef
    mass_kind: Literal['visible_mass_at_capture']
    mass_g: MassInterval
    method: PortionMethod
    measurement: Measurement | None
    calibration_ref: CalibrationRef | None
    producer: Producer
    review: PortionReview
    usage: Literal['diagnostic/test_only']


class CalibrationDataset(TypedDict):
    reference: str  # shareable locator, never a machine path
    sha256: str
    split: Literal['held_out_by_source_group']
    ground_truth: Literal['direct_weighing']
    sample_count: int
    source_group_count: int


class CoverageEvaluation(TypedDict):
    nominal: str
    covered_count: int


class ValidityScope(TypedDict):
    ingredient_candidates: list[str]
    mass_range_g: dict  # {'low': str, 'high': str}


class CalibrationApproval(TypedDict):
    status: Literal['not_requested', 'pending', 'approved', 'rejected']
    approver_ref: str | None
    approved_at: str | None


class CalibrationRecord(TypedDict):
    contract: Literal['foodcal.portion_calibration']
    schema_version: Literal[0]
    calibration_id: str
    method: PortionMethod
    status: Literal['draft', 'approved', 'revoked']
    dataset: CalibrationDataset
    coverage: CoverageEvaluation
    error: dict  # {'mae_g': str}
    validity_scope: ValidityScope
    producer_ref: str
    approval: CalibrationApproval
    usage: Literal['diagnostic/test_only']


class PortionItem(TypedDict):
    evidence: dict  # evidence_identity() of the visual evidence item
    image_region: dict | None
    status: Literal['estimated', 'unavailable']
    reason: str | None
    mass_kind: Literal['visible_mass_at_capture']
    mass_g: MassInterval | None
    estimate_id: str | None
    method: PortionMethod | None
    calibration_id: str | None
    ready_for_energy: bool
    blocking_reasons: list[str]
    issues: list[Issue]


class PortionAssessment(TypedDict):
    contract: Literal['foodcal.photo_portion_assessment']
    schema_version: Literal[0]
    usage: Literal['diagnostic/test_only']
    status: Literal['estimated', 'partial', 'unavailable']
    items: list[PortionItem]
    hidden_ingredients: list[dict]  # recipe priors: always unavailable, mass_g None
    photo_portion_grams: None  # legacy scalar is never filled; intervals live per item
    nutrition_eligible: Literal[False]
    issues: list[Issue]


class GateRecord(TypedDict):
    status: Literal['pending', 'passed', 'failed', 'not_applicable']
    evidence_ref: str | None


class EnergyReference(TypedDict):
    original_name: str
    unit: Literal['kcal', 'kJ']  # never converted between units
    per: Literal['per_100g_edible_portion']
    value: str
    interval: MassInterval | None  # same {low, high, coverage} shape, energy per 100 g


class PhotoNutritionBasis(TypedDict):
    basis_id: str
    estimate_id: str
    ingredient_candidate: str
    purpose: Literal['photo_calculation']
    energy: EnergyReference
    gates: dict[str, GateRecord]  # source_use, identity, basis, integrity
    reviewer_ref: str
    usage: Literal['diagnostic/test_only']


class ConsumptionAssertion(TypedDict):
    status: Literal['whole_visible_portion_asserted']
    asserted_by: str
    evidence_ref: str


class EnergyItem(TypedDict):
    estimate_id: str | None
    ingredient_candidate: str | None
    basis_id: str | None
    status: Literal['computed', 'unavailable']
    reason: str | None
    blocking_reasons: list[str]
    unit: Literal['kcal', 'kJ'] | None
    energy: dict | None  # {'low': str, 'high': str}
    coverage_lower_bound: str | None
    coverage_note: Literal['bonferroni_union_bound', 'composition_uncertainty_not_quantified'] | None
    issues: list[Issue]


class PhotoEnergyEstimate(TypedDict):
    contract: Literal['foodcal.photo_energy_estimate']
    schema_version: Literal[0]
    usage: Literal['diagnostic/test_only']
    status: Literal['computed', 'unavailable']
    scope: Literal['visible_items_only']
    unit: Literal['kcal', 'kJ'] | None
    energy: dict | None
    coverage_lower_bound: str | None
    coverage_note: str | None
    consumption: ConsumptionAssertion | None
    items: list[EnergyItem]
    portion_assessment: PortionAssessment  # recomputed from raw records, never caller-supplied
    excluded_recipe_priors: list[dict]
    completeness: Literal['visible_items_only', 'incomplete_hidden_ingredients_unquantified']
    reasons: list[str]
    nutrition_eligible: Literal[False]
    production_eligible: Literal[False]
