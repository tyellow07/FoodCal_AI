"""Default offline fixture demo; optional single YOLO diagnostic. No LLM or calories."""
import argparse
from copy import deepcopy
from dataclasses import fields, is_dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

from src.recipes import RecipeRetriever, InvalidDetectionResultError
from src.recipe_parsing import MockRecipeParsingAdapter
from src.nutrition.mapping import MockNutritionMappingAdapter
from src.nutrition.candidate_lookup import review_ingredient_sources


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = ROOT / 'examples/diagnostic_v0'
PARSING_FIXTURE = ROOT / 'docs/examples/recipe_parsing_contract_v1_cases.json'


def _parsing_fixture_hash():
    # Existing design file is Git text: pin LF-normalized UTF-8 bytes across checkouts.
    # Source.text hashes and nutrient raw values are never normalized here.
    return sha256(PARSING_FIXTURE.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def json_value(value):
    """Explicit diagnostic serialization, never normalize nutrient raw strings."""
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    return value


def _load(path):
    def pairs(items):
        value = {}
        for k, v in items:
            if k in value:
                raise ValueError('Duplicate fixture key')
            value[k] = v
        return value
    def constant(value):
        raise ValueError('Non-finite fixture JSON')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _reason(status, reason):
    return {'status': status, 'reason': reason}


def _association(candidate, catalog_hash, manifest, parser):
    links = [a for a in manifest['associations'] if a.get('recipe_id') == candidate['recipe_id']
             and a.get('source_id') == candidate['source']['source_id']]
    if len(links) != 1:
        raise ValueError('fixture_association_missing_or_ambiguous')
    link = links[0]
    if link['catalog_sha256'] != catalog_hash or link['parsing_fixture_sha256'] != _parsing_fixture_hash():
        raise ValueError('fixture_hash_mismatch')
    source = link.get('expected_source')
    if not isinstance(source, dict) or not isinstance(source.get('text'), str) or not source['text']:
        raise ValueError('recipe_text_unavailable')
    if sha256(source['text'].encode('utf-8')).hexdigest() != source.get('text_sha256'):
        raise ValueError('source_text_hash_mismatch')
    if any(candidate['source'].get(k) != source.get(k) for k in ('source_id', 'reference', 'kind', 'review_status')):
        raise ValueError('recipe_source_mismatch')
    parsed = parser.parse(link['input_id'])
    if parsed['source'] is None:
        raise ValueError('parsing_fixture_unavailable')
    if parsed['source'] != source or parsed['retrieval_binding'] is not None:
        raise ValueError('parsing_source_or_binding_mismatch')
    return link, parsed


def _nutrition_source(parsed, mapping, link, mapper, adapter):
    assertions, links = link.get('identity_assertions', {}), link.get('source_links', {})
    diagnostics = []
    eligible_links = {}
    ids = {i['ingredient_id'] for i in parsed['ingredients']}
    if not isinstance(links, dict) or any(i not in ids for i in links):
        return {'status': 'blocked', 'reason': 'invalid_source_link_scope', 'ingredients': [], 'lookup': None}
    for item in mapping['ingredients']:
        identifier = item['ingredient_id']
        assertion = item['identity_assertion']
        reason = None
        if item['issues'] or item['mapping']['status'] == 'error':
            reason = 'invalid_identity_assertion'
        elif assertion is None:
            reason = 'missing_identity_assertion'
        elif any(v is None for v in assertion['attributes'].values()):
            reason = 'identity_unresolved'
        elif identifier not in links:
            reason = 'missing_source_link'
        else:
            request = links[identifier]
            scope = {'ingredient_id': identifier, 'input_id': parsed['input_id'],
                     'recipe_text_sha256': parsed['source']['text_sha256']}
            if (not isinstance(request, dict) or any(request.get(k) != v for k, v in scope.items())
                    or not isinstance(request.get('candidates'), list) or not request['candidates']
                    or any(not isinstance(c, dict) or not isinstance(c.get('food_id'), str)
                           or not c['food_id'].strip() or not isinstance(c.get('evidence_reference'), str)
                           or not c['evidence_reference'].strip()
                           or any(not isinstance(c.get(k), str) or re.fullmatch('[0-9a-f]{64}', c[k]) is None
                                  for k in ('expected_archive_sha256', 'expected_sample_sha256'))
                           for c in request['candidates'])):
                reason = 'invalid_source_link_or_hash'
            elif adapter is None:
                reason = 'external_snapshot_not_supplied'
            else:
                eligible_links[identifier] = request
        diagnostics.append({'ingredient_id': identifier, 'status': 'blocked' if reason else 'ready',
                            'reason': reason, 'nutrition_eligible': False})
    lookup = None
    if eligible_links:
        lookup = review_ingredient_sources(parsed, expected_source=link['expected_source'], mock_mapper=mapper,
                                          source_adapter=adapter, identity_assertions=assertions,
                                          source_links=eligible_links)
    return {'status': 'diagnostic' if lookup is not None else 'blocked',
            'reason': None if lookup is not None else 'candidate_lookup_prerequisites_unmet',
            'ingredients': diagnostics, 'lookup': lookup}


def run_demo(fixtures_dir=DEFAULT_FIXTURES, *, source_adapter=None):
    """Optional source_adapter is caller-controlled; CLI never constructs an external backend."""
    result = {'diagnostic_version': 1, 'mode': 'fixture_offline', 'usage': 'diagnostic/test_only',
              'status': 'failed', 'fixture_provenance': None, 'detection_snapshot': None,
              'retrieval_snapshot': None, 'objects': [], 'issues': [],
              'stages': {'detection': _reason('not_executed', 'fixture_not_loaded'),
                         'retrieval': _reason('not_executed', 'fixture_not_loaded'),
                         'parsing': _reason('not_executed', 'no_resolved_recipe'),
                         'mapping': _reason('not_executed', 'no_valid_parsing'),
                         'tfda': _reason('not_queried', 'candidate_lookup_prerequisites_unmet')},
              'review': _reason('not_requested', 'no_formal_review'),
              'trust': {'source_authentication': 'not_performed', 'semantic_review': 'not_performed',
                        'nutrition_eligible': False}, 'photo_portion_grams': None,
              'calorie_estimate': {'status': 'unavailable', 'value': None,
                                   'reasons': ['fixture_only', 'recipe_is_prior', 'photo_mass_unknown',
                                               'nutrition_basis_unresolved']}}
    try:
        folder = Path(fixtures_dir)
        manifest = _load(folder / 'associations.json')
        if (manifest.get('fixture_version') != 1 or type(manifest.get('fixture_version')) is not int
                or manifest.get('kind') != 'synthetic' or manifest.get('review_status') != 'test_only'
                or not isinstance(manifest.get('associations'), list)):
            raise ValueError('invalid_fixture_profile')
        catalog_raw = (folder / 'catalog.json').read_bytes()
        if sha256((folder / 'classes.yaml').read_bytes()).hexdigest() != manifest['classes_sha256']:
            raise ValueError('taxonomy_hash_mismatch')
        detection = _load(folder / 'detection.json')
        if (detection.get('model', {}).get('identifier') != 'synthetic-detection-v0'
                or detection.get('image', {}).get('source') != 'test://synthetic-image-v0'):
            raise ValueError('synthetic_detection_required')
        catalog = _load(folder / 'catalog.json')
        if any(r.get('source', {}).get('kind') != 'test_fixture' for r in catalog.get('recipes', [])):
            raise ValueError('test_only_catalog_required')
        retriever = RecipeRetriever(folder / 'catalog.json', include_test_fixtures=True,
                                    classes_path=folder / 'classes.yaml')
        retrieval = retriever.retrieve(detection)
        # Absolute catalog path is presentation-only. Never change the original adapter result.
        projection = deepcopy(retrieval)
        projection['catalog']['path'] = 'fixture://diagnostic-v0/catalog.json'
        result.update(detection_snapshot=detection, retrieval_snapshot=projection,
                      fixture_provenance={'fixture_version': 1, 'kind': 'synthetic', 'review_status': 'test_only',
                                          'catalog_sha256': sha256(catalog_raw).hexdigest(),
                                          'associations_sha256': sha256((folder / 'associations.json').read_bytes()).hexdigest(),
                                          'classes_sha256': manifest['classes_sha256'],
                                          'path_projection': 'catalog path replaced with fixture URI; original not mutated'})
        result['stages']['detection'] = _reason('replayed', 'synthetic_fixture_not_image_inference')
        result['stages']['retrieval'] = _reason('executed', retrieval['status'])
        parser, mapper = MockRecipeParsingAdapter(), MockNutritionMappingAdapter()
        count = 0
        for obj in retrieval['objects']:
            out = {'detection_id': obj['detection']['detection_id'], 'detection': deepcopy(obj['detection']),
                   'retrieval_status': obj['status'], 'reason': obj['reason'], 'recipe_candidates': []}
            result['objects'].append(out)
            for candidate in obj['candidates']:
                entry = {'recipe': deepcopy(candidate), 'status': 'unavailable', 'reason': None,
                         'association': None, 'parsing_snapshot': None, 'mapping_snapshot': None,
                         'source_diagnostic': _reason('not_queried', 'no_valid_parsing')}
                out['recipe_candidates'].append(entry)
                try:
                    link, parsed = _association(candidate, retrieval['catalog']['sha256'], manifest, parser)
                    mapping = mapper.map(parsed, expected_source=link['expected_source'],
                                         identity_assertions=link.get('identity_assertions', {}))
                    if mapping['status'] == 'failed':
                        entry.update(reason='invalid_mapping_input', mapping_snapshot=mapping)
                        continue
                    entry.update(status='diagnostic',
                                 association={'status': 'fixture_association_only', 'input_id': parsed['input_id'],
                                              'catalog_sha256': link['catalog_sha256'],
                                              'source_text_sha256': parsed['source']['text_sha256'],
                                              'contract_retrieval_binding': None},
                                 parsing_snapshot=parsed, mapping_snapshot=mapping,
                                 source_diagnostic=_nutrition_source(parsed, mapping, link, mapper, source_adapter))
                    count += 1
                except (KeyError, ValueError, TypeError) as error:
                    entry['reason'] = 'fixture_association_invalid'
                    # Codes expose the cause without raw paths, recipe text or exception details.
                    if isinstance(error, ValueError) and str(error) in {
                        'fixture_association_missing_or_ambiguous', 'fixture_hash_mismatch', 'recipe_text_unavailable',
                        'source_text_hash_mismatch', 'recipe_source_mismatch', 'parsing_fixture_unavailable',
                        'parsing_source_or_binding_mismatch'}:
                        entry['reason'] = str(error)
        if count:
            result['stages']['parsing'] = _reason('replayed', 'explicit_fixture_association')
            result['stages']['mapping'] = _reason('executed', 'synthetic_mapping')
        if any(c['source_diagnostic'].get('lookup') is not None for o in result['objects'] for c in o['recipe_candidates']):
            result['stages']['tfda'] = _reason('executed', 'explicit_caller_links')
        result['status'] = 'no_detections' if not result['objects'] else 'partial'  # Never completeness/approval.
    except (OSError, UnicodeError, KeyError, ValueError, TypeError, InvalidDetectionResultError):
        result['issues'] = [{'code': 'invalid_or_unavailable_demo_fixture', 'path': '/fixtures',
                             'message': 'Fixture cannot be safely replayed; no fallback'}]
    return json_value(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Default synthetic fixture demo; explicit opt-in single YOLO validation diagnostic')
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument('--mode', choices=('fixture', 'yolo'), default='fixture')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--approval-record', type=Path)
    parser.add_argument('--fixture-recipes', action='store_true', help='YOLO mode: show synthetic recipe priors only, never parse them')
    args = parser.parse_args(argv)
    if args.mode == 'yolo':
        if args.image is None or args.approval_record is None:
            parser.error('YOLO mode requires --image and --approval-record')
        from .yolo_diagnostic import run_yolo_diagnostic
        result = run_yolo_diagnostic(args.image, args.approval_record, fixture_recipes=args.fixture_recipes)
    else:
        if args.image is not None or args.approval_record is not None or args.fixture_recipes:
            parser.error('Live inputs require explicit --mode yolo')
        result = run_demo(args.fixtures)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    count = sum(c['parsing_snapshot'] is not None for o in result['objects'] for c in o['recipe_candidates'])
    kind = 'TRUE MODEL DETECTION' if args.mode == 'yolo' else 'SYNTHETIC FIXTURE ONLY'
    print(f"{kind} | {result['status']} | objects={len(result['objects'])} parsed={count} | calories unavailable", file=sys.stderr)
    return 2 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
