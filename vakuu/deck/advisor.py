"""Card ratings for rewards and shops from daily Spire Codex community data, with the deck's direction."""
from spire_codex import card_metrics as codex_metrics
from vakuu_deck import deck_direction

CHARACTERS = {name.upper(): name for name in ("Ironclad", "Silent", "Defect", "Necrobinder", "Regent")}


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
    # BoberInSpire's offline rules were dropped (no redistribution license): community ratings,
    # read daily and cached, plus the deck direction carry the advice.
    metrics = codex_metrics(character)
    if not metrics:
        return {"status": "no_ratings", "message": "還沒取得 Spire Codex 社群評分（需要連網一次）。"}
    community = codex_advice(state, offered, metrics)
    direction, fits = deck_direction(state, offered, None)
    for row in community['ranked_cards']: row['deck_fit'] = fits[row['index']]
    community.update(deck_direction=direction, skip_evaluated=False)
    return community
