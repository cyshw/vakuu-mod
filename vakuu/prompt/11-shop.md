【商店】先補這副牌組的即時輸出、防禦或核心配合，不預設第一筆必須刪打擊。deck_direction.protect_starter_damage 為 true 時，刪打擊可能讓輸出更弱；先比較買有效傷害牌、刪真正負擔的牌或不買，不因此自動改刪防禦。只刪選牌畫面實際可選的牌，永恆牌不能當成刪牌目標。action 是第一筆購買，next_purchases 依序列出之後商品 index，離開在最後加 {{LEAVE_SHOP}}；總金幣必須足夠，刪牌後先處理選牌。shop_card_advice 的 score 只供參考，deck_fit 才列出目前配合與缺口；沒值得買的商品就 proceed。非商店畫面 next_purchases 為空陣列。

