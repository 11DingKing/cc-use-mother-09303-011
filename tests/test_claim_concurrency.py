"""并发认领：多个专员同时认领同一案件，只能成功一次。"""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from support import build_service, intake_and_triage

from case_backend import Actor, CaseState, ClaimConflictError, Role


class ClaimConcurrencyTest(unittest.TestCase):
    def test_only_one_claim_succeeds_under_concurrency(self) -> None:
        service, _ = build_service()
        case = intake_and_triage(service)
        specialists = [
            Actor(f"spec-{i}", Role.SPECIALIST, group_id="intern-office") for i in range(8)
        ]

        outcomes = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for specialist in specialists:
                outcomes.append(pool.submit(self._try_claim, service, case.case_id, specialist))
        results = [future.result() for future in outcomes]

        successes = [r for r in results if r == "ok"]
        conflicts = [r for r in results if r == "conflict"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), len(specialists) - 1)

        final = service.repo.get(case.case_id)
        self.assertEqual(final.state, CaseState.IN_PROGRESS)
        self.assertIn(final.assignee_id, {s.actor_id for s in specialists})
        claim_events = [
            h for h in final.history if h.to_state is CaseState.IN_PROGRESS
        ]
        self.assertEqual(len(claim_events), 1)

    @staticmethod
    def _try_claim(service, case_id, specialist) -> str:
        try:
            service.claim(case_id, specialist=specialist)
            return "ok"
        except ClaimConflictError:
            return "conflict"


if __name__ == "__main__":
    unittest.main()
