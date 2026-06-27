# 引き継ぎ資料

## セッション概要

**日時**: 2026-06-27 (Session 2)
**目的**: LangGraph 構造の脆弱性修正、ファイル統合、DI 統一、バグ修正

---

## 前回からの変更サマリー

### A. セッション1 (前回) の成果

- **JULES SDK → stdio MCP 化** の実現可能性調査
- **Phase 0 クリーンアップ**: `AgentProtocol` 定義、`JulesClient` リファクタリング、`MasterIntegratorClient` 分離
- 詳細: [`plans/jules-mcp-architecture.md`](plans/jules-mcp-architecture.md), [`plans/jules-cleanup-analysis.md`](plans/jules-cleanup-analysis.md)

### B. セッション2 (今回) の成果

#### B1. LangGraph 脆弱性修正

| # | 問題 | 修正内容 | ファイル |
|---|---|---|---|
| C1 | `integration_fixer_node` 未接続 | Integration Graph で `success` → `integration_fixer_node` → `END` に接続 | [`graph.py:181`](src/graph.py:181) |
| C2 | QA graph の lambda ルーター | `route_qa()` を QA graph 用に修正し lambda を置換 | [`routers.py:63`](src/nodes/routers.py:63) |
| M1 | 未使用ルーター (`route_auditor`, `route_final_critic`, `route_coder_critic`) | 削除 + `IGraphNodes` インターフェース整理 | [`routers.py`](src/nodes/routers.py) |
| M2 | `ArchitectNodes` 冗長バリデーション | `getattr` チェック削除 (Protocol が保証) | [`graph.py:30`](src/graph.py:30) |

#### B2. ファイル統合 (7ファイル削減)

| 削除したファイル | 移行先 |
|---|---|
| `src/nodes/committee.py` | `graph_nodes.py` (インライン化) |
| `src/nodes/uat.py` | `graph_nodes.py` (インライン化) |
| `src/nodes/ux_audit.py` | `graph_nodes.py` (インライン化) |
| `src/nodes/qa.py` | `graph_nodes.py` (インライン化) |
| `src/state_validators.py` | `state.py` (関数を直接定義) |
| `src/utils_json.py` | `utils.py` |
| `src/utils_sanitization.py` | `utils.py` |

#### B3. DI 修正

- `CommitteeUseCase` / `UatUseCase` / `UxAuditorUseCase` / `QaUseCase`: **per-call 生成 → `__init__` で一度保持**
- `CycleNodes.__init__`: `ServiceContainer.default()` → DI パラメータ化
- `JulesClient()` fallback: 全削除 (`workflow.py` 3箇所, `refactor_usecase.py`, `graph_nodes.py`)
- 型チェーン: `JulesClient` → `AgentProtocol` (Protocol による構造的サブタイピング)

#### B4. 発見・修正した隠れバグ

| # | 問題 | 影響 | 修正 |
|---|---|---|---|
| 🐛 B1 | `CommitteeState` に `is_refactoring` フィールド未定義 | `global_refactor.py` で設定しても Pydantic が**サイレントドロップ** — `route_committee` で `is_refactoring` が常に `False`、リファクタリングパスが絶対に通らない | [`state.py:28`](src/state.py:28) にフィールド追加 |
| 🐛 B2 | `ArchitectNodes.jules: AgentProtocol` → Pydantic クラッシュ | Protocol クラスは `isinstance` 検証で `SchemaError` | [`architect.py:18`](src/nodes/architect.py:18) `jules: Any` |
| 🐛 B3 | `_send_message` (private) → `send_message` (public) | `AgentProtocol` にない private メソッドを呼び出し | [`qa_usecase.py:42`](src/services/qa_usecase.py:42), [`architect.py:135`](src/nodes/architect.py:135) |

---

## 現在のアーキテクチャ

```mermaid
flowchart TB
    subgraph GraphDefinitions["src/graph.py — 4 Graphs"]
        AG["Architect Graph<br/>session → critic → END"]
        CG["Coder Graph<br/>coder → critic → auditor → committee → (loop/refactor/final)"]
        QG["QA Graph<br/>uat_evaluate → (qa_auditor|ux_auditor) → END"]
        IG["Integration Graph<br/>git_merge → (conflict→integrator→merge | success→fixer→END)"]
    end

    subgraph RouterLayer["src/nodes/routers.py — Routers"]
        R1["check_coder_outcome"]
        R2["route_committee"]
        R3["route_architect_session/critic"]
        R4["route_qa"]
        R5["route_merge"]
    end

    subgraph NodeLayer["src/graph_nodes.py — CycleNodes"]
        N1["ArchitectNodes<br/>ArchitectCriticNodes"]
        N2["CoderNodes<br/>CoderCriticNodes"]
        N3["AuditorNodes"]
        N4["CommitteeUseCase*<br/>UatUseCase*<br/>UxAuditorUseCase*<br/>QaUseCase*"]
        N5["GlobalRefactorNodes<br/>MasterIntegratorNodes<br/>IntegrationFixerNodes"]
    end

    subgraph ServiceLayer["src/services/ — Usecases"]
        S1["CoderUseCase"]
        S2["AuditorUseCase"]
        S3["CommitteeUseCase"]
        S4["QaUseCase<br/>UatUseCase<br/>UxAuditorUseCase"]
        S5["RefactorUsecase<br/>IntegrationUsecase"]
    end

    AG --> NodeLayer
    CG --> NodeLayer
    QG --> NodeLayer
    IG --> NodeLayer
    NodeLayer --> RouterLayer
    NodeLayer --> ServiceLayer
```

`*` = 今回インライン化したクラス (直接 usecase を保持)

---

## テスト状況 (2026-06-27 Session 3 時点)

```
tests/unit/ .......................................... 106/106 PASS
tests/integration/test_coder_graph.py ............... 1/1 PASS
tests/integration/test_tracing_integration.py ....... 2/2 PASS
tests/e2e/test_coder_graph.py ....................... 2/2 PASS
tests/e2e/test_architect_graph.py ................... 3/3 PASS
tests/e2e/test_qa_graph.py ......................... 2/2 PASS
-----------------------------------------------------
合計 (live除く) ................................... 114/114 PASS
```

全4グラフに構造テスト + 実行時テストあり。

### 既知の事前存在エラー (今回の変更と無関係)

| ファイル | エラー |
|---|---|
| `src/utils.py` | `BaseCallbackHandler` has type `Any` |
| `src/services/base_jules_usecase.py` | `FlowStatus.AUDIT_FAILED` 不存在 |
| `src/config.py` | `BaseSettings` has type `Any` |
| `src/services/llm_reviewer.py` | Returning `Any` from function declared to return `bool` |

---

## 次回着手推奨タスク

### P1: `src/services/git/` ディレクトリ統合 (6ファイル→1)

**問題**: 970行のGit操作コードが6ファイル + mixinパターンで分割されている。
`GitManager` が5つのmixinクラスを多重継承しているが、すべて `_run_git()` をラップしただけ。

**ファイル**:
```
src/services/git/
├── base.py         (153行) — BaseGitManager
├── branching.py    (149行) — GitBranchingMixin
├── checkout.py     (236行) — GitCheckoutMixin
├── merging.py      (246行) — GitMergingMixin
├── state.py        (111行) — GitStateMixin
└── worktree.py     (75行)  — GitWorktreeManager
```

**方法**: 全mixinの中身を `git_ops.py` にフラットにマージ。mixinクラスを削除し、全メソッドを直接 `GitManager` に配置。

**リスク**: 中。Pythonの実行時には影響しないが、970行のマージを慎重に行う必要がある。テストは既存のものでカバー可能。

### P2: `src/services/workflow.py` 分割 (1041行→複数ファイル)

**問題**: ワークフロー全ロジックが単一ファイルに集中。`WorkflowService` クラスが巨大。

**改善案**: 機能別に分割:
- `workflow_orchestrator.py` — メインのrun_cycle/run_gen_cycles
- `workflow_session.py` — セッション管理(start_session/finalize_session)
- `workflow_setup.py` — 初期化・環境セットアップ

**リスク**: 高。`workflow.py` は全機能のハブ。慎重な分割とテストが必要。

### P3: `src/services/jules_client.py` 整理 (658行)

**問題**: 監視ループ、プラン監査、問い合わせ応答が1クラスに混在。

**改善案**:
- `JulesClient` — API呼び出しのみ
- 監視ループを分離 (例: `SessionWatcher`)

### P4: QA系3usecase統合 (qa + uat + ux_auditor → 1ファイル)

**問題**: 3ファイルに分かれているが、すべてQA Graphのノードとして連携。

### 中期的課題 (未着手)

| 課題 | 優先度 | 備考 |
|---|---|---|
| `MemorySaver` → 永続チェックポインタ (`SqliteSaver`) | 🟠 中 | プロセス再起動で LangGraph 状態消失 |
| ノードメソッド内の `MasterIntegratorClient()` 直接生成 | 🟡 低 | `master_integrator_node` のみ残存 |
| `e2e/test_integration_graph.py` の `mock_global_sandbox` 参照 | 🟢 低 | 削除されたsandbox_nodeを参照。テスト修正が必要 |

---

## 関連ドキュメント

| ドキュメント | 説明 |
|---|---|
| [`plans/jules-mcp-architecture.md`](plans/jules-mcp-architecture.md) | MCP 化の全体設計・ツール定義・移行計画 |
| [`plans/jules-cleanup-analysis.md`](plans/jules-cleanup-analysis.md) | JulesClient の問題洗い出しとクリーンアップ詳細 |
| [`plans/handover.md`](plans/handover.md) | **このファイル** — 引き継ぎ資料 |
