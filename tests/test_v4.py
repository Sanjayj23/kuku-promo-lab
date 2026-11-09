"""Synthetic V4 contract/regression tests. Mock responses are NOT inference evidence."""
import copy
import hashlib
import json
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

from kuku_promo_lab.analyst import analyst_payload, base_windows, legacy_base_windows
from kuku_promo_lab.discovery_v4 import (boundary_options, complete_opener, deduplicate_events,
                                         menu, select_bases)
from kuku_promo_lab.editorial_v4 import (binding_flags, editorial_result, ending_schema, restricted_packet,
    ending_view, full_review_schema, opener_payload, opener_schema, validate_evidence,
    validate_full_review, validate_v4_ending, validate_v4_opener)
from kuku_promo_lab.models import ModelClient, check_schema, request_key
from kuku_promo_lab.pipeline import run
from kuku_promo_lab.projection import project
from kuku_promo_lab.schemas import CRITERIA, DEFAULTS, canonical, config_from, plain
from kuku_promo_lab.timeline import candidate, coordinates, validate_cut
from kuku_promo_lab.validation import validate_run
from test_core import FIXTURE, analysis_for, episode


def v4_config(**changes):
    config = config_from(Path(__file__).parents[1]/'config/v4.json')
    config.update(provider='local',max_attempts=100,model_sha256='0'*64,runtime_identity='TEST-MOCK-NOT-INFERENCE')
    config.update(changes)
    return config


def ref(packet, index=0):
    u = packet['utterances'][index]
    return {'utterance_id': u['id'], 'quote': u['text']}


def evidence_model(packet):
    first, last = packet['utterances'][0], packet['utterances'][-1]
    target = next((u for u in packet['utterances'] if u['voice'] == 'voice_3'), None)
    actor = next((u for u in packet['utterances'] if 'pull in' in u['text'] and u['voice']=='voice_1'), None)
    return {'proposition': 'voice_1 pulls in voice_3',
        'actor': {'label': 'voice_1', 'voice': first['voice'], 'support': 'supported' if actor else 'uncertain',
                  'basis': 'self_statement' if actor else 'unknown',
                  'evidence': [{'utterance_id':actor['id'],'quote':actor['text']}] if actor else []},
        'target': {'label': 'voice_3' if target else 'unknown', 'voice': target['voice'] if target else None,
                   'support': 'supported' if target else 'uncertain',
                   'basis': 'self_statement' if target else 'unknown',
                   'evidence': [{'utterance_id':target['id'],'quote':target['text']}] if target else []},
        'entities': [], 'reference_chains': [],
        'opener_evidence': [ref(packet, 0)] if first['id'] in packet['opener_ids'] else [],
        'ending_evidence': [ref(packet, -1)], 'local_stakes': [ref(packet, -1)], 'uncertainty': []}


def full_review(packet):
    ids = [u['id'] for u in packet['utterances']]
    return {'interpretation': {'text': 'Synthetic test judgment; not an editorial finding.', 'evidence_ids': ids[:1]},
        'missing_context': [], 'essential_context_missing': False,
        'unresolved_question': {'text': 'Will voice_1 pull in voice_3?', 'evidence_ids': ids[-1:]},
        'criteria': {k: {'score': 3 if k != 'hook' or packet['opener_ids'] else 0,
                          'evidence_ids': packet['opener_ids'][:1] if k == 'hook' else ids[-1:],
                          'reason': 'Authored mock contract response.'} for k in CRITERIA},
        'uncertainty_flags': [], 'evidence_model': evidence_model(packet)}


def opening(packet):
    return {'hook_present': True, 'cue_quote': packet['utterances'][0]['text'],
            'cue_type': 'threat', 'specificity': 2, 'reason': 'Authored mock contract response.',
            'evidence_scope': 'dialogue_only', 'uncertain': False, 'standalone': 'supported',
            'evidence_ids': packet['opener_ids'][:1]}


def ending(packet, question=None, proposition=None):
    return {'question': question or 'Will voice_1 pull in voice_3?',
        'proposed_proposition': proposition or 'voice_1 pulls in voice_3',
        'status': 'unanswered', 'answer_kind': 'none', 'answer_quote': '', 'answer_evidence': [],
        'ending_evidence': [ref(packet, -1)], 'context': 'Authored synthetic context.',
        'unresolved_consequence': 'Authored synthetic consequence.', 'evidence_model': evidence_model(packet),
        'uncertainty': []}


def mock_v4(self, payload, schema, prompt, errors):
    if 'proposals' in schema['properties']:
        value = analysis_for(payload)
        pair = next((p for p in payload['allowed_base_windows'] if p[0].endswith('000000000') and p[1].endswith('000031000')), payload['allowed_base_windows'][0])
        p = value['proposals'][0];p['start_boundary_id'], p['end_boundary_id'] = pair[:2]
        contained = [b for b in payload['blocks'] if int(pair[0].split(':t')[1]) <= b[4] < b[5] <= int(pair[1].split(':t')[1])]
        speech = [b for b in contained if b[2] == 'dialogue']
        p['hook']['evidence_ids'] = [speech[0][0]];p['cliffhanger']['evidence_ids'] = [speech[-1][0]]
        p.update(event_anchor_ids=[speech[-1][0]], event_type='rescue', pending_change='Synthetic pending event')
    elif 'proposed_proposition' in schema['properties']:
        value = ending(payload, payload['proposed_question'], payload['proposed_proposition'])
        value['evidence_model']['proposition'] = payload['proposed_proposition']
    elif 'standalone' in schema['properties']:
        value = opening(dict(payload, opener_ids=[u['id'] for u in payload['utterances']]))
    else:
        value = full_review(payload)
    return json.dumps(value), {'prompt_tokens': 1, 'completion_tokens': 1}, 'TEST-MOCK-NOT-INFERENCE', 'stop', {'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': canonical(payload)}]}


class DiscoveryV4Tests(unittest.TestCase):
    def test_refill_retains_original_feasible_pairs_and_source_constraints(self):
        ep=episode();config=v4_config()
        old=legacy_base_windows(ep,config);new,audit=menu(ep,config,old)
        feasible={p for p in old if complete_opener(ep.boundaries[p[0]]['ms'],ep)}
        self.assertTrue(feasible <= set(new));self.assertEqual(len(new),len(set(new)))
        self.assertLessEqual(len(new),48)
        for s,e in new:
            self.assertFalse(validate_cut(candidate(ep,s,e,'test'),ep,config))
            self.assertTrue(complete_opener(ep.boundaries[s]['ms'],ep))
        self.assertTrue(all(x['route']=='visual_or_human_review' for x in audit['unknown_openings']))

    def test_legacy_menu_is_unchanged_without_opt_in(self):
        ep=episode();self.assertEqual(base_windows(ep,DEFAULTS),legacy_base_windows(ep,DEFAULTS))
        config=v4_config(v4_menu_policy='original')
        self.assertEqual(base_windows(ep,config),legacy_base_windows(ep,config))

    def test_v4_context_does_not_call_a_distant_episode_adjacent(self):
        ep=episode();index=[{'episode_id':ep.episode_id,'metadata':{}},
                            {'episode_id':'ep-015','metadata':{'future':'must not imply adjacency'}}]
        self.assertEqual(len(analyst_payload(ep,index,v4_config())['metadata_context']),1)
        self.assertEqual(len(analyst_payload(ep,index,DEFAULTS)['metadata_context']),2)

    def test_duplicate_events_not_counted_as_boundary_discoveries(self):
        ep=episode();config=v4_config();s,e=base_windows(ep,config)[0]
        cut=candidate(ep,s,e,'f');p={'scores':{k:3 for k in CRITERIA},'event_summary':'Synthetic event', 'event_anchor_ids':[ep.blocks[0].block_id]}
        kept,rejected=deduplicate_events([(cut,p),(candidate(ep,s,e,'second-family'),p)],{ep.episode_id:ep})
        self.assertEqual((len(kept),len(rejected)),(1,1));self.assertEqual(rejected[0]['classification'],'provisional_duplicate_not_semantic_proof')

    def test_distinct_nonoverlapping_events_remain_available(self):
        ep=episode();config=v4_config();a=candidate(ep,'ep-001:t000000000','ep-001:t000023000','a');b=candidate(ep,'ep-001:t000034000','ep-001:t000058000','b')
        p=lambda block:{'scores':{k:3 for k in CRITERIA},'event_summary':'Test event','event_anchor_ids':[block]}
        kept,_=deduplicate_events([(a,p(ep.blocks[0].block_id)),(b,p(ep.blocks[-2].block_id))],{'ep-001':ep})
        self.assertEqual(len(kept),2)

    def test_combined_cut_is_bounded_and_moves_both_edges(self):
        ep=episode();config=v4_config();base=candidate(ep,'ep-001:t000006000','ep-001:t000031000','f')
        extras,_=boundary_options(base,ep,config)
        self.assertLessEqual(len(extras),3);joint=[c for c in extras if c.variant=='combined_start_end']
        self.assertLessEqual(len(joint),1)
        for c in extras:self.assertFalse(validate_cut(c,ep,config))
        for c in joint:
            self.assertNotEqual(c.start_boundary_id,base.start_boundary_id);self.assertNotEqual(c.end_boundary_id,base.end_boundary_id)
        self.assertEqual(boundary_options(base,ep,v4_config(max_variants=1))[0],[])

    def test_invalid_duration_not_rescued_by_combined_option(self):
        ep=episode();config=v4_config();bad=candidate(ep,'ep-001:t000000000','ep-001:t000060880','bad')
        self.assertIn('duration',validate_cut(bad,ep,config))

    def test_pilot_configuration_is_opt_in_and_bounded(self):
        config=v4_config();self.assertEqual(DEFAULTS['discovery_version'],1)
        for change in [{'max_proposals_per_episode':3},{'v4_extra_reviews':25},{'max_variants':5},{'max_base_windows':49}]:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'cfg.json';p.write_text(json.dumps(dict(config,**change)))
                with self.assertRaises(ValueError):config_from(p)

    def test_episode_diversity_precedes_second_event_from_same_episode(self):
        ep=episode();other=copy.deepcopy(ep);other.episode_id='ep-002'
        for b in other.blocks:b.block_id=b.block_id.replace('ep-001','ep-002')
        from kuku_promo_lab.timeline import register
        register(other)
        p=lambda score:{'scores':{k:score for k in CRITERIA},'event_summary':'Synthetic event','event_type':'rescue'}
        a=candidate(ep,'ep-001:t000000000','ep-001:t000023000','a')
        b=candidate(ep,'ep-001:t000034000','ep-001:t000058000','b')
        c=candidate(other,'ep-002:t000000000','ep-002:t000023000','c')
        selected,_,log=select_bases([(a,p(4)),(b,p(4)),(c,p(2))],{'ep-001':ep,'ep-002':other},2)
        self.assertEqual({cut.episode_id for cut,_ in selected},{'ep-001','ep-002'})
        self.assertTrue(all(row['event_group_status']=='provisional_not_human_verified' for row in log))


class SemanticV4Tests(unittest.TestCase):
    def setUp(self):
        self.ep=episode();self.packet=project(candidate(self.ep,'ep-001:t000000000','ep-001:t000031000','f'),self.ep)[0]

    def test_schema_and_quote_provenance_for_structured_record(self):
        full=full_review(self.packet);check_schema(full,full_review_schema(self.packet));validate_full_review(full,self.packet)
        value=ending(self.packet);check_schema(value,ending_schema(self.packet,value['question'],value['proposed_proposition']))
        validate_v4_ending(value,self.packet,value['question'],value['proposed_proposition'])
        self.assertEqual(ending_view(value)['question'],value['question'])

    def test_source_id_grammar_excludes_nonexistent_voices_and_utterances(self):
        full=full_review(self.packet);full['evidence_model']['actor']['voice']='voice_99'
        with self.assertRaises(ValueError):check_schema(full,full_review_schema(self.packet))

    def test_uncited_interpretation_cannot_pass_runtime_validation(self):
        full=full_review(self.packet);full['interpretation']['evidence_ids']=[]
        with self.assertRaises(ValueError):validate_full_review(full,self.packet)
        full=full_review(self.packet);full['evidence_model']['actor']['evidence'][0]['utterance_id']='U999'
        with self.assertRaises(ValueError):check_schema(full,full_review_schema(self.packet))

    def test_no_forward_question_is_an_explicit_valid_abstention(self):
        full=full_review(self.packet);full['unresolved_question']={'text':'','evidence_ids':[]}
        full['evidence_model']['proposition']='';full['criteria']['cliffhanger']['score']=0
        full['evidence_model']['uncertainty']=['No supported forward question']
        check_schema(full,full_review_schema(self.packet));validate_full_review(full,self.packet)
        self.assertEqual(editorial_result(self.packet,full,opening(self.packet),None)[1],'Excluded')

    def test_cold_opener_payload_has_no_later_context(self):
        payload=opener_payload(self.packet)
        self.assertEqual(set(payload),{'duration_ms','utterances'});self.assertEqual(payload['duration_ms'],3000)
        self.assertTrue(all(u['end_ms']<=3000 for u in payload['utterances']))
        self.assertNotIn('metadata',canonical(payload));self.assertNotIn('source_sha256',canonical(payload))
        self.assertNotIn(self.packet['utterances'][-1]['text'],canonical(payload))

    def test_hidden_context_is_rejected_before_transport(self):
        for key in ['metadata','canonical_identities','analyst_rationale','source_auditor_conclusion']:
            packet=copy.deepcopy(self.packet);packet[key]='must not reach a reader'
            with self.subTest(key=key), self.assertRaises(ValueError):restricted_packet(packet)
        packet=copy.deepcopy(self.packet);packet['utterances'][0]['source_speaker']='HIDDEN'
        with self.assertRaises(ValueError):restricted_packet(packet)

    def test_opener_cannot_cite_later_speech_or_partial_word(self):
        value=opening(self.packet);value['evidence_ids']=[self.packet['utterances'][-1]['id']]
        with self.assertRaises(ValueError):validate_v4_opener(value,self.packet)
        value=opening(self.packet);value['cue_quote']='air'
        with self.assertRaises(ValueError):validate_v4_opener(value,self.packet)

    def test_fixed_question_cannot_be_rewritten(self):
        value=ending(self.packet);value['question']='Will they later escape?'
        with self.assertRaises(ValueError):validate_v4_ending(value,self.packet,'Will voice_1 pull in voice_3?',value['proposed_proposition'])

    def test_offer_request_threat_question_cannot_be_called_an_answer(self):
        for kind in ['offer','request','threat','question']:
            value=ending(self.packet);value.update(status='answered',answer_kind=kind,answer_quote=ref(self.packet,-1)['quote'],answer_evidence=[ref(self.packet,-1)])
            with self.subTest(kind=kind), self.assertRaises(ValueError):validate_v4_ending(value,self.packet,value['question'],value['proposed_proposition'])

    def test_answered_and_contradicted_do_not_export_as_unanswered(self):
        for status in ['answered','contradicted']:
            value=ending(self.packet);value.update(status=status,answer_kind='assertion',answer_quote=ref(self.packet,-1)['quote'],answer_evidence=[ref(self.packet,-1)])
            result=editorial_result(self.packet,full_review(self.packet),opening(self.packet),value)
            self.assertEqual(result[1],'Excluded');self.assertIn('question_'+status,result[2])

    def test_dispute_and_unknown_require_review_not_a_definitive_answer(self):
        for status in ['disputed','unknown']:
            value=ending(self.packet);value.update(status=status,uncertainty=['Synthetic uncertainty'])
            if status=='disputed':value.update(answer_kind='denial',answer_quote=ref(self.packet,-1)['quote'],answer_evidence=[ref(self.packet,-1)])
            result=editorial_result(self.packet,full_review(self.packet),opening(self.packet),value)
            self.assertEqual(result[1],'B')

    def test_literal_object_not_grounded_by_one_shared_token(self):
        packet=copy.deepcopy(self.packet);packet['utterances'][0]['text']='Bring back my ring.'
        model=evidence_model(packet);model['entities']=[{'entity':'ring the bell','source_mention':'ring',
            'source_quote':'Bring back my ring.','utterance_id':'U001','kind':'physical_object',
            'support':'supported','question_terms':['bell']}]
        validate_evidence(model,packet)
        self.assertIn('entity_mention_not_grounded',binding_flags(model,packet)[0])

    def test_unsupported_entity_can_abstain_without_fabricating_a_quote(self):
        full=full_review(self.packet)
        full['evidence_model']['entities']=[{'entity':'bell','source_mention':'','source_quote':'',
            'utterance_id':None,'kind':'physical_object','support':'unsupported','question_terms':['bell']}]
        check_schema(full,full_review_schema(self.packet));validate_full_review(full,self.packet)
        self.assertIn('entity_uncertain_or_unsupported',binding_flags(full['evidence_model'],self.packet)[0])
        full['evidence_model']['entities'][0]['support']='supported'
        with self.assertRaises(ValueError):validate_full_review(full,self.packet)

    def test_swapped_self_statement_voice_is_flagged(self):
        model=evidence_model(self.packet);model['actor']['voice']='voice_2'
        self.assertIn('participant_binding_mismatch',binding_flags(model,self.packet)[0])
        model['actor']['basis']='mentioned'
        self.assertIn('participant_voice_not_established',binding_flags(model,self.packet)[0])

    def test_explicit_addressee_is_not_inferred_to_be_the_speaker(self):
        model=evidence_model(self.packet);model['actor']['basis']='explicit_address'
        self.assertIn('speaker_addressee_binding_mismatch',binding_flags(model,self.packet)[0])

    def test_absent_actor_is_not_falsely_mapped_to_present_voice(self):
        model=evidence_model(self.packet);model['actor'].update(voice=None,basis='mentioned')
        validate_evidence(model,self.packet)
        self.assertNotIn('participant_voice_not_established',binding_flags(model,self.packet)[0])
        model['actor']['support']='uncertain'
        self.assertIn('participant_uncertain',binding_flags(model,self.packet)[0])

    def test_antecedent_chain_is_cited_and_chronological(self):
        model=evidence_model(self.packet);model['reference_chains']=[{'antecedent':ref(self.packet,-1),'referring_expression':ref(self.packet,0),'meaning':'test','resolution':'supported'}]
        with self.assertRaises(ValueError):validate_evidence(model,self.packet)
        model['reference_chains'][0].update(antecedent=ref(self.packet,0),referring_expression=ref(self.packet,-1),resolution='uncertain')
        validate_evidence(model,self.packet);self.assertIn('reference_chain_uncertain',binding_flags(model,self.packet)[0])

    def test_semantic_paraphrase_clearance_is_explicit_and_audited(self):
        packet=copy.deepcopy(self.packet);packet['utterances'][-1]['text']='Pay me before midnight.'
        full=full_review(packet);full['unresolved_question']['text']='Will voice_1 be paid before midnight?'
        full['evidence_model']['proposition']='voice_1 receives payment before midnight'
        value=ending(packet,full['unresolved_question']['text'],full['evidence_model']['proposition'])
        value['evidence_model']['proposition']=full['evidence_model']['proposition']
        value['evidence_model']['entities']=[{
            'entity':'payment','source_mention':'Pay','source_quote':'Pay me before midnight.',
            'utterance_id':packet['utterances'][-1]['id'],'kind':'event','support':'supported','question_terms':['paid']}]
        full['evidence_model']['entities']=copy.deepcopy(value['evidence_model']['entities'])
        review,tier,flags,_=editorial_result(packet,full,opening(packet),value)
        self.assertIn('question_terms_unverified',review['grounding']['legacy_flags'])
        self.assertNotIn('question_terms_unverified',flags)
        self.assertEqual(review['grounding']['resolved_flags'][0]['terms'],['paid'])
        full['evidence_model']['entities']=[]
        self.assertIn('question_terms_unverified',editorial_result(packet,full,opening(packet),value)[2])

    def test_incompatible_legacy_request_keys_cannot_be_reused(self):
        config=v4_config();payload=self.packet;schema=full_review_schema(payload)
        self.assertNotEqual(request_key('reviewer',payload,schema,'same',config),request_key('v4/reviewer',payload,schema,'same',config))
        root=Path(__file__).parents[1]/'src/kuku_promo_lab/prompts'
        expected=json.loads((FIXTURE.parent/'prompt_identity.json').read_text())
        self.assertEqual({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.md')},expected)


class PipelineV4Tests(unittest.TestCase):
    def test_mock_pipeline_export_validation_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            config=v4_config(v4_initial_candidates=2,v4_extra_reviews=3,v4_extra_families=1,
                             cache_dir=str(Path(d)/'cache'))
            root=Path(d)/'run'
            with patch.object(ModelClient,'_local',mock_v4):manifest=run(FIXTURE,root,config)
            self.assertEqual(manifest['run_status'],'complete')
            self.assertGreaterEqual(manifest['statistics']['recommendations'],1)
            self.assertLessEqual(manifest['statistics']['variants_reviewed'],5)
            self.assertTrue(manifest['run_id'].startswith('run-v4-'))
            result=validate_run(FIXTURE,root);self.assertTrue(result['valid'],result)
            with patch.object(ModelClient,'_local',side_effect=AssertionError('Replay must not infer')):
                replay=run(FIXTURE,Path(d)/'replay',config,replay_dir=root)
            self.assertEqual(replay['inference']['attempts'],0)
            self.assertEqual(json.loads((root/'promos.json').read_text())['recommendations'],json.loads((Path(d)/'replay/promos.json').read_text())['recommendations'])

    def test_episode_limit_blocks_full_pack_before_inference(self):
        with tempfile.TemporaryDirectory() as d, patch.object(ModelClient,'_local',side_effect=AssertionError('No inference')):
            with self.assertRaisesRegex(ValueError,'episode limit'):
                run(FIXTURE,Path(d)/'run',v4_config(v4_episode_limit=1))
            self.assertFalse((Path(d)/'run').exists())

    def test_saved_response_provenance_survives_checksum_recalculation(self):
        from kuku_promo_lab.export import checksums
        with tempfile.TemporaryDirectory() as d:
            config=v4_config(v4_initial_candidates=1,v4_extra_reviews=0,cache_dir=str(Path(d)/'cache'))
            root=Path(d)/'run'
            with patch.object(ModelClient,'_local',mock_v4):run(FIXTURE,root,config)
            path=root/'candidate_families.jsonl';families=[json.loads(x) for x in path.read_text().splitlines()]
            row=families[0]['variants'][0]
            row['full_review']['interpretation']['text']='An unrecorded invented interpretation.'
            row['review']['interpretation']['text']='An unrecorded invented interpretation.'
            path.write_text(''.join(json.dumps(f)+'\n' for f in families));checksums(root)
            result=validate_run(FIXTURE,root)
            self.assertFalse(result['valid']);self.assertIn('v4_saved_response_provenance_mismatch',canonical(result))

    def test_v4_rejects_historical_prior_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'historical'):
                run(FIXTURE,Path(d)/'run',v4_config(),prior_dir='not-read')

    def test_unidentified_live_model_is_blocked_before_transport(self):
        with tempfile.TemporaryDirectory() as d, patch.object(ModelClient,'_local',side_effect=AssertionError('No transport')):
            with self.assertRaisesRegex(ValueError,'checkpoint SHA256'):
                run(FIXTURE,Path(d)/'run',v4_config(model_sha256='unknown'))
            self.assertFalse((Path(d)/'run').exists())

    def test_validation_failure_is_partial_and_abstains(self):
        def malformed(self,*args):return '{',{},'TEST-MOCK','stop',{}
        with tempfile.TemporaryDirectory() as d:
            config=v4_config(cache_dir=str(Path(d)/'cache'))
            with patch.object(ModelClient,'_local',malformed):result=run(FIXTURE,Path(d)/'run',config)
            self.assertEqual(result['run_status'],'partial');self.assertEqual(result['statistics']['recommendations'],0)
            self.assertGreater(result['inference']['attempts'],0)

    def test_attempt_budget_is_global(self):
        with tempfile.TemporaryDirectory() as d:
            config=v4_config(cache_dir=str(Path(d)/'cache'));config['max_attempts']=1
            with patch.object(ModelClient,'_local',side_effect=OSError('Synthetic timeout')):
                result=run(FIXTURE,Path(d)/'run',config)
            self.assertEqual(result['inference']['attempts'],1);self.assertEqual(result['run_status'],'partial')

    def test_v4_evaluation_preserves_blank_human_fields_and_zero_v3_reference(self):
        from kuku_promo_lab.evaluate import evaluate
        import csv
        with tempfile.TemporaryDirectory() as d:
            config=v4_config(v4_initial_candidates=1,v4_extra_reviews=0,cache_dir=str(Path(d)/'cache'))
            root=Path(d)/'run'
            with patch.object(ModelClient,'_local',mock_v4):run(FIXTURE,root,config)
            reference=Path(d)/'mock-v3-reference';reference.mkdir()
            (reference/'run_manifest.json').write_text(json.dumps({'run_id':'TEST-REFERENCE-NOT-A-RUN','config':{'editorial_gate_version':3}}))
            (reference/'promos.json').write_text(json.dumps({'recommendations':[]}))
            out=Path(d)/'evaluation';evaluate(root,out,reference)
            metrics=json.loads((out/'metrics.json').read_text())
            self.assertGreater(metrics['analyst_responses_recorded'],0)
            self.assertEqual(metrics['invalid_analyst_responses'],0)
            self.assertEqual(metrics['v4']['v3_reference']['actual_production_recommendations'],0)
            self.assertFalse(metrics['v4']['v3_reference']['exploratory_B_cuts_counted_as_recommendations'])
            ratings=list(csv.DictReader((out/'human_ratings.csv').open()))
            identifiers={'pair_id','reviewer_slot','first_exposure'}
            self.assertTrue(all(not value for row in ratings for key,value in row.items() if key not in identifiers))


class HarnessV4Tests(unittest.TestCase):
    @staticmethod
    def module(name):
        path=Path(__file__).parents[1]/'scripts'/f'{name}.py'
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module);return module

    def test_role_diagnostics_do_not_claim_mock_identity_or_human_evidence(self):
        module=self.module('run_semantic_model_comparison')
        ep=episode();packet=project(candidate(ep,'ep-001:t000000000','ep-001:t000031000','test'),ep)[0]
        case={'id':'unit-only','origin':'synthetic_mock_not_inference','packet':packet,
              'question':'Will voice_1 pull in voice_3?','proposition':'voice_1 pulls in voice_3'}
        with tempfile.TemporaryDirectory() as d, patch.object(ModelClient,'_local',mock_v4):
            result=module.run_comparison([case],v4_config(model_sha256='0'*64),Path(d)/'diagnostic')
            self.assertFalse(result['response_identity_matches_alias'])
            self.assertEqual(result['independent_source_truth_audit'],'pending')
            self.assertIsNone(result['recommendations'])

    def test_discovery_ablations_change_one_factor_without_forcing_two_events(self):
        module=self.module('run_discovery_ablation_v4')
        with tempfile.TemporaryDirectory() as d, patch.object(ModelClient,'_local',mock_v4):
            result=module.ablate(FIXTURE,v4_config(model_sha256='0'*64),Path(d)/'ablation')
            self.assertEqual(result['total_attempts'],6)
            self.assertEqual(len(result['arms']),3)
            self.assertTrue(all(row['raw_proposals']==2 for row in result['arms']))
