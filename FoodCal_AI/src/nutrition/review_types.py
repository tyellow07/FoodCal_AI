"""Review v1 result types; no trust is conferred by constructing these objects."""
from dataclasses import dataclass
from typing import Literal, TypedDict


class RecipeScope(TypedDict):
    input_id: str
    ingredient_id: str
    recipe_text_sha256: str


class CandidateScope(TypedDict):
    provider: Literal['tfda']
    dataset: Literal['8543']
    food_id: str
    original_name: str
    data_type: str
    archive_sha256: str
    member_sha256: str
    sample_sha256: str


class AttachmentRef(TypedDict):
    attachment_id: str
    representation: Literal['bytes', 'native']
    sha256: str | None


class AttachmentRefs(TypedDict):
    parsing_snapshot: AttachmentRef
    expected_recipe_source: AttachmentRef
    candidate_lookup: AttachmentRef
    source_provenance: AttachmentRef
    evidence_files: list[AttachmentRef]


class RequiredNutrient(TypedDict):
    original_name: str
    expected_unit: str


class BasisTarget(TypedDict):
    purpose: Literal['recipe_declared_ingredient_reference']
    required_nutrients: list[RequiredNutrient]


class ReviewClaim(TypedDict):
    claim_id: str
    domain: Literal['identity', 'basis']
    attribute: str
    nutrient_key: str | None
    side: Literal['recipe', 'source']
    value_state: Literal['known', 'missing', 'unknown']
    value_kind: Literal['text', 'text_list', 'decimal']
    value: str | list[str] | None
    unit: str | None
    evidence_ids: list[str]


class ReviewEvidence(TypedDict):
    evidence_id: str
    origin: Literal['official_record', 'official_document', 'recipe_text', 'caller_assertion', 'unverified_claim']
    attachment_ref: str
    attachment_sha256: str
    locator: dict
    raw_value: str | int | float | bool | None
    evidence_text: str | None
    statement_role: str


class ReviewInput(TypedDict):
    contract: Literal['foodcal.ingredient_source_review']
    schema_version: Literal[1]
    purpose: Literal['diagnostic/test_only']
    recipe_scope: RecipeScope
    candidate_scope: CandidateScope
    attachment_refs: AttachmentRefs
    basis_target: BasisTarget
    claims: list[ReviewClaim]
    evidence: list[ReviewEvidence]


class AttributeResult(TypedDict):
    domain: Literal['identity', 'basis']
    attribute: str
    nutrient_key: str | None
    status: Literal['verified', 'mismatch', 'unresolved', 'not_applicable']
    reason_code: str
    recipe_claim_ref: str | None
    source_claim_ref: str | None
    evidence_ids: list[str]
    rule_id: str
    rule_version: str
    reviewer_ref: str
    reviewed_at: str


@dataclass(frozen=True)
class ReviewIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class ReviewValidation:
    issues: tuple[ReviewIssue, ...]
    state: Literal['valid', 'invalid', 'blocked'] = 'invalid'

    @property
    def valid(self) -> bool:
        return self.state == 'valid' and not self.issues


@dataclass(frozen=True)
class ReviewTrustAnchor:
    """Independently configured application pin; never copied from model/request."""
    authority_ref: str
    context_sha256: str


@dataclass(frozen=True)
class ReviewContext:
    record_bytes: bytes
    attachments: dict[str, object]


class AggregateDecision(TypedDict):
    identity_decision: Literal['compatible', 'incompatible', 'needs_review', 'invalid']
    basis_decision: Literal['compatible', 'incompatible', 'needs_review', 'invalid']
    input_integrity: Literal['verified', 'invalid']
    blocking_reasons: list[str]
    attribute_results: list[AttributeResult]
    usage: Literal['diagnostic/test_only']
    nutrition_eligible: Literal[False]
