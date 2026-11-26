"""Dish recognition scoring and the Gemini batch runner, on synthetic labels, answers and photos only."""
from contextlib import redirect_stdout
from decimal import Decimal
from functools import partial
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from src.evaluation import recognition_metrics, score_recognition
from src.evaluation.recognition_metrics import JUDGEMENT_COLUMNS, LABEL_COLUMNS, PREDICTIONS_SCHEMA, normalise_name
from tools import evaluate_recognition
from tools.meal_workspace import write_csv
from tools.run_gemini_batch import load_predictions, photo_files, run_batch


def label(image, dish, name, box=None, aliases='', ingredients='', size=(1000, 800)):
    row = {k: '' for k in LABEL_COLUMNS}
    row.update(image_id=image, dish_id=dish, name=name, aliases=aliases, ingredients=ingredients)
    if box:
        row.update(zip(('x_min', 'y_min', 'x_max', 'y_max'), map(str, box)))
        row.update(image_width=str(size[0]), image_height=str(size[1]))
    return row


def item(detection_id, name_zh, box, name_en='', components=(), confidence='high'):
    names = {'zh-TW': name_zh, **({'en': name_en} if name_en else {})}
    return {'detection_id': detection_id, 'display_name': name_zh, 'names': names, 'confidence': confidence,
            'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box)),
            'components': [list(c) for c in components]}


def answers(images, size=(1000, 800)):
    return {'schema': PREDICTIONS_SCHEMA,
            'images': {k: ({'width': size[0], 'height': size[1], 'items': v} if isinstance(v, list) else v)
                       for k, v in images.items()}}


def judge(pending, verdict, who='judge-a'):
    return [{**row, 'verdict': verdict, 'judge': who} for row in pending]


class NameMatching(unittest.TestCase):
    def test_exact_alias_and_english_names_match_without_a_person(self):
        labels = [label('m1', '1', '白飯', (0, 0, 400, 400)),
                  label('m1', '2', '炒高麗菜', (500, 0, 900, 400), aliases='高麗菜; 炒青菜'),
                  label('m1', '3', 'Fried egg', (0, 450, 400, 780))]
        preds = answers({'m1': [item('G1', '白 飯', (10, 10, 390, 410)), item('G2', '高麗菜', (510, 0, 900, 390)),
                                item('G3', '荷包蛋', (0, 460, 400, 780), name_en='FRIED EGG')]})
        metrics = recognition_metrics(labels, preds)
        self.assertEqual(metrics['status'], 'scored')
        self.assertEqual((metrics['correct'], metrics['correct_by_name_rule'], metrics['missed'], metrics['extra']),
                         (3, 3, 0, 0))
        self.assertEqual(metrics['f1'], 1)
        self.assertEqual(score_recognition(metrics)['2a_detection'], 5)

    def test_normalise_folds_width_case_spaces_and_punctuation(self):
        self.assertEqual(normalise_name(' 滷肉飯（小） '), normalise_name('滷肉飯(小)'))
        self.assertEqual(normalise_name('Ｆｒｉｅｄ　Ｅｇｇ!'), 'friedegg')

    def test_a_different_name_waits_for_a_person_then_counts_as_misnamed_or_correct(self):
        labels = [label('m1', '1', '宮保雞丁', (0, 0, 400, 400))]
        preds = answers({'m1': [item('G1', '醬燒雞丁', (0, 0, 380, 400))]})
        pending = recognition_metrics(labels, preds)
        self.assertEqual(pending['status'], 'needs_judgement')
        self.assertEqual([(r['dish_id'], r['detection_id'], r['gold_name'], r['predicted_name'])
                          for r in pending['pending']], [('1', 'G1', '宮保雞丁', '醬燒雞丁')])
        self.assertEqual(score_recognition(pending)['2a_detection'], None)

        wrong = recognition_metrics(labels, preds, judge(pending['pending'], 'different'))
        self.assertEqual((wrong['correct'], wrong['misnamed'], wrong['missed'], wrong['extra']), (0, 1, 0, 0))
        self.assertEqual(wrong['name_accuracy_when_located'], 0)
        self.assertEqual(wrong['localisation_recall'], 1)
        self.assertEqual(wrong['failure_cases'][0]['type'], 'misnamed')
        self.assertEqual(wrong['judges'], ['judge-a'])

        same = recognition_metrics(labels, preds, judge(pending['pending'], 'SAME'))
        self.assertEqual((same['correct'], same['correct_by_judgement'], same['misnamed']), (1, 1, 0))

    def test_a_box_that_barely_overlaps_is_a_miss_and_an_extra_not_a_question(self):
        labels = [label('m1', '1', '白飯', (0, 0, 400, 400))]
        preds = answers({'m1': [item('G1', '白飯', (300, 300, 700, 700))]})
        metrics = recognition_metrics(labels, preds)
        self.assertEqual((metrics['status'], metrics['missed'], metrics['extra']), ('scored', 1, 1))
        missed = next(f for f in metrics['failure_cases'] if f['type'] == 'missed')
        self.assertEqual(missed['closest_prediction']['detection_id'], 'G1')
        self.assertLess(missed['closest_prediction']['iou'], Decimal('0.5'))
        self.assertEqual(recognition_metrics(labels, preds, threshold=Decimal('0.03'))['correct'], 1)

    def test_one_label_takes_one_prediction(self):
        labels = [label('m1', '1', '白飯', (0, 0, 400, 400))]
        preds = answers({'m1': [item('G1', '白飯', (0, 0, 400, 380)), item('G2', '白飯', (0, 0, 400, 400))]})
        metrics = recognition_metrics(labels, preds)
        self.assertEqual((metrics['correct'], metrics['extra']), (1, 1))
        self.assertEqual(next(f for f in metrics['failure_cases'] if f['type'] == 'extra')['detection_id'], 'G1')
        self.assertEqual(metrics['precision'], Decimal('0.5'))

    def test_labels_without_boxes_match_by_name_anywhere_in_the_photo(self):
        labels = [label('m1', '1', '白飯'), label('m1', '2', '滷雞腿')]
        preds = answers({'m1': [item('G1', '雞腿', (0, 0, 10, 10)), item('G2', '白飯', (500, 500, 900, 700))]})
        pending = recognition_metrics(labels, preds)
        self.assertEqual([(r['dish_id'], r['detection_id'], r['iou']) for r in pending['pending']], [('2', 'G1', '')])
        metrics = recognition_metrics(labels, preds, judge(pending['pending'], 'same'))
        self.assertEqual((metrics['correct'], metrics['boxed_gold_dishes'], metrics['misnamed']), (2, 0, 0))
        self.assertIsNone(metrics['localisation_recall'])
        self.assertIsNone(score_recognition(metrics)['2a_detection'])
        self.assertIn('not item 2a', score_recognition(metrics)['note'])

    def test_a_photo_without_an_answer_misses_every_dish(self):
        labels = [label('m1', '1', '白飯'), label('m2', '1', '麵'), label('m3', '1', '湯')]
        preds = answers({'m2': {'error': 'ServerError: 503'}, 'm3': {'skipped': 'card_not_found'},
                         'other': []})
        metrics = recognition_metrics(labels, preds)
        self.assertEqual(metrics['images_without_result'], ['m1', 'm2', 'm3'])
        self.assertEqual((metrics['missed'], metrics['recall'], metrics['precision']), (3, 0, None))
        self.assertTrue(all(f['no_result'] for f in metrics['failure_cases']))
        self.assertEqual(metrics['unlabelled_result_images'], 1)

    def test_prediction_boxes_are_rescaled_to_the_labelled_photo(self):
        labels = [label('m1', '1', '白飯', (0, 0, 2000, 2000), size=(4000, 3000))]
        preds = answers({'m1': [item('G1', '白飯', (0, 0, 500, 500))]}, size=(1000, 750))
        self.assertEqual(recognition_metrics(labels, preds)['correct'], 1)
        turned = answers({'m1': [item('G1', '白飯', (0, 0, 500, 500))]}, size=(750, 1000))
        with self.assertRaisesRegex(ValueError, 'image_size_mismatch'):
            recognition_metrics(labels, turned)

    def test_ingredients_are_compared_on_correctly_named_dishes(self):
        labels = [label('m1', '1', '炒高麗菜', (0, 0, 400, 400), ingredients='leafy_vegetable;carrot')]
        preds = answers({'m1': [item('G1', '炒高麗菜', (0, 0, 400, 400),
                                     components=[('leafy_vegetable', 80), ('pork', 20)])]})
        found = recognition_metrics(labels, preds)['ingredients']
        self.assertEqual((found['dishes'], found['precision'], found['recall']), (1, Decimal('0.5'), Decimal('0.5')))
        self.assertEqual((found['most_missed'], found['most_extra']), ([('carrot', 1)], [('pork', 1)]))


class BadInput(unittest.TestCase):
    def test_label_and_judgement_mistakes_are_refused(self):
        preds = answers({'m1': []})
        cases = {'unknown_ingredient_key': [label('m1', '1', '飯', ingredients='rice_typo')],
                 'invalid_ground_truth_box': [{**label('m1', '1', '飯', (0, 0, 9, 9)), 'x_max': ''}],
                 'duplicate_dish_id': [label('m1', '1', '飯'), label('m1', '1', '湯')],
                 'label_missing': [label('m1', '1', '')],
                 'invalid_image_size': [{**label('m1', '1', '飯', (0, 0, 9, 9)), 'image_width': ''}]}
        for code, rows in cases.items():
            with self.subTest(code), self.assertRaisesRegex(ValueError, code):
                recognition_metrics(rows, preds)
        row = {k: '' for k in JUDGEMENT_COLUMNS} | {'image_id': 'm1', 'dish_id': '1', 'detection_id': 'G1'}
        for code, rows in {'invalid_judgement': [row | {'verdict': 'maybe', 'judge': 'a'}],
                           'invalid_judgement: m1': [row | {'verdict': 'same'}],
                           'conflicting_judgement': [row | {'verdict': 'same', 'judge': 'a'},
                                                     row | {'verdict': 'different', 'judge': 'b'}]}.items():
            with self.subTest(code), self.assertRaisesRegex(ValueError, code):
                recognition_metrics([label('m1', '1', '飯')], preds, rows)
        with self.assertRaisesRegex(ValueError, 'predictions_schema_mismatch'):
            recognition_metrics([label('m1', '1', '飯')], {'images': {}})


def photo(path, size=(60, 40), orientation=None):
    image = Image.new('RGB', size, (200, 180, 150))
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    image.save(path, format='JPEG', exif=exif.tobytes())
    return path


class Batch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.work = self.tmp / 'work'
        self.photos = self.tmp / 'photos'
        self.photos.mkdir()
        photo(self.photos / 'a.jpg')
        photo(self.photos / 'b.JPG')
        photo(self.photos / 'c.png')
        (self.photos / 'notes.txt').write_text('not a photo')
        self.sent, self.masked = [], []

    def recognize(self, image):
        self.sent.append(image.info.get('tag'))
        return {'items': [item('G1', '白飯', (0, 0, 10, 10))], 'notes': '', 'model': 'fake-model'}

    def decode(self, raw):
        image = Image.new('RGB', (30, 20))
        image.info['tag'] = 'plain'
        return image

    def mask(self, image, quad):
        self.masked.append(quad)
        masked = image.copy()
        masked.info['tag'] = 'masked'
        return masked

    def run_batch(self, find_card, **options):
        return run_batch(photo_files(self.photos), self.work / 'p.json', recognize=self.recognize,
                         find_card=find_card, decode=self.decode, mask=self.mask, root=self.work,
                         log=lambda _: None, now=lambda: '2026-11-26T00:00:00+00:00', **options)

    def test_only_card_masked_photos_are_sent_and_no_photo_is_kept(self):
        card = {'quad': [[0, 0], [5, 0], [5, 3], [0, 3]], 'method': 'logo'}
        sleeps = []
        data = self.run_batch(lambda image: card, pause_s=2.0, sleep=sleeps.append)
        self.assertEqual(sorted(data['images']), ['a', 'b', 'c'])
        self.assertEqual(self.sent, ['masked'] * 3)
        self.assertEqual(sleeps, [2.0, 2.0])
        entry = data['images']['a']
        self.assertEqual((entry['width'], entry['height'], entry['card_masked'], entry['card_method']),
                         (30, 20, True, 'logo'))
        self.assertEqual(len(entry['file_sha256']), 64)
        self.assertFalse({'image', 'raw', 'bytes'} & set(entry))
        self.assertEqual(load_predictions(self.work / 'p.json'), data)

    def test_no_card_found_is_skipped_unless_asked_and_answered_photos_are_not_resent(self):
        data = self.run_batch(lambda image: None)
        self.assertEqual({e.get('skipped') for e in data['images'].values()}, {'card_not_found'})
        self.assertEqual(self.sent, [])
        data = self.run_batch(lambda image: None, send_unmasked=True)
        self.assertEqual(self.sent, ['plain'] * 3)
        self.assertFalse(data['images']['a']['card_masked'])
        self.run_batch(lambda image: None, send_unmasked=True)
        self.assertEqual(len(self.sent), 3)  # all answered: nothing sent again
        photo(self.photos / 'a.jpg', size=(61, 40))  # the file changed: asked again
        self.run_batch(lambda image: None, send_unmasked=True)
        self.assertEqual(len(self.sent), 4)
        self.run_batch(lambda image: None, send_unmasked=True, redo=True)
        self.assertEqual(len(self.sent), 7)

    def test_failures_are_recorded_and_retried_next_time(self):
        def broken(image):
            raise RuntimeError('quota')
        data = run_batch(photo_files(self.photos), self.work / 'p.json', recognize=broken,
                         find_card=lambda image: None, decode=self.decode, mask=self.mask, send_unmasked=True,
                         root=self.work, log=lambda _: None)
        self.assertEqual(data['images']['a']['error'], 'RuntimeError: quota')
        data = self.run_batch(lambda image: None, send_unmasked=True)
        self.assertEqual(len(self.sent), 3)

        def unreadable(raw):
            raise ValueError('invalid_image_content')
        data = run_batch(photo_files(self.photos), self.work / 'q.json', recognize=self.recognize,
                         find_card=lambda image: None, decode=unreadable, mask=self.mask, root=self.work,
                         log=lambda _: None)
        self.assertEqual(data['images']['c']['error'], 'invalid_image_content')

    def test_output_stays_under_work_and_photo_names_are_unique(self):
        with self.assertRaisesRegex(ValueError, 'output_must_be_under_ignored_work'):
            run_batch({}, self.tmp / 'p.json', recognize=None, find_card=None, decode=None, mask=None,
                      root=self.work)
        photo(self.photos / 'a.png')
        with self.assertRaisesRegex(ValueError, 'duplicate_image_id: a'):
            photo_files(self.photos)
        with self.assertRaisesRegex(ValueError, 'no_photos_found'):
            (self.tmp / 'empty').mkdir()
            photo_files(self.tmp / 'empty')

    def test_cli_refuses_to_send_without_agree(self):
        from tools import run_gemini_batch
        with redirect_stdout(StringIO()), mock.patch('sys.stderr', StringIO()), self.assertRaises(SystemExit):
            run_gemini_batch.main(['--photos', str(self.photos)])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.write = partial(write_csv, root=self.tmp)

    def test_template_has_one_blank_row_per_photo_with_its_upright_size(self):
        folder = self.tmp / 'photos'
        folder.mkdir()
        photo(folder / 'turned.jpg', size=(60, 40), orientation=6)
        photo(folder / 'flat.jpg', size=(60, 40))
        rows = evaluate_recognition.label_template(photo_files(folder))
        self.assertEqual([(r['image_id'], r['image_width'], r['image_height'], r['name']) for r in rows],
                         [('flat', '60', '40', ''), ('turned', '40', '60', '')])

    def test_score_writes_pending_pairs_then_scores_the_filled_file(self):
        labels = self.write(self.tmp / 'labels.csv', LABEL_COLUMNS, [label('m1', '1', '宮保雞丁', (0, 0, 400, 400))])
        preds = self.tmp / 'p.json'
        preds.write_text(json.dumps(answers({'m1': [item('G1', '醬燒雞丁', (0, 0, 400, 400))]})), encoding='utf-8')
        args = ['score', '--labels', str(labels), '--predictions', str(preds)]
        with mock.patch.object(evaluate_recognition, 'write_csv', self.write), redirect_stdout(StringIO()) as out:
            code = evaluate_recognition.main(args + ['--write-pending', str(self.tmp / 'pending.csv')])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.getvalue())['metrics']['status'], 'needs_judgement')
        pending = evaluate_recognition.read_rows(self.tmp / 'pending.csv', JUDGEMENT_COLUMNS)
        filled = self.write(self.tmp / 'judged.csv', JUDGEMENT_COLUMNS, judge(pending, 'different'))
        with redirect_stdout(StringIO()) as out:
            code = evaluate_recognition.main(args + ['--judgements', str(filled)])
        report = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual((report['metrics']['misnamed'], report['metrics']['f1']), (1, '0.0000'))
        self.assertEqual(report['rubric_points']['2a_detection'], 0)


if __name__ == '__main__':
    unittest.main()
