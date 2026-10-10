# 呼叫瓦庫

《殺戮尖塔 2》的本機遊戲助手：遊戲內模型選單、瓦庫風格文案、只給建議、接手遊玩與停止。透過 STS2MCP 本機 API 讀取及操作遊戲，不依賴 Computer Use。

目前基準：Windows、遊戲 v0.111.0、.NET SDK 9.0.318、Python 3.12、Claude Code 2.1.295。Claude 模型固定為 Sonnet／Opus／Haiku 5.5；各模型是否可用依帳號而定。完整戰鬥接手尚未驗證。

## 資料夾

- `work/STS2MCP`：修改後的 C# 模組原始碼與原作者授權。
- `outputs`：Python 程式、資料及使用說明。
  - `spire-buttons.py`：接手主流程（讀局面、等待畫面穩定、執行動作）與遊戲內面板的 HTTP 背景程式。
  - `vakuu_rules.py`：各畫面允許的動作、檢查模型動作、比對局面、過渡畫面判斷。
  - `vakuu_facts.py`：替模型先算好的數字：戰鬥、能量、牌組摘要、路線。
  - `vakuu_plans.py`：不問模型的步驟：固定規則、出牌計畫、購物計畫。
  - `vakuu_router.py`：自動調度：哪些畫面交給強模型、何時換更強的模型。
  - `spire_models.py`、`spire_quota.py`、`spire_codex.py`、`sts2_advisor.py`、`sts2-control.py`：模型命令列、額度、Spire Codex 評分、抓牌評分、遊戲 API。
- `outputs/vakuu_prompt.md`：給模型的指示（角色語氣與各畫面規則）。每次呼叫都重新讀取，修改後不必重開遊戲；`{{END_TURN}}`、`{{LEAVE_SHOP}}` 由程式代入。
- `work/test_*.py`：控制器、模型與抓牌轉接器測試。
- `scripts`：建置、測試與安裝工具。
- `tools`：開發測試工具：`takeover_log.py` 用磁碟上的最新程式接手並記錄每一步（不必重開遊戲），`reload_room.py` 強制重啟遊戲並按「繼續」，重打目前房間（可用來比較不同設定，死亡後存檔會被刪除，無法重打）。
- `docs/DEPENDENCIES.md`：外部來源、固定版本與重建需求。

這裡是後續維護的原始碼倉庫，遊戲目前也使用這裡的 Python 程式；上層原本的 outputs 保留為舊部署副本。Python 修改在遊戲重開後載入，C# 修改需要重建與安裝，再重開遊戲。

## 重建與安裝

需要合法 Steam 遊戲安裝、.NET 9 SDK、Python 3.12與本機 Codex CLI。Claude 可使用已安裝的 Claude Code，或將官方原生執行檔放到 `outputs/claude/claude.exe`；程式與帳號登入資料不納入 Git。

在倉庫根目錄執行：

```powershell
./scripts/test.ps1 -Python '你的 python.exe 完整路徑'
./scripts/build.ps1 -GameDir '遊戲安裝資料夾' -Dotnet '你的 dotnet.exe 完整路徑'
# 正常退出遊戲後執行；遊戲位於 Program Files 時需要適當寫入權限。
./scripts/install.ps1 -GameDir '遊戲安裝資料夾' -Pythonw '你的 pythonw.exe 完整路徑'
```

安裝會寫入 STS2_MCP.dll、STS2_MCP.json、STS2_CodexPanel.conf，不啟動或關閉遊戲。設定檔只在建置目錄生成，記錄本機 Python 與控制器路徑。請保留本倉庫位置。遊戲重開後讀取新版本。

## 日常維護

修改前查看 `git status`，修改後執行相應檢查，完成再建立一筆 commit。遊戲更新造成相容性問題時，先確認 API 狀態與卡牌資料，再重建模組；停止接手，避免依舊版狀態操作。

`v0.1.0` 是目前可用程式的起點標籤，附相容性與驗證範圍。回看舊版本可使用 `git switch --detach v0.1.0`；回到主分支使用 `git switch main`。切換前先保存尚未提交的修改，再重新建置及安裝，Git 不會自動替换已安裝的 DLL。

目前只有本機 Git，沒有遠端備份。之後若要推到私人 GitHub 倉庫，可再設定 remote；公開發布前需確認第三方程式與資料的授權。

帳號、設定、呼叫紀錄、SDK、遊戲檔案與執行檔均不提交。Git 不備份遊戲存檔。請勿把 `.gitignore` 排除的登入或執行紀錄手動加進版本紀錄。
