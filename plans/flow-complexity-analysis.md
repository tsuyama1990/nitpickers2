# LangGraph Flow Complexity Analysis

> **Objective:** Identify unnecessary complexity in the LangGraph flow/control of Architect, Coder, and QA nodes, with DRY/SOLID violations highlighted.
> **最終確認:** 2026-06-28 — 記載された問題の大部分は未修正。赤字項目のみSession 2で対応済み。


---

## 1. ARCHITECT NODE — Complexity Violations

### 1.1 Dead-code critic retry loop (Graph vs. Implementation mismatch)

In [`src/graph.py`](src/graph.py:36-52), the architect graph is wired as a loop:

```
architect_session → route → architect_critic → route → architect_session (retry loop)
```

However, in [`src/nodes/critic_nodes.py`](src/nodes/critic_nodes.py:49), the logic is:

```python
if critic_result.is_approved or critic_retry_count >= 0:
```

Since `critic_retry_count` initializes at `0` and `0 >= 0` is **always `True`**, the rejection/retry branch (lines 70-90) is **dead code**. The `architect_critic` can **never** route back to `architect_session`. The graph advertises a retry loop that cannot execute.

**Violations:**
- **DRY:** The graph structure implies retry capability that contradicts the implementation — wasted cognitive load.
- **SRP:** The `architect_critic_node` forces approval even when `is_approved=False`, violating the Single Responsibility of a critic (which should approve or reject).
- **Action:** Remove the dead retry branches from routing and node logic, or fix the critic comparison operator.

### 1.2 `architect_session_node` — Two responsibilities in one method

In [`src/nodes/architect.py`](src/nodes/architect.py:64-142), the node handles:
- **Path A (line 70-71):** Feedback loop re-entry from critic rejection (despite being dead code as shown above)
- **Path B (line 73-142):** Initial architect session creation

These are distinct workflows mashed into a single node method with a conditional gate at line 70.

**Violation:** **SRP** — A node should do one thing. Create a separate `architect_feedback_node` or inline the feedback path.

### 1.3 `BaseNode` is defined but not enforced

In [`src/nodes/architect.py`](src/nodes/architect.py:17-31), `BaseNode` is an abstract base class defining `__call__`. Only `ArchitectNodes` inherits from it. `CoderNodes`, `AuditorNodes`, `CoderCriticNodes`, etc. do **not** inherit from `BaseNode`.

**Violation:** **LSP / ISP** — The base contract is defined but not universally enforced, creating a false sense of type safety.

---

## 2. CODER NODE — Complexity Violations

### 2.1 `CoderUseCase.execute()` — God Method (~224 lines)

In [`src/services/coder_usecase.py`](src/services/coder_usecase.py:56-280), the `execute()` method is suppressed with `# noqa: C901, PLR0915` (too complex, too many lines). It handles:

| Section | Lines | Responsibility |
|---------|-------|---------------|
| A | 70-93 | Wait/resume existing session |
| B | 97-109 | Reuse session for retry/post-audit |
| B2 | 112-128 | Handle already-completed session |
| C | 131-183 | Launch brand-new session |
| D | 186-272 | Post-session processing with ~10 conditional branches |

The `target_status` logic (lines 201-213) alone has 5 independent `if` blocks determining the next state.

**Violation:** **SRP** — This should be decomposed into:
- `CoderSessionResumer` (handles A, B2)
- `CoderSessionReuser` (handles B)
- `CoderSessionLauncher` (handles C)
- `CoderPostProcessor` (handles D)

### 2.2 DUPLICATE: Three graph nodes call the exact same method

In [`src/graph_nodes.py`](src/graph_nodes.py:86-93):

```python
async def self_critic_node(self, state):    # → self._coder_critic.coder_critic_node(state)
async def final_critic_node(self, state):   # → self._coder_critic.coder_critic_node(state)  
async def coder_critic_node(self, state):   # → self._coder_critic.coder_critic_node(state)
```

Three graph node names all **delegate to the identical method**. In [`src/graph.py`](src/graph.py), only `self_critic_node` (line 67) and `final_critic_node` (line 69) are registered in the coder graph — `coder_critic_node` is never wired to a graph edge, making it dead code in `CycleNodes`.

**Violation:** **DRY / YAGNI** — Either merge them into one node or differentiate their behavior. The dead `coder_critic_node` should be removed.

### 2.3 `_try_reuse_session` duplicates status-checking from `execute()`

In [`src/services/coder_usecase.py`](src/services/coder_usecase.py:346-443), `_try_reuse_session` has its own `REUSABLE_STATUSES` set (line 351-358) that partially overlaps with the status checks already performed in `execute()` at lines 97-109. The caller (`execute()`) checks `state.status in SHOULD_REUSE_STATUSES` AND `_try_reuse_session` checks `state.status in REUSABLE_STATUSES` again with a slightly different set.

**Violation:** **DRY** — Two layers of the same status filtering. The caller should be the single gatekeeper.

### 2.4 `_send_audit_feedback_to_session` — Hidden complexity with push-nudge fallback

In [`src/services/coder_usecase.py`](src/services/coder_usecase.py:547-616), `_send_audit_feedback_to_session` contains:
- Polling for state change (12 attempts × 5s)
- Commit hash comparison to detect no-op
- A "push nudge" fallback that sends a second message if no new commits found
- An exception re-raise after fallback

This is a **state machine within a graph node**. The graph itself should handle retry, not an inline polling loop.

**Violation:** **KISS / Hierarchy inversion** — The LangGraph framework is designed to handle this at the graph level, not inside a node.

### 2.5 `CoderUseCase` is shared across `CoderNodes` and `CoderCriticNodes`

In [`src/nodes/coder.py`](src/nodes/coder.py:10-34), `CoderNodes` creates `CoderUseCase(self.jules)` per-call (lines 13, 26). In [`src/nodes/critic_nodes.py`](src/nodes/critic_nodes.py:127), `CoderCriticNodes` also creates `CoderUseCase(self.jules)` per-call. The critic calls `usecase.run_critic_phase()` (line 140), which is a method on the same class used for implementation.

**Violation:** **SRP / Interface Segregation** — `CoderUseCase` should be split into `CoderImplementationUseCase` and `CoderCriticUseCase` (or have the critic extracted to a separate critic-specific use case). The critic doesn't need `execute()`, `_try_reuse_session()`, `_run_jules_session()`, etc.

---

## 3. CODER ROUTING — Complexity Violations

### 3.1 `check_coder_outcome` — Ambiguous fallback

In [`src/nodes/routers.py`](src/nodes/routers.py:7-23), `check_coder_outcome` routes `CODER_RETRY` back to `"impl_coder_node"`. But `route_committee` (lines 26-47) also routes `RETRY_FIX` back to `"impl_coder_node"`. The flow is:

```
impl_coder_node → check_coder_outcome → self_critic_node → auditor → committee_manager → route_committee → impl_coder_node
```

That's a **5-node traversal** before returning to `impl_coder_node`. If the intent was a "retry" loop, some of these intermediate nodes may be unnecessary for certain retry paths.

**Violation:** **YAGNI / KISS** — Every retry path passes through all 5 nodes even when skipping critic/audit might be appropriate.

### 3.2 `route_committee` — Complex branching (7+ distinct code paths)

In [`src/nodes/routers.py`](src/nodes/routers.py:26-47), `route_committee` has 6 `if`/`elif` conditions and returns a string edge name. The routing depends on:
- `state.status` (5 different FlowStatus values checked)
- `state.current_phase` (2 different WorkPhase values checked)
- The fallback is `"impl_coder_node"`

This creates an **implicit state machine** that is hard to reason about. New status values added to `FlowStatus` implicitly change router behavior.

**Violation:** **OCP (Open/Closed Principle)** — Adding a new status requires modifying this router. Consider a strategy pattern or explicit routing table.

---

## 4. QA NODE — Complexity Violations

### 4.1 `QaUseCase._send_audit_feedback_to_session` — DUPLICATE of Architect's version

Compare [`src/services/qa_usecase.py`](src/services/qa_usecase.py:34-92) with [`src/nodes/architect.py`](src/nodes/architect.py:144-201):

| Feature | `qa_usecase.py` | `architect.py` |
|---------|----------------|----------------|
| Send message to Jules | `send_message` | `send_message` |
| Poll 12×5s for state change | Lines 47-75 | Lines 158-185 |
| Check active states | Lines 53-61 | Lines 164-170 |
| Wait for completion | Line 78 | Line 187 |

**~90% identical logic.** And there's a **third** version in [`src/services/coder_usecase.py`](src/services/coder_usecase.py:547-616).

**Violation:** **DRY** — Extract a shared `JulesSessionFeedbackSender` utility.

### 4.2 QA graph has structural ordering issues

In [`src/graph.py`](src/graph.py:109-133), the QA graph is:

```
START → uat_evaluate → {qa_auditor (UAT_FAILED) | ux_auditor (passed) | end}
qa_auditor → qa_session → END
ux_auditor → END
```

The flow for UAT failure:
1. UAT tests **fail** → `qa_auditor` (diagnoses failure via LLM) → `qa_session` (generates tutorials)

But the `qa_session` node in [`src/services/qa_usecase.py`](src/services/qa_usecase.py:94-212) has its **own internal retry loop** (up to 5 retries via lines 125-131) that polls for Jules session state changes — all hidden inside a single graph node.

**Violation:** **KISS / Layering** — The graph should model the retry explicitly as edges, not hide it inside a node.

### 4.3 `QaUseCase` takes 3 dependencies but uses `Any`

In [`src/services/qa_usecase.py`](src/services/qa_usecase.py:24-32), `jules_client: Any` is typed as `Any`. Same for many other classes. This defeats static analysis.

**Violation:** **DIP (Dependency Inversion)** — Dependencies should be typed to interfaces (protocols), not `Any`.

---

## 5. CROSS-CUTTING VIOLATIONS

### 5.1 `FlowStatus` enum — Flat namespace with 30+ values

In [`src/enums.py`](src/enums.py:16-55), `FlowStatus` contains 30+ values for all phases mixed together:
- `CODERRETRY` and `RETRYFIX` both mean "return to coder" but are different values
- `ARCHITECT_COMPLETED`, `ARCHITECT_FAILED`, `ARCHITECT_SESSION_COMPLETED`, `ARCHITECT_CRITIC_REJECTED` — 4 architect-specific values mixed with coder/QA values
- No namespacing or grouping

The routers must check this flat set, leading to complex conditionals like `route_committee` with 6+ branches.

**Violation:** **SRP** — `FlowStatus` has too many unrelated responsibilities. Consider `ArchitectStatus`, `CoderStatus`, `QAStatus` sub-enums.

### 5.2 `CycleState` — ~150 lines of boilerplate property delegation

In [`src/state.py`](src/state.py:172-358), `CycleState` has ~30 properties delegating to sub-states (`committee.*`, `session.*`, `audit.*`, `test.*`, `uat.*`, `config.*`). Each property is ~5 lines. Total boilerplate: ~150 lines.

Many of these exist only for backward compatibility but could be eliminated with:
- Direct sub-state access (`state.committee.current_auditor_index` vs `state.current_auditor_index`)
- Or a dynamic `__getattr__` delegator

**Violation:** **DRY** — Property boilerplate is manually maintained.

### 5.3 `graph_nodes.py` — God-object aggregator

In [`src/graph_nodes.py`](src/graph_nodes.py:37-163), `CycleNodes` aggregates:
- 4 sub-node classes (`ArchitectNodes`, `ArchitectCriticNodes`, `CoderNodes`, `CoderCriticNodes`, `AuditorNodes`)
- 4 use cases (`CommitteeUseCase`, `UatUseCase`, `UxAuditorUseCase`, `QaUseCase`)
- 6 router functions
- `GlobalRefactorNodes`

Many methods are one-line pass-throughs (lines 72-163), creating unnecessary indirection.

**Violation:** **Law of Demeter** — The graph builder accesses `self.nodes.xxx_node(state)` which chains through `CycleNodes` → sub-node → use case. The pass-through layer adds no value.

### 5.4 `CommitteeUseCase.execute()` — Complex status-mutation logic

In [`src/services/committee_usecase.py`](src/services/committee_usecase.py:19-148), `execute()` is suppressed with `# noqa: PLR0911` (too many return statements). It has:
- 6 explicit `return` paths
- Multiple cooldown-wait logic blocks (duplicated lines 70-79 and 99-109)
- Manual iteration counting via `committee` state updates mixed with top-level state updates

**Violation:** **DRY** — Cooldown logic is copy-pasted within the same method (lines 70-79 and 99-109). Extract to a `_apply_cooldown()` helper.

---

## 6. SUMMARY TABLE

| # | Location | Issue | Principle Violated | Status |
|---|----------|-------|-------------------|--------|
| 1 | [`critic_nodes.py:49`](src/nodes/critic_nodes.py:49) | Dead-code loop: `critic_retry_count >= 0` always True | DRY, SRP | ❌ 未修正 |
| 2 | [`architect.py:64-142`](src/nodes/architect.py:64-142) | Two workflows in one node | SRP | ❌ 未修正 |
| 3 | [`architect.py:17-31`](src/nodes/architect.py:17-31) | `BaseNode` not enforced across all nodes | LSP | ❌ 未修正 |
| 4 | [`coder_usecase.py:56-280`](src/services/coder_usecase.py:56-280) | God method with 5 sections, 10+ branches | SRP | ❌ 未修正 |
| 5 | [`graph_nodes.py:86-93`](src/graph_nodes.py:86-93) | 3 graph nodes calling same method + 1 dead | DRY, YAGNI | ❌ 未修正 |
| 6 | [`coder_usecase.py:346-443`](src/services/coder_usecase.py:346-443) | Duplicated status filtering vs execute() | DRY | ❌ 未修正 |
| 7 | [`coder_usecase.py:547-616`](src/services/coder_usecase.py:547-616) | Hidden state machine (polling + push nudge) | KISS | ❌ 未修正 |
| 8 | [`coder.py`](src/nodes/coder.py) + [`critic_nodes.py:127`](src/nodes/critic_nodes.py:127) | `CoderUseCase` shared for impl + critic | ISP | ❌ 未修正 |
| 9 | [`routers.py:7-47`](src/nodes/routers.py:7-47) | 5-node retry traversal + 6-branch router | KISS, OCP | ❌ 未修正 |
| 10 | [`qa_usecase.py:34-92`](src/services/qa_usecase.py:34-92) | Duplicate `_send_audit_feedback_to_session` | DRY | ❌ 未修正 |
| 11 | [`architect.py:144-201`](src/nodes/architect.py:144-201) | Same duplicate, 3rd version in coder_usecase.py | DRY | ❌ 未修正 |
| 12 | [`qa_usecase.py:94-212`](src/services/qa_usecase.py:94-212) | Hidden 5-retry loop inside single graph node | KISS | ❌ 未修正 |
| 13 | [`enums.py:16-55`](src/enums.py:16-55) | 30+ flat status values, no namespacing | SRP | ❌ 未修正 |
| 14 | [`state.py:172-358`](src/state.py:172-358) | 150 lines of property boilerplate | DRY | ❌ 未修正 |
| 15 | [`graph_nodes.py:37-163`](src/graph_nodes.py:37-163) | God-object aggregator with pass-through layer | Law of Demeter | ❌ 未修正 |
| 16 | [`committee_usecase.py:70-109`](src/services/committee_usecase.py:70-109) | Duplicated cooldown logic within same method | DRY | ❌ 未修正 |

> **凡例**: ❌ = 未修正 (Session 2 で対応なし)。Session 2 で対応した内容 (git/統合、Protocol削除、file統合) はこの分析の対象外。


---

## 7. RECOMMENDED PRIORITY ACTIONS

### P0 — Bug-level (broken or misleading behavior)
1. **Fix `critic_retry_count >= 0`** (Item 1) — This breaks the architect critic loop. Change to `>= 1` or make the comparison meaningful. ⚠️ 未対応
2. **Remove dead `coder_critic_node`** (Item 5) — Method exists in `CycleNodes` but no graph edge uses it. ⚠️ 未対応

### P1 — Major refactoring (SRP / DRY violations with highest complexity cost)
3. **Split `CoderUseCase.execute()`** (Item 4) — Extract Session Resumer, Reuser, Launcher, PostProcessor.
4. **Extract `send_audit_feedback_to_session`** (Items 10, 11) — One shared utility, not 3 implementations.
5. **Decompose `route_committee`** (Item 9) — Use a routing table or strategy pattern instead of 6-branch if/elif.

### P2 — Structural improvements
6. **Namespace `FlowStatus`** (Item 13) — Split into `ArchitectStatus`, `CoderStatus`, `QAStatus` sub-enums.
7. **Eliminate property boilerplate** (Item 14) — Use `__getattr__` or direct sub-state access.
8. **Remove pass-through layer** (Item 15) — Wire graph nodes directly to use cases, bypass `CycleNodes` where possible.

### P3 — Cleanup
9. **Remove dead architect critic retry code** (Item 1) — Remove the dead rejection path from critic.
10. **Enforce `BaseNode` contract** (Item 3) — Make all node classes inherit from `BaseNode`.
