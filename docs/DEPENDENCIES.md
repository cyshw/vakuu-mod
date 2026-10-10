# 來源與版本

記錄日期：2026-10-10。

| 元件 | 基準與來源 |
|---|---|
| 遊戲 | Steam《殺戮尖塔 2》v0.111.0，build 24724944；遊戲 DLL 僅從使用者安裝位置引用 |
| STS2MCP | https://github.com/Gennadiyev/STS2MCP ，commit 55e064850a68f3b4cde7e5fd525bf9b2dec4e885；MIT 原作者通知保留在 work/STS2MCP/LICENSE |
| BoberInSpire | https://github.com/S0ul3r/BoberInSpire ，commit 67263f2a7328783bbce201a064a90565f6a1447e；本倉庫保留當時使用的 python_app 子集與 data 快照 |
| Spire Codex | https://spire-codex.com 公開 API `/api/runs/metrics/cards?bracket=a10&character=<角色>`；每天最多抓一次，快取於 `outputs/button-runtime/spire-codex/`（不提交）。依[使用條款](https://spire-codex.com/terms)使用文件化的公開 API，不爬網頁 |
| .NET | SDK 9.0.318，net9.0；不提交 SDK 或編譯結果 |
| Python | 3.12，標準庫；沒有額外 pip 依賴 |
| Claude Code | 官方原生 Windows CLI 2.1.295，訂閱登入；不提交執行檔或登入資料 |
| Codex | 本機已登入 CLI；預設模型依本機設定，指定模型由 spire_models.py 控制 |

卡牌獎勵與商店卡牌評分優先使用 Spire Codex 的 Codex Score（進階10以上社群對局的勝率）；從未成功取得資料時才退回 BoberInSpire 的離線快照。BoberInSpire 的取用版本未找到 LICENSE；目前用途為使用者本機維護，不宣稱取得公開再散布授權。其資料包含第三方卡牌與攻略快照，評分不代表勝率；Mobalytics 資料基準為 2026-03-19，不會自動同步遊戲更新。

客製修改包含完整牌組狀態、v0.111.0 的大廳欄位適配、Godot 遊戲內面板、模型選單、API 操作驗證、Bober 轉接與瓦庫原創文案。不是原始上游發行版本。

## 基準驗證

- C# Release 編譯成功，0 警告、0 錯誤。
- 13 項 Python 測試：只讀建議、局面改變阻止操作、停止、索引與動作限制、中文卡牌 ID、模型設定與 Claude 回應格式。
- 遊戲內面板、中文與模型選單已驗證；本機 API 正常。
- Codex 已實測涅奧建議；Claude Sonnet 5.5 已实測讀取主選單並回傳結構化建議。
- 尚未完整驗證自動接手一整場戰鬥或整局，Opus／Haiku 未逐一實際呼叫。
