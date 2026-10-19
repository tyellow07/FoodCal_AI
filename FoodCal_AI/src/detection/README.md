# Food Detection Python API

`src.detection` exposes the existing local Taiwanese-food YOLO baseline as a
Python API. It does not train, download checkpoints, write prediction images,
modify review records, or compute nutrition. Importing this package does not
load YOLO. Existing validation/export/diagnostic scripts remain available.

## Usage

Run from the project root using the existing project Python environment:

```python
from pathlib import Path
from src.detection import detect_food

image = Path('data/roboflow_taiwanese_food_detection/valid/images') / (
    '2583_jpg.rf.adb6bef6999f8e2de5eb51fb3daa225b.jpg'
)
result = detect_food(image)
for food in result['detections']:
    print(food['class_name'], food['confidence'], food['bounding_box'])
```

The convenience function reuses a process-local cached default `FoodDetector`.
For explicit ownership/device selection:

```python
from src.detection import FoodDetector

detector = FoodDetector(device='cpu')  # None selects the backend default device
first = detector.detect_food(image)
second = detector.detect_food(image)  # same loaded model and predictor
```

Each instance loads its model lazily on first valid input and serializes model
loading/inference with a lock because the underlying predictor is mutable.
Keep an instance for repeated calls; constructing new instances creates separate
model owners. The default cache is process-local, not shared across processes.
The constructor also accepts `model_path` and `classes_path`; their class mappings
must agree. `model_factory` is an injection point for test doubles.

Inputs: local path (`str`/`Path`), PIL image (converted from RGB), or nonempty
`uint8` NumPy array with shape H x W x 3 **in BGR order**. There is one image per
call and potentially many returned objects. URLs, batches, grayscale arrays and
floating-point arrays are unsupported. Local images are decoded before invoking
the backend; Windows Unicode paths are supported. Default checkpoint/YAML paths
are relative to the module's project root, independent of the working directory.

## Result contract (schema_version = 1)

The return value is a JSON-serializable dictionary:

| Field | Meaning |
| --- | --- |
| `status` | `detections_available` or `no_detections` |
| `image` | Input path when present, original `width` and `height` |
| `model` | `identifier`, local `checkpoint`, weight `sha256`, `class_count` |
| `inference_settings` | Fixed baseline parameters, requested/actual device |
| `detections` | List of zero or more objects in backend output order |
| `limitations` | Explicit uncertainty and downstream-use limitations |

Each detection contains:

```python
{
    'detection_id': 1,  # local to this call; not a tracking identity
    'class_id': 7,
    'class_name': 'cold_noodle',
    'confidence': 0.9,  # illustrative score, not a claimed smoke-test value
    'interpretation': 'candidate',
    'bounding_box': {
        'format': 'xyxy',
        'coordinate_space': 'original_image_pixels',
        'x_min': 10.0, 'y_min': 20.0, 'x_max': 300.0, 'y_max': 400.0,
    },
    'model_identifier': 'taiwanese_food_yolo_baseline_10ep:sha256:<weight digest>',
}
```

Box coordinates describe the original decoded image, not the resized model
tensor. The model identifier includes the checkpoint hash. Class names come
from `data/roboflow_taiwanese_food_detection/foodcal_data.yaml`, and the loaded
checkpoint must expose the same ID/name mapping. All 15 IDs are tested.

Baseline settings: confidence threshold 0.25 (strictly greater), NMS IoU 0.7,
image size 640, single-image batch, rectangular preprocessing, max 300 detections,
no augmentation, class-aware NMS, no class filter, FP32 (`quantize=None` in the
installed Ultralytics 8.4.174). The current checkpoint uses ordinary best-class
selection with `multi_label=False`. Parameters are not exposed per call for
tuning. A future backend/checkpoint change must revalidate this contract.

## Errors and interpretation

- `FileNotFoundError`: checkpoint or YAML missing; no automatic model download.
- `InvalidImageError`: absent, unsupported, empty or undecodable input.
- `ModelConfigurationError`: invalid YAML mapping or checkpoint/YAML disagreement.
- `DetectionInferenceError`: model load/inference failure or malformed backend
  output. Backend failure is never converted into an empty detections list.

`no_detections` means this model returned no retained candidates under these
settings. It does **not** establish that no food is present. Every predicted
class remains a candidate; confidence is an uncalibrated score, including above
the threshold. The known weak Over-easy egg scores remain a baseline limitation.
No ingredients, grams, nutrients or calories are returned. Overlapping dish and
component boxes must not automatically become independent portions.

## Verification and integration boundary

```powershell
python -B -m unittest tests.test_food_detection -v
python -B -m unittest discover -s tests -t . -p "test*.py" -v
python -B -m tests.evaluation.smoke_food_detection_api
```

The explicit smoke test uses three existing **validation** images, one reused
model, and compares class IDs, confidence and boxes with the existing Manifest.
Confidence tolerance is 1e-6 and box tolerance is 0.001 pixels. Its saved evidence
is `tests/evaluation/food_detection_api_smoke.json`; reruns preserve it and
require matching evidence. Different devices/framework versions may produce
numeric differences; a mismatch fails explicitly rather than changing the
baseline. Protected input/report fingerprints are checked before and after.
The smoke test is intentionally outside automatic unittest discovery.

The separate `src.recipes.retrieve_recipes(result)` adapter now consumes these
candidates and retains confidence, box, model identity and uncertainty. It performs
exact taxonomy matching in a local source-traceable catalog; see
`src/recipes/README.md`. No production recipe source is configured, so the default
returns unavailable for detected objects; synthetic fixtures require explicit
opt-in. Recipes remain priors, not proof of ingredients or weight; empty detection
must not yield zero calories. These APIs do not alter the existing Food-101 MVP
pipeline.
