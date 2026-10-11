"""Review a takeover log while it runs (or afterwards): fights against community averages, warning signs
inside turns, and the long-term decisions with the deck they were made with.

    python tools/live_review.py vakuu/button-runtime/logs/run27-full.jsonl
    python tools/live_review.py <log> --since-floor 5      # only what happened from floor 5 on
"""
import argparse
import json
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'vakuu'
sys.path.insert(0, str(SOURCE))
import spire_codex  # noqa: E402
from vakuu_facts import BLOCK_TEXT, card_cost, deals_damage, deck_profile, enemy_ids, gives_energy_now  # noqa: E402
from vakuu_rules import COMBAT  # noqa: E402

BIG_LOSS = 0.2  # A turn costing this share of max HP is worth asking whether a potion should have been drunk.
DECISIONS = ('event', 'card_reward', 'shop', 'rest_site', 'map', 'card_select', 'treasure', 'relic_select')


def encounter_for(state, fight_index, stats):
    """The community encounter matching this fight's monsters (weak pools for an act's first fights)."""
    candidates = None
    for monster in enemy_ids(state):
        intel = spire_codex.monster_intel(monster, (state.get('run') or {}).get('ascension') or 0) or {}
        found = set(intel.get('encounters') or [])
        candidates = found if candidates is None else candidates & found
    candidates = sorted(c for c in candidates or () if c in stats)
    if len(candidates) > 1:
        weak = [c for c in candidates if c.endswith('_WEAK')]
        candidates = weak if weak and fight_index < 3 else [c for c in candidates if c not in weak] or candidates
    return candidates[0] if candidates else None


def community(stats, encounter, character):
    row = stats.get(encounter) or {}
    mine = next((c for c in row.get('characters') or [] if c.get('character') == character), None) or row
    return mine.get('avg_damage'), mine.get('avg_turns')


def turn_flags(state):
    """Warning signs at the moment the turn was ended."""
    player = state.get('player') or {}
    energy = player.get('energy') or 0
    hand = [c for c in player.get('hand') or [] if c.get('can_play')]
    flags = []
    affordable = [c for c in hand if not gives_energy_now(c) and card_cost(c) <= energy]
    if energy and affordable:
        flags.append(f"剩 {energy} 能量，手上還有可打的牌：{'、'.join(c['name'] for c in affordable)}")
    blockers = [c for c in affordable if BLOCK_TEXT.search(str(c.get('description') or ''))]
    return flags, blockers


def review(path, since_floor=0):
    rows = [json.loads(line) for line in open(path, encoding='utf-8')]
    stats = spire_codex.encounter_stats()
    lines, fights, last, fight, turn_state, step_state = [], [], None, None, None, None
    act_fights = {}
    for record in rows:
        kind = record['kind']
        if kind == 'state':
            state = record['state']; last = state
            run = state.get('run') or {}
            if state.get('state_type') in COMBAT and (state.get('player') or {}).get('hand') and state.get('battle'):
                key = (run.get('act'), run.get('floor'))
                if fight is None or fight['key'] != key:
                    index = act_fights.get(run.get('act'), 0); act_fights[run.get('act')] = index + 1
                    fight = {'key': key, 'type': state['state_type'], 'start': state['player']['hp'], 'end': state['player']['hp'],
                             'rounds': 0, 'enemies': sorted({e['name'] for e in state['battle']['enemies']}),
                             'encounter': encounter_for(state, index, stats), 'warnings': [], 'self_loss': 0, 'turn': None,
                             'character': state['player'].get('character_id')}
                    fights.append(fight)
                fight['rounds'] = max(fight['rounds'], state['battle'].get('round') or 0)
                fight['end'] = state['player'].get('hp')
            elif fight and run.get('floor') == fight['key'][1] and state.get('player'):
                fight['end'] = state['player'].get('hp')
        elif kind == 'report' and '接手中' in record['message']:
            # The state takeover decided this step on. States read right after a play can still show the
            # hand and energy from before it (the API lags), so judging the turn by the last read misfires.
            step_state = last
        if kind == 'post' and fight and step_state and step_state.get('state_type') in COMBAT and step_state.get('battle'):
            # HP lost between the first move of a turn and ending it happened on our own turn: enchant costs,
            # self-damaging cards, enemy Thorns. A floor 46 "loss" of 10 against a community 1 was all this.
            round_, hp = step_state['battle'].get('round'), (step_state.get('player') or {}).get('hp') or 0
            if not fight['turn'] or fight['turn'][0] != round_:
                # Effects of the last card can land after end_turn; with no enemy attacking, the HP lost
                # between turns is ours too (floor 46: 2 of 10 self-damage landed there).
                gap = fight.pop('gap', None)
                if gap and not gap[1]: fight['self_loss'] += max(0, gap[0] - hp)
                fight['turn'] = (round_, hp)
            if record['body'].get('action') == 'end_turn':
                if fight['turn'][1] is not None: fight['self_loss'] += max(0, fight['turn'][1] - hp)
                fight['turn'] = (round_, None)
                attacked = any(deals_damage(i) for e in step_state['battle'].get('enemies') or [] if (e.get('hp') or 0) > 0
                               for i in e.get('intents') or [])
                fight['gap'] = (hp, attacked)
        if kind == 'post' and record['body'].get('action') == 'end_turn' and fight and step_state \
                and step_state.get('state_type') in COMBAT:
            turn_state = step_state
            flags, blockers = turn_flags(turn_state)
            fight['warnings'] += [f"第 {turn_state['battle'].get('round')} 回合：{f}" for f in flags]
            potions = [p['name'] for p in turn_state['player'].get('potions') or [] if p.get('can_use_in_combat')]
            fight['pending_block'] = (turn_state['battle'].get('round'), turn_state['player'].get('hp'), blockers, potions,
                                      turn_state['player'].get('max_hp') or 0)
            fight['potions'] = potions
        if kind == 'state' and fight and fight.get('pending_block') and (last.get('battle') or {}).get('round') not in (None, fight['pending_block'][0]):
            round_, hp_before, blockers, potions, max_hp = fight.pop('pending_block')
            lost = hp_before - ((last.get('player') or {}).get('hp') or hp_before)
            if lost > 0 and blockers:
                fight['warnings'].append(f"第 {round_} 回合：吃了 {lost} 傷害，手上卻留著格擋牌 {'、'.join(c['name'] for c in blockers)}")
            if potions and lost >= max(1, max_hp * BIG_LOSS):
                fight['warnings'].append(f"第 {round_} 回合：吃了 {lost} 傷害，身上還有藥水 {'、'.join(potions)}")
        if kind == 'model' and last and last.get('state_type') in DECISIONS and (last.get('run') or {}).get('floor', 0) >= since_floor:
            decision = record['decision']; player = last.get('player') or {}
            profile = deck_profile(player.get('deck') or [])
            advice = decision['advice'].splitlines()[-1].replace('建議：', '').replace('建議:', '')
            lines.append(f"- 第 {(last.get('run') or {}).get('floor')} 層 {last['state_type']}（{(record.get('model') or '')[7:]}，血 {player.get('hp')}/{player.get('max_hp')}，"
                         f"金 {player.get('gold')}，牌 {profile['size']} 張、非防禦格擋 {profile['block_sources_besides_defend']}、打擊 {profile['starter_strikes']}）："
                         f"`{decision['action']} {decision['parameters_json']}` {advice}")
    if fights and last and last.get('state_type') == 'game_over' and fights[-1].get('potions'):
        fights[-1]['warnings'].append(f"帶著藥水死亡：{'、'.join(fights[-1]['potions'])}")
    out = [f'# 接手檢視：{Path(path).name}', '', '## 戰鬥', '',
           '| 層 | 類型 | 敵人 | 血量 | 掉血 | 其中出牌時 | 社群平均掉血（同角色） | 回合（社群） | 警示 |',
           '|---|---|---|---|---|---|---|---|---|']
    for f in fights:
        if f['key'][1] < since_floor: continue
        avg, turns = community(stats, f['encounter'], f['character']) if f['encounter'] else (None, None)
        lost = f['start'] - (f['end'] or 0)
        own = f['self_loss']
        if f['turn'] and f['turn'][1] is not None: own += max(0, f['turn'][1] - (f['end'] or 0))  # The killing turn.
        own = min(own, max(lost, 0))
        # Judged on what the enemies did; our own costs (enchants, self-damage) are shown apart.
        verdict = '' if avg is None else (' 🟢' if lost - own <= avg else (' 🟡' if lost - own <= avg * 1.5 else ' 🔴'))
        out.append(f"| {f['key'][1]} | {f['type']} | {'、'.join(f['enemies'])} | {f['start']}→{f['end']} | {lost}{verdict} | "
                   f"{own or ''} | {avg if avg is not None else '?'} | {f['rounds']}（{turns if turns is not None else '?'}） | {len(f['warnings'])} |")
    out += ['', '🟢 不高於社群平均、🟡 高出 50% 以內、🔴 高出 50% 以上；燈號只看敵人造成的部分（掉血扣掉出牌時掉的血）。',
            '「其中出牌時」是自己回合出牌期間掉的血：附魔或卡牌的自傷、敵人荊棘反傷等。社群數據為 Spire Codex 進階10以上同角色對局。', '']
    warned = [f for f in fights if f['warnings'] and f['key'][1] >= since_floor]
    if warned:
        out += ['## 回合提示（值得檢查，不一定是錯誤）', '']
        for f in warned:
            out += [f"**第 {f['key'][1]} 層（{'、'.join(f['enemies'])}）**"] + [f'- {w}' for w in f['warnings']] + ['']
    out += ['## 長期決策', ''] + (lines or ['（尚無）'])
    p = (last or {}).get('player') or {}
    out += ['', f"目前：{(last or {}).get('state_type')}，第 {((last or {}).get('run') or {}).get('floor')} 層，血 {p.get('hp')}/{p.get('max_hp')}"]
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('log', type=Path)
    parser.add_argument('--since-floor', type=int, default=0)
    parser.add_argument('--out', type=Path, help='also write the review to this markdown file')
    args = parser.parse_args()
    text = review(args.log, args.since_floor)
    if args.out: args.out.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
