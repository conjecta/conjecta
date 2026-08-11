import asyncio

from math_agent.lean.result import LeanResult
from math_agent.verification import LeanRunnerVerificationBackend


class FakeLeanRunner:
    async def check_proof(self, lean_code: str):
        assert lean_code == "example : True := trivial"
        return LeanResult(
            success=False,
            errors=["type mismatch"],
            failure_kind="type_mismatch",
            output="compiler output",
        )


def test_lean_runner_backend_returns_verification_report_with_metadata():
    backend = LeanRunnerVerificationBackend(FakeLeanRunner(), name="lean_backend")

    report = asyncio.run(
        backend.verify(
            "example : True := trivial",
            metadata={"proof_artifact_id": "p1"},
        )
    )

    assert report.source == "lean_backend"
    assert report.passed is False
    assert report.issues == ["type mismatch"]
    assert report.evidence == "compiler output"
    assert report.metadata["failure_kind"] == "type_mismatch"
    assert report.metadata["proof_artifact_id"] == "p1"
