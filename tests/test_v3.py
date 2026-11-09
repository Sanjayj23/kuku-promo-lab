import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kuku_promo_lab.parser import ParseError, parse
from kuku_promo_lab.pipeline import run
from kuku_promo_lab.models import ModelClient
from kuku_promo_lab.schemas import DEFAULTS
from test_core import FIXTURE, mock_local, source


class ParserRecoveryTests(unittest.TestCase):
    def test_incomplete_timing_is_parse_error(self):
        original = source()['text']
        cases = [
            original.replace('Scene start: 00:00.00\n', '', 1),
            re.sub(r'^Scene start:.*\n', '', original, flags=re.M),
            re.sub(r'^Scene end:.*\n', '', original, flags=re.M),
        ]
        for text in cases:
            with self.subTest(text_length=len(text)):
                with self.assertRaisesRegex(ParseError, 'incomplete scene timing'):
                    parse(source(text))

    def test_bad_file_isolated_from_valid_episode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = root/'inputs'
            inputs.mkdir()
            (inputs/'broken.txt').write_text(source()['text'].replace('Scene start: 00:00.00\n', '', 1))
            (inputs/'valid.txt').write_bytes((FIXTURE/'orbit-2.txt').read_bytes())
            config = dict(DEFAULTS, editorial_version=1, cache_dir=str(root/'cache'))
            with patch.object(ModelClient, '_local', mock_local):
                manifest = run(inputs, root/'run', config)
            self.assertEqual(manifest['run_status'], 'partial')
            coverage = json.loads((root/'run/coverage.json').read_text())['episodes']
            self.assertEqual(next(x for x in coverage if x['source_name']=='broken.txt')['error_type'], 'ParseError')
            self.assertEqual(next(x for x in coverage if x['source_name']=='valid.txt')['analysis_status'], 'analyzed')

from kuku_promo_lab.editorial import editorial_result
from kuku_promo_lab.grounding import content_tokens, quote_in_words
from kuku_promo_lab.models import request_key
from test_v2 import packet_for, opener, ending
from test_core import judgment


def clean_case():
    packet = packet_for(['Drop the knife or I shoot.', 'Will you drop the knife?'])
    packet['duration_ms'] = packet['utterances'][-1]['end_ms']
    last = packet['utterances'][-1]
    answer = ending(packet, 'unanswered')
    answer.update(question='Will you drop the knife?', actor_voice=last['voice'])
    review = judgment(packet)
    review['unresolved_question'] = {'text': answer['question'], 'evidence_ids': [last['id']]}
    return packet, review, opener(packet['utterances'][0]['text']), answer


class GateThreeTests(unittest.TestCase):
    def check(self, case, gate=3):
        return editorial_result(*case, False, False, gate_version=gate)

    def test_v2_explicit_result_and_clean_v3(self):
        case = clean_case()
        expected = copy.deepcopy(case[1])
        expected['criteria']['hook'] = dict(score=2, evidence_ids=['U001'], reason=case[2]['reason'])
        expected['unresolved_question'] = dict(text=case[3]['question'], evidence_ids=['U002'])
        self.assertEqual(self.check(case, 2), (expected, 'A', [], []))
        output = self.check(case)
        self.assertEqual(output[1:], ('A', [], []))
        self.assertEqual(output[0], dict(expected, grounding={}))

    def test_entity_question_and_v2_probe(self):
        case = clean_case()
        case[3].update(question='Will she ring the bell before midnight?',
                       entities=[dict(entity='bell',source_quote='knife',utterance_id='U001')])
        self.assertEqual(self.check(case,2)[1], 'A')
        result,tier,flags,_ = self.check(case)
        self.assertEqual(tier,'B')
        self.assertIn('entity_not_in_quote',flags)
        self.assertIn('question_terms_unverified',flags)
        self.assertIn('bell',result['grounding']['question_terms_unverified'])

    def test_roles_evidence(self):
        case=clean_case()
        case[3]['actor_voice']=case[3]['target_voice']='voice_1'
        self.assertIn('role_evidence_mismatch', self.check(case)[2])
        case[3]['target_voice']=None
        self.assertIn('role_evidence_mismatch', self.check(case)[2])
        case[3]['actor_voice']='voice_2'
        self.assertNotIn('role_evidence_mismatch', self.check(case)[2])

    def test_hard_failure_vs_uncertainty(self):
        for key,value,reason in [('comprehension',1,'comprehension_below_3'),('coherence',None,'coherence_unknown')]:
            case=clean_case();case[1]['criteria'][key]['score']=value
            output=self.check(case)
            self.assertEqual(output[1],'Excluded' if value is not None else 'B')
            self.assertIn(reason,output[3])
        case=clean_case();case[1]['essential_context_missing']=True
        self.assertEqual(self.check(case)[1],'Excluded')
        self.assertEqual(self.check(case,2)[1],'B')

    def test_question_rewrite_and_paraphrase(self):
        case=clean_case();case[3]['question']='Will the captain launch the rocket?'
        self.assertIn('question_rewritten',self.check(case)[2])
        self.assertEqual(self.check(case,2)[1],'A')
        case[3]['question']='Will the knife drop?'
        self.assertNotIn('question_rewritten',self.check(case)[2])

    def test_opener_floor_and_token_alignment(self):
        case=clean_case();case[0]['utterances'][0]['text']='No.';case[2]['cue_quote']='No.'
        self.assertEqual(self.check(case,2)[1],'A')
        self.assertIn('minimal_opener',self.check(case)[2])
        case[0]['utterances'][0]['text']='Nobody leaves this room.'
        case[2]['cue_quote']='o'
        self.assertIn('cue_quote_not_word_aligned',self.check(case)[2])
        self.assertNotIn('minimal_opener',self.check(clean_case())[2])

    def test_later_speech_and_missing_end(self):
        case=clean_case();case[3]['ending_evidence_ids']=['U001']
        self.assertIn('speech_after_cited_ending',self.check(case)[2])
        case[3]['ending_evidence_ids']=[]
        self.assertIn('ending_evidence_missing',self.check(case)[2])
        self.assertNotIn('speech_after_cited_ending',self.check(clean_case())[2])

    def test_gate_does_not_change_request_identity(self):
        args=('reviewer',{'test':True},{},'fixed prompt')
        self.assertEqual(request_key(*args,dict(DEFAULTS,editorial_gate_version=2)),
                         request_key(*args,dict(DEFAULTS,editorial_gate_version=3)))
        self.assertTrue(quote_in_words('drop the knife','Drop the knife!'))
        self.assertFalse(quote_in_words('o','Nobody leaves.'))

from kuku_promo_lab.ranking import compare_family


class BoundaryDecisionTests(unittest.TestCase):
    def record(self, cid, duration, tier='A', flags=None):
        packet,review,op,answer=clean_case()
        return dict(candidate=dict(candidate_id=cid,variant=cid),duration_ms=duration,
                    start_ms=0,end_ms=duration,review=review,mapping={'U001':'b1'},tier=tier,
                    flags=flags or [],eligibility_errors=flags or [])

    def test_explicit_tie_break_and_tier_reason(self):
        a=self.record('base',30000);b=self.record('previous_end',25000)
        winner,comparison=compare_family([a,b])
        self.assertTrue(comparison['decision']['tie_break_only'])
        self.assertIn('tie-break',comparison['decision']['summary'])
        self.assertEqual(comparison['decision']['winner_interval_ms'],[0,25000])
        a=self.record('base',30000,'B',['speech_after_cited_ending'])
        _,comparison=compare_family([a,b])
        alt=comparison['decision']['alternatives'][0]
        self.assertEqual(alt['decided_by'],'tier')
        self.assertIn('speech_after_cited_ending',alt['flags_absent_on_winner'])

class FixtureAndHarnessTests(unittest.TestCase):
    def test_prompt_identity_lock(self):
        # Editing these prompts invalidates historical request identities and replay fixtures.
        expected=json.loads((FIXTURE.parent/'prompt_identity.json').read_text())
        root=Path(__file__).parents[1]/'src/kuku_promo_lab/prompts'
        self.assertEqual({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.md')},expected)

    def test_real_v2_fixture_replays_without_model(self):
        from kuku_promo_lab.validation import validate_run
        with tempfile.TemporaryDirectory() as folder:
            config=json.loads((FIXTURE.parent/'synthetic_replay_v2/run_manifest.json').read_text())['config']
            with patch.object(ModelClient,'_local',side_effect=AssertionError('Replay attempted inference')):
                m=run(FIXTURE,Path(folder)/'replay',config,replay_dir=FIXTURE.parent/'synthetic_replay_v2')
            self.assertEqual(m['inference']['attempts'],0)
            self.assertTrue(validate_run(FIXTURE,Path(folder)/'replay')['valid'])

    def test_harness_mock_transport_and_label(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('controls_script',Path(__file__).parents[1]/'scripts/run_editorial_controls.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def mocked(client,payload,schema,prompt,errors):
            if 'hook_present' in schema['properties']:
                value=opener(payload['utterances'][0]['text'])
            else:
                value=ending(payload,'unknown')
            return json.dumps(value),{},'TEST-MOCK','stop',{}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ModelClient,'_local',mocked):
                result=module.run_controls(DEFAULTS,Path(folder)/'controls',1)
            self.assertEqual(len(result['rows']),14)
            self.assertIn('not a holdout',result['label'])

class IndependentCheckerTests(unittest.TestCase):
    def test_reserved_tag_and_block_extent_fallback(self):
        import importlib.util
        import zipfile
        spec=importlib.util.spec_from_file_location('independent',Path(__file__).parents[1]/'scripts/independent_source_check.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        text=source()['text'].replace('**ENGINEER (Urgent):**','**LIGHTING (bright):**',1)
        text=re.sub(r'^Scene (?:start|end):.*\n','',text,flags=re.M)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'input.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('test.txt',text)
            run(archive,root/'run',dict(DEFAULTS,cache_dir=str(root/'cache')),mode='random')
            self.assertTrue(module.check(archive,root/'run')['valid'])
