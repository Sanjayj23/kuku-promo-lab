"""V5 source check: positive and negative controls for each rule, plus integrity guarantees."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from kuku_promo_lab import source_check as sc
from kuku_promo_lab.studio_demo import make_demo

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT/'tests'/'fixtures'/'synthetic'


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob('*')):
        if p.is_file():
            h.update(p.relative_to(path).as_posix().encode()); h.update(p.read_bytes())
    return h.hexdigest()


def episode(lines, eid='ep-900'):
    """Tiny fake episode: [(start_s, end_s, text)] dialogue blocks with safe edges."""
    blocks, bounds = [], {}
    for i, (a, b, t) in enumerate(lines, 1):
        blk = NS(block_id=f'{eid}:b{i:04d}', kind='dialogue', speaker='S', text=f'"{t}"', start_ms=int(a*1000), end_ms=int(b*1000), scene_id='scene-001')
        blocks.append(blk)
        for ms in (blk.start_ms, blk.end_ms):
            bounds[f'{eid}:t{ms:09d}'] = {'ms': ms, 'safe': True}
    return NS(episode_id=eid, blocks=blocks, boundaries=bounds)


class SpeechActControls(unittest.TestCase):
    def test_explicit_assertions_do_not_become_requests_or_possibilities(self):
        for text in ['I can confirm the door is locked.', 'I accept your offer, please stop asking.',
                     'I accept, please stop asking.', 'Then I accept.', 'We refuse, please leave.']:
            self.assertEqual(sc.speech_act(text), 'assertion', text)
        for text in ['Please accept my offer.', 'I can accept tomorrow.', 'I will confirm the result tomorrow.']:
            self.assertNotEqual(sc.speech_act(text), 'assertion', text)
        self.assertEqual(sc.speech_act('I accept, if you pay me.'), 'conditional')

    def test_controls(self):
        cases = {
            'Lady Mara... accept my ring.': 'request_or_command',   # a proposal, not acceptance
            'Burn the letters. Burn every one.': 'request_or_command',  # a dare, nothing burned
            'The tonic may help draw the venom out.': 'possibility_or_intention',
            'If you sign that confession, I lose my farm.': 'conditional',
            'Will you marry me?': 'question',
            'Then I accept.': 'assertion',            # genuine acceptance: must NOT be disputed
            'I refuse your proposal.': 'assertion',   # genuine refusal: must NOT be disputed
            "I didn't!": 'denial',
        }
        for text, act in cases.items():
            self.assertEqual(sc.speech_act(text), act, text)


class EndingChecks(unittest.TestCase):
    def test_explicit_confirmation_and_polite_acceptance_are_not_disputed(self):
        for q, a in [('Is the door locked?', 'I can confirm the door is locked.'),
                     ('Did she accept the offer?', 'I accept your offer, please stop asking.')]:
            flags, details = sc.ending_check({'question': q, 'status': 'answered', 'answer_quote': a})
            self.assertNotIn('answer_not_a_resolution', flags)
            self.assertIn('advisory', details['assessment_basis'])

    def test_request_is_not_a_resolution(self):
        flags, _ = sc.ending_check({'question': 'Will Lady Mara accept the proposal?', 'status': 'answered',
                                    'answer_quote': 'Lady Mara... accept my ring.'})
        self.assertIn('answer_not_a_resolution', flags)

    def test_explicit_acceptance_stays_answered(self):
        flags, _ = sc.ending_check({'question': 'Will the woman accept the ring?', 'status': 'answered', 'answer_quote': 'Then I accept.'})
        self.assertNotIn('answer_not_a_resolution', flags)
        self.assertNotIn('answer_restates_question', flags)

    def test_restatement(self):
        flags, _ = sc.ending_check({'question': 'Who is the someone else on this channel?', 'status': 'answered',
                                    'answer_quote': 'There is someone else on this channel.'})
        self.assertIn('answer_restates_question', flags)
        self.assertIn('question_backward_or_background', flags)

    def test_attempt_is_not_outcome(self):
        flags, det = sc.ending_check(None, {'question': 'Will the tonic work in time?',
                                            'proposed_proposition': 'A healer is attempting to treat the sick man with a tonic.',
                                            'status': 'answered', 'answer_quote': 'The tonic may help draw the venom out.'})
        self.assertIn('proposition_attempt_not_outcome', flags)
        self.assertIn('answer_not_a_resolution', flags)
        self.assertIn('question_proposition_mismatch', flags)

    def test_aligned_question_and_proposition(self):
        flags, _ = sc.ending_check(None, {'question': 'Will Mara return the stolen ledger before midnight?',
                                          'proposed_proposition': 'Mara returns the stolen ledger before midnight.', 'status': 'unanswered'})
        self.assertNotIn('question_proposition_mismatch', flags)
        self.assertNotIn('proposition_attempt_not_outcome', flags)

    def test_unanswered_question_not_flagged_as_resolution(self):
        flags, _ = sc.ending_check({'question': 'Will the pilot stop the launch?', 'status': 'unanswered', 'answer_quote': ''})
        self.assertEqual(flags, [])


class Openers(unittest.TestCase):
    def test_strength(self):
        for t in ['No.', 'Ah!', 'Poison?', 'Mara!', 'Please...', 'Weeks.']:
            self.assertEqual(sc.opener_strength(t), 'minimal', t)
        for t in ['Someone is drugging the captain.', 'He will be hanged at dawn.', 'Say farewell to your brother.']:
            self.assertEqual(sc.opener_strength(t), 'substantive', t)
        self.assertEqual(sc.opener_strength(''), 'none')


class ClaimGrounding(unittest.TestCase):
    def test_reversed_participants_and_negation_never_ground_a_quotation(self):
        ep = episode([(0, 2, 'Rowan killed Mara.'), (4, 6, 'Mara did not sign the contract.')])
        for quote in ['Mara killed Rowan.', 'Mara did sign the contract.', 'Mara signed the contract.']:
            self.assertIsNone(sc.locate(quote, ep, 0, 10000), quote)
        claims = sc.audit_claims({'hook': {'text': '"Mara killed Rowan."', 'evidence_ids': ['ep-900:b0001']}}, ep, 0, 10000)
        self.assertEqual(claims[0]['status'], 'not_in_script')

    def test_contiguous_fragment_and_normalization_preserve_word_order(self):
        ep = episode([(0, 2, 'Mara, you must leave the ship now!')])
        self.assertIsNotNone(sc.locate('you must leave the ship', ep, 0, 3000))
        self.assertIsNotNone(sc.locate('MARA... you must leave the ship now.', ep, 0, 3000))
        self.assertIsNone(sc.locate('the ship must leave you', ep, 0, 3000))

    def setUp(self):
        self.ep = episode([(0, 2, 'He will be hanged at dawn.'), (4, 5, 'When?'), (6, 8, 'And when did you leave the ship?'),
                           (10, 13, 'The ship sails in two days.'), (30, 33, 'And now they have taken the child!')])

    def test_short_quote_needs_exact_line(self):
        b = sc.locate('When?', self.ep, 0, 25000)
        self.assertEqual(b.text, '"When?"')
        self.assertIsNone(sc.locate('Never?', self.ep))

    def test_in_cut_outside_and_invented(self):
        prop = {'hook': {'text': 'VOICE: "He will be hanged at dawn."', 'evidence_ids': ['ep-900:b0001']},
                'cliffhanger': {'text': '"And now they have taken the child!" and "You will regret this!"', 'evidence_ids': ['ep-900:b0004']}}
        claims = sc.audit_claims(prop, self.ep, 0, 14000)
        status = {c['quote']: c['status'] for c in claims}
        self.assertEqual(status['He will be hanged at dawn.'], 'in_cut')
        self.assertEqual(status['And now they have taken the child!'], 'after_cut')
        self.assertEqual(status['You will regret this!'], 'not_in_script')
        flags = sc.claim_flags(claims)
        self.assertIn('analyst_quote_outside_cut', flags)
        self.assertIn('analyst_quote_not_in_script', flags)
        self.assertIn('analyst_cited_id_mismatch', flags)  # cited b0004 is not the quoted line

    def test_prose_claim_is_marked_unverifiable(self):
        claims = sc.audit_claims({'hook': {'text': 'A tense confrontation begins.', 'evidence_ids': []}}, self.ep, 0, 14000)
        self.assertEqual(claims[0]['status'], 'unquoted_description')

    def test_repair_reaches_described_ending(self):
        prop = {'cliffhanger': {'text': '"And now they have taken the child!"', 'evidence_ids': []}}
        e = sc.edges(self.ep, 0, 14000)
        rep = sc.repairs(self.ep, 0, 14000, e, sc.audit_claims(prop, self.ep, 0, 14000), min_ms=10000)
        self.assertTrue(any(r['kind'] == 'reach_claimed_ending' and r['end_ms'] == 33000 for r in rep))
        self.assertTrue(all(r['status'] == 'suggested_not_model_reviewed' for r in rep))


class TierRule(unittest.TestCase):
    def test_never_creates_tier_a_and_only_reroutes_sole_disputed_answer(self):
        rec = {'tier': 'Excluded', 'eligibility_errors': ['question_answered']}
        self.assertEqual(sc.v5_tier(rec, ['answer_not_a_resolution']), 'B')
        self.assertEqual(sc.v5_tier(rec, []), 'Excluded')
        rec2 = {'tier': 'Excluded', 'eligibility_errors': ['question_answered', 'comprehension_below_3']}
        self.assertEqual(sc.v5_tier(rec2, ['answer_not_a_resolution']), 'Excluded')
        self.assertEqual(sc.v5_tier({'tier': 'B', 'eligibility_errors': []}, ['answer_not_a_resolution']), 'B')
        self.assertEqual(sc.v5_tier({'tier': 'A', 'eligibility_errors': []}, ['opening_minimal']), 'A')


class RunLevel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.demo = Path(cls.tmp.name)/'demo'
        make_demo(cls.demo)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_zero_calls_new_directory_and_input_untouched(self):
        run = self.demo/'synthetic-v2-gate3-replay'
        before = tree_hash(run)
        out = Path(self.tmp.name)/'v5'
        summary = sc.write_check(SYN, run, out)
        self.assertEqual(tree_hash(run), before)
        self.assertEqual(summary['model_calls'], 0)
        self.assertEqual(summary['tier_A_created'], 0)
        for name in ('v5_source_check.jsonl', 'v5_analyst_audit.jsonl', 'v5_summary.json', 'v5_editor_briefs.csv'):
            self.assertTrue((out/name).is_file(), name)
        self.assertEqual((out/'promos.json').read_bytes(), (run/'promos.json').read_bytes())
        flags = [f for l in (out/'v5_source_check.jsonl').read_text().splitlines() for f in json.loads(l)['v5_flags']]
        self.assertIn('answer_restates_question', flags)  # the synthetic "someone else on this channel" misjudgment
        with self.assertRaises(ValueError):
            sc.write_check(SYN, run, out)

    def test_refuses_mismatched_pack(self):
        other = Path(self.tmp.name)/'other'
        shutil.copytree(SYN, other)
        (other/'orbit-1.txt').write_text((SYN/'orbit-1.txt').read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'pack_hash'):
            sc.write_check(other, self.demo/'synthetic-v1-replay', Path(self.tmp.name)/'bad')


if __name__ == '__main__':
    unittest.main()

class SingleQuotedClaims(unittest.TestCase):
    def test_invented_single_quotes_are_flagged(self):
        ep = episode([(0, 2, 'The ship leaves at dawn.')])
        for text in ["The opener says 'The dragon stole a crown.'", 'The opener says ‘The dragon stole a crown.’']:
            claims = sc.audit_claims({'hook': {'text': text}}, ep, 0, 10000)
            self.assertIn('analyst_quote_not_in_script', sc.claim_flags(claims))

    def test_contractions_and_possessives_inside_quotes(self):
        ep = episode([(0, 2, "I don't have the captain's key.")])
        for text in ["The opener says 'I don't have the captain's key.'", "The opener says ‘I don’t have the captain’s key.’"]:
            claims = sc.audit_claims({'hook': {'text': text}}, ep, 0, 10000)
            self.assertEqual(claims[0]['status'], 'in_cut')

    def test_unquoted_apostrophes_remain_unverified(self):
        ep = episode([(0, 2, 'The ship leaves at dawn.')])
        claims = sc.audit_claims({'hook': {'text': "The captain's plan doesn't work."}}, ep, 0, 10000)
        self.assertEqual(claims[0]['status'], 'unquoted_description')
