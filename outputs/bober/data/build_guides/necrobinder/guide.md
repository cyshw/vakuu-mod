# Necrobinder – Build Reference Guide
> Source: Mobalytics Slay the Spire 2 Necrobinder Guide
> Used by: AI overlay card-pick advisor

---

## Overview

Necrobinder has two primary archetypes built around unique mechanics: **Doom** (a death-mark execution system) and **Osty** (a reanimated hand companion that acts as a persistent shield).

Important character note: Necrobinder starts with a **significantly lower HP pool** than other characters — survival and Block planning are always a priority regardless of archetype.

---

## Archetype 1: Doom Deck

**Win Condition:** Stack Doom on enemies (a death-mark that executes when HP drops to or below Doom stacks at end of their turn). Cards have higher damage values to compensate for the delay. Requires a solid Block plan to survive until the execution triggers.

> **Keyword — Doom:** Enemies with HP at or below their Doom stacks are executed at the end of their turn.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Blight Strike | Damage + Doom | Solid damage card on its own; potential 1 Energy for 16 damage |
| Defile | High-ratio damage | Not a Doom card but high damage/cost ratio — drops enemies into Doom range |
| Negative Pulse | AoE Doom + Block | Fits the defense plan naturally; great early pickup |
| Scourge | Doom + draw | 1 Energy for 13 damage with built-in draw — excellent all-rounder |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Deathbringer | AoE Doom + Weak | Large AoE Doom with Weak debuff — strong combo for the game plan |
| Delay | Block + Energy refund | Survival tool; Energy refund helps afford expensive cards |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Death's Door | Passive Block | Excellent Block source once you have a good threshold of Doom cards |
| End of Days | Execution bypass | Solves the "waiting" problem — high cost but skips the delay |
| No Escape | Single-target Doom | Incredibly potent single-target Doom stacker |
| Oblivion | Mass damage | High potential alongside multiple cheap cards like Souls |
| Shroud | Doom → Block conversion | Converts Doom triggers into Block throughout the fight |
| Time's Up | Finisher | Great against high-HP targets like Elites and Bosses |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Book Repair Knife | Very reliable healing — commit signal if found early |
| Undying Sigil | Weakens enemies on their final turn alive — strong with Doom timing |

### AI Pick Logic – Doom Deck
- **Block is mandatory** — delayed kill timing means you must tank hits; always have a Block plan.
- **Prioritize Scourge early** — damage + draw in one card is exceptional value.
- **Defile** is a strong pick even without direct Doom synergy — drops HP into execution range.
- **End of Days** is a high-priority payoff if Energy generation is sufficient.
- **Consider a small Soul package** as a splash — Souls provide card draw to find key Doom cards faster.
- **Shroud** turns Doom triggers into Block — pick it if survival is consistently difficult.
- **Time's Up** is the go-to finisher for Elite and Boss fights where Doom alone is slow.
- **Book Repair Knife or Undying Sigil** early = strong signal to commit to Doom.

---

## Archetype 2: Osty Deck

**Win Condition:** Grow and sustain Osty (the reanimated hand companion) via Summon mechanics. Osty's HP functions as a persistent shield that doesn't expire like Block — effectively providing unlimited health stacking if kept alive.

> **Mechanic — Summon:** Cards and effects that buff or restore Osty during combat. Osty's HP persists between turns (unlike Block).

> **Important variant warning:** There is a sacrificial Osty variant using cards like Bone Shards. Do NOT mix cards from the growth variant and the sacrificial variant — they have conflicting game plans.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Pull Aggro | Block equivalent | Effectively 11 points of Block value + Summon synergies |
| Snap | Cheap Osty attack | Enables other Osty cards; Retain adds extra flexibility |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| High Five | AoE + Vulnerable | AoE damage with Vulnerable — always strong |
| Rattle | Multi-hit damage | Key multi-attack source with great combo potential |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Fetch | 0-cost cycling attack | Cycles itself; excellent alongside Rattle |
| Flatten | Free damage | Free damage once enough Osty attacks are in deck |
| Necro Mastery | Defense → AoE offense | Converts defense into damage — requires Osty to reliably grow |
| Reanimate | Burst Summon | Big burst of Summon — only recommended with extra Energy |
| Sic 'Em | Osty attack + Summon | Great Summon capabilities, especially alongside Rattle |
| Spur | Healing | Helpful passive healing once Osty is consistently kept alive |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Bone Flute | Every bit of Block matters on low-HP Necrobinder — commit signal if found early |

### AI Pick Logic – Osty Deck
- **Keep Osty alive** — the entire deck's value collapses if Osty dies. Prioritize Summon cards.
- **Rattle + Fetch** is the core combo — multi-hit cycling for explosive Osty turns.
- **Sic 'Em** is a high priority alongside Rattle — strong Summon generation.
- **Necro Mastery** is a late-game payoff — only pick once Osty growth is reliable.
- **Reanimate** is situational — only pick with surplus Energy, otherwise too costly.
- **Do NOT mix growth and sacrificial variants** — Bone Shards and similar sacrificial cards conflict directly with the growth game plan.
- **Bone Flute** early = strong signal to commit to Osty.
- **Always maintain awareness of Osty's HP** — the deck is significantly weaker when Osty is low.

---

## Cross-Archetype Notes

| Card / Mechanic | Works In |
|---|---|
| Block cards (any) | Doom (survival), Osty (supplements Pull Aggro) |
| Souls (splash) | Doom (draw engine), Osty (minor cycling) |
| Vulnerable sources (High Five) | Doom (drop HP into range faster), Osty (damage amplifier) |

---

## AI Decision Framework

When a card pick appears after combat, the overlay AI should:

1. **Detect current archetype commitment** based on cards already in deck:
   - Blight Strike, Scourge, Deathbringer, or No Escape → Doom build
   - Pull Aggro, Snap, Sic 'Em, or Rattle → Osty build
   - Mixed / early game → stay generic, prioritize Block and draw

2. **Always flag Block options** — Necrobinder's low base HP makes Block universally important regardless of archetype.

3. **Score each offered card** against the detected archetype's priority list above.

4. **Warn on variant mixing in Osty deck** — if a sacrificial card (e.g. Bone Shards) is offered and the deck is built for Osty growth, flag the conflict clearly.

5. **Flag draw gaps in Doom deck** — if draw is insufficient, suggest considering a Soul package splash to find Doom cards more consistently.

6. **Note relic synergies** if the player holds Book Repair Knife, Undying Sigil, or Bone Flute.

7. **Flag Energy constraints** — both archetypes have costly key cards (End of Days, Reanimate). Highlight Energy-generating options when relevant.
