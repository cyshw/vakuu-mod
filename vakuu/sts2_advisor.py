"""Card ratings for rewards and shops: daily Spire Codex community data, BoberInSpire as offline fallback."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "bober"))
# BoberInSpire's offline rules have no license for redistribution, so release packages leave them
# out; community ratings and the deck direction then carry the advice on their own.
try: from python_app.reward_advisor import recommend
except ImportError: recommend = None
from spire_codex import card_metrics as codex_metrics
from vakuu_deck import deck_direction

DATA = ROOT / "bober" / "data"


def _catalog(name):
    try: return {row["id"]: row["name"] for row in json.loads((DATA / name).read_text(encoding="utf-8"))}
    except OSError: return {}


CARDS = _catalog("spire_codex_cards.json")
RELICS = _catalog("spire_codex_relics.json")
CHARACTERS = {name.upper(): name for name in ("Ironclad", "Silent", "Defect", "Necrobinder", "Regent")}


def names(rows, catalog, unknown, kind, upgraded=False):
    result = []
    for row in rows:
        key = row.get("id", "").split(".")[-1]
        name = catalog.get(key)
        if name is None:
            unknown.append({"kind": kind, "id": key, "name": row.get("name")})
            name = key or "UNKNOWN"
        if upgraded and row.get("is_upgraded"):
            name += "+"
        result.append(name)
    return result


def offered_cards(state):
    """Cards on offer with their UI index: card reward choices, or stocked shop cards."""
    if state.get("state_type") == "card_reward":
        return state.get("card_reward", {}).get("cards", [])
    if state.get("state_type") == "shop":
        # Shop items use card_* fields and share one index space with relics and potions.
        return [{"index": item["index"], "id": item.get("card_id") or "", "name": item.get("card_name"),
                 "is_upgraded": False}
                for item in state.get("shop", {}).get("items", [])
                if item.get("category") == "card" and item.get("is_stocked")]
    return None


def codex_advice(state, offered, metrics):
    """Rank offers by Codex Score (0-100, from win rates of A10+ runs with this character)."""
    ranked, unknown = [], []
    for card in offered:
        key = card.get("id", "").split(".")[-1]
        row = metrics["cards"].get(key)
        if row is None: unknown.append({"kind": "offer", "id": key, "name": card.get("name")})
        ranked.append({**(row or {"score": None}), "index": card["index"], "id": card.get("id"),
                       "name": card.get("name"), "is_upgraded": card.get("is_upgraded", False)})
    ranked.sort(key=lambda row: -1 if row["score"] is None else row["score"], reverse=True)
    return {
        "status": "incomplete_data" if unknown else "ok",
        "engine": "Spire Codex", "character": metrics["character"], "bracket": metrics["bracket"],
        "data_through": metrics["data_through"], "baseline_win_rate": metrics["baseline_win_rate"],
        "ranked_cards": ranked,
        "best_scored_index": None if unknown else ranked[0]["index"],
        "can_skip": state.get("card_reward", {}).get("can_skip", False),
        "unknown_items": unknown,
        "limitations": ["score 是社群對局的 Codex Score（0-100），依拿到該牌的玩家勝率計算，不是這副牌組的專屬評估。",
                        "lift 是拿牌玩家勝率比其自身平均高出的百分點；升級版與未升級版共用同一筆統計。"],
    }


def advise(state):
    offered = offered_cards(state)
    if offered is None:
        return {"status": "not_card_reward", "state_type": state.get("state_type"),
                "message": "抓牌評分會在卡牌獎勵或商店畫面出現。"}
    player = state.get("player", {})
    if "deck" not in player or "character_id" not in player:
        return {"status": "missing_deck_api", "message": "請重啟遊戲載入新版 API 模組，才能取得牌組與角色 ID。"}
    character = CHARACTERS.get(player["character_id"].split(".")[-1])
    if not character:
        return {"status": "unsupported_character", "character_id": player["character_id"]}
    if not offered:
        return {"status": "no_offers"}
    metrics = codex_metrics(character)
    if recommend is None:  # Release package without the offline rules.
        if not metrics: return {"status": "no_ratings", "message": "還沒取得社群評分（需要連網一次）。"}
        community = codex_advice(state, offered, metrics)
        direction, fits = deck_direction(state, offered, None)
        for row in community['ranked_cards']: row['deck_fit'] = fits[row['index']]
        community.update(archetype=None, deck_direction=direction, skip_evaluated=False, warnings=[])
        return community
    unknown = []
    deck = names(player["deck"], CARDS, unknown, "deck")
    relics = names(player.get("relics", []), RELICS, unknown, "relic")
    options = names(offered, CARDS, unknown, "offer", upgraded=True)
    if not options:
        return {"status": "no_offers"}
    result = recommend(character, deck, relics, options)
    direction, fits = deck_direction(state, offered, result.archetype)
    # Match by normalized English name while preserving actual UI indices,
    # including duplicate offers and upgraded copies.
    unused = list(zip(options, offered))
    ranked = []
    for rec in result.recommendations:
        pos = next(i for i, (name, _) in enumerate(unused) if name == rec.name)
        _, card = unused.pop(pos)
        row = asdict(rec)
        row["english_name"] = row.pop("name")
        row.update({"index": card["index"], "id": card["id"], "name": card.get("name"),
                    "is_upgraded": card.get("is_upgraded", False), "deck_fit": fits[card['index']]})
        ranked.append(row)
    if metrics:
        community = codex_advice(state, offered, metrics)
        local = {row['index']: row for row in ranked}
        for row in community['ranked_cards']:
            row['deck_fit'] = fits[row['index']]
            row['offline_fit'] = {k: v for k, v in local[row['index']].items()
                                  if k not in {'index', 'id', 'name', 'is_upgraded', 'deck_fit'}}
        community.update(archetype=result.archetype, deck_direction=direction,
                         skip_evaluated=False, warnings=result.warnings)
        return community
    tier_meta = json.loads((DATA / "tier_lists" / "mobalytics_cards.json").read_text(encoding="utf-8"))
    return {
        "status": "incomplete_data" if unknown else "ok",
        "engine": "BoberInSpire", "character": character,
        "deck_count": len(deck), "archetype": result.archetype,
        "deck_direction": direction,
        "ranked_cards": ranked,
        "best_scored_index": None if unknown else ranked[0]["index"],
        "can_skip": state.get("card_reward", {}).get("can_skip", False),
        "skip_evaluated": False,
        "unknown_items": unknown,
        "warnings": result.warnings,
        "tier_snapshot_date": tier_meta.get("snapshot_date"),
        "limitations": ["本機規則評分，並非勝率或最佳策略保證。",
                        "未評估血量、路線、頭目與跳過選項。",
                        "上游評級快照未確認符合 v0.111.0 的全部平衡調整。"],
    }
