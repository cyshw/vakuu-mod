【節奏】手牌上標了 skip_this_turn 的牌，是程式已經算好的結論：本回合不要打（原因寫在上面），不要自己重算或推翻；能量改花在其他牌上。combat_facts 出現 block_surplus 時，格擋已經多於敵人攻擊，再補格擋是浪費，剩餘能量改打傷害。出現 can_afford_to_attack 時，血量足夠且這回合不致命：照它的 rule 先把能量花在傷害上，優先集火 weakest_enemy 這類最容易打死的敵人，不要為了零傷害補滿格擋；多隻敵人、菁英與頭目拖得越久累積的傷害越多。沒有出現這兩項、或 lethal_if_no_more_block 為 true 時，照原本的血量與致命判斷。

