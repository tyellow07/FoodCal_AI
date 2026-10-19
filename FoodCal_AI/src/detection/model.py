"""Reusable local Taiwanese-food baseline detector; no nutrition assumptions."""
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import threading

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = PROJECT_ROOT / 'runs/detect/taiwanese_food_baseline_10ep/weights/best.pt'
DEFAULT_CLASSES_PATH = PROJECT_ROOT / 'data/roboflow_taiwanese_food_detection/foodcal_data.yaml'
BASELINE_SETTINGS = {'conf': 0.25, 'iou': 0.7, 'imgsz': 640, 'max_det': 300,
                     'batch': 1, 'rect': True, 'augment': False,
                     'agnostic_nms': False, 'classes': None, 'quantize': None}


class InvalidImageError(ValueError):
    """Input is absent, unsupported, empty or cannot be decoded."""


class ModelConfigurationError(ValueError):
    """Checkpoint and dataset class names do not match the 15-class contract."""


class DetectionInferenceError(RuntimeError):
    """Backend failed or returned malformed detection data."""


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_yolo(path):
    from ultralytics import YOLO
    return YOLO(path)


def _decode_image(image):
    import cv2
    import numpy as np
    from PIL import Image

    source = None
    if isinstance(image, (str, Path)) or hasattr(image, '__fspath__'):
        path = Path(image).expanduser().resolve()
        if not path.is_file():
            raise InvalidImageError(f'Image file does not exist: {path}')
        try:
            # imdecode supports Windows Unicode paths without passing paths/URLs
            # to YOLO (which could otherwise acquire remote inputs).
            array = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except (OSError, ValueError, cv2.error) as error:
            raise InvalidImageError(f'Cannot decode image: {path}') from error
        source = str(path)
    elif isinstance(image, Image.Image):
        try:
            array = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
        except Exception as error:
            raise InvalidImageError('Cannot decode PIL image') from error
    elif isinstance(image, np.ndarray):
        array = image
    else:
        raise InvalidImageError('Expected local image path, PIL image or uint8 BGR ndarray')
    if (array is None or array.dtype != np.uint8 or array.ndim != 3 or
            array.shape[2] != 3 or array.shape[0] == 0 or array.shape[1] == 0):
        raise InvalidImageError('Expected nonempty H x W x 3 uint8 image (ndarrays use BGR)')
    return np.ascontiguousarray(array).copy(), source


class FoodDetector:
    """One lazily loaded model per instance; synchronized for concurrent callers.

    detect_food accepts a local path, PIL image, or uint8 HWC BGR ndarray.
    Output is a JSON-serializable dict; detections are hypotheses, not confirmed
    food/ingredients. A missing detection never asserts absence of food.
    """
    def __init__(self, model_path=DEFAULT_MODEL_PATH, classes_path=DEFAULT_CLASSES_PATH,
                 device=None, model_factory=None):
        import yaml

        self.model_path = Path(model_path).expanduser().resolve()
        self.classes_path = Path(classes_path).expanduser().resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(f'Local detector checkpoint missing: {self.model_path}')
        if not self.classes_path.is_file():
            raise FileNotFoundError(f'Dataset class configuration missing: {self.classes_path}')
        try:
            config = yaml.safe_load(self.classes_path.read_text(encoding='utf-8'))
            names = config['names']
        except (OSError, KeyError, TypeError, yaml.YAMLError) as error:
            raise ModelConfigurationError('Cannot read dataset class names') from error
        if (not isinstance(names, list) or len(names) != 15 or
                any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != 15):
            raise ModelConfigurationError('Expected 15 unique nonempty YAML class names in ID order')
        self.class_names = tuple(names)
        self.device = device
        self._factory = model_factory or _load_yolo
        self._model = None
        self._model_metadata = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None:
            return
        # Recheck before YOLO construction: a missing local path must not trigger
        # backend download behavior. The digest identifies the loaded checkpoint.
        if not self.model_path.is_file():
            raise FileNotFoundError(f'Local detector checkpoint missing: {self.model_path}')
        digest = _sha256(self.model_path)
        try:
            model = self._factory(str(self.model_path))
        except Exception as error:
            raise DetectionInferenceError(f'Cannot load local detector: {self.model_path}') from error
        if getattr(model, 'names', None) != dict(enumerate(self.class_names)):
            raise ModelConfigurationError('Checkpoint class IDs/names differ from dataset YAML')
        if _sha256(self.model_path) != digest:
            raise ModelConfigurationError('Checkpoint changed while loading')
        self._model_metadata = {'identifier': f'taiwanese_food_yolo_baseline_10ep:sha256:{digest}',
                                'checkpoint': str(self.model_path), 'sha256': digest,
                                'class_count': len(self.class_names)}
        self._model = model

    def detect_food(self, image):
        array, source = _decode_image(image)
        height, width = array.shape[:2]
        # Ultralytics predictor is mutable; serialize shared-instance inference.
        with self._lock:
            self._ensure_model()
            settings = dict(BASELINE_SETTINGS)
            if self.device is not None:
                settings['device'] = self.device
            try:
                results = self._model.predict(array, save=False, verbose=False, **settings)
                if len(results) != 1 or tuple(results[0].orig_shape) != (height, width):
                    raise ValueError('Expected one result in original image coordinates')
                rows = [] if results[0].boxes is None else results[0].boxes.data.cpu().tolist()
                detections = []
                for index, row in enumerate(rows, 1):
                    if len(row) != 6 or not all(math.isfinite(value) for value in row):
                        raise ValueError('Invalid detection row')
                    x1, y1, x2, y2, confidence, cls = row
                    if (cls != int(cls) or not 0 <= cls < len(self.class_names) or
                            not 0 <= confidence <= 1 or confidence <= BASELINE_SETTINGS['conf'] or
                            not -1e-4 <= x1 < x2 <= width+1e-4 or
                            not -1e-4 <= y1 < y2 <= height+1e-4):
                        raise ValueError('Invalid class, score or original-image box')
                    detections.append({'detection_id': index, 'class_id': int(cls),
                                       'class_name': self.class_names[int(cls)],
                                       'confidence': float(confidence), 'interpretation': 'candidate',
                                       'bounding_box': {'format': 'xyxy', 'coordinate_space': 'original_image_pixels',
                                                        'x_min': float(x1), 'y_min': float(y1),
                                                        'x_max': float(x2), 'y_max': float(y2)},
                                       'model_identifier': self._model_metadata['identifier']})
            except Exception as error:
                raise DetectionInferenceError('Food detector inference failed or returned invalid output') from error
            actual_device = getattr(getattr(self._model, 'predictor', None), 'device', None)
            metadata = dict(self._model_metadata)
        return {'schema_version': 1, 'status': 'detections_available' if detections else 'no_detections',
                'image': {'source': source, 'width': width, 'height': height},
                'model': metadata, 'inference_settings': {**BASELINE_SETTINGS,
                    'device_requested': self.device, 'device_actual': str(actual_device) if actual_device is not None else None,
                    'multi_label': False},
                'detections': detections,
                'limitations': ['No detections does not mean the image contains no food.',
                                'Class labels are model candidates; confidence is an uncalibrated score, not certainty.',
                                'Only the configured 15 classes are supported; Over-easy egg scores are known to be weak.',
                                'Boxes may describe overlapping dish/component regions; do not assume independent portions.',
                                'This API does not infer ingredients, mass, nutrition or calories.']}


@lru_cache(maxsize=1)
def get_default_detector():
    """Process-local shared detector; constructing it does not yet load YOLO."""
    return FoodDetector()


def detect_food(image):
    """Convenience API using the cached default local baseline detector."""
    return get_default_detector().detect_food(image)
