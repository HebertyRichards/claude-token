#!/usr/bin/env python3
"""claude-token benchmark: one task of yours, with the optimizer off and on.

Nothing is canned. You describe the task and point at your project:

  python benchmark/run.py \\
      --prompt "Os testes de pagamento estão falhando. Corrija sem alterar os testes." \\
      --workspace ~/projetos/minha-api --kind bugfix \\
      --check "pytest -q" --unchanged tests/test_pagamento.py \\
      --model haiku --reps 3

  python benchmark/run.py ... --dry-run     show the plan and your plan usage; run nothing
  python benchmark/run.py --resume [file]   continue an unfinished results file
  python benchmark/run.py --report [file]   print a results file's comparison again

Every run works on a fresh copy of the project, never on the original. It
spends real tokens: before every run it reads the plan's 5-hour and weekly
windows and stops at the ceilings (--max-5h 80, --max-week 85). Everything is
on disk by then, so --resume continues from where it stopped.
"""
import argparse
import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, ROOT]

import guard as guards
import rag
import runner
import scenario
import state as states
import summary
from common import MARKER, SETTINGS, fmt_k, load_json, now_iso

DEFAULTS = {"model": ["haiku"], "reps": 2, "level": 2, "effort": "",
            "max_5h": 80.0, "max_week": 85.0, "max_cost": 0.0,
            "run_budget": 3.0, "timeout": 900}
# What may still be changed when resuming: the plan itself is fixed by then.
ADJUSTABLE = ("max_5h", "max_week", "max_cost", "run_budget", "timeout")
STATUS_TEXT = {"ok": "ok", "check_failed": "FALHOU na verificação", "error": "ERRO",
               "limit": "LIMITE da conta", "invalid": "INVÁLIDA",
               "interrupted": "interrompida"}


def say(*parts, **kw):
    print(*parts, flush=True, **kw)


def parse(argv):
    p = argparse.ArgumentParser(
        prog="benchmark/run.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    task = p.add_argument_group("a tarefa (você descreve)")
    task.add_argument("--prompt", help="o pedido, como você digitaria no Claude Code")
    task.add_argument("--prompt-file", metavar="ARQUIVO",
                      help="lê o pedido de um arquivo (para textos longos)")
    task.add_argument("--workspace", metavar="PASTA",
                      help="projeto em que a tarefa roda (padrão: a pasta atual); "
                           "cada execução trabalha numa cópia nova")
    task.add_argument("--kind", help="rótulo do tipo de tarefa: bugfix, feature, "
                                     "analise... (padrão: tarefa)")
    task.add_argument("--name", help="nome da tarefa no relatório")
    task.add_argument("--setup", action="append", default=[], metavar="CMD",
                      help="comando rodado na cópia antes de cada execução "
                           "(instalar dependências, por exemplo); repetível")
    task.add_argument("--check", action="append", default=[], metavar="CMD",
                      help="comando rodado na cópia depois: saída 0 = tarefa "
                           "cumprida; {answer} vira o arquivo com a resposta "
                           "final; repetível")
    task.add_argument("--expect", action="append", default=[], metavar="REGEX",
                      help="a resposta final precisa casar com a expressão; repetível")
    task.add_argument("--unchanged", action="append", default=[], metavar="ARQUIVO",
                      help="arquivo que não pode ser alterado; repetível")
    task.add_argument("--allow", action="append", default=[], metavar="REGRA",
                      help='ferramenta extra liberada, na sintaxe do --allowedTools '
                           '(ex.: "Bash(npm *)"); repetível')

    how = p.add_argument_group("como rodar")
    how.add_argument("--model", action="append",
                     help="modelo ou alias aceito pelo `claude --model`; repita "
                          "para vários (padrão: haiku)")
    how.add_argument("--reps", type=int, help="execuções por braço (padrão: 2)")
    how.add_argument("--level", type=int, choices=(1, 2),
                     help="nível do claude-token no braço ON (padrão: 2)")
    how.add_argument("--effort", help="repassado ao `claude --effort`")
    how.add_argument("--timeout", type=int, help="segundos por execução (padrão: 900)")
    how.add_argument("--keep-workspaces", action="store_true",
                     help="não apaga as cópias de trabalho (para depurar)")
    how.add_argument("--claude", nargs="+", metavar="CMD",
                     help="comando da CLI (padrão: `claude` do PATH)")

    lim = p.add_argument_group("tetos")
    lim.add_argument("--max-5h", type=float,
                     help="teto da janela de 5 h do plano, em %% (padrão: 80)")
    lim.add_argument("--max-week", type=float,
                     help="teto da janela semanal do plano, em %% (padrão: 85)")
    lim.add_argument("--max-cost", type=float,
                     help="teto do custo total estimado, em US$ (padrão: sem teto)")
    lim.add_argument("--run-budget", type=float,
                     help="teto de uma execução, em US$, via --max-budget-usd "
                          "(padrão: 3; 0 desliga)")
    lim.add_argument("--no-plan-guard", action="store_true",
                     help="não ler o uso do plano (contas sem assinatura); "
                          "combine com --max-cost")

    out = p.add_argument_group("resultados")
    out.add_argument("--out", help="arquivo de resultados (padrão: benchmark/results/)")
    out.add_argument("--resume", nargs="?", const="", metavar="ARQUIVO",
                     help="continua um arquivo inacabado (sem valor: o mais recente)")
    out.add_argument("--retry-errors", action="store_true",
                     help="com --resume: refaz também as execuções que deram erro")
    out.add_argument("--report", nargs="?", const="", metavar="ARQUIVO",
                     help="imprime a comparação de um arquivo (sem valor: o mais recente)")
    out.add_argument("--dry-run", action="store_true",
                     help="mostra o plano e o uso do plano; não executa nada")
    return p.parse_args(argv)


def build_plan(sc, models, reps):
    """Every (model, repetition, arm), in execution order. The arm that goes
    first alternates: the first run of a pair pays the cache write for the
    prefix both share, so a fixed order would favour the second."""
    plan = []
    for model in models:
        for rep in range(1, reps + 1):
            for arm in (("off", "on") if rep % 2 else ("on", "off")):
                plan.append({"scenario": sc["id"], "kind": sc["kind"], "model": model,
                             "rep": rep, "arm": arm, "status": "pending",
                             "attempts": []})
    return plan


def new_state(args):
    cfg = {k: (getattr(args, k) if getattr(args, k) is not None else v)
           for k, v in DEFAULTS.items()}
    if cfg["reps"] < 1:
        raise ValueError("--reps precisa ser pelo menos 1")
    sc = scenario.describe(args)
    # The whole description goes into the results file: --resume needs no
    # flags, and the file says by itself what was measured.
    cfg.update(plan_guard=not args.no_plan_guard, scenario=sc,
               headless_note=scenario.HEADLESS_NOTE,
               denied_tools=scenario.denied_tools())
    env = {"platform": sys.platform, "python": platform.python_version(),
           "claude": "", "claude_token": _git_version()}
    return states.new(cfg, build_plan(sc, cfg["model"], cfg["reps"]), env)


def open_resume(args):
    path = os.path.abspath(args.resume) if args.resume else states.latest(unfinished=True)
    if not path:
        raise ValueError("nenhum resultado inacabado em benchmark/results")
    state = states.load(path)
    if state["status"] == "done" and not args.retry_errors:
        raise ValueError(f"{path} já está concluído; use --report para ver")
    workspace = state["config"]["scenario"]["workspace"]
    if not os.path.isdir(workspace):
        raise ValueError(f"o projeto deste benchmark não existe mais: {workspace}")
    recover(states.reopen(state, args.retry_errors), _archive_dir(path))
    for key in ADJUSTABLE:
        if getattr(args, key) is not None:
            state["config"][key] = getattr(args, key)
    if args.no_plan_guard:
        state["config"]["plan_guard"] = False
    return path, state


def recover(orphans, archive_to):
    """An attempt whose process died still spent tokens: count them from the
    transcript it left, file that away, and clear its working copy."""
    for att in orphans:
        path = runner.find_transcript(att.get("session_id", ""), wait=0)
        if path:
            with contextlib.suppress(OSError):
                att["usage"] = runner.measure(path)
                att["usage"]["transcript"] = runner.archive(path, archive_to)
        _drop_workspace(att.get("workspace") or "")


def _drop_workspace(ws):
    root = os.path.dirname(ws)
    if runner.WORKSPACE_PREFIX in os.path.basename(root):
        shutil.rmtree(root, ignore_errors=True)


def _archive_dir(path):
    return os.path.splitext(path)[0] + ".transcripts"


def _git_version():
    def git(*a):
        try:
            r = subprocess.run(("git", "-C", ROOT) + a, capture_output=True,
                               text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return r.stdout.strip() if r.returncode == 0 else ""
    head = git("rev-parse", "--short", "HEAD")
    return head + ("+alterado" if head and git("status", "--porcelain") else "")


def _claude_version(claude):
    try:
        r = subprocess.run(list(claude) + ["--version"], capture_output=True,
                           text=True, timeout=30)
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hooks_installed():
    hooks = (load_json(SETTINGS, {}) or {}).get("hooks") or {}
    return any(MARKER in json.dumps(h) for h in hooks.get("UserPromptSubmit") or [])


def spent(state):
    return sum((a.get("usage") or {}).get("cost") or 0
               for r in state["runs"] for a in r["attempts"])


def about(sc):
    """The task, as the header and --dry-run show it."""
    checks = ["--unchanged " + ", ".join(c["unchanged"]) if "unchanged" in c else
              f"--expect /{c['answer']}/" if "answer" in c else f"--check {c['run']}"
              for c in sc["check"]]
    held = sc.get("withheld") or []
    kept_out = "fora da cópia: histórico do git, dependências"
    if held:
        kept_out += (f" e {len(held)} arquivo(s) com cara de credencial ("
                     + ", ".join(held[:5]) + ("…" if len(held) > 5 else "") + ")")
    return [f"tarefa: {sc['id']} ({sc['kind']}) em {sc['workspace']} — "
            f"{sc['files']} arquivo(s), {fmt_k(sc['bytes'])}B por cópia",
            f"pedido: {' '.join(sc['prompt'].split())[:200]}",
            "verificação: " + ("; ".join(checks) if checks else summary.UNVERIFIED),
            kept_out,
            "proteções: git e gh bloqueados, leitura de credenciais negada, sem "
            "conectores MCP, escrita só dentro da cópia"]


def execute(run, attempt, sc, cfg, claude, archive_to, keep):
    """Run one attempt and fill it in. A run that fails is a result, not an
    exception: only Ctrl+C leaves this function by raising."""
    ws, sid, arm = attempt["workspace"], attempt["session_id"], run["arm"]
    index = transcript = ""
    usage = None
    try:
        baseline = scenario.prepare(sc, ws)
        if arm == "on" and cfg["level"] >= 2:
            # Level 2 points the model at line ranges, which needs an index of
            # this working copy. Built locally: no tokens.
            with contextlib.redirect_stdout(io.StringIO()):
                rag.cmd_index([ws])
            index = rag.index_path(ws)
        cmd = runner.command(claude, run["model"], sid, sc["allowed_tools"],
                             cfg["denied_tools"], cfg["run_budget"], cfg["effort"])
        code, out, err, secs, timed_out = runner.execute(
            cmd, scenario.full_prompt(sc), ws,
            runner.arm_env(arm, cfg["level"], os.path.dirname(ws)), cfg["timeout"])
        result = runner.parse_result(out)
        transcript = runner.find_transcript(sid)
        usage = runner.measure(transcript) if transcript else runner.usage_from_cli(result)
        status, error = runner.classify(code, timed_out, result, out, err)
        answer = str((result or {}).get("result") or "")
        checks = []
        if not status:
            checks = scenario.run_checks(sc, ws, answer, baseline)
            status = "ok" if all(c["ok"] for c in checks) else "check_failed"
            error = next((c["check"] + ": " + c["detail"] for c in checks if not c["ok"]), "")
            # The comparison is only worth anything if the arms really differ
            # by claude-token and nothing else, so each run proves its side.
            if usage and usage["source"] == "transcript":
                if arm == "off" and usage["markers"]:
                    status, error = "invalid", "o braço OFF recebeu texto do claude-token"
                elif arm == "on" and not usage["markers"]:
                    status, error = "invalid", ("o braço ON não recebeu nenhuma "
                                                "injeção do claude-token")
        attempt.update(status=status, error=error, seconds=round(secs, 1),
                       exit_code=code, usage=usage, checks=checks,
                       cli=runner.trim(result, answer))
    except Exception as e:
        # Whatever was measured before the failure still counts as spent.
        attempt.update(status="error", error=f"{type(e).__name__}: {e}"[:300],
                       usage=usage)
    finally:
        attempt["ended"] = now_iso()
        if transcript and attempt.get("usage"):
            with contextlib.suppress(OSError):
                attempt["usage"]["transcript"] = runner.archive(transcript, archive_to)
        if index:
            with contextlib.suppress(OSError):
                os.remove(index)
        if not keep:
            _drop_workspace(ws)


def _line(attempt):
    u = attempt.get("usage") or {}
    bits = [STATUS_TEXT.get(attempt["status"], attempt["status"])]
    if u:
        bits.append(f"{u['turns']} resp · {fmt_k(u['tokens'])} tok · ${u['cost']:.4f}")
    if attempt.get("seconds"):
        bits.append(f"{attempt['seconds']:.0f}s")
    if attempt.get("error"):
        bits.append(attempt["error"][:110])
    return " · ".join(bits)


def stop(path, state, reason, code):
    state["status"], state["stop_reason"] = "stopped", reason
    state["summary"] = summary.build(state)
    states.save(path, state)
    if state["summary"]["counts"]["settled"]:
        say(summary.render(state))
    say(f"\nPARADO: {reason}")
    say(f"tudo até aqui está em {path}")
    say(f"para continuar: python benchmark/run.py --resume \"{path}\"")
    return code


def loop(path, state, guard, claude, keep):
    cfg, total = state["config"], len(state["runs"])
    sc = cfg["scenario"]
    if not state["environment"].get("claude"):
        state["environment"]["claude"] = _claude_version(claude)
    owed = sum(r["status"] not in states.FINAL for r in state["runs"])
    say(f"benchmark {state['id']}: {owed} de {total} execuções a fazer • modelo(s) "
        f"{', '.join(cfg['model'])} • braço ON = nível {cfg['level']}")
    for text in about(sc):
        say(text)
    say(f"resultados (gravados a cada execução): {path}\n")
    errors, width = 0, len(str(total))
    for n, run in enumerate(state["runs"], 1):
        if run["status"] in states.FINAL:
            continue
        ok, reason = guard.check(spent(state))
        state["guard"] = guard.snapshot()
        if not ok:
            return stop(path, state, reason, 3)
        root = os.path.realpath(tempfile.mkdtemp(prefix=runner.WORKSPACE_PREFIX))
        attempt = {"session_id": str(uuid.uuid4()), "started": now_iso(),
                   "status": "running", "workspace": os.path.join(root, "w"),
                   "plan": {k: v["pct"] for k, v in (guard.reading or {}).items()}}
        run["attempts"].append(attempt)
        run["status"] = "running"
        states.save(path, state)    # on disk BEFORE the CLI starts spending
        say(f"[{n:>{width}}/{total}] {run['model']} · {run['arm']:<3} "
            f"#{run['rep']} ... ", end="")
        try:
            execute(run, attempt, sc, cfg, claude, _archive_dir(path), keep)
        except KeyboardInterrupt:
            attempt.update(status="interrupted", ended=now_iso(),
                           error="interrompida pelo usuário")
            recover([attempt], _archive_dir(path))
            run["status"] = "interrupted"
            say("interrompida")
            return stop(path, state, "interrompido (Ctrl+C)", 130)
        run["status"] = attempt["status"]
        state["summary"] = summary.build(state)
        states.save(path, state)
        say(_line(attempt))
        if attempt["status"] == "limit":
            return stop(path, state, "a conta atingiu o limite de uso — "
                        + attempt["error"][:160], 3)
        errors = errors + 1 if attempt["status"] == "error" else 0
        if errors >= 3:
            return stop(path, state, "3 execuções seguidas com erro: algo no "
                        "ambiente está quebrado", 2)
    state["status"] = "done"
    state["summary"] = summary.build(state)
    states.save(path, state)
    say(summary.render(state))
    say(f"\nresultados: {path}")
    return 0


def cmd_report(arg):
    path = os.path.abspath(arg) if arg else states.latest()
    if not path:
        raise ValueError("nenhum resultado em benchmark/results")
    state = states.load(path)
    say(summary.render(state))
    say(f"\nresultados: {path}")
    return 0


def cmd_dry_run(state, guard):
    cfg = state["config"]
    for text in about(cfg["scenario"]):
        say(text)
    say(f"plano: {len(cfg['model'])} modelo(s) × {cfg['reps']} repetição(ões) × "
        f"2 braços = {len(state['runs'])} execuções • modelo(s) "
        f"{', '.join(cfg['model'])} • braço ON = nível {cfg['level']}")
    if not cfg["plan_guard"]:
        say("uso do plano: não será lido (--no-plan-guard)")
        return 0
    now = guards.read_windows(guard.fetch(timeout=10))
    for key, label, _ in guards.WINDOWS:
        top = guard.ceilings.get(key)
        if key in now:
            say(f"janela de {label}: {now[key]['pct']:.0f}% agora, teto {top:.0f}% "
                f"(zera {guards.fmt_reset(now[key]['resets_at'])})")
        else:
            say(f"janela de {label}: não consegui ler — o benchmark não rodaria")
    return 0


def main(argv=None, fetch_usage=None):
    args = parse(argv)
    try:
        if args.report is not None:
            return cmd_report(args.report)
        if args.resume is not None:
            path, state = open_resume(args)
        else:
            state = new_state(args)
            path = states.path_for(state, args.out)
    except ValueError as e:     # ScenarioError included
        say(f"erro: {e}")
        return 1
    cfg = state["config"]
    guard = guards.Guard({"five_hour": cfg["max_5h"], "seven_day": cfg["max_week"]},
                         cfg["max_cost"], state.get("guard"), fetch_usage,
                         cfg["plan_guard"])
    if args.dry_run:
        return cmd_dry_run(state, guard)
    found = shutil.which("claude")
    claude = args.claude or ([found] if found else None)
    if not claude:
        say("erro: não achei o executável `claude` no PATH (use --claude)")
        return 1
    if not hooks_installed():
        say("erro: os hooks do claude-token não estão registrados, então o braço "
            "ON não teria o que medir. Rode `claude-token on` e tente de novo.")
        return 1
    return loop(path, state, guard, claude, args.keep_workspaces)


if __name__ == "__main__":
    # Windows consoles and pipes default to cp1252: the report has accents.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
