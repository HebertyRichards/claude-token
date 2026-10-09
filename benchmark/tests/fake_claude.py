"""Stand-in for the `claude` CLI, for the benchmark's own tests: no network,
no tokens. It leaves the transcript a real run would leave and prints a
result object. Knobs come from the JSON file named by FAKE_CLAUDE:

  limit_at    call number that fails the way an exhausted account does
  usage_step  points added to the fake 5-hour window on every call
  leak        write claude-token's tag even with the level at 0
"""
import json
import os
import re
import sys
from datetime import datetime, timezone


def main(argv):
    if "--version" in argv:
        print("0.0.0 (fake)")
        return 0
    path = os.environ["FAKE_CLAUDE"]
    with open(path, encoding="utf-8") as f:
        knobs = json.load(f)
    knobs["calls"] = knobs.get("calls", 0) + 1
    knobs["five_hour"] = knobs.get("five_hour", 0) + knobs.get("usage_step", 0)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(knobs, f)

    sid = argv[argv.index("--session-id") + 1]
    prompt = sys.stdin.read()
    # What this run was handed, for the tests that check the fences.
    with open(path + ".calls", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "argv": argv, "prompt": prompt, "files": sorted(os.listdir(".")),
            "git_dir_is_file": os.path.isfile(os.environ.get("GIT_DIR", "")),
            "gh_token": os.environ.get("GH_TOKEN"),
        }) + "\n")
    if knobs.get("limit_at") == knobs["calls"]:
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                          "session_id": sid,
                          "result": "Claude AI usage limit reached|1760000000"}))
        return 1

    on = int(os.environ.get("CLAUDE_TOKEN_LEVEL", "2")) > 0
    usage = {"input_tokens": 10,
             "output_tokens": (600 if on else 900) + knobs["calls"] * 3,
             "cache_read_input_tokens": 30_000 if on else 40_000,
             "cache_creation_input_tokens": 5_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                "ephemeral_1h_input_tokens": 5_000}}
    lines = []
    if on or knobs.get("leak"):
        lines.append({"type": "attachment", "sessionId": sid, "attachment": {
            "type": "hook_success", "hookEvent": "UserPromptSubmit",
            "stdout": "[claude-token] Lean-output rules for this session"}})
    lines.append({"type": "assistant", "sessionId": sid,
                  "timestamp": datetime.now(timezone.utc).isoformat(),
                  "message": {"id": "msg_" + sid, "role": "assistant",
                              "model": "claude-haiku-4-5-20251001",
                              "content": [{"type": "text", "text": "RESPOSTA: 42"}],
                              "usage": usage}})
    folder = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                          re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, sid + ".jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(l) for l in lines) + "\n")

    if os.path.exists("alvo.txt"):      # the "task": the checks look for this
        with open("alvo.txt", "w", encoding="utf-8") as f:
            f.write("feito\n")
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "session_id": sid, "result": "RESPOSTA: 42", "num_turns": 1,
                      "duration_ms": 5, "total_cost_usd": 0.01, "usage": usage}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
