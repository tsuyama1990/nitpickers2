# JulesClient 「ドカン工事」コード 徹底分析

## 概要

[`JulesClient`](src/services/jules_client.py)（726行）は本来「Jules API クライアント」だが、複数の unrelated concern が混入し、単一責任原則に違反している。加えて過去のリファクタリングの残骸（デッドコード、不整合、遅延インポート）が散在する。

---

## 問題一覧

### Category A: 異責務の混入（最も深刻）

#### A1. Master Integrator（ローカルLLM）の混入

| 箇所 | コード | 問題 |
|---|---|---|
| [L680-686](src/services/jules_client.py:680) | `create_master_integrator_session()` | UUID生成だけ。Jules API 無関係 |
| [L688-725](src/services/jules_client.py:688) | `send_message_to_session()` | `litellm.acompletion()` を直接呼ぶローカルLLM処理。Jules API 無関係 |
| [import L12](src/services/jules_client.py:12) | `import litellm` | この import は Master Integrator のためだけに存在 |

**コンシューマ**: [`IntegrationUsecase`](src/services/integration_usecase.py:80-81) のみ。

**処置**: `IntegrationUsecase` に移動。`MasterIntegratorClient` として分離。

**結果**: `JulesClient` から `litellm` 依存が消える。

#### A2. Git操作の混入

| 箇所 | コード | 問題 |
|---|---|---|
| [L56](src/services/jules_client.py:56) | `self.git = GitManager()` | JulesClient が Git を内包 |
| [L609-632](src/services/jules_client.py:609) | `get_latest_branch_commit()` | Jules API と無関係な Git 操作。内部で `self.git` を使わず新規 `GitManager()` 生成 |

**コンシューマ**: [`CoderUseCase`](src/services/coder_usecase.py:115,520-545), [`BaseJulesUseCase`](src/services/base_jules_usecase.py:113-118)

**処置**: `GitManager` の責務。呼び出し元が Git を注入して使う形に。

#### A3. プロンプト構築の混入

| 箇所 | 問題 |
|---|---|
| [L80](src/services/jules_client.py:80) | `self.context_builder = JulesContextBuilder(self.git)` |
| [L81](src/services/jules_client.py:81) | `self.git_context = JulesGitContext(self.git)` |

`JulesClient` が context builder と git context を内包しているが、これらは**利用側（`CoderUseCase` 等）の責務**。

#### A4. ManagerAgent / PlanAuditor の混入

| 箇所 | 問題 |
|---|---|
| [L71](src/services/jules_client.py:71) | `self.manager_agent = get_manager_agent()` |
| [L73-78](src/services/jules_client.py:73) | `self.plan_auditor = PlanAuditor()` |

Jules の問い合わせ応答に ManagerAgent を使い、プラン承認に PlanAuditor を使うのは**利用側のオーケストレーションロジック**。JulesClient がこれらを持つことで、`JulesClient` のインスタンス化が重くなる。

---

### Category B: デッドコードと不整合

#### B1. 完全なデッドコード

| 箇所 | コード |
|---|---|
| [L83-85](src/services/jules_client.py:83) | `_sleep()` — どこからも呼ばれていない (`self._sleep()` の使用箇所: 0) |
| [L120-122](src/services/jules_client.py:120) | `if not getattr(self.sdk_client, "_base_client", None) and ...: pass` — 何もしない条件分岐 |
| [L193-197](src/services/jules_client.py:193) | `_unused = tracing_config` — 代入のみ。未使用 |

#### B2. APIキー処理の不整合

[L59-69](src/services/jules_client.py:59):

```python
api_key = settings.JULES_API_KEY.get_secret_value() or os.getenv("JULES_API_KEY")

if not api_key and "PYTEST_CURRENT_TEST" not in os.environ:
    errmsg = "Missing JULES_API_KEY or ADC credentials."
    raise JulesSessionError(errmsg)

self.sdk_client = AsyncJulesClient(
    api_key=api_key or "",   # ← SDK は空文字で ValueError を raise する
    base_url=base_url,
)
```

- テスト時: API key なしでもエラーにならないが、`AsyncJulesClient("")` で SDK 内部で `ValueError` が発生
- エラー型が不統一: `JulesSessionError` vs `ValueError`

#### B3. 遅延インポート

4箇所の `from ... import ...` が関数/メソッド内部にある：

| 箇所 | インポート |
|---|---|
| [L51](src/services/jules_client.py:51) | `from jules_agent_sdk import AsyncJulesClient` |
| [L76](src/services/jules_client.py:76) | `from src.services.plan_auditor import PlanAuditor` |
| [L373](src/services/jules_client.py:373) | `from jules_agent_sdk.models import SessionState` |
| [L646](src/services/jules_client.py:646) | `from jules_agent_sdk.models import SessionState`（重複） |

トップレベルに移動可能なはず。

#### B4. `self.console` vs モジュール `console`

[L26](src/services/jules_client.py:26): `console = Console()`（モジュールレベル）
[L55](src/services/jules_client.py:55): `self.console = Console()`（インスタンス）
→ 2つの独立した `Console` インスタンスが存在。`self.console` に統一すべき。

#### B5. `plan_auditor` への不要な `getattr`

[L526](src/services/jules_client.py:526):
```python
plan_auditor = getattr(self, "plan_auditor", None)
```
`plan_auditor` は `__init__` で常に設定されるので、`self.plan_auditor` で直接アクセスできる。

---

### Category C: 設計上の問題

#### C1. Fire-and-Forget パターン

以下のコードは `run_session()` を呼ぶが `wait_for_completion()` を呼ばない：

| 呼び出し元 | ファイル |
|---|---|
| [`IntegrationFixerNodes.integration_fixer_node()`](src/nodes/integration_fixer.py:36) | Jules セッションを起動して即 return |
| [`RefactorUsecase.execute()`](src/services/refactor_usecase.py:108) | Jules セッションを起動して即 return |

**問題**: セッションの完了を待たない。結果（PR URL 等）を取得できない。エラー検知できない。

**確認結果 (2026-06-27)**: **バグ**。`wait_for_completion()` の呼び忘れ。

#### C2. `IntegrationFixerNodes` と `IntegrationUsecase` の二重構造

両方ともコンフリクト解決を行うが、アプローチが異なる：

| コンポーネント | 方法 | Jules 使用 |
|---|---|---|
| [`IntegrationFixerNodes`](src/nodes/integration_fixer.py) | `run_session()` で Jules API 直接呼び出し | ✅ Jules API |
| [`IntegrationUsecase`](src/nodes/master_integrator.py) → 実際は `MasterIntegratorNodes` | `send_message_to_session()` で `litellm.acompletion` | ❌ ローカルLLM |

同じノード名に「Integration Fixer」と「Master Integrator」が混在し、それぞれ全く異なるバックエンドを使っている。

#### C3. 型アノテーションの不統一

`JulesClient` を受け取るコンシューマの型がバラバラ：

| ファイル | 型 |
|---|---|
| [`CoderUseCase`](src/services/coder_usecase.py:46) | `JulesClient` |
| [`SelfCriticEvaluator`](src/services/self_critic_evaluator.py:15) | `Any` |
| [`CoderCriticNodes`](src/nodes/coder_critic.py:12) | `Any` |
| [`IntegrationFixerNodes`](src/nodes/integration_fixer.py:13) | `Any` |
| [`ArchitectCriticNodes`](src/nodes/architect_critic.py:13) | `Any` |

#### C4. 複数箇所での `JulesClient()` 直接生成

`new JulesClient()` が散在：

| 箇所 | ファイル |
|---|---|
| [L134](src/graph_nodes.py:134) | `JulesClient()` |
| [L141](src/graph_nodes.py:141) | `JulesClient()` |
| [integration_usecase.py:19](src/services/integration_usecase.py:19) | `JulesClient()`（デフォルト引数） |
| [refactor_usecase.py:18](src/services/refactor_usecase.py:18) | `JulesClient()`（デフォルト引数） |
| [master_integrator.py:13](src/nodes/master_integrator.py:13) | `JulesClient()`（デフォルト引数） |
| [workflow.py:42,693,713](src/services/workflow.py:42) | `JulesClient()`（fallback） |

DI コンテナ [`ServiceContainer`](src/service_container.py:16) があるにも関わらず、それをバイパスして直接生成している。`ServiceContainer` で `jules: JulesClient | None = None` とオプショナルになっているのも問題。

---

## クリーンアップ計画（Phase 0 詳細）

### Step 0: Protocol 定義

```python
# src/services/agent_protocol.py (NEW)
class AgentProtocol(Protocol):
    """Jules に限らないコードエージェントの抽象インターフェース"""
    async def run_session(...) -> dict[str, Any]: ...
    async def continue_session(...) -> dict[str, Any]: ...
    async def wait_for_completion(...) -> dict[str, Any]: ...
    async def get_session_state(...) -> str: ...
    async def list_activities(...) -> list[Any]: ...
    async def send_message(...) -> None: ...
    async def approve_plan(...) -> None: ...
```

### Step 1: Master Integrator の分離

```
移動前:
  JulesClient.create_master_integrator_session()  → IntegrationUsecase へ移動
  JulesClient.send_message_to_session()           → IntegrationUsecase へ移動
  import litellm                                  → JulesClient から削除

移動後:
  IntegrationUsecase が内部で MasterIntegratorClient を持つ
  JulesClient から litellm 依存が完全に消える
```

### Step 2: Git 操作の分離

```python
# JulesClient から削除
- self.git = GitManager()           # L56
- get_latest_branch_commit()        # L609-632

# 使う側が GitManager を inject する形に
class CoderUseCase:
    def __init__(self, jules_client: JulesClient, git: GitManager):
        ...
```

### Step 3: ManagerAgent / PlanAuditor の分離

```python
# JulesClient.__init__ 引数から削除
# 利用側（CoderUseCase, AuditOrchestrator）が管理

# JulesClient は純粋に SDK ラッパーに
class JulesClient:
    def __init__(self):
        self.sdk_client = AsyncJulesClient(...)
        # manager_agent, plan_auditor は持たない
```

### Step 4: デッドコード削除

- `_sleep()` メソッド削除
- L120-122 の `if ... pass` 削除
- `_unused = tracing_config` 削除（または実際に使う）
- `self.console` を `console`（モジュールレベル）に統一
- 遅延インポートをトップレベルに移動
- 重複インポート `from jules_agent_sdk.models import SessionState` を統合

### Step 5: 型の統一

全コンシューマで `JulesClient` → `AgentProtocol` に変更。これにより将来的な MCP 実装との差し替えが型安全になる。

### Step 6: DI の整理

`ServiceContainer` で `jules: JulesClient | None = None` → `jules: JulesClient`（必須）に変更。`JulesClient()` の直接生成を全て DI 経由に統一。

---

## 影響範囲まとめ

| Step | 変更ファイル | 影響度 |
|---|---|---|
| 0: Protocol | 新規: `src/services/agent_protocol.py` | 新規のみ |
| 1: Master Integrator 分離 | `jules_client.py`, `integration_usecase.py` | 中 |
| 2: Git 分離 | `jules_client.py`, `coder_usecase.py`, `base_jules_usecase.py` | 中 |
| 3: ManagerAgent/PlanAuditor 分離 | `jules_client.py`, `audit_orchestrator.py`, `coder_usecase.py` | 中 |
| 4: デッドコード削除 | `jules_client.py` | 低 |
| 5: 型統一 | 全コンシューマ（約15ファイル） | 低〜中 |
| 6: DI 整理 | `service_container.py`, `graph_nodes.py`, 各 usecase | 低 |
