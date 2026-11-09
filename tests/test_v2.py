import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kuku_promo_lab.editorial import editorial_result, opener_payload, validate_ending, validate_opener
from kuku_promo_lab.models import InferenceError, ModelClient
from kuku_promo_lab.parser import ParseError, parse
from kuku_promo_lab.pipeline import run
from kuku_promo_lab.schemas import DEFAULTS, obj
from kuku_promo_lab.validation import validate_run
from test_core import FIXTURE, analysis_for, judgment, source

CONTROLS = json.loads((Path(__file__).parent/'fixtures/editorial_controls.json').read_text())


def packet_for(lines):
    utterances = [
        dict(id=f'U{i+1:03}', voice=f'voice_{i%2+1}', text=line, start_ms=i*2000, end_ms=i*2000+1800)
        for i, line in enumerate(lines)
    ]
    return {'utterances': utterances, 'opener_ids': ['U001'] if lines else [], 'duration_ms': 8000}


def opener(quote, positive=True):
    return dict(hook_present=positive, cue_quote=quote, cue_type='threat' if positive else 'none',
                specificity=int(positive), reason='Synthetic contract example.', evidence_scope='dialogue_only',
                uncertain=False)


def ending(packet, status):
    last = packet['utterances'][-1]
    return dict(question='What will happen next?', actor_voice=last['voice'], target_voice=None,
                ending_evidence_ids=[last['id']], ending_quote=last['text'], status=status,
                answer_quote=last['text'] if status in {'answered','disputed'} else '',
                unresolved_consequence='The decision remains consequential.', context='The speakers face a decision.',
                entities=[], supporting_evidence_ids=[], uncertainty_flags=[],
                uncertainty_reason='The roles are unclear.' if status == 'unknown' else '')


class ReliabilityTests(unittest.TestCase):
    def test_usage_unavailable_retains_answer(self):
        for usage in (None, {}, {'prompt_tokens': '12', 'completion_tokens': 4}):
            with self.subTest(usage=usage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                client = ModelClient(dict(DEFAULTS, cache_dir=str(root/'cache')), root/'run')
                with patch.object(client, '_local', return_value=('{}', usage, 'test', 'stop', {})):
                    self.assertEqual(client.generate('doctor', {}, obj({})), {})
                saved = json.loads(next(client.logs.glob('*.json')).read_text())
                self.assertEqual(saved['raw_text'], '{}')
                self.assertEqual(saved['usage']['tokens'], 'unavailable')
                self.assertEqual(client.usage['tokens'], 'unavailable')

    def test_malformed_envelope_is_logged_and_bounded(self):
        class BadTransport:
            def open(self, req, timeout):
                return io.BytesIO(b'{"choices":[]}')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = ModelClient(dict(DEFAULTS, cache_dir=str(root/'cache')), root/'run')
            with patch('urllib.request.build_opener', return_value=BadTransport()):
                with self.assertRaisesRegex(InferenceError, 'failed after 2 attempt'):
                    client.generate('doctor', {}, obj({}))
            self.assertEqual(len(list(client.logs.glob('*.json'))), 2)
            self.assertTrue(all(json.loads(p.read_text())['raw_envelope']=='{"choices":[]}'
                                for p in client.logs.glob('*.json')))

    def test_duplicate_scene_field_names_both_values(self):
        text = source()['text'].replace('Scene end: 00:32.00', 'Scene end: 00:32.00\nScene end: 00:40.00')
        with self.assertRaisesRegex(ParseError, r'ep-001 scene-001.*duplicate Scene end.*00:32.00.*00:40.00'):
            parse(source(text))

    def test_reserved_and_ambiguous_tags(self):
        text = source()['text'].replace('**ENGINEER (Urgent):**','**LIGHTING (bright):**',1)
        text = text.replace('**CAPTAIN (Calm):**','**UNLISTED (setting):**',1)
        text = text.replace('"Our crew is still outside."','An undescribed production cue.')
        ep = parse(source(text))
        self.assertEqual([b.kind for b in ep.blocks[:2]], ['lighting', 'ambiguous_tag'])
        self.assertTrue(any(w['code']=='ambiguous_tag' for w in ep.warnings))


class EditorialTests(unittest.TestCase):
    def test_payload_excludes_crossing_line_and_later_context(self):
        packet = packet_for(['allowed', 'crossing secret', 'later secret'])
        packet['utterances'][0]['end_ms'] = 3000
        packet['utterances'][1]['end_ms'] = 3001
        packet['private_metadata'] = 'another secret'
        self.assertEqual(opener_payload(packet), {
            'utterances': [{'voice': 'voice_1', 'start_ms': 0, 'end_ms': 3000, 'text': 'allowed'}]})

    def test_invented_quote_and_voice_are_rejected(self):
        packet = packet_for(['An original line.'])
        with self.assertRaisesRegex(ValueError, 'verbatim'):
            validate_opener(opener('Invented line.'), opener_payload(packet))
        answer = dict(ending(packet, 'unanswered'), actor_voice='voice_42')
        with self.assertRaisesRegex(ValueError, 'invented voice'):
            validate_ending(answer, packet)
        with self.assertRaisesRegex(ValueError, 'verbatim answer quote'):
            validate_ending(dict(ending(packet,'answered'), answer_quote='Invented answer.'), packet)

    def test_frozen_control_gate_contracts(self):
        # These are deterministic contracts. The live control harness judges the same frozen texts.
        for case in CONTROLS:
            with self.subTest(case=case['id']):
                expected = case['expected']
                if case['role'] == 'opener':
                    packet = packet_for([case['text']] if case['text'] else [])
                    verdict = opener(case['text'] if expected == 'pass' else '', expected == 'pass')
                    validate_opener(verdict, opener_payload(packet))
                    result = editorial_result(packet, judgment(packet) if case['text'] else None,
                                              verdict if case['text'] else None, None,
                                              expected=='visual_hook_unverified', False)
                    self.assertEqual(result[1], 'Excluded' if expected == 'fail' else 'B')
                    if expected == 'visual_hook_unverified':
                        self.assertIn(expected, result[2])
                else:
                    packet = packet_for(case['lines'])
                    verdict = ending(packet, expected)
                    validate_ending(verdict, packet)
                    _, tier, flags, _ = editorial_result(packet, judgment(packet), opener(case['lines'][0]),
                                                          verdict, False, False)
                    self.assertEqual(tier, {'answered':'Excluded','disputed':'B','unknown':'B','unanswered':'A'}[expected])
                    if tier != 'A':
                        self.assertTrue(flags)

    def test_v2_pipeline_replay_validation_and_exports(self):
        def local(client, payload, schema, prompt, errors):
            properties = schema['properties']
            if 'proposals' in properties:
                data = analysis_for(payload)
            elif 'hook_present' in properties:
                data = opener(payload['utterances'][0]['text'])
            elif 'ending_quote' in properties:
                data = ending(payload, 'unanswered')
            else:
                data = judgment(payload)
            return json.dumps(data), None, 'test', 'stop', {}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = dict(DEFAULTS, cache_dir=str(root/'cache'), max_families=2)
            with patch.object(ModelClient, '_local', local):
                run(FIXTURE, root/'fresh', config)
            self.assertTrue(validate_run(FIXTURE, root/'fresh')['valid'])
            recommendations = json.loads((root/'fresh/promos.json').read_text())['recommendations']
            self.assertTrue(recommendations)
            self.assertEqual(recommendations[0]['tier'], 'A')
            self.assertEqual(recommendations[0]['human_posting_order'], None)
            replay = run(FIXTURE, root/'replay', config, replay_dir=root/'fresh')
            self.assertEqual(replay['inference']['attempts'], 0)
            self.assertEqual(json.loads((root/'replay/promos.json').read_text())['recommendations'], recommendations)
