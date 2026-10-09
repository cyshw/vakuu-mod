# Silent – Build Reference Guide
> Source: Mobalytics Slay the Spire 2 Silent Guide
> Used by: AI overlay card-pick advisor

---

## Overview

Silent has three primary archetypes. Each has distinct win conditions, key cards, and relic synergies. The AI should identify which archetype the current deck is committing to (or closest to) and recommend picks accordingly.

---

## Archetype 1: Shiv Deck

**Win Condition:** Generate, buff, and play as many Shiv cards as possible. Shivs are 0-cost cards that deal small damage — scaling comes from volume and multipliers.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Leading Strike | Damage + Shiv gen | Essentially a Strike that also adds a Shiv to hand |
| Cloak and Dagger | Defense + Shiv gen | Essentially a Defend that also adds a Shiv to hand |
| Blade Dance | AoE damage | Now has Exhaust. At 3 Shivs gained: minimum 12 damage, great Shiv synergy |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Accuracy | **#1 priority** | Doubles Shiv damage. Stack as many as possible |
| Infinite Blades | Consistency | Guarantees a Shiv each turn; upgraded = Innate (guaranteed Turn 1) |
| Hidden Daggers | Sly synergy | Great with Abrasive, Reflex, Untouchable |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Fan of Knives | AoE | Situational — 2 Energy is costly |
| Knife Trap | Finisher | Can chain 15–20 Shivs; Shivs are *played* so trigger Nunchaku, Shuriken, Kunai, etc. |
| Finisher | Burst damage | High damage on turns where many Shivs were played |
| Afterimage | Defense | Extra Block each turn |
| Serpent Form | Late-game powerhouse | Overkill but incredible with Shivs and Knife Trap |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Ninja Scroll | Strong Shiv-focused start |
| Helical Dart | Defensive coverage |
| Iron Club | Triggers on each Shiv played |
| Nunchaku | Triggers on each Shiv played |
| Shuriken | Triggers on each Shiv played |
| Kunai | Triggers on each Shiv played |
| Ornamental Fan | Triggers on each Shiv played |
| Kusarigama | Triggers on each Shiv played |
| Joss Paper | Draw, since Shivs Exhaust on play |

### AI Pick Logic – Shiv Deck
- **Always pick** Accuracy if available — it is the single best Shiv card.
- **Prioritize** any card that generates Shivs (Infinite Blades, Cloak and Dagger, Leading Strike).
- **Consider** Knife Trap once you have consistent Shiv generation.
- **Partial commitment** is valid: even 1–2 Shiv generators enable relic synergies (Nunchaku, Kunai, etc.).

---

## Archetype 2: Poison Deck

**Win Condition:** Stack Poison as fast as possible. Damage ramps exponentially — slow start, wins long fights.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Poisoned Stab | Efficient damage | Effectively 11 damage for 1 Energy |
| Deadly Poison | Poison stacker | 15 damage over 5 turns for 1 Energy |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Haze | AoE Poison | Fits many decks due to Discard synergy (Prepared, Acrobatics, Survivor) |
| Noxious Fumes | Guaranteed Poison | Applies Poison to all enemies every turn |
| Outbreak | AoE Poison | Great with Bouncing Flask |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Accelerant | Poison multiplier | Incredibly powerful upgraded; use after Poison is stacked |
| Bubble Bubble | Efficient stacker | Requires Poison to already exist on target |
| Mirage | Panic button | Great for late turns |
| Burst | Skill multiplier | Most Poison applicators are Skills — high value here |

> **Note:** Envenom and Corrosive Wave apply Poison but are better in other decks.

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Snecko Skull | Easier Poison application |
| Unsettling Lamp | Doubles first Poison hit |
| Twisted Funnel | Strong Poison start; enables Bubble Bubble immediately |
| History Course | Replays last Attack/Skill — great with Poison applicators |
| Anchor / Horn Cleat / Captain's Wheel | Survival tools to stay alive while Poison ramps |

### AI Pick Logic – Poison Deck
- **Priority 1:** Survive early — favor defensive and debuffing cards.
- **Priority 2:** Stack Poison fast — Deadly Poison, Poisoned Stab, Bubble Bubble.
- **Priority 3:** Once Poison is stacked, pick Accelerant to multiply it.
- **Avoid:** Picking Envenom/Corrosive Wave unless pivoting to a different archetype.
- **Watch out for:** Enemies with Artifact — it blocks Poison application.
- **AoE note:** This archetype is weak at AoE damage; use Haze, Outbreak, Accelerant for spread.

---

## Archetype 3: Sly Deck

**Win Condition:** Rapidly cycle through a thin deck, discarding Sly cards to play them for free.

> **Keyword — Sly:** If this card is discarded from your hand before the end of your turn, play it for free.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Flick-Flack | Damage | Strong even without discard trigger |
| Ricochet | Single-target damage | Better than Flick-Flack 1v1 but weaker AoE; weak without discard |
| Untouchable | Core Block | Main defensive card |
| Acrobatics | Draw + Sly activator | Key cycling card |
| Dagger Throw | Generically good | Improved with Sly |
| Prepared | 0-cost draw | Free Sly activator |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Haze | AoE damage | Good Poison crossover |
| Reflex | Cycling | Enables draw loop |
| Tactician | **Critical** | Keeps the cycle going — must not miss |
| Calculated Gamble | Hand reset | Replaces hand and activates all Sly cards — panic button |
| Speedster | Damage while cycling | |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Tools of the Trade | Power | Critical; helpful in any deck, essential here |
| Master Planner | Enabler | Makes all non-Sly Skills into Sly cards |
| Abrasive | Defense + Thorns | Great for corridor fights/elites; weaker vs bosses |
| Serpent Form | Damage | Expensive but high output |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Tingsha | Damage from discards |
| Tough Bandages | Block from discards |
| Iron Club / Nunchaku / Shuriken / Kunai / Ornamental Fan / Kusarigama | Sly cards are *played*, so all proc on discard-trigger |
| Pendulum | Extra draw during heavy cycling (often overkill) |
| The Abacus | Extra Block |

### AI Pick Logic – Sly Deck
- **Keep deck thin** — this is the most important constraint. Remove Strikes aggressively.
- **Always pick** Tactician if available — missing it breaks the cycle loop.
- **Prioritize** Acrobatics, Prepared, Reflex to cycle and discard Sly cards.
- **Avoid** bloating the deck — each non-essential card slows cycling.
- **Crossover:** Add Poison (Haze) for damage outside of Speedster/Serpent Form.
- **Starter card Survivor** is a natural Sly enabler — keep it.

---

## Cross-Archetype Notes

| Card / Relic | Works In |
|---|---|
| Serpent Form | Shiv, Sly |
| Haze | Poison, Sly |
| Nunchaku / Kunai / Shuriken / Ornamental Fan / Kusarigama | Shiv, Sly |
| Infinite Blades | Shiv (core), Sly (synergy) |
| Acrobatics / Prepared | Sly (core), Poison (activates Haze) |

---

## AI Decision Framework

When a card pick appears after combat, the overlay AI should:

1. **Detect current archetype commitment** based on cards already in deck:
   - 1+ Accuracy → Shiv deck
   - 2+ Poison applicators (Deadly Poison, Poisoned Stab, Noxious Fumes) → Poison deck
   - Tactician or Tools of the Trade → Sly deck
   - Mixed / early game → evaluate all options

2. **Score each offered card** against the detected archetype's priority list above.

3. **Flag crossover value** if a card appears in multiple archetypes.

4. **Warn** if picking a card would bloat the deck (especially in Sly deck).

5. **Note relic synergies** if the player holds any relics from the priority lists above.
