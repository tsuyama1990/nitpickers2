# ファイル統合計画: src/ の複雑さを解消

現状: **89ファイル** (templates除く)

## Step 1: デッドコード削除 (即座に安全)

| ファイル | 理由 | アクション |
|----------|------|-----------|
| [`src/nodes/state_machine.py`](src/nodes/state_machine.py) | `RouterEngine` はどこからも import されていない | **削除** |
| [`src/domain_models/uat_execution_state.py`](src/domain_models/uat_execution_state.py) | たった16行、1モデルのみ | 統合先へ移動 |
| [`src/domain_models/refactor.py`](src/domain_models/refactor.py) | 22行、1モデル | 統合先へ移動 |

## Step 2: domain_models/ 統合 (17→4ファイル)

### 現状

```
domain_models/ (17 files)
├── architecture.py    (23行) — SystemArchitecture
├── config.py         (17行) — DispatcherConfig
├── critic.py         (17行) — CriticResult
├── execution.py      (33行) — ConflictRegistryItem, ConflictResolutionSchema, UatAnalysis
├── file_ops.py       (39行) — FileArtifact, FileCreate, FileOperation, FilePatch
├── fix_plan_schema.py (23行) — FixPlanSchema
├── manifest.py       (76行) — CycleManifest, ProjectManifest
├── multimodal_artifact_schema.py (28行) — MultiModalArtifact
├── observability_config.py (51行) — ObservabilityConfig
├── refactor.py       (22行) — GlobalRefactorResult
├── review.py         (69行) — AuditorReport, AuditResult, PlanAuditResult, ReviewIssue
├── spec.py           (40行) — CyclePlan, Feature, StructuredSpec, TechnicalConstraint
├── tracing.py        (36行) — LangSmithConfig, TracingMetadata
├── uat_execution_state.py (16行) — UatExecutionState
├── ux_audit_report.py (22行) — UXAuditReport, UXViolation
├── verification_schema.py (62行) — StructuralGateReport, VerificationResult
└── __init__.py       (47行) — re-exports
```

### 統合先 (4ファイル)

| 新ファイル | 含めるモデル | 合計行数 |
|-----------|-------------|---------|
| `models.py` | AuditResult, AuditorReport, PlanAuditResult, ReviewIssue, CriticResult, GlobalRefactorResult, ConflictRegistryItem, ConflictResolutionSchema, FileArtifact, FileCreate, FileOperation, FilePatch, CycleManifest, ProjectManifest, FixPlanSchema, UatAnalysis, UatExecutionState, UXAuditReport, UXViolation, StructuralGateReport, VerificationResult, SystemArchitecture, CyclePlan, Feature, StructuredSpec, TechnicalConstraint | ~380行 |
| `config.py` | DispatcherConfig, ObservabilityConfig, LangSmithConfig, TracingMetadata | ~104行 |
| `multimodal_artifact_schema.py` | MultiModalArtifact (変更なし) | 28行 |

**手順:**
1. [`src/domain_models/models.py`](src/domain_models) を作成 (既存16ファイルの全モデルを集約)
2. [`src/domain_models/config.py`](src/domain_models/config.py) を設定系モデルに絞って上書き
3. `__init__.py` を新しい `models.py` からの re-export に変更
4. 古い16ファイルを削除
5. 各 `from src.domain_models.xxx import YYY` を確認 — 多くは `from src.domain_models import YYY` で済む

## Step 3: nodes/ 統合 (11→5ファイル)

### 現状

```
nodes/ (11 files)
├── architect.py      (185行) — ArchitectNodes
├── architect_critic.py (81行) — ArchitectCriticNodes
├── auditor.py        (20行) — AuditorNodes
├── base.py           (25行) — BaseNode (architect.pyのみ使用)
├── coder.py          (34行) — CoderNodes
├── coder_critic.py   (76行) — CoderCriticNodes
├── global_refactor.py (41行) — GlobalRefactorNodes
├── integration_fixer.py (59行) — IntegrationFixerNodes
├── master_integrator.py (41行) — MasterIntegratorNodes
├── routers.py        (88行) — 6 router関数 (そのまま維持)
├── state_machine.py  (28行) — RouterEngine (DEAD)
└── __init__.py       (27行)
```

### 統合先 (5ファイル)

| 新ファイル | 含めるクラス | 合計行数 | 備考 |
|-----------|-------------|---------|------|
| `architect.py` | BaseNode + ArchitectNodes (base.pyをinline化) | ~210行 | 変更なし＋BaseNode inline |
| `critic_nodes.py` | ArchitectCriticNodes, AuditorNodes, CoderCriticNodes | ~177行 | 3つのcritic/auditorを統合 |
| `coder_nodes.py` | CoderNodes | 34行 | 変更なし |
| `fixer_nodes.py` | GlobalRefactorNodes, IntegrationFixerNodes, MasterIntegratorNodes | ~141行 | 3つのfixer/integratorを統合 |
| `routers.py` | check_coder_outcome, route_committee, route_qa, route_architect_session, route_architect_critic, route_merge | 88行 | 変更なし |

**削除:** `state_machine.py`, `base.py`, `architect_critic.py`, `auditor.py`, `coder_critic.py`, `global_refactor.py`, `integration_fixer.py`, `master_integrator.py`

**手順:**
1. `nodes/__init__.py` を新しいファイル構成に合わせて更新
2. `graph_nodes.py` の import を更新
3. 古いファイルを削除

## Step 4: services/ スリム化 (低リスクから)

### デッドコード候補 (要確認)

サービスクラスの使用状況は別途確認が必要。以下のファイルは「使われていない可能性が高い」:

- [`src/services/post_mortem.py`](src/services/post_mortem.py)
- [`src/services/rca_service.py`](src/services/rca_service.py)
- [`src/services/initial_coder.py`](src/services/initial_coder.py)
- [`src/services/diagnostics.py`](src/services/diagnostics.py)

### 統合候補

| 分類 | 現状 | 統合案 |
|------|------|--------|
| `git/` | 6ファイル + `git_ops.py` | `git_ops.py` に全Git操作を集約（~400行） |
| `project_setup/` | 3ファイル | 1ファイルに統合（~350行） |
| `QA関連` | `qa_usecase.py` + `uat_usecase.py` + `ux_auditor_usecase.py` | 3→1 or 3→2 |
| `jules/` | `jules_client.py` + `jules/context_builder.py` + `jules/git_context.py` | 1ファイルに統合 |

## 期待される効果

| 段階 | 削減ファイル数 | 残りファイル数 |
|------|---------------|--------------|
| 現状 | - | 89 |
| Step 1 (デッドコード) | -1 | 88 |
| Step 2 (domain_models) | -13 | 75 |
| Step 3 (nodes) | -6 | 69 |
| Step 4 (services) | -10〜15 | 54〜59 |
| **合計** | **-30〜35** | **54〜59** |

## リスクと注意点

1. **domain_models の統合が最も安全**: pure Pydantic models, importの書き換えのみ
2. **nodes の統合は中程度**: 薄いラッパーばかりなので安全
3. **services の統合が最も risky**: ビジネスロジックを含む。事前に使用状況調査が必要
4. **テストは各Stepごとに実行**して回帰がないことを確認

## 実行順序（推奨）

1. Step 1: デッドコード削除 (安全)
2. Step 2: domain_models 統合 (安全)
3. Step 3: nodes 統合 (中程度)
4. Step 4: services スリム化 (事前調査後に判断)
