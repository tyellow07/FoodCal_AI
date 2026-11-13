"""Photo Item Source Review v0 JSON types; constructing them confers no trust.

No recipe, Parsing or candidate-lookup field exists here: recipe candidates may explain hidden calories,
but are not visual proof, so they cannot enter a review of a photo ingredient's nutrition basis.
"""
from typing import Literal, TypedDict

from .review_types import AggregateDecision, AttachmentRef, CandidateScope, RequiredNutrient


class PhotoScope(TypedDict):
    image_sha256: str
    evidence_type: Literal['ingredient_annotation', 'reviewed_prediction']
    evidence_id: str  # annotation_id or review_id of the visual evidence item
    ingredient_candidate: str
    evidence_sha256: str  # canonical JSON v0 hash of the whole visual evidence item
    estimate_id: str
    estimate_sha256: str  # canonical JSON v0 hash of the whole portion estimate


class SourceLink(TypedDict):
    """Explicit food link; never derived from a class name or fuzzy match."""
    food_id: str
    expected_archive_sha256: str
    expected_sample_sha256: str
    link_ref: str


class PhotoAttachmentRefs(TypedDict):
    visual_evidence: AttachmentRef  # bytes
    portion_estimate: AttachmentRef  # bytes
    source_lookup: AttachmentRef  # native SourceLookup
    source_provenance: AttachmentRef  # bytes
    evidence_files: list[AttachmentRef]  # bytes only


class PhotoBasisTarget(TypedDict):
    purpose: Literal['photo_consumed_mass_calculation']
    required_nutrients: list[RequiredNutrient]


class PhotoReviewClaim(TypedDict):
    claim_id: str
    domain: Literal['identity', 'basis']
    attribute: str
    nutrient_key: str | None
    side: Literal['photo', 'source']
    value_state: Literal['known', 'missing', 'unknown']
    # decimal_interval only for the photo side of photo_consumed_mass: {low, high, coverage} in grams.
    value_kind: Literal['text', 'text_list', 'decimal', 'decimal_interval']
    value: str | list[str] | dict | None
    unit: str | None
    evidence_ids: list[str]


class PhotoReviewEvidence(TypedDict):
    evidence_id: str
    origin: Literal['official_record', 'official_document', 'photo_evidence_record', 'photo_portion_record',
                    'photo_visual_review', 'caller_assertion', 'unverified_claim']
    attachment_ref: str
    attachment_sha256: str
    locator: dict
    raw_value: str | int | float | bool | None
    evidence_text: str | None
    statement_role: str


class PhotoReviewInput(TypedDict):
    contract: Literal['foodcal.photo_item_source_review']
    schema_version: Literal[0]
    purpose: Literal['diagnostic/test_only']
    photo_scope: PhotoScope
    candidate_scope: CandidateScope
    source_link: SourceLink
    attachment_refs: PhotoAttachmentRefs
    basis_target: PhotoBasisTarget
    claims: list[PhotoReviewClaim]
    evidence: list[PhotoReviewEvidence]


class PhotoAttributeResult(TypedDict):
    domain: Literal['identity', 'basis']
    attribute: str
    nutrient_key: str | None
    status: Literal['verified', 'mismatch', 'unresolved', 'not_applicable']
    reason_code: str
    photo_claim_ref: str | None
    source_claim_ref: str | None
    evidence_ids: list[str]
    rule_id: str
    rule_version: str
    reviewer_ref: str
    reviewed_at: str


class ImageRegion(TypedDict):
    format: str
    coordinate_space: str
    x_min: int | float
    y_min: int | float
    x_max: int | float
    y_max: int | float


class VisualJudgment(TypedDict):
    value_state: Literal['known', 'unknown', 'missing']
    value: str | None  # known only
    region: ImageRegion | None  # known only; inside the visual evidence region


class PhotoVisualReview(TypedDict):
    """foodcal.photo_visual_review.v0: an independent reviewer who looked at the original image."""
    profile: Literal['foodcal.photo_visual_review.v0']
    photo_scope: PhotoScope
    reviewer_ref: str
    reviewed_at: str
    image_access: Literal['reviewed_original_image']
    # food, part, state, processing, quality_composition, edible_fraction_visible
    judgments: dict[str, VisualJudgment]
    usage: Literal['diagnostic/test_only']


class SourceGateRecord(TypedDict):
    """foodcal.source_gate_record.v0, kept in the pinned review ledger; a request cannot declare it."""
    profile: Literal['foodcal.source_gate_record.v0']
    gate: Literal['source_use', 'integrity']
    purpose: Literal['photo_calculation']
    candidate_scope: CandidateScope
    status: Literal['pending', 'passed', 'failed']
    reason_code: str
    evidence_locator: str | None  # required when passed
    reviewer_ref: str | None  # required when passed or failed
    reviewed_at: str | None
    policy_revision: int | None  # the ledger's registered revision when passed or failed


class PhotoBasisBuild(TypedDict):
    status: Literal['built', 'unavailable']
    reasons: list[str]  # why no basis, or which basis gates still block photo calculation
    basis: dict | None  # src.portion.types.PhotoNutritionBasis
    decision: AggregateDecision
    usage: Literal['diagnostic/test_only']
    nutrition_eligible: Literal[False]
    production_eligible: Literal[False]
