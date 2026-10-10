"""Bounded turn search. Unknown effects decline; only the first action is executed.

This optimizes the current turn, not run win rate. Card descriptions from the API
already include player damage/block modifiers. Target modifiers and triggers need
explicit support; they must never silently disappear from the simulation.
"""
import json
import re
import time
from dataclasses import dataclass

from vakuu_facts import incoming_damage
from vakuu_rules import COMBAT
from vakuu_relics import tuning_fork_counter

# These only act on pickup, room entry, or turn start (already reflected in state).
PASSIVE_RELICS = {'CRACKED_CORE', 'HEFTY_TABLET', 'REGAL_PILLOW', 'BLOOD_VIAL',
                  'FESTIVE_POPPER', 'MERCURY_HOURGLASS', 'PAELS_FLESH', 'MEAL_TICKET',
                  'HORN_CLEAT', 'STONE_CRACKER', 'BURNING_BLOOD', 'NEW_LEAF', 'BLACK_STAR'}
PLAYER_STATUS = {'STRENGTH_POWER', 'DEXTERITY_POWER', 'WEAK_POWER', 'FRAIL_POWER', 'FOCUS_POWER',
                 # Supported moves never draw cards; these triggers cannot fire in the search.
                 'AUTOMATION_POWER', 'ITERATION_POWER', 'ENTROPY_POWER', 'LIGHTNING_ROD_POWER',
                 'ENERGY_NEXT_TURN_POWER',
                 # Search moves never play Power cards, so Storm cannot fire.
                 'STORM_POWER'}
# Supported moves do not apply debuffs, so Artifact cannot trigger here.
ENEMY_STATUS = {'STRENGTH_POWER', 'WEAK_POWER', 'ARTIFACT_POWER', 'RITUAL_POWER'}
INERT_HAND = {'ASCENDERS_BANE', 'CLUMSY', 'INJURY'}
INERT_TEXT = {'ASCENDERS_BANE': '不能被打出。虚无。永恒。',
              'CLUMSY': '不能被打出。虚无。', 'INJURY': '不能被打出。'}
# These known descriptions have no trigger when another card is played. Their
# own effects are unsupported, so they still prevent a non-winning turn plan.
HELD_SAFE = {
    # Its draw-time energy loss has already happened; playing another card does not trigger it.
    'VOID': r'不能被打出。虚无。每当你抽到这张牌时，失去\[defect_energy_icon.png\]。',
    'COMPACT': r'获得\d+点格挡。将你手牌中的全部状态牌变化为燃料。',
    # Spending energy changes this held card's damage display, not other cards'
    # effects. It remains unsupported to play; the queue's exact-hand guard will
    # stop if its displayed description changes during a selected prefix.
    'HELIX_DRILL': r'在本回合中，这张牌以外使用的每个\[defect_energy_icon.png\]，都会使此牌造成\d+点伤害一次。(?:（命中\d+次）)?',
    'STORM': r'每当你打出一张能力牌时，生成1个闪电充能球。',
    'CHARGE_BATTERY': r'获得\d+点格挡。在下个回合获得\[defect_energy_icon.png\]。',
    'SWEEPING_BEAM': r'对所有敌人造成\d+点伤害。抽\d+张牌。',
    'SKIM': r'抽\d+张牌。',
    'HOLOGRAM': r'获得\d+点格挡。将弃牌堆中的一张牌放入你的手牌。(?:消耗。)?',
    'LIGHTNING_ROD': r'获得\d+点格挡。在下\d+个回合开始时,生成1个闪电充能球。',
    'COOLHEADED': r'生成1个冰霜充能球。抽\d+张牌。',
    'OVERCLOCK': r'抽\d+张牌。将一张灼伤加入你的弃牌堆。',
    'ITERATION': r'每回合你第一次抽到状态牌时，抽\d+张牌。',
    'BULK_UP': r'失去1个充能球栏位。获得\d+点力量。获得\d+点敏捷。',
    'GO_FOR_THE_EYES': r'造成\d+点伤害。如果敌人的意图是攻击，则给予\d+层虚弱。',
    'TURBO': r'获得(?:\[defect_energy_icon.png\])+。将一张虚空加入你的弃牌堆。',
    'ROCKET_PUNCH': r'造成\d+点伤害。抽\d+张牌。每当你生成状态牌时，此牌的耗能将在下一次打出前减少1\[defect_energy_icon.png\]。',
    'SLIMED': r'抽1张牌。消耗。',
    'BURN': r'不能被打出。在你的回合结束时，如果这张牌在你的手牌中，你受到\d+点伤害。',
    'ENTROPY': r'在你的回合开始时，变化你手牌中的\d+张牌。',
    'CREATIVE_AI': r'在你的回合开始时，将一张随机能力牌加入你的手牌。',
    'ECHO_FORM': r'(?:虚无。)?你每回合打出的第一张牌会被打出两次。',
    'AUTOMATION': r'你每抽\d+张牌，获得\[defect_energy_icon.png\]。',
}
STRIKES = {'STRIKE_' + c for c in ('IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT')}
DEFENDS = {'DEFEND_' + c for c in ('IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT')}
DAMAGE = re.compile(r'造成(\d+)[点點][伤傷]害[。.]?')
BLOCK = re.compile(r'[获獲]得(\d+)[点點]格[挡擋][。.]?')


def key(row):
    return str(row.get('id') or '').split('.')[-1]


@dataclass(frozen=True)
class Move:
    index: int
    cost: int
    damage: int = 0
    block: int = 0
    channel: str = ''
    evokes: int = 0
    # Adaptive/Momentum have future-turn effects not valued by a one-turn search.
    kill_only: bool = False


def card_move(card):
    cid = key(card)
    if type(card.get('index')) is not int or card.get('star_cost') not in (None, 0): return None
    try: cost = int(card['cost'])
    except (KeyError, TypeError, ValueError): return None
    if cost < 0: return None
    text = re.sub(r'\s+', '', str(card.get('description') or ''))
    damage, block = DAMAGE.fullmatch(text), BLOCK.fullmatch(text)
    if cid in STRIKES and damage and card.get('target_type') == 'AnyEnemy':
        return Move(card['index'], cost, damage=int(damage[1]))
    if cid in DEFENDS and block and card.get('target_type') == 'Self':
        return Move(card['index'], cost, block=int(block[1]))
    # Exact descriptions prevent enchantments or added effects from being ignored.
    if cid == 'ZAP' and text == '生成1个闪电充能球。' and card.get('target_type') == 'Self':
        return Move(card['index'], cost, channel='LIGHTNING_ORB')
    if cid == 'DUALCAST' and text == '激发你最右侧的充能球两次。' and card.get('target_type') == 'Self':
        return Move(card['index'], cost, evokes=2)
    if cid == 'BALL_LIGHTNING' and card.get('target_type') == 'AnyEnemy':
        match = re.fullmatch(r'造成(\d+)点伤害。生成1个闪电充能球。', text)
        if match: return Move(card['index'], cost, damage=int(match[1]), channel='LIGHTNING_ORB')
    if cid == 'ADAPTIVE_STRIKE' and card.get('target_type') == 'AnyEnemy':
        match = re.fullmatch(r'造成(\d+)点伤害。将这张牌的一张0\[defect_energy_icon.png\]复制品添加到你的弃牌堆。', text)
        if match: return Move(card['index'], cost, damage=int(match[1]), kill_only=True)
    if cid == 'MOMENTUM_STRIKE' and card.get('target_type') == 'AnyEnemy':
        match = re.fullmatch(r'造成(\d+)点伤害。这张牌的耗能降为0\[defect_energy_icon.png\]。', text)
        if match: return Move(card['index'], cost, damage=int(match[1]), kill_only=True)
    return None


def search_turn(state, max_nodes=20000, max_seconds=0.05, include_candidates=False):
    """Return a decision and diagnostics; decision=None is an explicit model fallback."""
    def decline(reason, nodes=0): return {'decision': None, 'reason': reason, 'nodes': nodes}
    if max_nodes <= 0 or max_seconds <= 0: return decline('search_limit')
    if state.get('state_type') not in COMBAT: return decline('not_combat')
    player, battle = state.get('player') or {}, state.get('battle') or {}
    if battle.get('turn') != 'player' or battle.get('is_play_phase') is not True: return decline('not_play_phase')
    if any(key(r) not in PASSIVE_RELICS | {'TUNING_FORK'} for r in player.get('relics') or []): return decline('unknown_relic')
    try: fork_counter = tuning_fork_counter(player.get('relics') or [])
    except ValueError: return decline('unknown_relic')
    if any(key(s) not in PLAYER_STATUS for s in player.get('status') or []): return decline('unknown_player_status')
    enemies = [e for e in battle.get('enemies') or [] if (e.get('hp') or 0) > 0]
    if not enemies: return decline('no_enemies')
    if any(key(s) not in ENEMY_STATUS for e in enemies for s in e.get('status') or []):
        return decline('unknown_enemy_status')
    if len({e.get('entity_id') for e in enemies}) != len(enemies) or any(not e.get('entity_id') for e in enemies):
        return decline('missing_enemy_id')
    attacks = []
    for enemy in enemies:
        intents = enemy.get('intents')
        if not intents: return decline('missing_intents')
        amount = 0
        for intent in intents:
            if intent.get('type') not in {'Attack', 'Defend', 'Buff', 'Debuff', 'Sleep'}:
                return decline('special_intent')
            if intent.get('type') == 'Attack':
                if not re.fullmatch(r'\d+(?:\s*[x×*]\s*\d+)?', str(intent.get('label') or '')):
                    return decline('unknown_attack_damage')
                amount += incoming_damage(intent)
            elif '伤害' in str(intent.get('description') or '') or '傷害' in str(intent.get('description') or ''):
                return decline('special_intent')
        attacks.append(amount)
    if any(type(player.get(k)) is not int for k in ('energy', 'block', 'hp')) or player['hp'] <= 0:
        return decline('missing_player_numbers')
    if any(type(e.get(k)) is not int for e in enemies for k in ('hp', 'block')):
        return decline('missing_enemy_numbers')
    moves, complete = [], True
    for card in player.get('hand') or []:
        text = re.sub(r'\s+', '', str(card.get('description') or ''))
        if key(card) in INERT_HAND and card.get('can_play') is False and text == INERT_TEXT[key(card)]: continue
        move = card_move(card)
        if move is None:
            # An unknown card could be Pain or another on-play trigger. A lethal
            # combo does not justify ignoring damage received while executing it.
            if not re.fullmatch(HELD_SAFE.get(key(card), r'(?!)'), text):
                return decline('unknown_hand_trigger')
            complete = False
        elif card.get('can_play') is True:
            moves.append(move)
            complete = complete and not move.kill_only
        elif move.cost <= player['energy']:
            # A card blocked for a reason other than energy may unlock later.
            complete = False
    if not moves: return decline('no_supported_moves')
    skill_indices = {c['index'] for c in player.get('hand') or [] if c.get('type') == 'Skill' and 'index' in c}
    if fork_counter is not None and any(c.get('type') not in {'Attack', 'Skill'} for c in player.get('hand') or []
                                        if card_move(c) is not None):
        return decline('missing_card_type')
    if len(moves) > 12: return decline('too_many_cards')
    orbs = tuple((key(o), o.get('passive_val'), o.get('evoke_val')) for o in player.get('orbs') or [])
    if any(o[0] not in {'LIGHTNING_ORB', 'FROST_ORB'} or type(o[1]) is not int or type(o[2]) is not int for o in orbs):
        return decline('unknown_orb')
    if len(enemies) > 1 and (any(o[0] == 'LIGHTNING_ORB' for o in orbs) or any(m.channel for m in moves)):
        return decline('random_orb_targets')
    slots = player.get('orb_slots', 0)
    if type(slots) is not int or slots < len(orbs): return decline('unknown_orb_slots')
    if any(m.channel or m.evokes for m in moves) and slots <= 0: return decline('no_orb_slots')
    # API enumeration order is not a documented UI order. With identical orbs it
    # cannot affect the result; mixed queues must wait for an explicit API index.
    if any(m.channel or m.evokes for m in moves) and len(set(orbs)) > 1:
        return decline('mixed_orb_order')
    if any(m.channel for m in moves) and any(o[0] != 'LIGHTNING_ORB' for o in orbs):
        return decline('mixed_orb_order')
    focus = sum(s.get('amount', 0) for s in player.get('status') or [] if key(s) == 'FOCUS_POWER')
    lightning = ('LIGHTNING_ORB', max(0, 3 + focus), max(0, 8 + focus))
    # Check the API values before using base values to generate new orbs.
    if any(m.channel for m in moves) and any(o[0] == 'LIGHTNING_ORB' and o != lightning for o in orbs):
        return decline('modified_orb_values')

    start = time.perf_counter()
    visited, count, best = set(), 0, None
    portfolios = {'減傷': [], '輸出': [], '保留能量': []}
    initial_hp = tuple(e['hp'] for e in enemies)
    initial_block = tuple(e['block'] for e in enemies)

    def hit(hps, blocks, target, damage):
        hps, blocks = list(hps), list(blocks)
        absorbed = min(blocks[target], damage)
        blocks[target] -= absorbed
        hps[target] = max(0, hps[target] - (damage - absorbed))
        return tuple(hps), tuple(blocks)

    def orb_effect(orb, times, hps, blocks, block):
        if orb[0] == 'FROST_ORB': block += orb[2] * times
        elif hps[0] > 0: hps, blocks = hit(hps, blocks, 0, orb[2] * times)
        return hps, blocks, block

    def walk(mask, energy, block, hps, blocks, balls, sequence):
        nonlocal count, best
        signature = (mask, energy, block, hps, blocks, balls)
        if signature in visited: return
        visited.add(signature); count += 1
        if count > max_nodes or time.perf_counter() - start >= max_seconds: raise TimeoutError
        # Score the end of turn, after the current orbs' passive effects.
        end_hp, end_blocks, end_block = hps, blocks, block
        for orb in balls:
            if orb[0] == 'FROST_ORB': end_block += orb[1]
            elif end_hp[0] > 0: end_hp, end_blocks = hit(end_hp, end_blocks, 0, orb[1])
        loss = max(0, sum(a for a, hp in zip(attacks, end_hp) if hp > 0) - end_block)
        win = not any(end_hp)
        # Unknown hand effects may trigger at turn end. Only direct wins are safe in that case.
        direct_win = not any(hps)
        eligible = complete or direct_win
        rank = (win, loss < player['hp'], -loss, -sum(end_hp), energy, -len(sequence))
        if include_candidates:
            rankings = {'減傷': (win, loss < player['hp'], -loss, -sum(end_hp), energy, -len(sequence)),
                        '輸出': (win, -sum(end_hp), -loss, energy, -len(sequence)),
                        '保留能量': (win, loss < player['hp'], energy, -loss, -sum(end_hp), -len(sequence))}
            outcome = {'sequence': list(sequence), 'predicted_hp_loss': loss,
                       'predicted_enemy_hp': list(end_hp), 'remaining_energy': energy,
                       'end_turn': not direct_win,
                       'confidence': 'simulated' if complete or direct_win else 'partial',
                       'unmodeled_effects': [] if complete or direct_win else
                           ['未評估未打出的特殊牌在回合結束時的效果，也未評估抽牌與後續回合收益。']}
            for style, score in rankings.items():
                portfolio = portfolios[style]
                portfolio.append((score, outcome))
                portfolio.sort(key=lambda row: row[0], reverse=True)
                del portfolio[3:]
        if eligible and (best is None or rank > best[0]):
            best = (rank, sequence, loss, end_hp, direct_win)
        if direct_win: return
        for i, move in enumerate(moves):
            if mask & (1 << i) or move.cost > energy: continue
            targets = [j for j, hp in enumerate(hps) if hp > 0] if move.damage else [None]
            for target in targets:
                hp2, blocks2, block2, balls2 = hps, blocks, block + move.block, balls
                if fork_counter is not None and move.index in skill_indices:
                    skills_played = sum(bool(mask & (1 << j)) and m.index in skill_indices for j, m in enumerate(moves))
                    if (fork_counter + skills_played + 1) % 10 == 0: block2 += 7
                if target is not None: hp2, blocks2 = hit(hp2, blocks2, target, move.damage)
                if move.evokes and balls2:
                    # The live STS2 description says rightmost; do not assume STS1 ordering.
                    hp2, blocks2, block2 = orb_effect(balls2[-1], move.evokes, hp2, blocks2, block2)
                    balls2 = balls2[:-1]
                if move.channel:
                    if len(balls2) == slots:
                        hp2, blocks2, block2 = orb_effect(balls2[-1], 1, hp2, blocks2, block2)
                        balls2 = balls2[:-1]
                    balls2 = (lightning,) + balls2
                params = {'card_index': move.index}
                if target is not None: params['target'] = enemies[target]['entity_id']
                walk(mask | (1 << i), energy - move.cost, block2, hp2, blocks2, balls2, sequence + (params,))

    try: walk(0, player['energy'], player['block'], initial_hp, initial_block, orbs, ())
    except TimeoutError: return decline('search_limit', count)
    candidates, seen_outcomes = [], set()
    if include_candidates:
        for style, portfolio in portfolios.items():
            for _, outcome in portfolio:
                signature = (tuple(outcome['predicted_enemy_hp']), outcome['predicted_hp_loss'],
                             outcome['remaining_energy'])
                if signature in seen_outcomes: continue
                seen_outcomes.add(signature)
                candidates.append({'style': style, **outcome})
                break
    def fallback(reason):
        return {**decline(reason, count), 'candidates': candidates}
    if best is None: return fallback('unsupported_hand')
    rank, sequence, loss, end_hp, direct_win = best
    if not direct_win:
        if loss >= player['hp']: return fallback('needs_survival_strategy')
        if loss > 0 and any(p.get('can_use_in_combat') for p in player.get('potions') or []):
            return fallback('needs_potion_evaluation')
    params = sequence[0] if sequence else {}
    name = next((c.get('name') or key(c) for c in player.get('hand') or []
                 if c.get('index') == params.get('card_index')), '') if sequence else ''
    decision = {'action': 'play_card' if sequence else 'end_turn', 'stop': False,
                'parameters_json': json.dumps(params),
                'advice': '〔程式判斷〕\n建議：' +
                          (f'打出{name}。' if sequence else '結束回合。') +
                          (f'預估本回合掉血 {loss}。' if not direct_win else '可直接結束戰鬥。')}
    return {'decision': decision, 'reason': 'direct_win' if direct_win else 'supported_turn', 'nodes': count,
            'predicted_hp_loss': loss, 'predicted_enemy_hp': list(end_hp), 'sequence': list(sequence),
            **({'candidates': candidates} if include_candidates else {})}


def turn_candidates(state, search_result=None):
    """Small diverse menu for the model. Unsimulated proposals carry no invented forecasts.

    Known states get search-ranked sequences. Other states get short heuristic
    proposals to verify, not assertions that the turn's effects were simulated.
    The model can reject all of them and use any legal action, including potions.
    """
    if state.get('state_type') not in COMBAT: return None
    battle = state.get('battle') or {}
    if battle.get('turn') != 'player' or battle.get('is_play_phase') is not True: return None
    result = search_result if search_result is not None else search_turn(state, include_candidates=True)
    player = state.get('player') or {}
    hand = {c.get('index'): c for c in player.get('hand') or [] if type(c.get('index')) is int}
    candidates = [dict(candidate) for candidate in result.get('candidates') or []]
    enemies = [e for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0]
    energy = player.get('energy')

    def propose(style, cards, target=None):
        if len(candidates) >= 5 or type(energy) is not int: return
        budget, sequence, barrier = energy, [], False
        for card in cards:
            if type(card.get('index')) is not int or card.get('can_play') is not True: continue
            # X-cost / stars / negative costs need their own rules, not int truncation.
            if not re.fullmatch(r'\d+', str(card.get('cost'))) or card.get('star_cost') not in (None, 0): continue
            cost = int(card['cost'])
            if cost > budget: continue
            target_type = card.get('target_type')
            if target_type not in {'AnyEnemy', 'Self', 'AllEnemies', 'None'}: continue
            params = {'card_index': card['index']}
            if target_type == 'AnyEnemy':
                if target is None or not target.get('entity_id'): continue
                params['target'] = target['entity_id']
            sequence.append(params); budget -= cost
            move = card_move(card)
            # After any unsupported effect, ask the model to reassess instead of
            # pretending the rest of the starting hand/energy is unchanged.
            barrier = move is None or bool(move.channel or move.evokes)
            if barrier or len(sequence) == 3: break
        if not sequence or any(c['sequence'] == sequence for c in candidates): return
        candidates.append({'style': style, 'sequence': sequence, 'confidence': 'unverified',
                           'requires_replan': barrier, 'end_turn': False,
                           'unmodeled_effects': ['這是依牌型與目前費用提出的起手順序；未模擬傷害、格擋、觸發效果或後續局面。']})

    playable = [c for c in hand.values() if c.get('can_play') is True]
    def price(c):
        value = str(c.get('cost'))
        return int(value) if value.isdigit() else 999
    attacks = sorted((c for c in playable if c.get('type') == 'Attack'), key=price)
    defenses = sorted((c for c in playable if re.search(r'格[挡擋]|冰霜', str(c.get('description') or ''))
                        and c.get('type') != 'Attack'), key=price)
    weakest = min(enemies, key=lambda e: (e.get('hp') or 0) + (e.get('block') or 0), default=None)
    threat = max(enemies, key=lambda e: sum(incoming_damage(i) for i in e.get('intents') or []), default=None)
    setup = sorted((c for c in playable if c.get('type') == 'Power'), key=price)
    if setup: propose('能力牌起手', setup + defenses, threat)
    draws = sorted((c for c in playable if re.search(r'抽\d+张牌|抽\d+張牌', str(c.get('description') or ''))), key=price)
    if draws: propose('抽牌後重算', draws, threat)
    if defenses: propose('防禦起手', defenses + attacks, threat)
    propose('集火低血量敵人', attacks + defenses, weakest)
    if threat is not weakest: propose('集火攻擊威脅', attacks + defenses, threat)
    for i, candidate in enumerate(candidates):
        candidate['id'] = f'P{i + 1}'
        candidate['cards'] = [hand[p['card_index']].get('name') or key(hand[p['card_index']])
                              for p in candidate['sequence']]
    return {'search_reason': result['reason'], 'plans': candidates,
            'instructions': '比較候選並驗算未計效果；可修改或全部拒絕。sequence 是目前手牌 index，'
                            '不是必須執行的命令。end_turn=false 表示僅起手方案。'
                            '抽牌或未確定效果後停止列出；可另外使用藥水。'} if candidates else None


def search_decision(state):
    return search_turn(state)['decision']
