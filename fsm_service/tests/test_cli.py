"""Консольная программа: сценарий из файла, вывод, журнал JSONL."""

import json
import subprocess
import sys

from fsm_service.cli import main, to_message

from conftest import SPEC, V3


def test_to_message():
    assert to_message("start ivanov drw-1") == {"type": "start", "user": "ivanov", "object": "drw-1"}
    assert to_message("act petrov drw-1 accept") == {"type": "action", "user": "petrov",
                                                     "object": "drw-1", "action": "accept"}
    assert to_message('{"type": "start"}') == '{"type": "start"}'
    assert to_message("status drw-1") is None
    assert to_message("act petrov drw-1") == {"type": "action", "user": "petrov", "object": "drw-1"}


def test_variant3_demo_script(tmp_path, capsys):
    log = tmp_path / "demo.jsonl"
    assert main([str(V3), "--script", str(V3 / "demo.txt"), "--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "registration → composition" in out and "execution → done" in out
    assert "требуется роль economist" in out and "delivery_in_range" in out
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    results = [e["result"] for e in entries]
    assert results.count("error") == 1 and "rejected" in results
    assert [e for e in entries if e["object"] == "ord-1"][-2]["state_after"] == "done"


def test_spec_demo_script(tmp_path, capsys):
    log = tmp_path / "a.jsonl"
    main([str(SPEC), "--script", str(SPEC / "demo.txt"), "--log", str(log)])
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 19 and [e["result"] for e in entries].count("rejected") == 3


def test_log_overwritten_unless_append(tmp_path, capsys):
    log = tmp_path / "x.jsonl"
    script = tmp_path / "s.txt"
    script.write_text("start smirnova ord-1\n", encoding="utf-8")
    for _ in range(2):
        main([str(V3), "--script", str(script), "--log", str(log)])
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    main([str(V3), "--script", str(script), "--log", str(log), "--append"])
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


def test_bad_config_dir(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "ошибка конфигурации" in capsys.readouterr().err


def test_module_entry_point_reads_stdin(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "fsm_service", str(V3), "--log", str(tmp_path / "l.jsonl")],
                          input="start smirnova ord-1\nstatus ord-1\n", capture_output=True, text=True,
                          encoding="utf-8", cwd=V3.parent.parent, env={"PYTHONPATH": "src", **_env()})
    assert proc.returncode == 0, proc.stderr
    assert "— → registration" in proc.stdout and "smirnova: complete" in proc.stdout


def _env():
    import os
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def test_color_output_and_sections(tmp_path, capsys):
    script = tmp_path / "s.txt"
    script.write_text("## Раздел\n# пояснение\nstart smirnova ord-1\nact popova ord-1 complete\nlog\n",
                      encoding="utf-8")
    main([str(V3), "--script", str(script), "--log", str(tmp_path / "l.jsonl"), "--color", "always"])
    out = capsys.readouterr().out
    assert "\033[" in out                                  # ANSI-цвета
    assert "━━ Раздел" in out and "» пояснение" in out
    assert "✔ ПРИНЯТО" in out and "✘ ОТКАЗ" in out
    assert "состояние не изменилось: registration (Регистрация)" in out


def test_no_color_when_not_terminal(tmp_path, capsys):
    script = tmp_path / "s.txt"
    script.write_text("start smirnova ord-1\n", encoding="utf-8")
    main([str(V3), "--script", str(script), "--log", str(tmp_path / "l.jsonl")])
    assert "\033[" not in capsys.readouterr().out


def test_delay_between_commands(tmp_path, capsys, monkeypatch):
    pauses = []
    monkeypatch.setattr("fsm_service.cli.time.sleep", lambda s: pauses.append(s))
    script = tmp_path / "s.txt"
    script.write_text("# не команда — без паузы\nstart smirnova ord-1\nstatus ord-1\n", encoding="utf-8")
    main([str(V3), "--script", str(script), "--log", str(tmp_path / "l.jsonl"), "--delay", "0.5"])
    assert pauses == [0.5, 0.5]                            # после каждой команды, не после пояснений


def test_error_on_running_process_shows_its_state(tmp_path, capsys):
    script = tmp_path / "s.txt"
    script.write_text("start smirnova ord-1\nact smirnova ord-1\n", encoding="utf-8")
    main([str(V3), "--script", str(script), "--log", str(tmp_path / "l.jsonl")])
    out = capsys.readouterr().out
    assert "ОШИБКА" in out and "состояние не изменилось: registration" in out
