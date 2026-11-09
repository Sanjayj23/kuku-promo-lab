"""Export first-three-second packets without showing raters later dialogue."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pairs', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    target = Path(args.out)
    if target.exists():
        raise SystemExit('Opener export exists; choose a new path')
    data = json.loads(Path(args.pairs).read_text())
    pairs = []
    for pair in data['pairs']:
        result = {'pair_id': pair['pair_id']}
        for side in ['A', 'B']:
            packet = pair[side]
            window = packet['hook_window_ms']
            utterances = [u for u in packet['utterances'] if u['end_ms'] <= window]
            assert {u['id'] for u in utterances} == set(packet['opener_ids'])
            result[side] = {
                'hook_window_ms': window,
                'utterances': [{k: u[k] for k in ['id', 'voice', 'delivery', 'text', 'start_ms', 'end_ms']}
                               for u in utterances],
                'contains_unaligned_spoken_prefix': any(u['crosses_opener_unknown_prefix'] for u in packet['utterances']),
            }
        pairs.append(result)
    output = {'status': 'pending_authorized_reviewers', 'pairs': pairs,
              'reading_policy': 'Show each opener before its full packet. No later dialogue, model interpretation, source mapping or system scores are included. An utterance crossing three seconds is omitted because word timing is unknown. Timed reading is not video retention.'}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'pairs_exported': len(pairs), 'human_ratings_collected': 0, 'out': str(target.resolve())}, indent=2))


if __name__ == '__main__':
    main()
