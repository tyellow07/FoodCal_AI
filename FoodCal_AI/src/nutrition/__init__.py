"""Legacy database and independent, explicitly synthetic mapping prototype."""
from .mapping import MappingFixtureError, MockNutritionMappingAdapter
from .mapping_types import (
    EntryMapping, FoodIdentity, IdentityAssertion, IngredientMapping, MassResult,
    NutritionEntry, NutritionMappingResult, NutritionSource, UseQualification,
)

__all__ = ['MappingFixtureError', 'MockNutritionMappingAdapter', 'EntryMapping',
           'FoodIdentity', 'IdentityAssertion', 'IngredientMapping', 'MassResult',
           'NutritionEntry', 'NutritionMappingResult', 'NutritionSource', 'UseQualification']
