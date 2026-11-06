"""Справочник ролей и пользователей (п. 4.2 задания).

Роль: идентификатор и название. Пользователь: идентификатор, имя, одна или
несколько ролей. Формат YAML — как users.yaml в Приложении А:

    roles:
      - {id: developer, title: Разработчик}
      - {id: normcontroller, title: Нормоконтролер}
    users:
      - {id: ivanov, name: Иванов И. И., roles: [developer]}
      - {id: sidorov, name: Сидоров С. С., roles: [chief_designer, normcontroller]}

Аутентификации нет (п. 4.7): пользователь передаётся параметром операции —
в сервисе это поле sender сообщения.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .errors import ConfigurationError


@dataclass(frozen=True)
class Role:
    id: str
    title: str = ""


@dataclass(frozen=True)
class User:
    id: str
    name: str
    roles: frozenset[str]

    def has_any_role(self, roles: Iterable[str]) -> bool:
        return not self.roles.isdisjoint(roles)


class Directory:
    """Роли и пользователи. Пополняется динамически."""

    def __init__(self) -> None:
        self._roles: dict[str, Role] = {}
        self._users: dict[str, User] = {}

    def add_role(self, role_id: str, title: str = "") -> Role:
        if not role_id:
            raise ConfigurationError("идентификатор роли не может быть пустым")
        if role_id in self._roles:
            raise ConfigurationError(f"роль {role_id!r} уже существует")
        role = Role(role_id, title)
        self._roles[role_id] = role
        return role

    def add_user(self, user_id: str, name: str, roles: Iterable[str]) -> User:
        if not user_id:
            raise ConfigurationError("идентификатор пользователя не может быть пустым")
        if user_id in self._users:
            raise ConfigurationError(f"пользователь {user_id!r} уже существует")
        roles = frozenset(roles)
        if not roles:
            raise ConfigurationError(f"у пользователя {user_id!r} должна быть хотя бы одна роль")
        self.check_roles(roles, f"пользователь {user_id!r}")
        user = User(user_id, name, roles)
        self._users[user_id] = user
        return user

    def check_roles(self, roles: Iterable[str], where: str) -> None:
        unknown = sorted(set(roles) - self._roles.keys())
        if unknown:
            raise ConfigurationError(f"{where}: неизвестные роли {unknown}")

    def user(self, user_id: str | None) -> User | None:
        return self._users.get(user_id) if user_id is not None else None

    def role(self, role_id: str) -> Role | None:
        return self._roles.get(role_id)

    @property
    def roles(self) -> tuple[Role, ...]:
        return tuple(self._roles.values())

    @property
    def users(self) -> tuple[User, ...]:
        return tuple(self._users.values())


def load_directory(data: Mapping[str, Any]) -> Directory:
    directory = Directory()
    for r in data.get("roles") or []:
        if "id" not in r:
            raise ConfigurationError(f"у роли нет id: {r}")
        directory.add_role(r["id"], r.get("title", ""))
    for u in data.get("users") or []:
        if "id" not in u:
            raise ConfigurationError(f"у пользователя нет id: {u}")
        directory.add_user(u["id"], u.get("name", u["id"]), u.get("roles") or [])
    return directory


def load_directory_yaml(path: str | Path) -> Directory:
    with open(path, encoding="utf-8") as f:
        return load_directory(yaml.safe_load(f))
