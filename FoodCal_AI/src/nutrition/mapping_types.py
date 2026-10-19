"""Offline Ingredient -> Nutrition Mapping v1 JSON types."""
from typing import Literal, TypedDict

from src.recipe_parsing.types import EvidenceLink, Ingredient, Issue, RecipeParsingResult


class FoodIdentity(TypedDict):
    food: str | None
    part: str | None
    state: str | None
    quality: str | None
    weight_basis: str | None


class IdentityAssertion(TypedDict):
    attributes: FoodIdentity
    evidence_reference: str
    source_text_sha256: str


class NutritionSource(TypedDict):
    source_id: str
    reference: str
    kind: Literal['synthetic']
    review_status: Literal['test_only']


class NutritionEntry(TypedDict):
    entry_id: str
    ingredient_name: str
    identity: FoodIdentity
    source: NutritionSource
    nutrient_basis: Literal['per_100g_edible_portion']
    nutrients: None


class EntryMapping(TypedDict):
    status: Literal['matched', 'ambiguous', 'unavailable', 'error']
    reason: str
    selected_entry_id: str | None
    candidates: list[NutritionEntry]


class MassResult(TypedDict):
    status: Literal['available', 'unavailable']
    reason: str
    mass_g: int | float | None
    basis: Literal['recipe_declared_ingredient']
    rule: Literal['literal_g'] | None
    evidence_links: list[EvidenceLink]


class UseQualification(TypedDict):
    status: Literal['test_only']
    eligible_for_production: Literal[False]
    reasons: list[str]


class IngredientMapping(TypedDict):
    ingredient_id: str
    ingredient: Ingredient
    identity_assertion: IdentityAssertion | None
    preparation_step_ids: list[str]
    mapping: EntryMapping
    mass: MassResult
    qualification: UseQualification
    issues: list[Issue]


class NutritionMappingResult(TypedDict):
    contract: Literal['foodcal.ingredient_nutrition_mapping']
    schema_version: Literal[1]
    status: Literal['complete', 'partial', 'unavailable', 'failed']
    adapter_kind: Literal['mock']
    fixture_sha256: str
    parsing_snapshot: RecipeParsingResult | None
    source_check: Literal['matches_caller_snapshot', 'not_checked']
    qualification: UseQualification
    ingredients: list[IngredientMapping]
    issues: list[Issue]
