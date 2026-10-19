"""Traceable recipe candidates, independent of LLMs and remote services."""
from .retrieval import RecipeRetriever, retrieve_recipes, InvalidDetectionResultError

__all__ = ['RecipeRetriever', 'retrieve_recipes', 'InvalidDetectionResultError']
