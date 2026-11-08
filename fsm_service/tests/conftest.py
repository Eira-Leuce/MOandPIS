from pathlib import Path

import pytest

from fsm_service import MemoryLog, ProcessService, load_system_dir

EXAMPLES = Path(__file__).parent.parent / "examples"
SPEC = EXAMPLES / "document_approval"  # пример из раздела 1 задания
V3 = EXAMPLES / "variant3"


class Session:
    """Сервис команд с журналом в памяти — для сценарных тестов."""

    def __init__(self, config_dir: Path) -> None:
        self.engine = load_system_dir(config_dir)
        self.log = MemoryLog()
        self.service = ProcessService(self.engine, self.log)

    def start(self, user: str, obj: str) -> dict:
        return self.service.send({"type": "start", "user": user, "object": obj})

    def act(self, user: str, obj: str, action: str) -> dict:
        return self.service.send({"type": "action", "user": user, "object": obj, "action": action})

    def state(self, obj: str) -> str | None:
        process = self.engine.process(obj)
        return process.state if process else None


@pytest.fixture
def spec():
    return Session(SPEC)


@pytest.fixture
def v3():
    return Session(V3)
