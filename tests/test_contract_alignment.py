"""后端实现与领域契约的一致性回归。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from domain_contract.validator import load_contract  # noqa: E402
from case_backend import CaseState, Role  # noqa: E402


class ContractAlignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT / "domain" / "contract.json")

    def test_backend_states_are_contract_states(self) -> None:
        backend_states = {state.value for state in CaseState}
        self.assertLessEqual(backend_states, set(self.contract["states"]))

    def test_backend_roles_are_contract_actors(self) -> None:
        backend_roles = {role.value for role in Role}
        self.assertLessEqual(backend_roles, set(self.contract["actors"]))

    def test_key_invariants_remain_declared(self) -> None:
        declared = set(self.contract["invariants"])
        for invariant in ("案件去重", "同意范围", "服务时限", "并发认领", "资料可见边界", "来源保留"):
            self.assertIn(invariant, declared)


if __name__ == "__main__":
    unittest.main()
