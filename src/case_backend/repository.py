"""线程安全的内存案件存储；认领等写操作在锁内原子完成。"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .errors import NotFoundError
from .models import Case


class InMemoryCaseRepository:
    """进程内案件存储。

    读取返回的是可变对象本身，因此所有写路径必须经由
    :meth:`update` / :meth:`transaction` 在锁内完成，
    以保证「认领只能成功一次」这类并发约束。
    """

    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._lock = threading.RLock()

    def add(self, case: Case) -> Case:
        with self._lock:
            if case.case_id in self._cases:
                raise ValueError(f"案件编号重复：{case.case_id}")
            self._cases[case.case_id] = case
            return case

    def get(self, case_id: str) -> Case:
        with self._lock:
            try:
                return self._cases[case_id]
            except KeyError:
                raise NotFoundError(f"案件不存在：{case_id}") from None

    def update(self, case_id: str, mutator: Callable[[Case], None]) -> Case:
        """在锁内校验并修改案件，成功后版本号加一。"""
        with self._lock:
            case = self.get(case_id)
            mutator(case)
            case.version += 1
            return case

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """跨多个案件的原子写（如合并），持锁期间其他线程不可写入。"""
        with self._lock:
            yield

    def list_all(self) -> list[Case]:
        with self._lock:
            return list(self._cases.values())

    def find_by_student(self, student_id: str) -> list[Case]:
        with self._lock:
            return [c for c in self._cases.values() if c.student_id == student_id]
