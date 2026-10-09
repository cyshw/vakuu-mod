# 呼叫瓦庫

《殺戮尖塔 2》的本機遊戲助手：遊戲內模型選單、瓦庫風格文案、只給建議、接手遊玩與停止。透過 STS2MCP 本機 API 讀取及操作遊戲，不依賴 Computer Use。

目前基準：Windows、遊戲 v0.111.0、.NET SDK 9.0.318、Python 3.12、Claude Code 2.1.295。Claude 模型固定為 Sonnet／Opus／Haiku 5.5；各模型是否可用依帳號而定。完整戰鬥接手尚未驗證。

## 資料夾

- `work/STS2MCP`：修改後的 C# 模組原始碼與原作者授權。
- `outputs`：Python 控制器、模型設定、Bober 抓牌轉接器、資料及使用說明。
- `work/test_*.py`：控制器、模型與抓牌轉接器測試。
- `scripts`：建置、測試與安裝工具。
- `docs/DEPENDENCIES.md`：外部來源、固定版本與重建需求。

這裡是後續維護的原始碼倉庫。上層原本的 outputs 是已部署副本，既有遊戲設定仍使用它；只修改本倉庫不會立即改動正在執行的遊戲。之後使用安裝工具，會讓模組改用本倉庫的 Python 程式。

## 重建與安裝

需要合法 Steam 遊戲安裝、.NET 9 SDK、Python 3.12（含 Tk）與本機 Codex CLI。Claude 可使用已安裝的 Claude Code，或將官方原生執行檔放到 `outputs/claude/claude.exe`；程式與帳號登入資料不納入 Git。

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
