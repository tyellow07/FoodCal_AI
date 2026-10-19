"""Inventory uses tiny synthetic files; no images/models/private data required."""
from pathlib import Path
import tempfile
import unittest
from tools.inventory_ingredient_data_v2 import inventory_yolo, inventory_food101


class IngredientInventoryTests(unittest.TestCase):
    def dataset(self, root):
        for split in ('train','valid','test'):
            (root/split/'images').mkdir(parents=True)
            (root/split/'labels').mkdir()
        (root/'train/images/same_jpg.rf.one.jpg').write_bytes(b'synthetic-image-one')
        (root/'valid/images/same_jpg.rf.two.jpg').write_bytes(b'synthetic-image-one')
        (root/'test/images/different.jpg').write_bytes(b'synthetic-image-two')
        (root/'train/labels/same_jpg.rf.one.txt').write_text('0 0.5 0.5 0.2 0.2\n14 0.5 0.5 0.8 0.8\n')
        (root/'valid/labels/same_jpg.rf.two.txt').write_text('0 0.5 0.5 0.2 0.2\n')
        (root/'test/labels/different.txt').write_text('7 0.5 0.5 0.8 0.8\n')

    def test_proxy_counts_never_become_accepted_ingredient_gt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.dataset(root)
            before=(root/'train/labels/same_jpg.rf.one.txt').read_bytes()
            result=inventory_yolo(root)
            self.assertEqual(result['splits']['train']['candidate_sampling_counts']['cooked_rice_region']['images_with_related_dish_or_component_label'],1)
            self.assertTrue(all(row['accepted_ingredient_training_boxes']==0 for s in result['splits'].values()
                                for row in s['candidate_sampling_counts'].values()))
            self.assertEqual(before,(root/'train/labels/same_jpg.rf.one.txt').read_bytes())

    def test_exact_and_prefix_cross_split_leakage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.dataset(root);result=inventory_yolo(root)['leakage_diagnostics']
            self.assertEqual(result['cross_split_exact_sha256_groups'],1)
            self.assertEqual(result['cross_split_filename_prefix_groups'],1)
            self.assertEqual(result['near_duplicate_visual_check'],'not_performed')

    def test_invalid_rows_missing_and_orphan_labels_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.dataset(root)
            (root/'train/labels/same_jpg.rf.one.txt').write_text('0 0.5 0.5 0.2 0.2\n15 0.5 0.5 0.2 0.2\n0 nan 0.5 0.2 0.2\n0 0.5 0.5 0.2\n')
            (root/'valid/labels/same_jpg.rf.two.txt').unlink()
            (root/'test/labels/orphan.txt').write_text('0 0.5 0.5 0.2 0.2')
            result=inventory_yolo(root)
            self.assertEqual(len(result['issues']),3)
            self.assertEqual(result['splits']['valid']['missing_labels'],1)
            self.assertEqual(result['splits']['test']['orphan_labels'],1)

    def test_missing_split_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'missing_dataset_split'):inventory_yolo(Path(folder))

    def test_food101_classification_is_not_bbox(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'meta').mkdir();(root/'images/omelette').mkdir(parents=True)
            (root/'meta/classes.txt').write_text('omelette\n')
            (root/'meta/train.txt').write_text('omelette/1\n');(root/'meta/test.txt').write_text('')
            (root/'images/omelette/1.jpg').write_bytes(b'synthetic')
            (root/'license_agreement.txt').write_text('synthetic license; no rights granted')
            result=inventory_food101(root)
            self.assertEqual(result['class_count'],1)
            self.assertEqual(result['image_counts_by_dish_class']['omelette'],1)
            self.assertEqual(result['ingredient_bbox_count'],0)

    def test_food101_missing_metadata_is_blocked_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            result=inventory_food101(Path(folder))
            self.assertEqual(result['status'],'blocked')
            self.assertIsNone(result['ingredient_bbox_count'])


if __name__=='__main__':unittest.main()
