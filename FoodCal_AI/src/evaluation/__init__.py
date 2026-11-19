"""Offline meal-level evaluation against directly weighed reference calories (rubric v0)."""
from .meal_metrics import evaluate_meals, prediction_from_energy_estimate, score_rubric

__all__ = ['evaluate_meals', 'prediction_from_energy_estimate', 'score_rubric']
