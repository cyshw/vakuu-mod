"""Run takeover with the current repo code and log every state, model decision and action as JSONL.

Used to test changes without restarting the game: the panel's bridge keeps the code it loaded at
game start, while this runs the files on disk. Do not touch the game while it runs.

    python tools/takeover_log.py --steps 50 --model auto:
    python tools/takeover_log.py --model claude:sonnet --effort high --one-fight
"""
import argparse
import json
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'vakuu'


def load_engine():
    sys.path.insert(0, str(SOURCE))
    import vakuu_engine
    return vakuu_engine


# Floors of the last rest row before each act's boss, used only when no route is planned.
BOSS_REST_FLOORS = {1: (16,), 2: (32,), 3: (48,)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--steps', type=int, default=50)
    parser.add_argument('--log', type=Path, help='JSONL file (default: vakuu/button-runtime/logs/<time>.jsonl)')
    parser.add_argument('--model', help='Model key for this run only, e.g. auto:, auto:brief, claude:sonnet')
    parser.add_argument('--effort', choices=['low', 'medium', 'high'])
    parser.add_argument('--one-fight', action='store_true', help='Stop as soon as the current fight ends')
    parser.add_argument('--stop-at-boss-rest', action='store_true',
                        help="Stop on reaching the rest site right before a boss, before anything is chosen there")
    parser.add_argument('--no-ai', action='store_true', help='Use local decisions only; stop when a model would be needed')
    parser.add_argument('--stop-after-candidate', action='store_true', help='Stop after executing one accepted candidate prefix; stop if its queue is invalidated')
    parser.add_argument('--candidate-min-cards', type=int, default=1, help='Only stop on accepted prefixes with at least this many cards')
    args = parser.parse_args()

    engine = load_engine()
    log_path = args.log or SOURCE / 'button-runtime' / 'logs' / time.strftime('%Y%m%d-%H%M%S.jsonl')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, 'a', encoding='utf-8')
    print('logging to', log_path, flush=True)

    def write(kind, **data):
        log.write(json.dumps({'t': round(time.time(), 2), 'kind': kind, **data}, ensure_ascii=False) + '\n')
        log.flush()

    if args.model or args.effort:  # Override for this run only; the panel's saved choice is left alone.
        saved = engine.models.settings
        override = {k: v for k, v in (('model_key', args.model), ('effort', args.effort)) if v}
        engine.models.settings = lambda: {**saved(), **override}

    controller = engine.Controller(lambda m: (write('report', message=m), print(m.replace('\n', ' | '), flush=True)),
                                   allow_model=not args.no_ai)
    raw_search = engine.search_turn
    candidate_remaining = None
    def logged_search(state, **kwargs):
        if args.stop_after_candidate and candidate_remaining is not None and candidate_remaining > 0:
            write('candidate_aborted', remaining=candidate_remaining)
            controller.stop_event.set()
            raise InterruptedError('候選隊列已失效，停止驗證')
        result = raw_search(state, **kwargs)
        write('search', result=result)
        return result
    engine.search_turn = logged_search
    raw_request = engine.api.request
    fight_started = False

    def request(path, body=None):
        nonlocal fight_started, candidate_remaining
        result = raw_request(path, body)
        if body is None:
            write('state', state=result)
            if result.get('state_type') in engine.COMBAT: fight_started = True
            if args.one_fight and fight_started and result.get('state_type') in ('rewards', 'card_reward', 'game_over', 'map'):
                controller.stop_event.set()
            if args.stop_at_boss_rest and boss_next(result):
                write('stopped_at_boss_rest')
                controller.stop_event.set()
        else:
            write('post', body=body, result=result)
            if candidate_remaining is not None and result.get('status') == 'ok': candidate_remaining -= 1
        return result
    engine.api.request = request

    def boss_next(state):
        """A rest site whose planned next room is the boss (row 14 of 15 when no route is planned)."""
        if state.get('state_type') != 'rest_site': return False
        route = controller.route or {}
        path = route.get('path') or []
        if path: return (route.get('types') or {}).get(path[0]) == 'Boss'
        run = state.get('run') or {}
        return run.get('floor') in BOSS_REST_FLOORS.get(run.get('act'), ())

    decide = engine.Controller.decide
    def logged_decide(self, state, selection=None):
        nonlocal candidate_remaining
        start = time.time()
        decision = decide(self, state, selection)
        write('model', seconds=round(time.time() - start, 1), model=(selection or {}).get('model_key'), decision=decision)
        card_count = int(decision.get('action') == 'play_card') + sum(c.get('hand_index', -1) >= 0 for c in decision.get('next_cards') or [])
        if args.stop_after_candidate and decision.get('candidate_id') and card_count >= args.candidate_min_cards:
            candidate_remaining = 1 + len(decision.get('next_cards') or [])
        return decision
    engine.Controller.decide = logged_decide
    raw_call = engine.Controller.call_model
    def logged_call(self, *args, **kwargs):
        write('model_input', stage='candidate_choice' if kwargs.get('candidate') else 'full_state',
              prompt=args[0], schema=args[1])
        answer = raw_call(self, *args, **kwargs)
        write('model_call', answer=answer, **self.last_call_metrics)
        return answer
    engine.Controller.call_model = logged_call
    raw_wait = controller.wait_for_change
    def wait_for_change(*args_, **kwargs):
        raw_wait(*args_, **kwargs)
        if args.stop_after_candidate and candidate_remaining == 0:
            write('candidate_completed')
            controller.stop_event.set()
    controller.wait_for_change = wait_for_change

    try:
        controller.run(True, args.steps, finish_candidate=args.stop_after_candidate)
    finally:
        controller.stop()  # The command exits here; do not leave an idle app-server behind.
        log.close()


if __name__ == '__main__':
    main()
