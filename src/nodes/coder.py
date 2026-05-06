from typing import Any

from src.state import CycleState


class CoderNodes:
    def __init__(self, jules: Any) -> None:
        self.jules = jules

    async def test_coder_node(self, state: CycleState) -> dict[str, Any]:
        from src.services.coder_usecase import CoderUseCase

        usecase = CoderUseCase(self.jules)
        # Ensure we set tdd_phase so the usecase/prompt knows we only want failing tests
        state.test.tdd_phase = "red"
        result = dict(await usecase.execute(state))

        test_update = state.test.model_copy(update={"tdd_phase": "red"})
        result["test"] = test_update
        return result

    async def impl_coder_node(self, state: CycleState) -> dict[str, Any]:
        from src.enums import WorkPhase
        from src.services.coder_usecase import CoderUseCase

        usecase = CoderUseCase(self.jules)
        state.test.tdd_phase = "green"
        state.current_phase = WorkPhase.CODER
        result = dict(await usecase.execute(state))

        test_update = state.test.model_copy(update={"tdd_phase": "green"})
        result["test"] = test_update
        result["current_phase"] = WorkPhase.CODER
        return result
