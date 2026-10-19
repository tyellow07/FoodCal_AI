"""Offline visual evidence boundary; never derive ingredients from dish labels."""
from .adapter import VisualIngredientEvidenceAdapter, recipe_assisted_hidden_ingredients
from .review import SampleReviewContext

__all__ = ['VisualIngredientEvidenceAdapter', 'recipe_assisted_hidden_ingredients', 'SampleReviewContext']
