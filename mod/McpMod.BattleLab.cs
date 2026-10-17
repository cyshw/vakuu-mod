using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;

namespace STS2_MCP;

public static partial class McpMod
{
    private sealed class LabCard
    {
        public string Id { get; set; } = "";
        public int Upgrades { get; set; }
    }
    private sealed class LabSpec
    {
        public string Character { get; set; } = "IRONCLAD";
        public string Encounter { get; set; } = "";
        public int Ascension { get; set; }
        public int Act { get; set; } = 1;
        public int Floor { get; set; } = 1;
        public int ActFloor { get; set; } = 1;
        public int Hp { get; set; } = 80;
        public int MaxHp { get; set; } = 80;
        public int Gold { get; set; }
        public List<LabCard> Deck { get; set; } = new();
        public List<string> Relics { get; set; } = new();
        public List<string> Potions { get; set; } = new();
    }
    // Initial supported set excludes relic pickups that require a player choice.
    private static readonly HashSet<string> LabRelics = new(StringComparer.Ordinal)
    {
        "BURNING_BLOOD", "CRACKED_CORE", "RING_OF_THE_SNAKE", "REGAL_PILLOW",
        "TUNING_FORK", "RUNIC_PYRAMID", "BLACK_STAR", "HORN_CLEAT", "MERCURY_HOURGLASS",
        "ARCANE_SCROLL", "MUMMIFIED_HAND", "GORGET", "RADIANT_PEARL", "UNCEASING_TOP",
        "BAG_OF_MARBLES", "VAJRA", "DIAMOND_DIADEM", "FRAGRANT_MUSHROOM", "ANCHOR",
        "CENTENNIAL_PUZZLE", "WAR_PAINT", "STONE_CRACKER",
        // Combat relics without a pickup choice; counters (Pen Nib, Happy Flower) start at 0 in the lab.
        "BAG_OF_PREPARATION", "HAPPY_FLOWER", "STRIKE_DUMMY", "PENDULUM", "RED_MASK",
        "ODDLY_SMOOTH_STONE", "LANTERN", "BLOOD_VIAL", "BRONZE_SCALES", "FESTIVE_POPPER",
        "VAMBRACE", "ORICHALCUM", "CANDELABRA", "PANTOGRAPH", "LETTER_OPENER",
        "RIPPLE_BASIN", "PAELS_BLOOD", "SPARKLING_ROUGE", "ORNAMENTAL_FAN", "DATA_DISK",
        "REPTILE_TRINKET", "AKABEKO", "PERMAFROST", "RED_SKULL", "PEN_NIB",
        "GREMLIN_HORN", "POCKETWATCH", "NUNCHAKU", "MINIATURE_CANNON", "JOSS_PAPER",
        "CLOAK_CLASP", "CHANDELIER", "PARRYING_SHIELD", "BOOMING_CONCH"
    };
    private static readonly List<JsonElement> _labEvents = new();
    private static readonly HashSet<GameAction> _labSeen = new();
    private static LabSpec? _labSpec;
    private static RunState? _labRun;
    private static string? _labAttempt;
    private static bool _labBusy, _labRecording, _labSawCombat;
    private static int _labReadyRound = -1;
    private static string? _labError;

    private static object GetBattleLabStatus(int after = -1) => new
    {
        attempt = _labAttempt, busy = _labBusy, recording = _labRecording,
        error = _labError, events = _labEvents.Skip(Math.Max(0, after + 1)).ToArray(),
        supported_relics = LabRelics.OrderBy(x => x).ToArray()
    };

    private static async Task<object> ExecuteBattleLab(string body)
    {
        if (_labBusy) return Error("Lab setup already in progress");
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        var action = root.GetProperty("action").GetString();
        if (action == "stop")
        {
            if (_labRecording) LabRecord("stopped", null);
            _labRecording = false;
            return GetBattleLabStatus();
        }
        if (action != "start" && action != "retry") return Error("Use start, retry or stop");
        if (IsMultiplayerRun()) return Error("Battle lab is singleplayer only");
        var manager = RunManager.Instance;
        if (manager.IsInProgress && (manager.ShouldSave || manager.DebugOnlyGetState() != _labRun))
            return Error("Return to the main menu first; cannot replace an existing run");
        LabSpec? spec = action == "retry" ? _labSpec :
            JsonSerializer.Deserialize<LabSpec>(root.GetProperty("spec").GetRawText(), _jsonOptions);
        if (spec == null) return Error("No lab configuration to retry");
        // Validate all IDs and resources before leaving an existing lab attempt.
        var character = ModelDb.GetById<CharacterModel>(new ModelId(ModelId.SlugifyCategory<CharacterModel>(), spec.Character));
        var encounter = ModelDb.GetById<EncounterModel>(new ModelId(ModelId.SlugifyCategory<EncounterModel>(), spec.Encounter));
        if (spec.Ascension < 0 || spec.Ascension > 10 || spec.Act < 1 || spec.Act > 3
            || spec.Floor < 1 || spec.Floor > 100 || spec.ActFloor < 1 || spec.ActFloor > spec.Floor
            || spec.MaxHp < 1 || spec.MaxHp > 999 || spec.Hp < 1 || spec.Hp > spec.MaxHp
            || spec.Gold < 0 || spec.Gold > 9999 || spec.Deck.Count < 1 || spec.Deck.Count > 100
            || spec.Potions.Count > 3 || spec.Relics.Distinct().Count() != spec.Relics.Count)
            return Error("Invalid lab resources or floor/ascension bounds");
        foreach (var card in spec.Deck)
        {
            var model = ModelDb.AllCards.Single(c => c.Id.Entry == card.Id);
            if (card.Upgrades < 0 || card.Upgrades > model.MaxUpgradeLevel)
                return Error($"Invalid upgrade level: {card.Id}");
        }
        foreach (var id in spec.Relics)
        {
            if (!LabRelics.Contains(id)) return Error($"Relic not supported in lab yet: {id}");
            _ = ModelDb.AllRelics.Single(r => r.Id.Entry == id);
        }
        foreach (var id in spec.Potions) _ = ModelDb.AllPotions.Single(p => p.Id.Entry == id);
        _labBusy = true;
        try
        {
            if (_labRecording) LabRecord("stopped_for_retry", null);
            _labRecording = false;
            var game = NGame.Instance ?? throw new InvalidOperationException("Game scene unavailable");
            if (manager.IsInProgress) await game.ReturnToMainMenu();
            string seed = Guid.NewGuid().ToString("N");
            var run = await game.StartNewSingleplayerRun(character, false,
                ActModel.GetDefaultList(), Array.Empty<ModifierModel>(), seed, GameMode.Standard, spec.Ascension);
            _labRun = run; // Ownership guard remains valid even if setup later fails.
            if (spec.Act > 1) await manager.EnterAct(spec.Act - 1, false);
            var player = LocalContext.GetMe(run)!;
            foreach (var relic in player.Relics.ToArray()) await RelicCmd.Remove(relic);
            foreach (var id in spec.Relics)
                await RelicCmd.Obtain(ModelDb.AllRelics.Single(r => r.Id.Entry == id).ToMutable(), player);
            foreach (var old in player.Deck.Cards.ToArray()) await CardPileCmd.RemoveFromDeck(old);
            foreach (var entry in spec.Deck)
            {
                var card = run.CreateCard(ModelDb.AllCards.Single(c => c.Id.Entry == entry.Id), player);
                for (int i = 0; i < entry.Upgrades; i++) CardCmd.Upgrade(card);
                await CardPileCmd.Add(card, PileType.Deck);
            }
            foreach (var potion in player.Potions.ToArray()) await PotionCmd.Discard(potion);
            foreach (var id in spec.Potions)
                await PotionCmd.TryToProcure(ModelDb.AllPotions.Single(p => p.Id.Entry == id).ToMutable(), player);
            player.Creature.SetMaxHpInternal(spec.MaxHp);
            player.Creature.SetCurrentHpInternal(spec.Hp);
            player.Gold = spec.Gold;
            // Debug additions do not play the acquisition animation that updates the top bar.
            player.Deck.InvokeCardAddFinished();
            foreach (var history in run.MapPointHistory)
                ((System.Collections.IList)history).Clear();
            while (run.TotalFloor < spec.Floor - 1)
                run.AppendToMapPointHistory(MapPointType.Unassigned, RoomType.Monster, null);
            run.ActFloor = spec.ActFloor;
            _labSpec = spec;
            _labAttempt = Guid.NewGuid().ToString("N");
            _labEvents.Clear(); _labSeen.Clear(); _labError = null; _labSawCombat = false; _labReadyRound = -1;
            _labRecording = true;
            LabRecord("configuration", new { spec, seed, random_retry = true });
            var mutable = encounter.ToMutable();
            mutable.DebugRandomizeRng();
            await manager.EnterRoomDebug(RoomType.Monster, MapPointType.Unassigned, mutable);
            _labSawCombat = CombatManager.Instance.IsInProgress;
            LabRecord("ready", null);
            _labBusy = false;
            return GetBattleLabStatus();
        }
        catch (Exception ex)
        {
            _labError = ex.Message; _labRecording = false;
            return Error($"Lab setup failed: {ex.Message}. Return to menu or retry.");
        }
        finally { _labBusy = false; }
    }

    private static void LabRecord(string kind, object? action)
    {
        if (_labEvents.Count >= 1500)
        {
            _labError = "Event buffer full; recording stopped. Export before retry.";
            _labRecording = false;
            return;
        }
        _labEvents.Add(JsonSerializer.SerializeToElement(new
        {
            index = _labEvents.Count, attempt = _labAttempt,
            timestamp = DateTimeOffset.UtcNow, kind, action, state = BuildGameState()
        }, _jsonOptions));
    }

    private static void ObserveBattleLab()
    {
        if (!_labRecording || _labBusy) return;
        if (CombatManager.Instance.IsInProgress)
        {
            _labSawCombat = true;
            var combat = CombatManager.Instance.DebugOnlyGetState();
            var player = LocalContext.GetMe(_labRun!);
            if (combat != null && IsPlayPhase(combat) && combat.RoundNumber != _labReadyRound
                && player?.PlayerCombatState?.Hand.Cards.Count > 0)
            {
                _labReadyRound = combat.RoundNumber;
                LabRecord("turn_ready", null);
            }
        }
        else if (_labSawCombat)
        {
            LabRecord("combat_end", null);
            _labRecording = false;
        }
    }

    private static void RecordLabAction(GameAction action)
    {
        if (!_labRecording || _labBusy || !_labSeen.Add(action)) return;
        var run = RunManager.Instance.DebugOnlyGetState();
        if (run != _labRun || action.OwnerId != LocalContext.GetMe(run!)?.NetId) return;
        object? descriptor = action switch
        {
            PlayCardAction play => new { action = "play_card", card_id = play.CardModelId.Entry,
                card_index = play.Player.PlayerCombatState!.Hand.Cards.ToList().IndexOf(play.NetCombatCard.ToCardModel()),
                target_combat_id = play.TargetId },
            UsePotionAction potion => new { action = "use_potion", slot = potion.PotionIndex, target_combat_id = potion.TargetId },
            EndPlayerTurnAction => new { action = "end_turn" },
            _ => null
        };
        if (descriptor == null) return;
        int beforeIndex = _labEvents.Count;
        LabRecord("before_action", descriptor);
        string? attempt = _labAttempt;
        action.AfterFinished += finished =>
        {
            if (attempt == _labAttempt && _labRecording)
                LabRecord("after_action", new { action = descriptor, before_index = beforeIndex,
                    status = finished.Exception == null ? finished.State.ToString() : "failed" });
        };
        action.BeforePausedForPlayerChoice += _ =>
        {
            if (attempt == _labAttempt && _labRecording) LabRecord("choice_pause", descriptor);
        };
    }

    [HarmonyPatch(typeof(GameAction), nameof(GameAction.Execute))]
    private static class LabActionRecorder
    {
        private static void Prefix(GameAction __instance)
        {
            try { RecordLabAction(__instance); }
            catch (Exception ex)
            {
                _labError = $"Recorder failed: {ex.Message}";
                _labRecording = false;
            }
        }
    }

    [HarmonyPatch(typeof(PlayerChoiceSynchronizer), nameof(PlayerChoiceSynchronizer.SyncLocalChoice))]
    private static class LabChoiceRecorder
    {
        private static void Postfix(Player player, uint choiceId, PlayerChoiceResult result)
        {
            if (!_labRecording || _labBusy || RunManager.Instance.DebugOnlyGetState() != _labRun
                || player != LocalContext.GetMe(_labRun!)) return;
            try
            {
                object? selected = result.ChoiceType.ToString() switch
                {
                    "Index" => result.AsIndexes(),
                    "Player" => result.AsPlayerId(),
                    _ => result.AsCards(result.ChoiceType).Select(c => new
                    {
                        id = c.Id.Entry, is_upgraded = c.IsUpgraded,
                        hand_index = player.PlayerCombatState?.Hand.Cards.ToList().IndexOf(c)
                    }).ToArray()
                };
                LabRecord("player_choice", new { choice_id = choiceId,
                    choice_type = result.ChoiceType.ToString(), selected });
            }
            catch (Exception ex)
            {
                _labError = $"Choice recorder failed: {ex.Message}";
                _labRecording = false;
            }
        }
    }
}
