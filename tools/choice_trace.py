"""Verify actual candidate prompts, replies and following game actions from a live log."""
import argparse
import json
from pathlib import Path


def trace(path):
    calls, active, queued, state = [], None, None, None
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row['kind'] == 'state': state = row['state']
        elif row['kind'] == 'model_input':
            if queued is not None: queued = None
            if row['stage'] == 'candidate_choice':
                payload = json.loads(row['prompt'].split('局面與候選：', 1)[1])
                active = {'prompt_chars': len(row['prompt']), 'has_full_hand': 'hand' in payload['player'],
                          'has_deck': 'deck' in payload['player'], 'plans': payload['plans'],
                          'hand_count': payload['hand_count'], 'hidden_hand_count': payload['hidden_hand_count'],
                          'candidate_cards': payload['candidate_cards'],
                          'posts': []}
                calls.append(active)
            else: active = None
        elif row['kind'] == 'model_call' and active is not None:
            active.update({k: v for k, v in row.items() if k not in {'kind', 't'}})
            answer = row.get('answer') or {}
            active['valid_choice'] = answer.get('choice') in [p['id'] for p in active['plans']] + ['reject']
        elif row['kind'] == 'model':
            decision = row['decision']
            if active and decision.get('candidate_id'):
                queued = active
                queued['selected_id'] = decision['candidate_id']
                selected = next(p for p in queued['plans'] if p['id'] == queued['selected_id'])
                cards = {c['index']: c['id'] for c in queued['candidate_cards']}
                queued['expected_card_ids'] = [cards[a['card_index']] for a in selected['sequence']]
                queued['expected_posts'] = len(selected['sequence']) + int(selected.get('confidence') == 'simulated' and selected.get('end_turn', False))
                queued['expected_actions'] = [{'action': 'play_card', 'card_id': cards[a['card_index']],
                                               'target': a.get('target') or ''} for a in selected['sequence']]
                if selected.get('confidence') == 'simulated' and selected.get('end_turn', False):
                    queued['expected_actions'].append({'action': 'end_turn', 'card_id': None, 'target': ''})
        elif row['kind'] == 'post' and queued is not None:
            body = row['body']
            card_id = next((c.get('id') for c in (state or {}).get('player', {}).get('hand', [])
                            if c.get('index') == body.get('card_index')), None) if body['action'] == 'play_card' else None
            queued['posts'].append({'t': row['t'], 'body': body, 'card_id': card_id, 'result': row['result']})
            queued['executed_card_ids'] = [p['card_id'] for p in queued['posts'] if p['body']['action'] == 'play_card']
            executed = [{'action': p['body']['action'], 'card_id': p['card_id'],
                         'target': p['body'].get('target') or ''} for p in queued['posts']]
            queued['sequence_matches'] = (executed == queued['expected_actions'] and
                                          all(p['result'].get('status') == 'ok' for p in queued['posts']))
            queued['completed_without_reasking'] = queued['sequence_matches']
            queued['post_intervals'] = [round(b['t'] - a['t'], 2)
                                        for a, b in zip(queued['posts'], queued['posts'][1:])]
            if len(queued['posts']) >= queued['expected_posts']: queued = None
    return {'candidate_calls': len(calls), 'accepted_calls': sum('selected_id' in c for c in calls),
            'rejected_calls': sum(c.get('answer', {}).get('choice') == 'reject' for c in calls),
            'calls': calls}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    print(json.dumps(trace(args.log), ensure_ascii=False, indent=2))
