# LoopX Advisor Mode Experiment Report 2026-07-27

Source boundary: this report uses public PR metadata, compact experiment
summaries, aggregate case outcomes, and protocol-level observations only. It
does not include raw task text, raw trajectories, raw verifier output, raw
model output, private run paths, credentials, local machine paths, or internal
operating context.

## 中文版

### 摘要

LoopX Advisor 模式的目标是用更强模型先做有限、只读的分析和建议，再
由成本更低的模型执行具体代码修改，从而在质量可接受的前提下降低总
Token 和成本。

PR #2406 已经打通了产品链路：它引入了 opt-in 的 LoopX Turn Advisor
模式、模型用量回执、验证边界和 qualification 覆盖。PR 中记录的一次
provider-backed 单 case qualification 显示了正向 Token 结果；但后续更大
范围实验没有复现稳定 uplift。

当前结论是：产品 plumbing 有价值，应保留为实验能力；但当前“执行前
Advisor 建议”协议尚未证明能在 benchmark 规模上稳定提升弱执行模型，也
不能证明稳定降本。

### 已实现的产品能力

PR #2406 "Codex/loopx advisor" 引入了以下公开产品能力：

- opt-in 的 LoopX Turn Advisor 模式；
- 在低成本执行模型前运行 bounded、read-only Advisor；
- 分阶段记录 Advisor 与 executor 的 provider token usage；
- fail-closed usage validation；
- CLI 输出预算约束；
- 确定性本地 smoke 覆盖；
- provider-backed paired qualification；
- 文档中将自动 Advisor 路径标记为 experimental。

PR 描述记录了一次单 case provider-backed qualification：两个质量 arm 都
通过，总 Token 从 263,472 降到 130,782，下降 50.36%。这个结果可以证明
链路具备可运行性和单点正向可能性，但不足以支持规模化收益结论。

### 更大范围实验结果

后续 20 case 实验没有支持“当前 Advisor 协议能提升弱执行模型”的假设。

| Arm | SWE-bench Verified 通过数 | 解读 |
| --- | ---: | --- |
| DeepSeek-V4-Flash direct | 11 / 20 | 本批次最高通过率 |
| DeepSeek-V4-Pro direct | 8 / 20 | Pro 在该 harness 下不是稳定强基线 |
| Pro Advisor plus Flash | 10 / 20 | 无 rescue，且出现 1 个 harm |

关键观察：

- Advisor rescue 数为 0，没有出现 Flash direct 失败但 Advisor plus Flash
  通过的 case。
- Advisor harm 数为 1，有一个 case 从 Flash direct 通过变成 Advisor 组合
  失败。
- Advisor 阶段有 6 / 20 个 case 没有产生可用 patch，主要原因是超时或
  缺少有效回执。
- 在三组都有可归因 token 数据的 13 个 case 上，Flash direct 和 Advisor
  plus Flash 都通过 10 / 13。
- 在同一批 13 个 case 上，Advisor 组合的成本代理高于 Flash direct，但
  通过数没有提升。

这些结果不支持“当前 Advisor wrapper 已经能节省成本或提升弱模型通过率”
这一广义结论。

### 调优探针结果

后续三 case 探针将 Advisor 路径调整为更结构化的输出和 fail-open 行为。
这个探针改善了低质量 Advisor 注入的安全性，但仍没有产生功能 rescue。

| Case 类型 | Advisor 处理 | 结果 |
| --- | --- | --- |
| 高置信度 Advisor packet applied | 接受 Advisor 输出 | 失败 |
| Advisor 超时并 fail-open | Executor 不注入 Advisor 继续执行 | 失败 |
| Advisor 超时并 fail-open | Executor 不注入 Advisor 继续执行 | 通过 |

聚合结果：

- 通过数：1 / 3；
- rescue 数：0；
- harm 数：0；
- 唯一通过来自 fallback，不是 applied Advisor packet 带来的成功。

这说明结构校验和 fail-open 行为值得保留，但仍不能证明 advice 本身提升了
executor。

### 诊断

主要问题不是“强模型永远无法帮助弱模型”，而是当前 Advisor 角色与最终
patch 的绑定太弱。

当前协议主要是在实现前给建议。弱执行模型写出 patch 后，没有强模型检查
最终 diff 是否遵守建议、是否保留关键不变量、是否引入已知回归。观察到的
失败中，Advisor 能识别有用方向，但 executor 仍可能落地出细节错误的
patch。

具体协议问题包括：

- Advisor 输出更像 recommendation，而不是 enforceable contract。
- root cause、evidence、patch steps、invariants、forbidden changes 和
  tests 没有足够强地分离。
- Executor 可以重复宽泛探索，并偏离 Advisor 建议。
- 缺少 post-patch review，无法检查最终实现是否违反 Advisor 约束。
- 超时或低置信度 Advisor 输出必须 fail-open，否则会伤害 executor。

### 建议的下一版协议

下一版 Advisor 应该是闭环控制，而不是一次性的执行前建议。

| 阶段 | 职责 |
| --- | --- |
| Executor checkpoint | 不编辑代码，只识别候选文件、不确定点和复杂度 |
| Advisor contract | 产出带证据和不变量的紧凑 implementation contract |
| Executor implementation | 在 contract 和 write scope 内修改 |
| Advisor patch review | 对照 contract 检查实际 diff |
| Bounded repair | 最多允许一次定向修复 |
| Independent verifier | 决定最终结果 |

Advisor contract 应至少包含：

- 目标文件和 symbol；
- root cause；
- evidence-backed facts；
- minimal implementation steps；
- 必须保持的不变量；
- 禁止修改的文件或行为；
- 正例和回归反例；
- 聚焦测试及预期观察；
- unresolved assumptions，且不能把这些假设当事实使用。

post-patch review 只应返回 approve 或精确 repair request，不应开启新的
宽泛规划回合。这样可以控制 Advisor 花费，同时修复“建议与最终实现脱节”
的问题。

### 结论边界

可以主张：

- LoopX 已有 experimental Advisor 产品路径；
- 该路径能分阶段记录 usage，而不保存 raw private model material；
- 单 case qualification 显示过正向 Token 结果；
- 更大范围实验没有发现当前协议下的稳定 rescue；
- fail-open 和结构化 Advisor 输出是必要的安全属性；
- 下一步最有希望的方向是 post-patch Advisor review 和 bounded repair。

不能主张：

- Advisor mode 已经在 benchmark 规模上提升通过率；
- Advisor mode 已经可靠降低总成本；
- Pro Advisor plus Flash 在该 harness 中强于 Flash direct；
- 当前执行前 Advisor 协议可以 default-on。

### 推荐状态

保持 Advisor mode experimental 和 default-off。保留产品 plumbing、usage
receipt 与验证边界，但不要把当前 pre-execution Advisor protocol 宣传为已
验证的成本节省功能。

下一阶段的可度量里程碑应要求：在 closed-loop Advisor contract 和
post-patch review 协议下，至少出现一个稳定 rescue 且 harm 为 0；之后再
进行更大范围 rerun，最后再提出产品结论。

## English Version

### Summary

LoopX Advisor mode was designed to use a stronger model for bounded advice and
a lower-cost model for implementation. The intended product outcome was stable
solution quality with lower total token usage and cost.

The product path was implemented and submitted in PR #2406, which added an
opt-in LoopX Turn Advisor mode, model-usage receipts, validation boundaries,
and qualification coverage. A one-case provider-backed qualification in that
PR showed a positive token result, but later broader experiments did not
reproduce stable uplift.

Current conclusion: the product plumbing is useful and should be kept as an
experimental capability, but the current pre-execution advice protocol does
not yet prove that Advisor reliably improves a weaker executor or lowers cost
at benchmark scale.

### Implemented Product Surface

PR #2406, "Codex/loopx advisor", introduced these public product surfaces:

- opt-in LoopX Turn Advisor mode;
- bounded read-only Advisor execution before lower-cost model execution;
- exact provider token usage capture for Advisor and executor phases;
- fail-closed usage validation;
- CLI output budget enforcement;
- deterministic local smoke coverage;
- provider-backed paired qualification;
- public documentation marking the automatic Advisor path as experimental.

The PR body recorded a one-case provider-backed qualification where both
quality arms passed and total tokens fell from 263,472 to 130,782, a 50.36%
reduction. That result is useful qualification evidence for the path, but it
is not enough to claim broad uplift.

### Broader Experiment Results

The later 20-case experiment did not support the hypothesis that the current
Advisor protocol improves the weaker executor.

| Arm | SWE-bench Verified pass count | Interpretation |
| --- | ---: | --- |
| DeepSeek-V4-Flash direct | 11 / 20 | Best observed pass rate in this batch |
| DeepSeek-V4-Pro direct | 8 / 20 | Pro was not a stable stronger baseline in this harness |
| Pro Advisor plus Flash | 10 / 20 | No rescue and one harm |

Key observations:

- Advisor rescue count was 0. No case showed Flash direct failing while
  Advisor plus Flash passed.
- Advisor harm count was 1. One case moved from Flash direct pass to Advisor
  combination fail.
- Advisor produced no usable patch in 6 of 20 cases, mostly from timeout or
  missing effective receipts.
- On the 13 cases with attributable token data across all arms, Flash direct
  and Advisor plus Flash both passed 10 of 13.
- On those same 13 cases, the Advisor combination had a higher cost proxy than
  Flash direct, while still not improving pass count.

These results invalidate the broad claim that the current Advisor wrapper
already saves cost or improves weaker-model pass rate.

### Tuned Probe Results

A later three-case probe changed the Advisor path toward structured output and
fail-open behavior. The probe improved safety against harmful Advisor
injection, but still did not produce a functional rescue.

| Case class | Advisor handling | Result |
| --- | --- | --- |
| High-confidence Advisor packet applied | Advisor output accepted | Failed |
| Advisor timeout with fail-open fallback | Executor ran without Advisor injection | Failed |
| Advisor timeout with fail-open fallback | Executor ran without Advisor injection | Passed |

Aggregate result:

- pass count: 1 / 3;
- rescue count: 0;
- harm count: 0;
- the only pass came from fallback, not from an applied Advisor packet.

This supports retaining structure validation and fail-open behavior, but does
not prove that the advice itself improves the executor.

### Diagnosis

The main failure is not that stronger models can never help weaker models. The
failure is that the current Advisor role is too weakly connected to the final
patch.

The current protocol mostly gives advice before implementation. After the weak
executor writes a patch, there is no strong-model check that the final diff
actually follows the advice, preserves required invariants, or avoids known
regressions. In observed failures, Advisor could identify useful direction,
but the executor could still implement a subtly wrong patch.

Several protocol issues contributed:

- Advisor output was closer to a recommendation than an enforceable contract.
- Root cause, evidence, patch steps, invariants, forbidden changes, and tests
  were not separated strongly enough.
- The executor could repeat broad exploration and drift away from the advice.
- There was no post-patch review of whether the implementation violated the
  Advisor's constraints.
- Timeout and low-confidence Advisor output needed fail-open handling to avoid
  harming the executor.

### Recommended Next Protocol

The next Advisor iteration should be a closed loop rather than a single
pre-execution suggestion.

| Stage | Responsibility |
| --- | --- |
| Executor checkpoint | Identify candidate files, uncertainty, and complexity without editing |
| Advisor contract | Produce a compact implementation contract with evidence and invariants |
| Executor implementation | Modify only within the contract and write scope |
| Advisor patch review | Review the actual diff against the contract |
| Bounded repair | Allow at most one targeted executor repair |
| Independent verifier | Decide the final outcome |

The Advisor contract should include:

- target files and symbols;
- root cause;
- evidence-backed facts;
- minimal implementation steps;
- invariants that must remain true;
- forbidden files or behaviors;
- positive and regression examples;
- focused tests and expected observations;
- unresolved assumptions that must not be treated as facts.

The post-patch review should return only an approval or a precise repair
request. It should not open a new broad planning round. This keeps Advisor
spend bounded while addressing the observed gap between advice and final
implementation.

### Claim Boundary

It is valid to claim that:

- LoopX has an experimental Advisor product path;
- the path can capture phase-specific usage without storing raw private model
  material;
- a one-case qualification showed a positive token result;
- broader experiments found no stable rescue under the current protocol;
- fail-open and structured Advisor output are necessary safety properties;
- the next promising direction is post-patch Advisor review and bounded repair.

It is not yet valid to claim that:

- Advisor mode improves pass rate at benchmark scale;
- Advisor mode reliably lowers total cost;
- Pro Advisor plus Flash is stronger than Flash direct in the tested harness;
- the current pre-execution advice protocol is ready for default-on use.

### Recommended Status

Keep Advisor mode experimental and default-off. Preserve the product plumbing,
usage receipts, and validation boundaries. Do not promote the current
pre-execution Advisor protocol as a proven cost-saving feature.

The next measurable milestone should require at least one stable rescue with
zero harm under a closed-loop Advisor contract and post-patch review protocol,
followed by a broader rerun before making product claims.
