# 深層複雑性分析

## 現状: 66ファイル / 12,000+行

表面的なファイル数は減ったが、内部に複雑性を生む設計パターンが残っている。

---

## 発見1: Single-Implementation Protocols (YAGNI)

3つのProtocolが定義されているが、いずれも実装が1つ以下：

| Protocol | 実装 | 状態 |
|----------|------|------|
| `IGraphNodes` ([`src/interfaces.py`](src/interfaces.py:7)) | `CycleNodes` のみ | 使用中だが過剰 |
| `AgentProtocol` ([`src/services/agent_protocol.py`](src/services/agent_protocol.py:11)) | `JulesClient` のみ | 102行, 10メソッド, 実装1つ |
| `IWorkflowOrchestrator` ([`src/interfaces.py`](src/interfaces.py:42)) | **なし** | デッドコード |

**問題**: Protocol＋実装の分離がコード量を2倍にしている。将来の差し替え予定がないなら、Protocolを削除して直接依存すべき。

**改善**: `IGraphNodes` + `IWorkflowOrchestrator` in `interfaces.py` → 削除。`AgentProtocol` in `agent_protocol.py` → 削除 or 縮小。

---

## 発見2: Git mixin over-engineering

[`GitManager`](src/services/git_ops.py) が5つのmixinを継承：

```
git/
├── __init__.py     (exists?)
├── base.py         (86 lines)  — BaseGitManager
├── branching.py    (87 lines)  — GitBranchingMixin
├── checkout.py     (145 lines) — GitCheckoutMixin
├── merging.py      (127 lines) — GitMergingMixin
├── state.py        (56 lines)  — GitStateMixin
└── worktree.py     (??)        — worktree (使われていない可能性)
```

6ファイル / 500行以上。1ファイルに統合すれば `git_ops.py` に収まる。テスト容易性のためにsplitする必要はない（すべてGitコマンドをラップしただけ）。

---

## 発見3: `src/state.py` のレガシー複雑性

[`CycleState`](src/state.py:146) に以下の過剰構造がある：

**1) 6つのサブステート分割（約70行）**
```
CommitteeState, SessionPersistenceState, AuditState,
TestState, UATState, ConfigurationState
```
これらは常に一緒に使われる。分割のメリットが実質ない。

**2) レガシープロパティブリッジ（約180行, 186-369行目）**
```python
@property
def current_auditor_index(self) -> int:
    return self.committee.current_auditor_index

@current_auditor_index.setter
def current_auditor_index(self, value: int) -> None:
    self.committee.current_auditor_index = value
```

これが延々と40回繰り返されている。すべて `state.committee.xxx` で直接アクセス可能。

**3) model_validator(mode="before") のレガシーキーワードマッピング（56行）**
フラットなキーワード引数をサブステートに振り分ける処理。新しいコードはサブステートを直接指定するので不要。

**4) レガシートップレベルフィールド（9フィールド, 175-184行目）**
`plan`, `code_changes`, `loop_count`, `correction_history` など、もう使われていないかサブステートに移動可能なフィールド。

---

## 発見4: `src/config.py` の肥大化（737行）

設定ファイルとしては異常に大きい。以下のセクションを含む：
- 環境変数ロード (`_load_env`, `_validate_env_value`)
- Settingsクラス（400+行）
- テンプレート読み込み
- パス管理
- ファイル名定数

設定とユーティリティが混ざっている。

---

## 発見5: `ServiceContainer` の存在意義が薄い

[`ServiceContainer`](src/service_container.py) は単なる dataclass で全依存関係を束ねているだけ。実際のDIはほとんどの場所で個別に行われている。

---

## 優先順位提案

| # | タスク | 削減行数 | リスク | 効果 |
|---|--------|---------|-------|------|
| P0 | Protocol類の削除 (interfaces.py, agent_protocol.py) | ~150行 | 低 | 即効, 概念削減 |
| P0 | レガシープロパティブリッジ削除 | ~180行 | 低 | 使っている場所を直接アクセスに置き換え |
| P1 | Git mixinの統合 (6→1ファイル) | 0 (ファイル削減) | 中 | git_ops.pyに統合 |
| P1 | workflow.pyの分割 or リファクタ | 要分析 | 高 | 最大のファイル |
| P2 | ServiceContainerの評価・削除 | ~30行 | 低 | シンプルに |
| P2 | state.pyサブステート統合 | ~100行 | 中 | CycleStateをフラットに |

---

## 状態遷移図: CycleStateサブステート統合案

```mermaid
flowchart LR
    subgraph Before["現在: CycleState + 6 sub-states + 40 property bridges"]
        A[CycleState] --> B[committee: CommitteeState]
        A --> C[session: SessionPersistenceState]
        A --> D[audit: AuditState]
        A --> E[test: TestState]
        A --> F[uat: UATState]
        A --> G[config: ConfigurationState]
        A -.-> H[40 property bridges]
    end

    subgraph After["統合案: フラットなCycleState"]
        I[CycleState]
        I --> J[current_auditor_index: int]
        I --> K[jules_session_name: str | None]
        I --> L[audit_result: AuditResult | None]
        I --> M[test_logs: str]
        I --> N[...]
    end

    Before -. 冗長な委譲 .-> After
```

## 実行戦略

P0（Protocol削除 + プロパティブリッジ削除）が最もリスク低く効果大。P1以降は別セッションで。
