"""FoodCal's Python food detection API (no model load at import)."""
from .model import (FoodDetector, detect_food, get_default_detector,
                    InvalidImageError, ModelConfigurationError, DetectionInferenceError)

__all__ = ['FoodDetector', 'detect_food', 'get_default_detector',
           'InvalidImageError', 'ModelConfigurationError', 'DetectionInferenceError']
