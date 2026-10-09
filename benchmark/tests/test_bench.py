"""Offline tests of the benchmark harness. A fake CLI stands in for `claude`
and the home directory is a temp folder, so nothing here spends tokens or
touches the real ~/.claude.

    python -m unittest discover -s benchmark/tests
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="ctbench-tests-")
os.environ["HOME"] = os.environ["USERPROFILE"] = TMP    # before common is imported
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))]

import run
import runner
from common import PROJECTS, SETTINGS, load_json, save_json

FAKE = [sys.executable, os.path.join(HERE, "fake_claude.py")]
HOOKS = {"hooks": {"UserPromptSubmit": [
    {"hooks": [{"type": "command", "command": "claude-token hook"}]}]}}
RESETS = {"five_hour": "2030-01-01T05:00:00+00:00", "seven_day": "2030-01-05T00:00:00+00:00"}
PROMPT = "Faça a tarefa de teste agora mesmo"


def setUpModule():
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    save_json(SETTINGS, HOOKS)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def plan_usage(five_hour=10.0, seven_day=5.0):
    return {"five_hour": {"utilization": five_hour, "resets_at": RESETS["five_hour"]},
            "seven_day": {"utilization": seven_day, "resets_at": RESETS["seven_day"]}}


class Bench(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)
        # The user's "project": the fake CLI does the task by writing alvo.txt.
        self.ws = os.path.join(self.dir, "projeto")
        os.makedirs(self.ws)
        self.write(os.path.join(self.ws, "alvo.txt"), "pendente\n")
        self.write(os.path.join(self.ws, "fixo.txt"), "não mexer\n")
        self.checker = os.path.join(self.dir, "feito.py")
        self.write(self.checker, "import sys\nsys.exit('feito' not in "
                                 "open('alvo.txt', encoding='utf-8').read())\n")
        self.knobs = os.path.join(self.dir, "knobs.json")
        self.set_knobs()
        os.environ["FAKE_CLAUDE"] = self.knobs
        self.out = os.path.join(self.dir, "res.json")

    @staticmethod
    def write(path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def set_knobs(self, **knobs):
        save_json(self.knobs, knobs)

    def bench(self, *args, fetch=None, describe=True, checks=True):
        """(exit code, printed text) of one benchmark invocation. Unless
        resuming, it describes the default task: 4 repetitions, 8 runs."""
        argv = [*args, "--claude", *FAKE]
        if "--resume" not in args:
            task = ["--out", self.out, "--reps", "4"]
            if describe:
                task += ["--prompt", PROMPT, "--workspace", self.ws, "--kind", "teste"]
            if checks:
                task += ["--expect", "42", "--unchanged", "fixo.txt",
                         "--check", f'"{sys.executable}" "{self.checker}"']
            argv = task + argv
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = run.main(argv, fetch_usage=fetch or (lambda timeout=3: plan_usage()))
        return code, printed.getvalue()

    def state(self):
        return load_json(self.out, None)

    def statuses(self):
        return [r["status"] for r in self.state()["runs"]]

    def test_full_run_compares_the_arms(self):
        code, printed = self.bench()
        st = self.state()
        self.assertEqual(code, 0, printed)
        self.assertEqual(st["status"], "done")
        self.assertEqual(self.statuses(), ["ok"] * 8)
        # The arm that goes first alternates from one pair to the next.
        self.assertEqual([r["arm"] for r in st["runs"][:4]], ["off", "on", "on", "off"])
        cell = st["summary"]["cells"][0]
        self.assertEqual(cell["verdict"], "economizou")
        self.assertLess(cell["delta"]["cost_pct"], 0)
        self.assertGreater(cell["off"]["cost"]["median"], cell["on"]["cost"]["median"])
        self.assertIn("ECONOMIZOU", printed)
        # The description travels in the results file.
        self.assertEqual(st["config"]["scenario"]["prompt"], PROMPT)
        self.assertEqual(st["config"]["scenario"]["kind"], "teste")
        # Transcripts are filed with the results, and the per-copy folders the
        # CLI created under ~/.claude/projects are gone.
        for r in st["runs"]:
            self.assertTrue(os.path.exists(r["attempts"][-1]["usage"]["transcript"]))
        self.assertEqual(os.listdir(PROJECTS), [])

    def test_the_project_itself_is_never_modified(self):
        self.assertEqual(self.bench("--reps", "1")[0], 0)
        with open(os.path.join(self.ws, "alvo.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "pendente\n")
        self.assertEqual(sorted(os.listdir(self.ws)), ["alvo.txt", "fixo.txt"])

    def test_a_run_gets_no_repository_no_secrets_and_no_connectors(self):
        for name in (".env", ".env.local", "secrets.json", "id_rsa", "chave.pem"):
            self.write(os.path.join(self.ws, name), "SEGREDO=1\n")
        os.makedirs(os.path.join(self.ws, ".git"))
        self.write(os.path.join(self.ws, ".git", "config"), "[core]\n")
        self.write(os.path.join(self.ws, "password_reset.py"), "# código, não segredo\n")

        code, printed = self.bench("--reps", "1", "--allow", "Bash(echo *)")
        self.assertEqual(code, 0, printed)
        with open(self.knobs + ".calls", encoding="utf-8") as f:
            calls = [json.loads(line) for line in f]
        self.assertEqual(len(calls), 2)
        for call in calls:
            argv = call["argv"]
            # The copy: the code, without the history or anything secret-shaped.
            self.assertEqual(call["files"], ["alvo.txt", "fixo.txt", "password_reset.py"])
            self.assertIn("--strict-mcp-config", argv)
            denied = argv[argv.index("--disallowedTools") + 1:argv.index("--allowedTools")]
            self.assertIn("Bash(git *)", denied)
            self.assertIn("Bash(gh *)", denied)
            self.assertIn("Read(**/.env*)", denied)
            self.assertIn("Edit(~/.ssh/**)", denied)
            # No blanket file rules: edits are accepted inside the copy only.
            self.assertEqual(argv[argv.index("--allowedTools") + 1:], ["Bash(echo *)"])
            self.assertTrue(call["git_dir_is_file"])
            self.assertEqual(call["gh_token"], "benchmark-sem-acesso")
            self.assertIn("Não use git", call["prompt"])
        self.assertIn("5 arquivo(s) com cara de credencial", printed)
        self.assertIn("secrets.json", printed)

    def test_git_cannot_be_allowed_back_in(self):
        code, printed = self.bench("--allow", "Bash(git commit *)")
        self.assertEqual(code, 1)
        self.assertIn("não libera git", printed)
        self.assertIsNone(self.state())

    @unittest.skipUnless(shutil.which("git"), "git não está instalado")
    def test_git_is_inert_in_a_run_environment(self):
        env = runner.arm_env("on", 2, self.dir)
        repo = os.path.dirname(os.path.dirname(HERE))

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.dir, env=env,
                                  capture_output=True).returncode

        self.assertNotEqual(git("init"), 0)                 # no new repository
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".git")))
        self.assertNotEqual(git("-C", repo, "status"), 0)   # none reachable elsewhere
        self.assertNotEqual(git("-C", repo, "commit", "--allow-empty", "-m", "x"), 0)
        self.assertNotEqual(git("clone", repo, "copia"), 0)  # no transport
        self.assertFalse(os.path.exists(os.path.join(self.dir, "copia")))

    def test_account_limit_stops_and_resume_finishes(self):
        self.set_knobs(limit_at=3)
        code, printed = self.bench()
        self.assertEqual(code, 3, printed)
        self.assertEqual(self.state()["status"], "stopped")
        self.assertEqual(self.statuses(), ["ok", "ok", "limit"] + ["pending"] * 5)
        self.assertIn("--resume", printed)

        # Resuming takes no description: it is all in the file.
        code, printed = self.bench("--resume", self.out)
        st = self.state()
        self.assertEqual(code, 0, printed)
        self.assertEqual(self.statuses(), ["ok"] * 8)
        self.assertEqual([len(r["attempts"]) for r in st["runs"]], [1, 1, 2, 1, 1, 1, 1, 1])
        self.assertEqual(st["summary"]["spent"]["attempts"], 9)

    def test_plan_ceiling_is_never_crossed(self):
        self.set_knobs(usage_step=4)
        seen = []

        def fetch(timeout=3):
            seen.append(70.0 + load_json(self.knobs, {}).get("five_hour", 0))
            return plan_usage(five_hour=seen[-1])

        code, printed = self.bench(fetch=fetch)
        self.assertEqual(code, 3, printed)
        # 70 -> 74 -> 78: a third run of 4 points would land on 82.
        self.assertEqual(seen, [70.0, 74.0, 78.0])
        self.assertEqual(self.statuses().count("ok"), 2)
        self.assertIn("5 h", self.state()["stop_reason"])

    def test_unreadable_plan_usage_runs_nothing(self):
        code, printed = self.bench(fetch=lambda timeout=3: None)
        self.assertEqual(code, 3, printed)
        self.assertEqual(self.statuses(), ["pending"] * 8)

    def test_cost_budget_stops_the_benchmark(self):
        code, printed = self.bench("--max-cost", "0.000001")
        self.assertEqual(code, 3, printed)
        self.assertEqual(self.statuses().count("ok"), 1)

    def test_run_killed_mid_flight_is_redone(self):
        self.assertEqual(self.bench("--reps", "1")[0], 0)
        st = self.state()
        st["status"] = "stopped"
        st["runs"][1]["status"] = st["runs"][1]["attempts"][0]["status"] = "running"
        save_json(self.out, st)

        code, printed = self.bench("--resume", self.out)
        run1 = self.state()["runs"][1]
        self.assertEqual(code, 0, printed)
        self.assertEqual([a["status"] for a in run1["attempts"]], ["interrupted", "ok"])
        self.assertEqual(run1["status"], "ok")

    def test_arm_that_received_injection_while_off_is_invalid(self):
        self.set_knobs(leak=True)
        code, printed = self.bench("--reps", "1")
        st = self.state()
        self.assertEqual(code, 0, printed)
        self.assertEqual({r["arm"]: r["status"] for r in st["runs"]},
                         {"off": "invalid", "on": "ok"})
        self.assertEqual(st["summary"]["cells"][0]["verdict"], "sem dados")

    def test_failed_check_is_not_a_run_that_worked(self):
        code, printed = self.bench("--reps", "1", "--expect", "resposta que o fake nunca dá")
        self.assertEqual(code, 0, printed)
        self.assertEqual(self.statuses(), ["check_failed"] * 2)
        self.assertEqual(self.state()["summary"]["cells"][0]["off"]["ok"], 0)

    def test_task_without_checks_is_reported_as_unverified(self):
        code, printed = self.bench("--reps", "1", checks=False)
        self.assertEqual(code, 0, printed)
        self.assertEqual(self.statuses(), ["ok"] * 2)
        self.assertIn("não sabe se a tarefa foi cumprida", printed)

    def test_the_task_has_to_be_described(self):
        code, printed = self.bench(describe=False, checks=False)
        self.assertEqual(code, 1)
        self.assertIn("--prompt", printed)
        self.assertIsNone(self.state())

    def test_prompt_the_hook_would_skip_is_rejected(self):
        code, printed = self.bench("--prompt", "palavra " * 250)
        self.assertEqual(code, 1)
        self.assertIn("palavras", printed)
        self.assertIsNone(self.state())

    def test_refuses_to_run_without_the_hooks(self):
        save_json(SETTINGS, {})
        try:
            code, printed = self.bench()
        finally:
            save_json(SETTINGS, HOOKS)
        self.assertEqual(code, 1)
        self.assertIn("claude-token on", printed)


if __name__ == "__main__":
    unittest.main()
