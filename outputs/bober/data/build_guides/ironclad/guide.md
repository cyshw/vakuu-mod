# Ironclad – Build Reference Guide
> Source: Mobalytics Slay the Spire 2 Ironclad Guide
> Used by: AI overlay card-pick advisor

---

## Overview

Ironclad has five primary archetypes. The character emphasizes ramping Strength, converting resources (HP and cards) into power, and defence-first turtling. Archetypes can partially overlap — especially Strength + Bloodletting, and Block + Exhaust.

---

## Archetype 1: Strength Build

**Win Condition:** Stack Strength buffs, then chain multi-hit attacks. Every hit benefits from Strength separately, making multi-hit cards exponentially more powerful.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Twin Strike | Damage | Benefits from Strength **twice** — core card |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Fight Me! | Strength gen + damage | Multi-hit + Strength generator |
| Inflame | Strength gen | Cheap and efficient |
| Rupture | Strength gen | Best combined with self-damage (see Bloodletting) |
| Whirlwind | AoE multi-hit | Best multi-hit card in the deck, has AoE |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Demon Form | Passive Strength | Best passive generation but expensive to set up |
| Brand | Deck thinner | Thins Strikes/Defends/Status cards; scales with Rupture |
| Thrash | Ramping damage | Multi-hit; consumes Strength-buffed cards to scale exponentially |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Anchor / Horn Cleat / Permafrost | Survival tools while setting up Demon Form |
| Brimstone | Risk/reward — heavily favours player in Strength deck |
| Ruined Helmet | ~2 extra Strength, not build-defining but helpful |
| Sword of Jade | Free 3 Strength |

### AI Pick Logic – Strength Build
- **Prioritize** any multi-hit card — each hit scales with Strength separately.
- **Always consider** Inflame and Fight Me! for cheap Strength generation.
- **Pick Demon Form** when you have enough Energy/survival to set it up.
- **Apply Vulnerable** before big Strength-buffed hits for maximum damage.
- **Extra Energy** relics are very valuable — allow setup AND offense in the same turn.

---

## Archetype 2: Block Build

**Win Condition:** Maximize Block generation, use it as both defense and offense via Body Slam. Juggernaut converts Block into passive damage.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Body Slam | **Main damage** | Scales with current Block — goes nuclear late game |
| Shrug It Off | Defense + draw | Solid all-round card for multiple decks |
| True Grit | Deck thinner | Removes Strike cards, improves consistency |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Flame Barrier | Defense + damage | Expensive but high Block + punishes multi-hit enemies |
| Taunt | Body Slam setup | Upgraded gives 2 Vulnerable — great with Colossus |
| Stone Armor | Passive armor | Multi-turn Block generation |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Juggernaut | Damage | Main damage dealer outside Body Slam — very important |
| Barricade | Block retention | Keeps Block from resetting each turn — enables consistent Body Slam |
| Crimson Mantle | Block enabler | Cheap; watch for negative synergy with Unmovable |
| Impervious | Panic button | Becomes core with Barricade and/or Unmovable |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Cloak Clasp | Triggers Juggernaut at end of turn |
| Fresnel Lens | Multiplies Block gained from cards |
| Vambrace | Functions like Unmovable |
| Sai | Simple passive Block generator |
| Parrying Shield | Bonus damage |
| Pael's Legion | Block accumulation, excellent with Barricade |
| Bronze Scales | Punishes attackers — this deck tanks well |

### AI Pick Logic – Block Build
- **Core combo:** Barricade → stack Block → Body Slam.
- **Always pick** Juggernaut once committing to Block — it turns defense into free offense.
- **Prioritize** Barricade highly — without it, Body Slam is inconsistent.
- **Reduce enemy damage** via Weak or Strength reduction to preserve Block.
- **Passive Block sources** (Plating, Stone Armor) are especially valuable here.

---

## Archetype 3: Exhaust Build

**Win Condition:** Exhaust cards rapidly using the holy trinity (Corruption + Dark Embrace + Feel No Pain) for cycling and passive Block. Use Body Slam or Ashen Strike / Pact's End as finishers.

> **Note:** Removal of Dead Branch in StS2 made this a faster, finisher-reliant deck rather than an infinite loop.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Corruption | **Core card** | Foundation of all Exhaust synergies |
| True Grit | Block + thinner | Removes Strike cards passively |
| Body Slam | Damage | Skills generate Block via Feel No Pain — Body Slam punishes this |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Ashen Strike | Finisher | New to StS2; strong late-turn closer |
| Burning Pact | Draw | Less critical once Dark Embrace is online |
| Evil Eye | Defense | Often acts as 1 Energy / 16 Block |
| Feel No Pain | **Critical** | Passive Block on every Exhaust |
| Forgotten Ritual | Energy gen | Free Energy with enough draw |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Dark Embrace | **Critical** | Keeps cycling going — draw on every Exhaust |
| Brand | Exhaust engine | Free Exhaust; less needed with Corruption |
| Offering | Acceleration | Heavy price, high upswing |
| Pact's End | AoE finisher | 17 AoE damage for 0 mana |
| Thrash | Damage | Multi-hit; removes Strike cards AND gets Exhaust benefit |
| Juggernaut | Bonus damage | Deals damage each time a card is Exhausted |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Charon's Ashes | AoE damage on Exhaust — excellent |
| Forgotten Soul | Smaller version of Charon's Ashes |
| Burning Sticks | Smaller Dead Branch substitute |
| Joss Paper | Extra draw |

### AI Pick Logic – Exhaust Build
- **Holy trinity first:** Corruption → Dark Embrace → Feel No Pain. All three are needed.
- **Deck should be mostly Skills** — they provide Block via Feel No Pain and feed the cycle.
- **Pick Juggernaut** once Exhaust engine is running for free damage.
- **Finishers needed:** Ashen Strike, Body Slam, Pact's End to close fights.
- **Apply Vulnerable** before finisher attacks.

---

## Archetype 4: Bloodletting Build

**Win Condition:** Play HP-costing cards to trigger Rupture (HP loss → Strength) and Inferno (HP loss → AoE damage). Self-damage is a resource, not a penalty.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Breakthrough | AoE damage | Only costs 1 HP — best damage/cost ratio here |
| Bloodletting | Energy gen | Only good with strong Draw; 3 HP is steep |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Rupture | **Pivotal** | HP loss → Strength. Upgraded doubles efficiency |
| Inferno | **Pivotal** | HP loss → AoE damage every turn |
| Hemokinesis | Single-target damage | 2 HP cost — somewhat high price |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Crimson Mantle | Guaranteed HP loss | -1 HP/turn; activates Rupture/Inferno passively |
| Brand | Deck thinner | Removes Strikes; all-round good here |
| Offering | High value | Heavy HP price but large upswing |
| Feed | HP buffer | Flat damage taken, not percentage — higher total to draw from |
| Tear Asunder | Win condition | With Strength scaling + self-damage, can solo runs |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Centennial Puzzle | Free draw on turn 1 |
| Demon Tongue | Works excellently with Bloodletting and Offering |
| Self-Forming Clay | Passive Block accumulation |

### AI Pick Logic – Bloodletting Build
- **Must have** Rupture — without it, self-damage has no payoff.
- **Prioritize** Inferno for guaranteed passive self-damage trigger.
- **Breakthrough** is the best attack here: AoE + only 1 HP cost.
- **Multi-hit cards** remain best offense once Strength is built via Rupture.
- **The Pain curse** (1 HP per card played) is actually *useful* in this deck — don't remove it blindly.
- **Watch HP total** — Feed and Crimson Mantle help manage the HP pool.

---

## Archetype 5: Strike Build (Perfected Strike)

**Win Condition:** Maximize the count of "Strike"-named cards to pump Perfected Strike into a massive single-target nuke. Simple to build, no rare cards required.

### Early Game Priorities (Commons)
| Card | Role | Notes |
|------|------|-------|
| Perfected Strike | **Core** | Starts at 18 damage with starter deck (5 Strikes). Scales with every Strike card |
| Twin Strike | Strike counter + damage | 10 damage for 1 Energy; benefits from Strength twice |
| Pommel Strike | Draw | Key draw source for the deck |
| Breakthrough | AoE coverage | Good early AoE option |
| Tremble | Vulnerable | Not great alone, but Vulnerable + big nukes = lethal |

### Mid Game Priorities (Uncommons)
| Card | Role | Notes |
|------|------|-------|
| Taunt | Vulnerable setup | Upgraded gives 2 Vulnerable — excellent with Perfected Strike |
| Expect a Fight | Energy gen | Very useful since Perfected Strike costs 2 Energy |

### High-Commitment / Payoff Cards
| Card | Role | Notes |
|------|------|-------|
| Pyre | Extra Energy | More flexibility to play Perfected Strike |
| Hellraiser | Deck transformer | Changes how deck is played; invest in draw — **potential infinite with Pommel Strike** |
| Colossus | Defense | Supplementary Block since deck otherwise lacks it |
| Cruelty | Damage bonus | Great with Vulnerable-heavy builds |

### Relic Priorities
| Relic | Reason |
|-------|--------|
| Strike Dummy | Direct Strike synergy |
| Intimidating Helmet | Extra Block on expensive-card turns |
| Any Ancient Energy relic | Helps pay for Perfected Strike's 2 Energy cost |

### AI Pick Logic – Strike Build
- **Do NOT add every Strike card** — marginal scaling not worth weak cards bloating deck.
- **Energy management** is key — Perfected Strike costs 2. Prioritize Energy relics/cards.
- **Deck lacks Block** — always include some defensive cards; don't rely solely on Colossus.
- **Vulnerable** is critical before Perfected Strike — always look for application.
- **Hellraiser** is a build-defining find — pivot heavily to draw if found.
- **Late-game warning:** Deck can fall off in later Acts without additional scaling. Plan ahead.

---

## Cross-Archetype Notes

| Card / Relic | Works In |
|---|---|
| Twin Strike | Strength, Strike |
| Body Slam | Block, Exhaust |
| Brand | Strength, Exhaust, Bloodletting |
| True Grit | Block, Exhaust |
| Rupture | Strength, Bloodletting |
| Breakthrough | Bloodletting, Strike |
| Juggernaut | Block, Exhaust |
| Vulnerable (any source) | Strength, Strike |

---

## AI Decision Framework

When a card pick appears after combat, the overlay AI should:

1. **Detect current archetype commitment** based on cards already in deck:
   - Corruption in deck → Exhaust build
   - Rupture or Inferno in deck → Bloodletting build
   - 2+ Perfected Strike or heavy Strike count → Strike build
   - Barricade or Juggernaut → Block build
   - Demon Form or Inflame → Strength build
   - Mixed / early game → evaluate all options

2. **Score each offered card** against the detected archetype's priority list above.

3. **Flag crossover value** if a card appears across multiple archetypes.

4. **Warn on bloat** for Strike build — adding weak Strikes for marginal scaling is a known trap.

5. **Note relic synergies** if the player holds relics from the priority lists above.

6. **Flag finisher gap** in Exhaust build — ensure Ashen Strike, Pact's End, or Body Slam is present.
