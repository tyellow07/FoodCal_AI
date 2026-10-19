from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tools.ingredient_box_edit import edit_box,write_revision
from tools.annotate_ingredient_pilot import AnnotationApp

SPEC=json.loads((Path(__file__).resolve().parents[1]/'examples/visual_ingredient_detection_v2/annotation_spec.json').read_text(encoding='utf-8'))


class Var:
    def __init__(self,value=None):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class BoxEditTests(unittest.TestCase):
    def setUp(self):
        box={'annotation_id':'synthetic-a','class_id':0,'bounding_box':[10,10,30,30],
             'granularity':'visible_instance','visibility':'partial','occlusion':'unknown',
             'truncation':'unknown','uncertainty_reason':'','semantic_review_status':'accepted_for_pilot_annotation'}
        other=deepcopy(box);other['annotation_id']='synthetic-b'
        self.annotation={'pilot_image_id':'synthetic-test','boxes':[box,other],
                         'independent_review':{'status':'accepted','reviewer':'synthetic-reviewer'}}

    def test_edit_exact_box_only_and_preserve_input(self):
        original=deepcopy(self.annotation)
        result=edit_box(self.annotation,'synthetic-a',{'visibility':'clear','uncertainty_reason':'synthetic user-entered note'},dimensions=[100,100],spec=SPEC)
        self.assertEqual(original,self.annotation)
        self.assertEqual(result['boxes'][1],original['boxes'][1])
        self.assertEqual(result['boxes'][0]['annotation_id'],'synthetic-a')
        self.assertEqual(result['independent_review']['status'],'pending')
        self.assertEqual(result['boxes'][0]['semantic_review_status'],'pending')

    def test_coordinate_and_class_edit(self):
        result=edit_box(self.annotation,'synthetic-a',{'class_id':1,'bounding_box':[1,2,40,50]},dimensions=[100,100],spec=SPEC)
        self.assertEqual(result['boxes'][0]['granularity'],'visible_contiguous_group')

    def test_invalid_edits_fail_without_mutation(self):
        original=deepcopy(self.annotation)
        for update in ({'class_id':True},{'bounding_box':[0,0,101,10]},{'visibility':'fake'},
                       {'uncertainty_reason':None},{'annotation_id':'replacement'}):
            with self.assertRaises(ValueError):edit_box(self.annotation,'synthetic-a',update,dimensions=[100,100],spec=SPEC)
        self.assertEqual(original,self.annotation)

    def test_append_revision_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);old=directory/'old.json';old.write_text('synthetic original evidence')
            before=old.read_bytes()
            a=write_revision(directory,self.annotation,'a'*64);b=write_revision(directory,self.annotation,'a'*64)
            self.assertNotEqual(a,b);self.assertEqual(old.read_bytes(),before)
            self.assertEqual(json.loads(a.read_text())['parent_revision_sha256'],'a'*64)
            self.assertEqual(len(list(directory.glob('*.json'))),3)

    def fake_app(self):
        app=AnnotationApp.__new__(AnnotationApp)
        app.annotation=deepcopy(self.annotation);app.selected_id=None;app.start=None;app.review=False
        app.box_list=Mock();app.box_list.curselection.return_value=(0,)
        app.cls=Var();app.note=Var();app.flags={k:Var() for k in ('visibility','occlusion','truncation')}
        app.coordinates=[Var() for _ in range(4)];app.render=Mock()
        app.item={'dimensions':[100,100]};app.spec=SPEC
        return app

    def test_gui_selection_loads_and_apply_updates_existing_box(self):
        app=self.fake_app();app.select_box()
        self.assertEqual(app.selected_id,'synthetic-a');self.assertEqual(app.flags['occlusion'].get(),'unknown')
        app.flags['occlusion'].set('none');app.coordinates[0].set('5');app.note.set('synthetic note')
        other=deepcopy(app.annotation['boxes'][1]);app.apply_edit()
        self.assertEqual(len(app.annotation['boxes']),2)
        self.assertEqual(app.annotation['boxes'][0]['bounding_box'][0],5)
        self.assertEqual(app.annotation['boxes'][0]['occlusion'],'none')
        self.assertEqual(app.annotation['boxes'][1],other)

    def test_selection_does_not_draw_and_review_cannot_edit(self):
        app=self.fake_app();app.select_box();original=deepcopy(app.annotation)
        app.press(SimpleNamespace(x=1,y=2));app.release(SimpleNamespace(x=4,y=8))
        self.assertEqual(app.annotation,original)
        app.review=True;app.flags['visibility'].set('clear');app.apply_edit()
        self.assertEqual(app.annotation,original)

    def test_new_image_new_box_mode_resets_inherited_flags(self):
        app=self.fake_app();app.select_box();app.new_box_mode()
        self.assertIsNone(app.selected_id);self.assertEqual(app.note.get(),'')
        self.assertEqual(app.flags['visibility'].get(),'clear')
        self.assertEqual(app.flags['truncation'].get(),'unknown')


if __name__=='__main__':unittest.main()
