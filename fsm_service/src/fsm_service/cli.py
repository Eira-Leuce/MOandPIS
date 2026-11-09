"""Консольная программа для демонстрации.

    python -m fsm_service examples/variant3                        # интерактивно
    python -m fsm_service examples/variant3 --script examples/variant3/demo.txt
    python -m fsm_service examples/variant3 --script ... --step    # по Enter
    python -m fsm_service examples/variant3 --script ... --delay 2 # пауза 2 с

Команды (по одной в строке):
    start  <пользователь> <объект>            — запустить процесс над объектом
    act    <пользователь> <объект> <действие> — выполнить действие
    {"type": "action", ...}                   — команда в формате JSON
    status <объект>                           — параметры, состояние, доступные действия
    objects | users | log | log json | help | quit
В файле сценария: «## текст» — заголовок раздела, «# текст» — пояснение.

Результат каждой команды печатается и пишется в JSONL-журнал (--log, по умолчанию
log.jsonl; файл перезаписывается при каждом запуске, если не указан --append).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from typing import TextIO

from .commands import JsonLinesLog, MemoryLog, ProcessService, TeeLog
from .config import load_system_dir
from .engine import ProcessEngine
from .errors import ConfigurationError

HELP = __doc__.split("Команды", 1)[1].split("Результат", 1)[0]


# ---------------------------------------------------------------------------
# Разбор команд консоли
# ---------------------------------------------------------------------------


def to_message(line: str) -> dict | str | None:
    """Строка консоли → сообщение для ProcessService (None — не команда процесса)."""
    if line.startswith("{"):
        return line
    words = line.split()
    fields = {"start": ("user", "object"), "act": ("user", "object", "action"),
              "action": ("user", "object", "action")}.get(words[0])
    if fields is None:
        return None
    # Лишние или недостающие аргументы не исправляем: сервис вернёт ошибку и запишет её в журнал.
    message = {"type": "start" if words[0] == "start" else "action"}
    message.update(zip(fields, words[1:]))
    if len(words) - 1 > len(fields):
        message["extra"] = words[1 + len(fields):]
    return message


# ---------------------------------------------------------------------------
# Оформление
# ---------------------------------------------------------------------------


def enable_windows_console() -> None:
    """UTF-8 и ANSI-цвета в консоли Windows (cmd, PowerShell)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


class Style:
    CODES = {"bold": "1", "dim": "2", "italic": "3", "red": "31", "green": "32", "yellow": "33",
             "blue": "34", "magenta": "35", "cyan": "36", "gray": "90"}

    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(self.CODES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"


RESULT_LOOK = {
    "accepted": ("✔", "ПРИНЯТО", "green"),
    "rejected": ("✘", "ОТКАЗ", "red"),
    "error": ("⚠", "ОШИБКА", "yellow"),
}


class Console:
    """Печать результатов, таблиц и заголовков с паузами между шагами."""

    def __init__(self, engine: ProcessEngine, out: TextIO, color: bool, delay: float = 0.0,
                 step: bool = False, typing: bool = False) -> None:
        self.engine = engine
        self.out = out
        self.s = Style(color)
        self.delay = delay
        self.step = step
        self.typing = typing
        self.width = min(shutil.get_terminal_size((100, 24)).columns, 110)

    # --- базовое ------------------------------------------------------------------------
    def print(self, text: str = "") -> None:
        print(text, file=self.out)
        self.out.flush()

    def pause(self) -> None:
        if self.step and sys.stdin.isatty():
            self.out.write(self.s("  ⏎ Enter — дальше", "gray"))
            self.out.flush()
            sys.stdin.readline()
            self.out.write("\033[1A\033[2K" if self.s.color else "")  # стереть подсказку
        elif self.delay:
            time.sleep(self.delay)

    def type_out(self, text: str) -> None:
        """Эффект набора команды (только при паузах и цветном выводе)."""
        if not (self.typing and self.s.color):
            self.print(text)
            return
        for ch in text:
            self.out.write(ch)
            self.out.flush()
            time.sleep(0.015)
        self.out.write("\n")

    def state_title(self, obj: str | None, state: str | None) -> str:
        if obj is None or state is None:
            return ""
        process = self.engine.process(obj)
        if process is not None:
            template = process.template
        elif obj in self.engine.objects:
            template = self.engine.template_for(self.engine.objects.get(obj).class_id)
        else:
            template = None
        if template is None:
            return ""
        try:
            return template.state(state).title
        except KeyError:
            return ""

    # --- блоки ----------------------------------------------------------------------------
    def banner(self) -> None:
        e = self.engine
        line = "═" * self.width
        self.print(self.s(line, "cyan"))
        for t in e.templates:
            self.print(self.s(f"  {t.title or t.id}", "bold") + self.s(f"   шаблон {t.id}, класс {t.class_id}", "gray"))
        self.print(f"  Загружено: классов {len(e.classes.classes)}, объектов {len(e.objects.objects)}, "
                   f"шаблонов {len(e.templates)}. " + self.s("Процессы не запущены.", "italic"))
        self.print(self.s(line, "cyan"))

    def section(self, title: str) -> None:
        self.print()
        bar = f"━━ {title} "
        self.print(self.s(bar + "━" * max(self.width - len(bar), 3), "bold", "blue"))

    def note(self, text: str) -> None:
        self.print(self.s(f"  » {text}", "italic", "gray"))

    def command(self, line: str) -> None:
        self.type_out(self.s("> ", "cyan", "bold") + self.s(line, "cyan"))

    def result(self, entry: dict) -> None:
        mark, word, color = RESULT_LOOK.get(entry["result"], ("?", entry["result"], "yellow"))
        head = self.s(f"  {mark} {word:<8}", color, "bold")
        obj, before, after = entry["object"], entry["state_before"], entry["state_after"]
        if entry["result"] == "accepted":
            arrow = f"{before or '—'} → {after}"
            old, new = self.state_title(obj, before), self.state_title(obj, after)
            pretty = (f"{old} → {new}" if old else new) if new else ""
            self.print(f"{head} {self.s(arrow, 'bold')}   {self.s(pretty, 'gray')}")
        else:
            self.print(f"{head} {entry['reason']}")
            process = self.engine.process(obj) if obj else None
            current = after or (process.state if process else None)
            if current is None:
                note = "процесс не запущен"
            else:
                title = self.state_title(obj, current)
                note = f"состояние не изменилось: {current}" + (f" ({title})" if title else "")
            self.print(" " * 13 + self.s(note, "gray"))

    def table(self, header: list[str], rows: list[list[str]], colors: list[str | None] | None = None) -> None:
        widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)] if rows else [len(h) for h in header]
        fmt = lambda cells: ("  " + "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))).rstrip()
        self.print(self.s(fmt(header), "bold"))
        self.print(self.s("  " + "  ".join("─" * w for w in widths), "gray"))
        for i, row in enumerate(rows):
            color = (colors or [None] * len(rows))[i]
            self.print(self.s(fmt(row), color) if color else fmt(row))

    def objects(self) -> None:
        rows, colors = [], []
        for o in self.engine.objects.objects:
            t = self.engine.template_for(o.class_id)
            proc = self.engine.process(o.id)
            if proc:
                mark, color = f"процесс: {proc.state}", "green"
            elif t:
                mark, color = "можно запустить", None
            else:
                mark, color = "без шаблона", "gray"
            rows.append([o.id, o.class_id, mark])
            colors.append(color)
        self.table(["объект", "класс", "процесс"], rows, colors)

    def users(self) -> None:
        d = self.engine.directory
        if d is None:
            self.print("  справочник пользователей не задан")
            return
        self.table(["пользователь", "имя", "роли"], [[u.id, u.name, ", ".join(sorted(u.roles))] for u in d.users])

    def status(self, object_id: str) -> None:
        obj = self.engine.objects.find(object_id)
        if obj is None:
            self.print(self.s(f"  неизвестный объект {object_id!r}", "red"))
            return
        cls = self.engine.classes.get(obj.class_id)
        self.print(self.s(f"  {obj.id}", "bold") + self.s(f"  {cls.title or cls.id} ({cls.id})", "gray"))
        params = obj.registry.parameters(obj.class_id)
        rows = []
        for name, value in obj.values.items():
            p = params[name]
            low, high = p.bounds()
            bounds = "" if low is None and high is None else f"[{'' if low is None else low}..{'' if high is None else high}]"
            if value is None:
                shown = "— не задано"
            elif p.is_composition and not value:
                shown = "[] пусто"
            elif p.is_composition:
                shown = ", ".join(value)
            else:
                shown = str(value)
            rows.append((name, shown, bounds, p.title))
        widths = [max(len(r[i]) for r in rows) for i in range(3)] if rows else [0, 0, 0]
        for name, shown, bounds, title in rows:
            self.print(f"    {name.ljust(widths[0])} = {shown.ljust(widths[1])}  "
                       + self.s(f"{bounds.ljust(widths[2])}  {title}", "gray"))
        proc = self.engine.process(obj.id)
        if proc is None:
            self.print(self.s("  процесс не запущен", "italic"))
            return
        state = proc.template.state(proc.state)
        tail = " — конечное" if state.final else ""
        self.print("  состояние: " + self.s(f"{state.name} ({state.title}){tail}", "bold", "magenta"))
        if self.engine.directory is not None:
            for u in self.engine.directory.users:
                actions = proc.available_events(u.id)
                if actions:
                    self.print(f"    {u.id}: {', '.join(actions)}")

    def log(self, entries: list[dict], as_json: bool) -> None:
        if as_json:
            for entry in entries:
                self.print(json.dumps(entry, ensure_ascii=False))
            return
        rows, colors = [], []
        for i, e in enumerate(entries, 1):
            cmd = e["command"] if isinstance(e["command"], dict) else {}
            what = cmd.get("action") or cmd.get("type") or str(e["command"])
            rows.append([str(i), e["user"] or "—", e["object"] or "—", what, e["result"],
                         f"{e['state_before'] or '—'} → {e['state_after'] or '—'}", e["reason"] or ""])
            colors.append(RESULT_LOOK.get(e["result"], (None, None, None))[2])
        self.table(["№", "кто", "объект", "команда", "результат", "состояние", "причина"], rows, colors)

    def summary(self, entries: list[dict], log_name: str) -> None:
        count = {k: sum(e["result"] == k for e in entries) for k in RESULT_LOOK}
        self.print()
        self.print(self.s("═" * self.width, "cyan"))
        self.print(f"  Команд: {len(entries)}; "
                   + self.s(f"принято: {count['accepted']}", "green") + ", "
                   + self.s(f"отказов: {count['rejected']}", "red") + ", "
                   + self.s(f"ошибок: {count['error']}", "yellow") + ". "
                   + f"Журнал: {log_name}")
        self.print(self.s("═" * self.width, "cyan"))


# ---------------------------------------------------------------------------
# Цикл обработки
# ---------------------------------------------------------------------------


def run(engine: ProcessEngine, source: TextIO, out: TextIO, log: JsonLinesLog, echo: bool,
        console: Console | None = None) -> int:
    con = console or Console(engine, out, color=False)
    memory = MemoryLog()
    service = ProcessService(engine, TeeLog(log, memory))
    interactive = source is sys.stdin and sys.stdin.isatty()
    while True:
        if interactive:
            out.write(con.s("> ", "cyan", "bold"))
            out.flush()
        raw = source.readline()
        if not raw:
            break
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##"):
            con.section(line.lstrip("#").strip())
            continue
        if line.startswith("#"):
            if echo:
                con.note(line.lstrip("#").strip())
            continue
        if echo:
            con.command(line)
        words = line.split()
        if line in ("quit", "exit"):
            break
        message = to_message(line)
        if message is not None:
            con.result(service.send(message))
        elif words[0] == "log":
            con.log(memory.entries, as_json=len(words) > 1 and words[1] == "json")
        elif words[0] == "objects":
            con.objects()
        elif words[0] == "users":
            con.users()
        elif words[0] == "status" and len(words) == 2:
            con.status(words[1])
        else:
            con.print(HELP.strip("\n"))
            continue
        if echo:
            con.pause()
    con.summary(memory.entries, log.stream.name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fsm_service", description="Исполнение процессов по конфигурации")
    parser.add_argument("config", help="каталог конфигурации: classes.yaml, objects.yaml, users.yaml, шаблон")
    parser.add_argument("--script", help="файл с командами (иначе — ввод с клавиатуры)")
    parser.add_argument("--log", default="log.jsonl", help="файл журнала JSONL (по умолчанию log.jsonl)")
    parser.add_argument("--append", action="store_true", help="дописывать журнал, а не перезаписывать")
    parser.add_argument("--delay", type=float, default=None,
                        help="пауза между командами сценария, с (по умолчанию 1.2 в консоли, 0 при выводе в файл)")
    parser.add_argument("--step", action="store_true", help="сценарий по шагам: следующая команда — по Enter")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="цветной вывод")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    enable_windows_console()
    tty = sys.stdout.isatty()
    color = args.color == "always" or (args.color == "auto" and tty and "NO_COLOR" not in os.environ)
    delay = args.delay if args.delay is not None else (1.2 if args.script and tty else 0.0)

    try:
        engine = load_system_dir(args.config)
    except ConfigurationError as exc:
        print(f"ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    console = Console(engine, sys.stdout, color=color, delay=delay, step=args.step,
                      typing=bool(args.script) and (delay > 0 or args.step))
    console.banner()
    log = JsonLinesLog(open(args.log, "a" if args.append else "w", encoding="utf-8"))
    try:
        if args.script:
            with open(args.script, encoding="utf-8") as src:
                return run(engine, src, sys.stdout, log, echo=True, console=console)
        return run(engine, sys.stdin, sys.stdout, log, echo=not sys.stdin.isatty(), console=console)
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
