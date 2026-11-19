"""Offline visual evidence boundary; never derive ingredients from dish labels."""
from .adapter import VisualIngredientEvidenceAdapter, ingredient_annotation_envelope, recipe_assisted_hidden_ingredients
from .review import SampleReviewContext

__all__ = ['VisualIngredientEvidenceAdapter', 'ingredient_annotation_envelope', 'recipe_assisted_hidden_ingredients',
           'SampleReviewContext']
