# Coder Final Self-Critic Instruction

You are now entering the **Final Self-Critic Review** phase.
Your functional implementation has already been approved by the external Auditor, and you have just completed the **Final Refactoring** for **Cycle {{cycle_id}}**.

Before this cycle is completely finalized and merged, you must perform one last critical review of your polished implementation.

**OPERATIONAL INSTRUCTIONS**:
1. **SELF-CORRECTION**: Do NOT just output a report to me. **YOU MUST FIX THE CODE YOURSELF.** If you find any issues during this final self-review, use your tools to modify the files, run tests, and ensure everything is perfect.
2. **POLISH FOCUS**: Since the Auditor already passed the core logic, your focus here is on architectural elegance, strict typing, clean code patterns, and ensuring no regressions were introduced during refactoring.

**CORE PRINCIPLE: HIERARCHICAL DESIGN (Thinking Protocol)**
You must verify your implementation against the architectural hierarchy:
1.  **Global Vision** (`SYSTEM_ARCHITECTURE.md`): Does the code respect the global boundaries and design patterns?
2.  **Detailed Design** (`SPEC.md`): Does the implementation exactly match the specific blueprint for this cycle?
3.  **Code Blueprint** (Schema/Pydantic): Are the data structures robust, strictly typed, and validation-ready?
4.  **Verification Design** (Tests): Are tests exhaustive and do they truly verify the requirements?

**THINKING BLOCK REQUIREMENT**: At the beginning of your response, you MUST include a `<thought>` block where you perform an "Audit Gap Analysis" and identify any discrepancies between your current code and the hierarchy before applying fixes.

**DOMAIN CONTEXT (CRITICAL CONSTRAINTS)**:
Verify against the Domain Context and Scale from the `SYSTEM_ARCHITECTURE.md`,  `SPEC.md` (in **Cycle {{cycle_id}}**) and `UAT.md` (in **Cycle {{cycle_id}}**) files.

**CONSTITUTION (IMPLICIT REQUIREMENTS)**:
Verify your code against these standards:
1.  **Scalability**: No OOM risks, No N+1 queries.
2.  **Security**: No hardcoded secrets.
3.  **Maintainability**: No hardcoded paths/settings. Everything must be in `config.py` or Pydantic models.
4.  **Strict Typing**: Every function MUST have complete type hints. No `Any` unless absolutely necessary and documented.

## Audit Guidelines
Review your code critically against the following checklists:

### 1. Functional Implementation & Scope
- [ ] **Logic Correctness**: Is the logic correct, optimal and efficient? Did your refactoring accidentally break anything?
- [ ] **System Integrity**: Did you accidentally delete or modify the `.nitpick/` directory? **Check your git status and ensures it is UNTOUCHED.** If your PR contains a deletion of `.nitpick/`, YOU MUST FIX IT IMMEDIATELY.
- [ ] **Preservation of Existing Assets**: Did you unnecessarily delete or rewrite existing code or tests? 

### 2. Architecture, Design & Maintainability
- [ ] **Layer Compliance**: Follows `SYSTEM_ARCHITECTURE.md` without bypassing layers?
- [ ] **Single Responsibility (SRP)**: No God Classes?
- [ ] **Simplicity (YAGNI)**: No over-engineering?
- [ ] **NO Hardcoded Settings**: All config via `config.py`/Pydantic?

### 3. Data Integrity & Security
- [ ] **Strict Typing**: Pydantic models used at boundaries?
- [ ] **Schema Rigidity**: `extra="forbid"` used on schemas?
- [ ] **Security**: No hardcoded secrets/paths? No injections?

### 4. Scalability & Efficiency
- [ ] **Memory Safety**: **NO** loading entire datasets into memory. Use Iterators/Streaming.
- [ ] **I/O Efficiency**: **NO** I/O inside tight loops. Use batching.

### 5. Test Quality
- [ ] **Traceability**: Tests exist for all requirements?
- [ ] **Edge Cases**: Are "unhappy paths" (errors, edge cases) tested?
- [ ] **Test Execution**: Do all tests pass without errors?

## 🚨 ZERO TOLERANCE FOR HARDCODING 🚨
Search your own code for magic numbers, magic strings, unexplained constants, hardcoded paths, and hardcoded credentials. If you find any, extract them to config.

## 🚨 ANTI-MOCK VERIFICATION (CRITICAL) 🚨
You must not leave any code half-finished.
Remove any `TODO`, `FIXME`, empty functions, `pass`, `...`, or fake log outputs.

## 🚨 MUST PASS STATIC TESTS 🚨
Before completing this self-review, you MUST confirm that all static checks and tests pass. This is non-negotiable.
1. **Tests & Coverage**: Run `pytest` and verify the coverage reports.
2. **Linting & Formatting**: Run `ruff check .` and `ruff format .`.
3. **Type Checking**: Run `mypy .`.

**CRITICAL LOOP RULE**: If you modify even a *single line of code* during this self-critic phase (either manually or via auto-format), you MUST restart the entire static validation sequence from the beginning before raising the PR.

**FINAL ACTION**:
If you found ANY of the above issues, **FIX THE CODE NOW** using your file editing tools, re-run your tests, and confirm they pass.
Once you are 100% confident your code is perfect, reply confirming that the Final Self-Critic review is complete and the code is finalized.
