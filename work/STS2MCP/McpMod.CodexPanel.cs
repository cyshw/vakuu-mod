using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Godot;

namespace STS2_MCP;

public static partial class McpMod
{
    private static CanvasLayer? _codexLayer;
    private static PanelContainer? _codexPanel;
    private static Label? _codexMessage;
    private static Button? _adviceButton;
    private static Button? _takeoverButton;
    private static OptionButton? _modelPicker;
    private static OptionButton? _effortPicker;
    private static Button? _claudeLoginButton;
    private static bool _authPolling;
    private static readonly List<string> _modelKeys = new();
    private static readonly string[] _effortKeys = { "", "low", "medium", "high" };
    private static bool _loadingModels;
    private static Godot.Timer? _codexTimer;
    private static Process? _codexBridge;
    private static bool _codexPolling;
    private static readonly System.Net.Http.HttpClient _codexHttp = new() { Timeout = TimeSpan.FromSeconds(8) };
    private static string _bridgeUrl = "http://127.0.0.1:15527";
    private static string? _codexPreviewPath;

    private static StyleBoxFlat SpireFrame(string background, string border, int width = 1)
    {
        var style = new StyleBoxFlat {
            BgColor = new Color(background), BorderColor = new Color(border),
            ContentMarginLeft = 14, ContentMarginRight = 14,
            ContentMarginTop = 10, ContentMarginBottom = 10,
            ShadowColor = new Color(0, 0, 0, 0.5f), ShadowSize = 5,
            ShadowOffset = new Vector2(0, 3)
        };
        style.SetBorderWidthAll(width); style.SetCornerRadiusAll(5);
        return style;
    }

    private static Theme CreateSpireTheme()
    {
        var theme = new Theme {
            DefaultFont = new SystemFont { FontNames = new[] { "Microsoft JhengHei", "Microsoft YaHei" } },
            DefaultFontSize = 18
        };
        theme.SetColor("font_color", "Label", new Color("e6d7b6"));
        theme.SetStylebox("panel", "PanelContainer", SpireFrame("211d18f5", "9f7847", 2));
        foreach (string type in new[] { "Button", "OptionButton" })
        {
            theme.SetStylebox("normal", type, SpireFrame("30271e", "8c6940"));
            theme.SetStylebox("hover", type, SpireFrame("4b3824", "e3ba71", 2));
            theme.SetStylebox("pressed", type, SpireFrame("1e1914", "d2a45d", 2));
            theme.SetStylebox("disabled", type, SpireFrame("25221e", "514638"));
            theme.SetStylebox("focus", type, SpireFrame("00000000", "e3ba71", 2));
            theme.SetColor("font_color", type, new Color("e6cf9c"));
            theme.SetColor("font_hover_color", type, new Color("ffe7aa"));
            theme.SetColor("font_pressed_color", type, new Color("f3d69b"));
            theme.SetColor("font_disabled_color", type, new Color("847969"));
        }
        theme.SetStylebox("panel", "PopupMenu", SpireFrame("211d18", "a88450", 2));
        theme.SetStylebox("hover", "PopupMenu", SpireFrame("493720", "a88450"));
        theme.SetColor("font_color", "PopupMenu", new Color("e6d7b6"));
        theme.SetColor("font_hover_color", "PopupMenu", new Color("ffe7aa"));
        theme.SetConstant("v_separation", "PopupMenu", 12);
        return theme;
    }

    private static void InitializeCodexPanel(SceneTree tree)
    {
        try
        {
            string modDir = Path.GetDirectoryName(typeof(McpMod).Assembly.Location)!;
            string configFile = Path.Combine(modDir, "STS2_CodexPanel.conf");
            if (!File.Exists(configFile)) { GD.Print("[STS2 Codex] No panel config; panel disabled."); return; }
            using var config = JsonDocument.Parse(File.ReadAllText(configFile));
            string python = config.RootElement.GetProperty("python").GetString()!;
            string script = config.RootElement.GetProperty("script").GetString()!;
            int port = config.RootElement.GetProperty("port").GetInt32();
            if (config.RootElement.TryGetProperty("preview_path", out var preview)) _codexPreviewPath = preview.GetString();
            _bridgeUrl = $"http://127.0.0.1:{port}";
            var start = new ProcessStartInfo(python) { UseShellExecute = false, CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(script)! };
            start.ArgumentList.Add(script); start.ArgumentList.Add("--bridge-server"); start.ArgumentList.Add(port.ToString());
            _codexBridge = Process.Start(start);
            tree.Root.TreeExiting += () => {
                try { if (_codexBridge != null && !_codexBridge.HasExited) _codexBridge.Kill(entireProcessTree: true); }
                catch { }
            };
            _mainThreadQueue.Enqueue(() => CreateCodexPanel(tree));
        }
        catch (Exception ex) { GD.PrintErr($"[STS2 Codex] Panel startup failed: {ex.Message}"); }
    }

    private static async Task StopCodexBridge()
    {
        try { await _codexHttp.PostAsync(_bridgeUrl + "/shutdown", new StringContent("{}",Encoding.UTF8,"application/json")); }
        catch { }
        try { if (_codexBridge != null && !_codexBridge.HasExited) _codexBridge.Kill(entireProcessTree: true); }
        catch { }
    }

    private static void CreateCodexPanel(SceneTree tree)
    {
        if (_codexLayer != null && GodotObject.IsInstanceValid(_codexLayer)) return;
        _codexLayer = new CanvasLayer { Name = "CodexGamePanel", Layer = 1000 };
        tree.Root.AddChild(_codexLayer);
        var root = new Control { Name = "CodexControls", MouseFilter = Control.MouseFilterEnum.Ignore };
        root.Theme = CreateSpireTheme();
        _codexLayer.AddChild(root);
        root.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.FullRect);
        var toggle = new Button { Text = "Codex", Name = "CodexToggle", FocusMode = Control.FocusModeEnum.None };
        root.AddChild(toggle);
        toggle.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.TopRight);
        toggle.Text = "呼叫瓦庫";
        toggle.OffsetLeft = -170; toggle.OffsetRight = -20; toggle.OffsetTop = 120; toggle.OffsetBottom = 168;
        toggle.Pressed += () => { if (_codexPanel != null) { _codexPanel.Visible = !_codexPanel.Visible; if (_codexPanel.Visible) _ = RefreshClaudeLogin(); } };
        _codexPanel = new PanelContainer { Name = "CodexPanel", Visible = true };
        root.AddChild(_codexPanel);
        _codexPanel.SetAnchorsAndOffsetsPreset(Control.LayoutPreset.TopRight);
        _codexPanel.OffsetLeft = -460; _codexPanel.OffsetRight = -20;
        _codexPanel.OffsetTop = 180; _codexPanel.OffsetBottom = 560;
        var margin = new MarginContainer();
        foreach (string side in new[] { "left", "right", "top", "bottom" }) margin.AddThemeConstantOverride("margin_" + side,6);
        _codexPanel.AddChild(margin);
        var box = new VBoxContainer(); box.AddThemeConstantOverride("separation",12); margin.AddChild(box);
        var title = new Label { Text = "◆  呼叫瓦庫", MouseFilter = Control.MouseFilterEnum.Ignore };
        title.AddThemeFontSizeOverride("font_size", 26);
        title.AddThemeColorOverride("font_color", new Color("f0ce85"));
        box.AddChild(title);
        var subtitle = new Label { Text = "第一個惡魔 · 一份力量的提案", MouseFilter = Control.MouseFilterEnum.Ignore };
        subtitle.AddThemeColorOverride("font_color", new Color("b4a080"));
        subtitle.AddThemeFontSizeOverride("font_size", 15); box.AddChild(subtitle);
        _modelPicker = new OptionButton { Disabled = true, CustomMinimumSize = new Vector2(390, 42) };
        _modelPicker.AddItem("讀取模型選單…"); box.AddChild(_modelPicker);
        var settingsRow = new HBoxContainer(); box.AddChild(settingsRow);
        settingsRow.AddChild(new Label { Text = "思考強度" });
        _effortPicker = new OptionButton { Disabled = true };
        foreach (var label in new[] { "預設", "低", "中", "高" }) _effortPicker.AddItem(label);
        settingsRow.AddChild(_effortPicker);
        _claudeLoginButton = new Button { Text = "登入 Claude", Visible = false, FocusMode = Control.FocusModeEnum.None };
        settingsRow.AddChild(_claudeLoginButton);
        _claudeLoginButton.Pressed += () => { _ = SendCodexCommand("login-claude"); };
        _modelPicker.ItemSelected += index => { if (!_loadingModels) _ = SaveModelSelection(); };
        _effortPicker.ItemSelected += index => { if (!_loadingModels) _ = SaveModelSelection(); };
        var buttons = new HBoxContainer(); buttons.AddThemeConstantOverride("separation", 8); box.AddChild(buttons);
        _adviceButton = new Button { Text = "只給建議", FocusMode = Control.FocusModeEnum.None };
        _takeoverButton = new Button { Text = "接手遊玩", FocusMode = Control.FocusModeEnum.None };
        var stop = new Button { Text = "停止", FocusMode = Control.FocusModeEnum.None };
        stop.AddThemeColorOverride("font_color", new Color("d9a499"));
        foreach (var button in new[] { _adviceButton, _takeoverButton, stop }) button.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
        buttons.AddChild(_adviceButton); buttons.AddChild(_takeoverButton); buttons.AddChild(stop);
        _adviceButton.Pressed += () => { _ = SendCodexCommand("advice"); };
        _takeoverButton.Pressed += () => { _ = SendCodexCommand("takeover"); };
        stop.Pressed += () => { _ = SendCodexCommand("stop"); };
        var messageFrame = new PanelContainer();
        messageFrame.AddThemeStyleboxOverride("panel", SpireFrame("151411", "554732"));
        box.AddChild(messageFrame);
        _codexMessage = new Label { Text = "又來尋求力量了？很好，我有一份提案。\n接手每次最多 30 步。",
            AutowrapMode = TextServer.AutowrapMode.WordSmart, CustomMinimumSize = new Vector2(360,110),
            MouseFilter = Control.MouseFilterEnum.Ignore };
        messageFrame.AddChild(_codexMessage);
        var footer = new Label { Text = "按下才呼叫 · 使用所選服務的額度", MouseFilter = Control.MouseFilterEnum.Ignore };
        footer.AddThemeFontSizeOverride("font_size", 14);
        footer.AddThemeColorOverride("font_color", new Color("a79577")); box.AddChild(footer);
        _codexTimer = new Godot.Timer { WaitTime = 0.5, OneShot = false };
        _codexLayer.AddChild(_codexTimer);
        _codexTimer.Timeout += () => { _ = PollCodexStatus(); };
        GD.Print("[STS2 Codex] In-game panel created.");
        _ = LoadModelSelection();
    }

    private static async Task LoadModelSelection()
    {
        for (int attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                string payload = await _codexHttp.GetStringAsync(_bridgeUrl + "/models");
                using var doc = JsonDocument.Parse(payload);
                var entries = new List<(string Key, string Label)>();
                foreach (var model in doc.RootElement.GetProperty("models").EnumerateArray())
                    entries.Add((model.GetProperty("key").GetString()!, model.GetProperty("label").GetString()!));
                var selection = doc.RootElement.GetProperty("selection");
                string key = selection.GetProperty("model_key").GetString()!;
                string effort = selection.GetProperty("effort").GetString()!;
                await RunOnMainThread(() => {
                    _loadingModels = true;
                    _modelPicker!.Clear(); _modelKeys.Clear();
                    foreach (var entry in entries) { _modelKeys.Add(entry.Key); _modelPicker.AddItem(entry.Label); }
                    _modelPicker.Select(Math.Max(0, _modelKeys.IndexOf(key)));
                    _effortPicker!.Select(Math.Max(0, Array.IndexOf(_effortKeys, effort)));
                    _modelPicker.Disabled = false; _effortPicker.Disabled = false;
                    _loadingModels = false;
                });
                await RefreshClaudeLogin();
                return;
            }
            catch { await Task.Delay(600); }
        }
        await RunOnMainThread(() => { _codexMessage!.Text = "無法讀取模型選單，請重開遊戲。"; });
    }

    private static async Task SaveModelSelection()
    {
        string payload = JsonSerializer.Serialize(new { model_key = _modelKeys[_modelPicker!.Selected], effort = _effortKeys[_effortPicker!.Selected] });
        _modelPicker.Disabled = true; _effortPicker.Disabled = true;
        _adviceButton!.Disabled = true; _takeoverButton!.Disabled = true;
        try
        {
            using var response = await _codexHttp.PostAsync(_bridgeUrl + "/settings", new StringContent(payload, Encoding.UTF8, "application/json"));
            response.EnsureSuccessStatusCode();
            await RunOnMainThread(() => { _codexMessage!.Text = "模型已儲存，下次呼叫使用此設定。"; });
        }
        catch (Exception ex)
        {
            await LoadModelSelection();
            await RunOnMainThread(() => { _codexMessage!.Text = "設定未儲存：" + ex.Message; });
        }
        finally { await RunOnMainThread(() => { _modelPicker!.Disabled = false; _effortPicker!.Disabled = false; _adviceButton!.Disabled = false; _takeoverButton!.Disabled = false; }); }
        await RefreshClaudeLogin();
    }

    private static async Task RefreshClaudeLogin()
    {
        string key = "";
        await RunOnMainThread(() => {
            if (_modelPicker != null && _modelPicker.Selected >= 0 && _modelPicker.Selected < _modelKeys.Count)
                key = _modelKeys[_modelPicker.Selected];
            if (!key.StartsWith("claude:")) _claudeLoginButton!.Visible = false;
        });
        if (!key.StartsWith("claude:") || _authPolling) return;
        _authPolling = true;
        try {
            using var doc = JsonDocument.Parse(await _codexHttp.GetStringAsync(_bridgeUrl + "/claude-auth"));
            bool needsLogin = doc.RootElement.GetProperty("logged_in").ValueKind == JsonValueKind.False;
            await RunOnMainThread(() => {
                if (_modelPicker!.Selected >= 0 && _modelKeys[_modelPicker.Selected] == key)
                    _claudeLoginButton!.Visible = needsLogin;
            });
        }
        catch { }
        finally { _authPolling = false; }
    }

    private static async Task SendCodexCommand(string command)
    {
        try
        {
            using var response = await _codexHttp.PostAsync(_bridgeUrl + "/" + command,
                new StringContent("{}",Encoding.UTF8,"application/json"));
            response.EnsureSuccessStatusCode();
            await RunOnMainThread(() => { _codexTimer?.Start(); });
            await PollCodexStatus();
        }
        catch (Exception ex) { await RunOnMainThread(() => { if (_codexMessage != null) _codexMessage.Text = "無法呼叫瓦庫：" + ex.Message; }); }
    }

    private static async Task PollCodexStatus()
    {
        if (_codexPolling) return;
        _codexPolling = true;
        try
        {
            string payload = await _codexHttp.GetStringAsync(_bridgeUrl + "/status");
            using var doc = JsonDocument.Parse(payload);
            bool busy = doc.RootElement.GetProperty("busy").GetBoolean();
            string message = doc.RootElement.GetProperty("message").GetString() ?? "";
            await RunOnMainThread(() => {
                if (_codexMessage != null) _codexMessage.Text = message;
                if (_adviceButton != null) _adviceButton.Disabled = busy;
                if (_takeoverButton != null) _takeoverButton.Disabled = busy;
                if (_modelPicker != null) _modelPicker.Disabled = busy;
                if (_effortPicker != null) _effortPicker.Disabled = busy;
                if (_claudeLoginButton != null) _claudeLoginButton.Disabled = busy;
                if (!busy) _codexTimer?.Stop();
            });
            if (!busy) await RefreshClaudeLogin();
        }
        catch (Exception ex) { await RunOnMainThread(() => { if (_codexMessage != null) _codexMessage.Text = "連線中斷：" + ex.Message; _codexTimer?.Stop(); }); }
        finally { _codexPolling = false; }
    }

    private static Dictionary<string, object?> GetCodexPanelStatus() => new() {
        ["created"] = _codexLayer != null && GodotObject.IsInstanceValid(_codexLayer),
        ["visible"] = _codexPanel?.Visible,
        ["message"] = _codexMessage?.Text,
        ["advice_enabled"] = !(_adviceButton?.Disabled ?? true),
        ["takeover_enabled"] = !(_takeoverButton?.Disabled ?? true),
        ["model_count"] = _modelKeys.Count,
        ["claude_login_visible"] = _claudeLoginButton?.Visible,
        ["model_key"] = _modelPicker != null && _modelKeys.Count > _modelPicker.Selected && _modelPicker.Selected >= 0 ? _modelKeys[_modelPicker.Selected] : null,
        ["panel_rect"] = _codexPanel?.GetGlobalRect().ToString()
    };

    private static object CaptureCodexPreview()
    {
        if (_codexPreviewPath == null) return new { status = "no_preview_path" };
        var image = ((SceneTree)Engine.GetMainLoop()).Root.GetTexture().GetImage();
        var result = image.SavePng(_codexPreviewPath);
        return new { status = result.ToString(), path = _codexPreviewPath };
    }
}
