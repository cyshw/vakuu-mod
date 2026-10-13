"""Numbers computed for the model so it does not have to: combat, potion and energy facts."""
import re
from pathlib import Path
from combat.relics import turn_end_defense
from combat.triggers import trigger_facts


COMMON_INTENTS = {'Attack', 'Defend', 'Buff', 'Debuff', 'Sleep', 'Unknown'}

def deals_damage(intent):
    # Not only "Attack": the Waterfall Giant's DeathBlow ("濒死一击") hits for 53 when it dies, and
    # counting it as 0 told the model no block was needed, which killed the run.
    kind = str(intent.get('type'))
    text = str(intent.get('description') or '')
    return 'Attack' in kind or 'DeathBlow' in kind or '伤害' in text or '傷害' in text

def incoming_damage(intent):
    if not deals_damage(intent): return 0
    numbers = [int(n) for n in re.findall(r'\d+', str(intent.get('label') or ''))]
    if not numbers: return 0
    return numbers[0] * (numbers[1] if len(numbers) > 1 else 1)  # "12×2" means two hits of 12.

def combat_facts(state):
    """Arithmetic the model kept getting wrong, computed once from the live state."""
    player = state.get('player') or {}
    enemies = [e for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0]
    incoming = sum(incoming_damage(i) for e in enemies for i in e.get('intents') or [])
    block = player.get('block') or 0
    hp = player.get('hp') or 0
    orbs = orb_end_of_turn(player)
    try: end_defense = turn_end_defense(player)
    except ValueError: end_defense = {}
    loss = max(0, incoming - block - orbs.get('block', 0) - end_defense.get('block', 0))
    retain_hand = any(r.get('id') in RETAIN_HAND_RELICS for r in player.get('relics') or [])
    return {
        **({'card_trigger_counts': triggers} if (triggers := trigger_facts(player)) else {}),
        'incoming_attack_damage': incoming,
        **({'end_of_turn_orbs': orbs} if orbs else {}),
        **({'end_turn_known_defense': end_defense} if end_defense.get('block') or end_defense.get('retain_block') else {}),
        # Unusual intents carry their own rules (timing, conditions); the model must read them itself.
        'special_intents': [{'enemy': e.get('name'), 'type': i.get('type'), 'label': i.get('label'),
                             'description': i.get('description')}
                            for e in enemies for i in e.get('intents') or []
                            if not any(kind in str(i.get('type')) for kind in COMMON_INTENTS)],
        'current_block': block,
        'block_needed_for_no_damage': loss,
        'hp_after_enemy_attacks_if_no_more_block': hp - loss,
        'lethal_if_no_more_block': loss >= hp > 0,
        **tempo_facts(enemies, player, incoming, block + orbs.get('block', 0), loss),
        'energy': player.get('energy'),
        'hand_end_of_turn_rule': ('符文金字塔使未打出的手牌不因回合結束而棄掉；虛無等效果仍依描述處理。' if retain_hand else
                                  '一般未打出的手牌回合結束會棄掉；保留、虛無或改變棄牌的特殊效果依描述處理，不要假設普通手牌能留到下回合。'),
        'playable_cards': [{'index': c.get('index'), 'name': c.get('name'), 'cost': c.get('cost'),
                            'gives_energy_now': gives_energy_now(c), 'exhausts': '消耗' in str(c.get('description') or '')}
                           for c in player.get('hand') or [] if c.get('can_play')],
        # >= 0: current energy already pays for every other card in hand, so energy cards would be wasted.
        'spare_energy_without_energy_cards': spare_energy(state),
        'timed_effects': timed_effects(player),
        # Sonnet kept a 0-cost "gain 10 block" card "for next turn" in two fights; it was discarded.
        # Runic Pyramid keeps the whole hand; saying otherwise would mislead the model's planning.
        'unplayed_cards_are_discarded_at_end_of_turn': not retain_hand,
        **({'cards_kept_by_retain': retained} if (retained := [c.get('name') for c in player.get('hand') or []
                                                                if '保留' in str(c.get('description') or '')]) else {}),
        **potion_facts(state, loss),
        'note': '敵人意圖、卡牌描述已含顯示修正；承傷已扣目前格擋、冰霜與已驗證的覆甲／扣環。其他未知觸發另計。',
    }

TEMPO_SAFE_HP = 0.6  # Share of max HP that must remain after this turn's attacks for damage to outrank more block.

def tempo_facts(enemies, player, incoming, covered, loss):
    """Tell the model when more block is wasted or when it can afford to attack instead.

    Haiku lost two elite fights (Decimillipede, three Knights) at full HP by stacking block 8 over the
    damage and dealing 6-23 a turn early; the damage it skipped came back as 20+ HP lost every later turn.
    """
    hp, max_hp = player.get('hp') or 0, player.get('max_hp') or 0
    facts = {}
    if incoming and covered > incoming:
        facts['block_surplus'] = covered - incoming
        facts['block_surplus_rule'] = '格擋已超過敵人本回合攻擊，不要再補格擋，剩下能量改打傷害'
    if enemies and max_hp and hp - loss >= TEMPO_SAFE_HP * max_hp and loss < hp:
        target = min(enemies, key=lambda e: (e.get('hp') or 0) + (e.get('block') or 0))
        facts['can_afford_to_attack'] = {
            'hp_after_attacks_if_no_more_block': hp - loss,
            'weakest_enemy': target.get('entity_id'), 'weakest_enemy_hp_with_block': (target.get('hp') or 0) + (target.get('block') or 0),
            'rule': '血量夠厚、這回合不致命：不要為了零傷害補滿格擋，先把能量花在傷害上（優先集火最容易打死的敵人），格擋只補到剩餘血量仍高於六成最大血量為止；拖越久敵人累積的傷害越多'}
    return facts


def poison_preview(state):
    """Current-state previews computed by the game, not a guessed damage formula."""
    result = []
    for enemy in (state.get('battle') or {}).get('enemies') or []:
        hp = enemy.get('hp') or 0
        if hp <= 0: continue
        for status in enemy.get('status') or []:
            damage = status.get('next_turn_hp_loss')
            if status.get('id') != 'POISON_POWER' or not isinstance(damage, int) or isinstance(damage, bool): continue
            if damage < 0: continue
            result.append({'enemy': enemy.get('entity_id'), 'stacks': status.get('amount'),
                           'next_own_turn_hp_loss': damage, 'hp_if_only_poison': max(0, hp - damage)})
    return result


def compact_poison(compact, state):
    """Remove repeated Poison prose only when authoritative numeric previews replace it."""
    ready = {entry['enemy']: entry for entry in poison_preview(state)}
    for enemy in (compact.get('battle') or {}).get('enemies') or []:
        if enemy.get('entity_id') in ready:
            enemy['status'] = [s for s in enemy.get('status') or [] if s.get('id') != 'POISON_POWER']
            entry = ready[enemy['entity_id']]
            enemy['poison_preview'] = {'stacks': entry['stacks'],
                                       'next_turn_hp_loss': entry['next_own_turn_hp_loss'],
                                       'hp_if_only_poison': entry['hp_if_only_poison']}


def orb_end_of_turn(player):
    """What channelled orbs do when the turn ends, before the enemies attack (passive_val includes Focus)."""
    totals = {}
    for orb in player.get('orbs') or []:
        value = orb.get('passive_val') or 0
        kind = {'FROST_ORB': 'block', 'LIGHTNING_ORB': 'damage_to_random_enemy', 'GLASS_ORB': 'damage_to_all_enemies'}.get(orb.get('id'))
        if kind and value: totals[kind] = totals.get(kind, 0) + value
    return totals

POTION_BLOCK = re.compile(r'(\d+)\s*[点點]\s*格[挡擋]')
POTION_DAMAGE = re.compile(r'(\d+)\s*[点點]\s*[伤傷]害')

RETAIN_HAND_RELICS = {'RUNIC_PYRAMID'}
# Potions whose value grows with the length of the fight (Strength, upgrades, Thorns...). An act 2
# boss lasted 11 turns while Strength and Blessing of the Forge stayed unused until death.
SETUP_POTION = re.compile(r'力量|敏捷|集中|升级|升級|荆棘|荊棘|覆甲|人工制品')
NORMAL_FIGHT_POTION_SAVE = 0.2  # Share of max HP a potion must save this turn to be drunk in an ordinary fight.
NORMAL_FIGHT_LONG_ROUND = 4  # From this round on a drawn-out ordinary fight may spend support potions (vulnerable, power, ...).

def potion_facts(state, loss):
    """Combat potions with their block and damage read off the text, and whether they change this turn.

    Run 27 died on floor 7 still holding a Fire Potion and Liquid Bronze: nothing told the model the
    potions were there to be drunk, or that one of them covered the damage that killed it.
    """
    player = state.get('player') or {}
    usable = []
    for potion in player.get('potions') or []:
        if not potion.get('can_use_in_combat'): continue
        text = str(potion.get('description') or '')
        entry = {'slot': potion.get('slot'), 'name': potion.get('name'), 'target_type': potion.get('target_type')}
        block, damage = POTION_BLOCK.search(text), POTION_DAMAGE.search(text)
        if block: entry['block'] = int(block.group(1))
        if damage: entry['damage'] = int(damage.group(1))
        usable.append(entry)
    if not usable: return {}
    enemies = [e for e in (state.get('battle') or {}).get('enemies') or [] if (e.get('hp') or 0) > 0]
    weakest = min(((e.get('hp') or 0) + (e.get('block') or 0) for e in enemies), default=None)
    potion_block = sum(p.get('block', 0) for p in usable)
    hp = player.get('hp') or 0
    lethal = loss >= hp > 0
    # Run 28 drank three potions in ordinary fights to save a few HP, then lacked them in the elite
    # and boss fights that ended the run; the model also talked 12 saved HP up to "a fifth of max HP".
    normal = state.get('state_type') == 'monster' and not lethal
    if normal:
        for potion in usable:
            saved = min(potion.get('block', 0), loss)
            if saved: potion['hp_saved_this_turn'] = saved
            kills = weakest is not None and potion.get('damage', 0) >= weakest
            support = 'block' not in potion and 'damage' not in potion
            dragging = (state.get('battle') or {}).get('round', 1) >= NORMAL_FIGHT_LONG_ROUND
            potion['worth_it_in_this_normal_fight'] = saved >= NORMAL_FIGHT_POTION_SAVE * (player.get('max_hp') or 0) or kills                 or (support and dragging)
    setup = [p['name'] for p, raw in zip(usable, [q for q in player.get('potions') or [] if q.get('can_use_in_combat')])
             if 'block' not in p and 'damage' not in p and SETUP_POTION.search(str(raw.get('description') or ''))]
    hard = state.get('state_type') in ('elite', 'boss')
    return {
        **({'setup_potions_drink_in_first_turns': setup} if setup and hard else {}),
        **({'normal_fight_potion_rule': '普通戰鬥且這回合不會死：只喝 worth_it_in_this_normal_fight 為 true 的藥水，其他留給菁英與頭目'}
           if normal else {}),
        'usable_potions': usable,
        # Sonnet once wrote "this turn cannot be survived" with a Fire Potion in hand and never mentioned it.
        **({'lethal_with_potions_in_hand': '不喝藥水這回合會死：先算 ' + '、'.join(p['name'] for p in usable) + ' 能不能救命'}
           if lethal else {}),
        'potion_belt_full': len(player.get('potions') or []) >= (player.get('max_potion_slots') or 99),
        'potion_block_covers_lethal': lethal and potion_block > 0 and loss - potion_block < hp,
        'potion_kills_an_enemy': weakest is not None and any(p.get('damage', 0) >= weakest for p in usable),
    }

def pile_contents(player):
    """Card names and counts in each pile; draw order is hidden, so the draw pile is only what is left."""
    piles = {}
    for pile in ('draw_pile', 'discard_pile', 'exhaust_pile'):
        cards = player.get(pile) or []
        if cards:
            counts = {}
            for card in cards: counts[card.get('name')] = counts.get(card.get('name'), 0) + 1
            piles[pile] = counts
    return piles

COUNTDOWN = re.compile(r'在第\s*(\d+)\s*回合[结結]束[时時]')

def timed_effects(player):
    """Player statuses that fire later, with turns left spelled out.

    The Bomb shows as a status "在第3回合结束时..." whose amount counts down; Sonnet once took that
    as exploding this turn, raced instead of blocking 20 damage, and the run died.
    """
    effects = []
    for status in player.get('status') or []:
        text = str(status.get('description') or '')
        if '回合' not in text: continue
        effect = {'name': status.get('name'), 'description': text}
        countdown = COUNTDOWN.search(text)
        if countdown:
            left = int(countdown.group(1))
            effect.update(turns_left=left, fires_at_end_of_this_turn=left == 1)
        effects.append(effect)
    return effects


ENERGY_NOW = re.compile(r'^[获獲]得\s*\d*\s*\[[^\]]*energy')

def gives_energy_now(card):
    # Only cards whose effect *is* gaining energy now, like Supercritical ("获得4[energy]。 消耗。").
    # Not next turn's energy ("在下个回合获得..."), and not attacks with an energy bonus: Sever
    # ("造成26点伤害。 如果这张牌击杀了敌人，则获得3[energy]") was once skipped as a wasted energy
    # card, leaving an enemy alive that it would have killed.
    text = str(card.get('description') or '').strip()
    if card.get('type') == 'Attack' or '如果' in text or '下个回合' in text or '下回合' in text: return False
    return bool(ENERGY_NOW.search(text))

def card_cost(card):
    try: return int(card.get('cost'))
    except (TypeError, ValueError): return 0  # X costs and unknowns: treat as free here.

def spare_energy(state):
    """Energy left after paying for every other card that could be played this turn (negative: short)."""
    player = state.get('player') or {}
    wanted = [c for c in player.get('hand') or []
              if (c.get('can_play') or c.get('unplayable_reason') == 'EnergyCostTooHigh') and not gives_energy_now(c)]
    return (player.get('energy') or 0) - sum(card_cost(c) for c in wanted)

def wastes_energy_card(state, card):
    """Playing an energy card while the current energy already covers the whole hand throws it away,
    and cards like Supercritical exhaust, so it is gone for the rest of the fight.

    A card that also draws (Fuel: "获得[energy]。 抽1张牌。 消耗。") is worth playing for the draw.
    """
    return gives_energy_now(card) and '抽' not in str(card.get('description') or '') and spare_energy(state) >= 0


ENEMY_NOTES = Path(__file__).resolve().parent / 'enemy_notes.md'
NOTE_LINE = re.compile(r'^-\s*([A-Z0-9_]+)\s*[：:]\s*(.+)$')

def enemy_notes(state):
    """Player-written tactics for the enemies in this fight (models misjudged some, e.g. Corpse Slugs).

    Read on every call from enemy_notes.md, so a note added mid-run applies to the next decision.
    """
    try: lines = ENEMY_NOTES.read_text(encoding='utf-8').splitlines()
    except OSError: return {}
    notes = dict(m.groups() for m in map(NOTE_LINE.match, lines) if m)
    return {enemy: notes[enemy] for enemy in enemy_ids(state) if enemy in notes}

def enemy_ids(state):
    """Distinct monster ids in the fight (entity ids without the _0, _1 suffix)."""
    return sorted({re.sub(r'_\d+$', '', str(e.get('entity_id') or '')) for e in (state.get('battle') or {}).get('enemies') or []} - {''})


PURE_BLOCK = re.compile(r'^[获獲]得\s*\d+\s*[点點]格[挡擋]。?$')

def hand_verdicts(state, facts):
    """Per-card "do not play this turn" notes, keyed by hand index.

    Haiku passed every exam rule given as a yes/no flag and missed the ones it had to work out from
    numbers: it still played Turbo-like energy cards with energy to spare and Defend over block
    already above the attack. The verdict sits on the card it chooses, with the reason.
    """
    notes = {}
    safe = not facts.get('lethal_if_no_more_block')
    enough_block = safe and ('block_surplus' in facts or 'can_afford_to_attack' in facts)
    for card in (state.get('player') or {}).get('hand') or []:
        if not card.get('can_play'): continue
        if wastes_energy_card(state, card):
            notes[card.get('index')] = '能量已夠打完其他手牌，這張的能量會浪費' + ('，而且打了就消耗' if '消耗' in str(card.get('description')) else '') + '：本回合不要打'
        elif enough_block and PURE_BLOCK.match(str(card.get('description') or '').strip()):
            notes[card.get('index')] = ('格擋已超過敵人本回合攻擊' if 'block_surplus' in facts else
                                        '血量夠厚、這回合不致命，不必補格擋') + '：本回合不要打，能量改打傷害'
    return notes

