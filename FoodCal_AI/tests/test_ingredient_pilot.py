from copy import deepcopy
import unittest
from tools.ingredient_pilot import select_records,validate_annotation,validate_splits,visual_evidence_from_annotation
from src.portion import assess_portions, evidence_identity
from src.portion.contract import evidence_blocker


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.item={'pilot_image_id':'p1','image_sha256':'a'*64,'source_id':'synthetic',
            'dimensions':[100,80],'rights_status':'review_only_unverified','source_group_status':'unresolved',
            'source_group_id':None,'split':None,'candidate_bucket_id':'g1'}
        self.annotation={'pilot_image_id':'p1','image_sha256':'a'*64,'source_id':'synthetic',
            'spec_version':'ingredient-bbox-v2-draft-0','annotator':'synthetic-annotator',
            'classes_checked':[0,1,2],'independent_review':{'status':'accepted','reviewer':'synthetic-reviewer'},
            'boxes':[{'annotation_id':'a1','class_id':0,'bounding_box':[10,10,50,40],
                'granularity':'visible_instance','semantic_review_status':'accepted_for_pilot_annotation',
                'visibility':'clear','occlusion':'none','truncation':'none'}]}

    def test_quality_not_training_rights(self):
        self.assertEqual(validate_annotation(self.item,self.annotation)['status'],'passed')
        result=validate_annotation(self.item,self.annotation,for_training=True)
        self.assertIn('rights_unverified',result['issues']);self.assertFalse(result['training_eligible'])

    def test_invalid_class_coordinates_and_granularity(self):
        for field,value,code in [('class_id',True,'invalid_class'),('class_id',14,'invalid_class'),
            ('bounding_box',[0,0,101,10],'out_of_bounds'),('bounding_box',[0,0,float('nan'),10],'invalid_coordinates'),
            ('granularity','whole_dish','invalid_granularity')]:
            a=deepcopy(self.annotation);a['boxes'][0][field]=value
            self.assertIn(code,validate_annotation(self.item,a)['issues'])

    def test_pending_and_self_review_blocked(self):
        a=deepcopy(self.annotation);a['independent_review']['status']='pending'
        self.assertIn('independent_review_not_accepted',validate_annotation(self.item,a)['issues'])
        a['independent_review'].update(status='accepted',reviewer=a['annotator'])
        self.assertIn('independent_reviewer_required',validate_annotation(self.item,a)['issues'])

    def test_scope_and_incomplete_class_review(self):
        for key in ('pilot_image_id','source_id','image_sha256'):
            a=deepcopy(self.annotation);a[key]='other'
            self.assertIn('scope_mismatch_'+key,validate_annotation(self.item,a)['issues'])
        a=deepcopy(self.annotation);a['classes_checked']=[0]
        self.assertIn('incomplete_target_class_review',validate_annotation(self.item,a)['issues'])

    def test_no_gt_needed_and_no_pending_semantics_pass(self):
        a=deepcopy(self.annotation);a['boxes'][0]['semantic_review_status']='pending'
        self.assertIn('box_semantics_unaccepted',validate_annotation(self.item,a)['issues'])
        a['boxes']=[] # Explicit synthetic reviewed-negative example, not a real annotation.
        self.assertEqual(validate_annotation(self.item,a)['status'],'passed')

    def test_accepted_annotation_becomes_bindable_visual_evidence(self):
        before=deepcopy(self.annotation)
        envelope=visual_evidence_from_annotation(self.item,self.annotation)
        self.assertEqual(self.annotation,before)
        self.assertEqual(envelope['status'],'partial')
        self.assertEqual(envelope['recipe_assisted_hidden_ingredients'],[])
        [item]=envelope['visual_ingredient_evidence']
        self.assertEqual(item['ingredient_candidate'],'visible_fried_egg')
        self.assertIsNone(evidence_blocker(item))
        source=item['evidence_source']
        self.assertEqual((source['annotator'],source['reviewer'],source['image_sha256']),
                         ('synthetic-annotator','synthetic-reviewer','a'*64))
        self.assertEqual(evidence_identity(item)['evidence_id'],'a1')
        self.assertEqual(item['image_region']['x_max'],50)
        # Admissible evidence now reaches the portion stage: the remaining gap is the mass itself.
        assessment=assess_portions(envelope)
        self.assertEqual(assessment['items'][0]['reason'],'photo_mass_unknown')
        self.assertIsNone(envelope['photo_portion_grams'])

    def test_blocked_or_empty_annotation_yields_no_evidence(self):
        a=deepcopy(self.annotation);a['independent_review']['status']='pending'
        envelope=visual_evidence_from_annotation(self.item,a)
        self.assertEqual((envelope['status'],envelope['reason']),('unavailable','pilot_annotation_not_accepted'))
        self.assertEqual(envelope['visual_ingredient_evidence'],[])
        a=deepcopy(self.annotation);a['boxes']=[]
        self.assertEqual(visual_evidence_from_annotation(self.item,a)['reason'],'reviewed_no_target_ingredient_boxes')

    def test_reference_card_is_optional_but_must_be_valid(self):
        a=deepcopy(self.annotation)
        a['reference_card']={'object':'iso_id1_card_85.60x53.98mm','bounding_box':[60,50,99,79]}
        self.assertEqual(validate_annotation(self.item,a)['status'],'passed')
        self.assertEqual(len(visual_evidence_from_annotation(self.item,a)['visual_ingredient_evidence']),1)
        for card in ({'object':'coin','bounding_box':[0,0,10,10]},{'object':'iso_id1_card_85.60x53.98mm','bounding_box':[0,0,101,10]},
                     {'object':'iso_id1_card_85.60x53.98mm','bounding_box':[5,5,5,9]},'card',
                     {'object':'iso_id1_card_85.60x53.98mm','bounding_box':[0,0,float('inf'),9]}):
            a['reference_card']=card
            self.assertIn('invalid_reference_card',validate_annotation(self.item,a)['issues'])

    def test_unresolved_and_cross_group_split(self):
        self.assertEqual(validate_splits([self.item])['status'],'blocked')
        a=dict(self.item,source_group_id='verified-group',source_group_status='verified',split='train')
        b=dict(a,split='test',image_sha256='b'*64)
        self.assertIn('cross_split_leakage',validate_splits([a,b])['issues'])
        b.update(source_group_id='other',candidate_bucket_id='g2',image_sha256=a['image_sha256'])
        self.assertIn('cross_split_leakage',validate_splits([a,b])['issues'])

    def test_sampling_groups_before_quota_and_no_confirmed_lineage_claim(self):
        candidates=[{'prefix':'same','image_sha256':'a'*64,'image_file':'one.jpg','original_split':'train','original_classes':[0]},
                    {'prefix':'same','image_sha256':'b'*64,'image_file':'two.jpg','original_split':'valid','original_classes':[0]},
                    {'prefix':'different','image_sha256':'a'*64,'image_file':'three.jpg','original_split':'train','original_classes':[14]},
                    {'prefix':'onlytest','image_sha256':'c'*64,'image_file':'test.jpg','original_split':'test','original_classes':[7]}]
        result=select_records(candidates)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['candidate_group_members'],3)
        self.assertEqual(result[0]['source_group_status'],'unresolved')
        self.assertIsNone(result[0]['split'])

    def test_quota_limit_and_determinism(self):
        candidates=[{'prefix':str(i),'image_sha256':str(i).zfill(64),'image_file':str(i)+'.jpg',
            'original_split':'train','original_classes':[0]} for i in range(30)]
        self.assertEqual(len(select_records(candidates)),20)
        self.assertEqual(select_records(candidates),select_records(candidates))
        with self.assertRaises(ValueError):select_records(candidates,21)


if __name__=='__main__':unittest.main()
