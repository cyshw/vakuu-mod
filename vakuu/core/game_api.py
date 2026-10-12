"""Local STS2 API client. Default command reads state; no external AI API needed."""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None:
    sys.stderr.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:15526"


def request(path, body=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # The mod refuses the singleplayer endpoint in a co-op run (its end_turn there is not
        # synchronised with the other players); the multiplayer endpoint takes the same reads and
        # actions, with end_turn waiting for everyone.
        if error.code != 409 or not path.startswith("/api/v1/singleplayer"): raise
        return request(path.replace("/api/v1/singleplayer", "/api/v1/multiplayer", 1), body)


def compact(state):
    # Keep decision information. Large card-pile lists become counts.
    if isinstance(state, dict):
        result = {}
        for key, value in state.items():
            if key in {"deck", "draw_pile", "discard_pile", "exhaust_pile"} and isinstance(value, list):
                result[key + "_count"] = len(value)
            else:
                result[key] = compact(value)
        return result
    if isinstance(state, list):
        return [compact(value) for value in state]
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["health", "state", "advice", "act"], default="state", nargs="?")
    parser.add_argument("--full", action="store_true", help="Include complete deck and card piles")
    parser.add_argument("--multiplayer", action="store_true")
    parser.add_argument("--file", type=Path, help="Action JSON file, required for act")
    args = parser.parse_args()
    endpoint = "/api/v1/" + ("multiplayer" if args.multiplayer else "singleplayer")
    try:
        if args.command == "health":
            result = request("/")
        elif args.command == "act":
            if args.file is None:
                parser.error("act requires --file with an action JSON document")
            action = json.loads(args.file.read_text(encoding="utf-8-sig"))
            if not isinstance(action, dict) or not isinstance(action.get("action"), str):
                parser.error("action JSON must be an object with a string 'action' field")
            result = request(endpoint, action)
        elif args.command == "advice":
            from sts2_advisor import advise
            state = (json.loads(args.file.read_text(encoding="utf-8-sig")) if args.file
                     else request(endpoint + "?format=json"))
            result = advise(state)
        else:
            result = request(endpoint + "?format=json")
            if result.get("state_type") == "card_reward":
                from sts2_advisor import advise
                result["card_advice"] = advise(result)
            if not args.full:
                result = compact(result)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Local game API unavailable: {exc}. Launch the game with STS2 MCP enabled.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
