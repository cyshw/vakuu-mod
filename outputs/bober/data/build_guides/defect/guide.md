# Defect – Build Reference Guide
> Source: Mobalytics Slay the Spire 2 Defect Guide
> Used by: AI overlay card-pick advisor

---

## Overview

Defect's gameplay revolves around **Channelling** and **Evoking** elemental Orbs for passive and active effects, with deck-thinning capabilities. Two primary archetypes: a **Claw Deck** (0-cost spam with no Orb reliance) and a **Simple Orb Deck** (balanced Lightning/Frost cycling for passive benefits).

> **Keywords:**
> - **Channel:** Add an Orb to your Orb slots.
> - **Evoke:** Trigger an Orb's active effect (happens when a new Orb is Channelled into a full slot).
> - **Focus:** Increases the passive effect of all Orbs each turn.

---

## Archetype 1: Claw Deck

**Win Condition:** Spam 0-cost attacks — primarily Claw — as many times as possible per turn. Each Claw played buffs subsequent Claws. Cycle through the deck rapidly using Scrape and All for One to replay Claw repeatedly.

> **Note:** This deck does **not** use Orbs. A small Frost stack for passive Block is a valid supplementary option.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Claw | **Core card** | Bread and butter — a few early means full commitment |
| Momentum Strike | 0-cost attack | Can be reduced to 0 Energy; strong cheap-card synergy |
| Beam Cell | Vulnerable | Easy Vulnerable applicator |
| Go for the Eyes | Weak | Easy Weak applicator |
| Flash of Steel | Cycling | Good shop pickup to keep the hand moving |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Scrape | **Primary cycler** | Key tool for cycling Scrape and other cheap cards |
| FTL | 0-cost draw | 0 Energy + minor draw |
| Skim | Draw | Main issue in this deck is draw — Skim addresses it |
| Hologram | Card recovery | Returns Claw (and later Scrape / All for One) to hand; upgrade removes Exhaust |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| All for One | Hand refill | Incredible for returning Claw and 0-cost cards to hand |
| Feral | 0-cost doubler | Like Echo Form but for 0-Energy cards; cheaper to set up |
| Machine Learning | Draw | Helpful but somewhat redundant once All for One is online |
| Panache | Bonus damage | Spam cards → trigger Panache repeatedly |
| Secret Weapon | Tutor | Pulls All for One or Scrape to get engine moving |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Iron Club | Great draw tool |
| Nunchaku | Triggers on every card played — excellent with spam |
| Shuriken | Triggers on every card played |
| Kunai | Triggers on every card played |
| Ornamental Fan | Triggers on every card played |
| Kusarigama | Triggers on every card played |
| Power Cell | Gets the engine started early |

### AI Pick Logic – Claw Deck
- **Always prioritize Claw early** — seeing multiple Claws is the signal to fully commit.
- **Do NOT bloat the deck** — adding too many 0-cost cards makes finding Claw harder; stay lean.
- **Scrape is the engine** — prioritize it highly, it's the primary cycling tool.
- **All for One** is the late-game payoff — pick it once Claw + Scrape base is established.
- **Hologram** should be upgraded as soon as possible to remove the Exhaust.
- **Passive Frost Block** is a valid supplement — without Orbs this deck can be fragile defensively.
- **Card-play relics** (Nunchaku, Kunai, etc.) are exceptionally strong here due to sheer card volume.

---

## Archetype 2: Simple Orb Deck

**Win Condition:** Cycle Orbs — primarily Lightning and Frost — to accumulate passive damage and Block each turn. High Focus amplifies all Orb passive effects. Channelling new Orbs into full slots Evokes them for active effects. Can pivot toward a more defensive Frost-heavy variant.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Ball Lightning | Damage + Orb | Decent early damage with Lightning Orb upside |
| Cold Snap | Defense + Orb | Like Ball Lightning but for Frost/Block |
| Coolheaded | All-round Orb card | Great in any Defect deck; upgraded draws 2 cards |
| Barrage | Multi-hit damage | Often 1 Energy for 15 damage with multi-hit upside |
| Compile Driver | Draw | Powerful draw tool for any Orb deck |
| Lightning Rod | Lightning gen | Weak Block but decent Lightning Orb generator |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Glacier | Defense | Solid Frost-focused defense option |
| Chaos | Orb variety | Like Zap but can Channel Dark and Plasma too |
| Capacitor | Orb slots | More Orb slots = more end-of-turn effects; pairs great with Focus but hinders Evoke |
| Loop | Passive power | Great with Focus; loops Orb effects |
| Thunder | Damage ramp | Racks up damage as you cycle through Orbs |
| Hailstorm | AoE | Extra AoE damage from Frost Orbs |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Defragment | Focus stacking | **Take as many as possible and upgrade them** — core scaling card |
| Modded | Orb boost | Good until it costs 2 Energy — pick early |
| Multi-Cast | Finisher | Great with Focus, extra Energy from Plasma, and Dark Orbs |
| Voltaic | Late finisher | Excess Orbs instantly Evoked — strong late-round burst |
| Tesla Coil | Damage | Good with extra Orb slots and some Focus |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Emotion Chip | Great if you can manage damage taken |
| Gold-Plated Cables | Simple Orb passive benefit |
| Metronome | Channelling often = free AoE 30 damage |
| Runic Capacitor | Good with heavy Frost or Dark builds |
| Data Disk | Focus is always welcome |
| Lost Wisp / Game Piece / Jeweled Mask | Power card synergy — Defect runs many Powers |

### AI Pick Logic – Simple Orb Deck
- **Defragment is the #1 priority** — stack and upgrade as many as possible; Focus is the primary scaling mechanism.
- **Coolheaded** is a safe early pick in any Defect deck — always good.
- **Capacitor vs Evoke tension:** More Orb slots = stronger passives but weaker Evoke timing. Decide which direction before picking Capacitor.
- **Defensive pivot:** If survival is difficult, shift toward more Frost Channelling cards and extra Orb slots — Barrage scales with slots, and Dark Orbs can "cook" safely behind Frost.
- **Multi-Cast** is the primary finisher — needs Focus + Plasma Energy to be reliable.
- **Power relic synergies** are very common in Defect — always flag Power-synergy relics.
- **High Focus + frequent Channelling** is the core win condition — every pick should serve one of these two goals.

---

## Defensive Variant Note (Orb Deck)

If committing to a Block-first playstyle within the Orb deck:
- Increase Orb slots (Capacitor).
- Draft more Frost Channel cards (Cold Snap, Glacier, Coolheaded).
- Allow Dark Orbs time to build behind Frost protection.
- Barrage benefits directly from more Orb slots.

---

## Cross-Archetype Notes

| Card / Relic | Works In |
|---|---|
| Frost Orbs (passive Block) | Orb (core), Claw (supplementary defense) |
| Nunchaku / Kunai / Shuriken / Ornamental Fan / Kusarigama | Claw (core), Orb (minor) |
| Compile Driver | Orb (core draw), Claw (useful if added) |

---

## AI Decision Framework

When a card pick appears after combat, the overlay AI should:

1. **Detect current archetype commitment** based on cards already in deck:
   - 2+ Claw cards → Claw deck
   - Scrape or All for One → Claw deck
   - Defragment, Capacitor, Loop, or Coolheaded → Orb deck
   - Mixed / early game → stay generic; Coolheaded is a safe pick for either

2. **Score each offered card** against the detected archetype's priority list above.

3. **Warn on Claw deck bloat** — flag if deck is getting too large; finding Claw becomes harder with more cards.

4. **Flag Focus gap in Orb deck** — if no Defragment has been picked yet, prioritize it highly whenever offered.

5. **Flag Orb slot vs Evoke tension** — if Capacitor is offered, note whether the deck benefits more from passive stacking or active Evoke triggers.

6. **Note Power relic synergies** — Defect decks run many Power cards; flag Lost Wisp, Game Piece, Jeweled Mask if held.

7. **Flag draw gaps in Claw deck** — Skim, FTL, or Iron Club should be considered if hand size is consistently an issue.
