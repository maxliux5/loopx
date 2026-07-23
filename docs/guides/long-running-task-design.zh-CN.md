# LoopX 长任务设计

这篇文档面向第一次接触 LoopX 的用户和贡献者，解释一个 Agent 任务怎样从
一次对话延伸成可以持续数天、数周，甚至数百小时的工作循环。

这里的“长任务”不要求同一个模型进程连续存活。它指一个目标可以跨越多次
Agent 执行、会话重启、人员决策和外部等待，并且下一轮仍能回答：

1. 最终目标是什么？
2. 当前阶段做到什么才算完成？
3. 现在应该做哪一件事？
4. 上一轮产生了什么可检查的证据？
5. 什么时候继续、等待、重新规划或请人决定？

LoopX 的核心做法是把这些答案保存在持久化控制面中。Codex、Claude Code、
OpenCode 或其他 Agent runtime 每次只执行一个有边界的工作段。

## 为什么长任务容易偏移

短任务通常可以写进一个 prompt：

> 修复这个函数并运行测试。

长期目标会不断产生新信息：

- 用户修正了方向；
- 一个假设被实验推翻；
- PR 正在等待 review；
- CI 或外部系统暂时不可用；
- 多个 Agent 同时认领不同任务；
- 旧计划已经完成，但最终结果还没有实现；
- 对话太长，早期上下文被压缩或丢失。

如果目标、决定和证据只存在于聊天记录中，后续 Agent 很容易发生四类偏移。

### 目标偏移

Agent 逐渐把“完成当前 Todo”当成“完成整个项目”。

### 路线偏移

原来的方案已经失效，Agent 仍重复相同操作；或者 Agent 悄悄换了一条路线，
却没有记录原因。

### 状态偏移

旧会话、旧调度结果或另一个 Agent 的状态被当成当前事实。

### 完成偏移

Agent 声称完成，但没有测试、产物、外部状态或其他独立证据支持。

长任务设计要做的事情，是让这些偏移能够被发现、拒绝或纠正。

## 整体模型

```text
长期 Goal
   │
   ▼
当前 Vision + Acceptance
   │
   ▼
Todo / Frontier / Gate / Claim
   │
   ▼
LoopX 决定本轮动作
   │
   ▼
Agent runtime 执行一个 bounded Turn
   │
   ▼
独立验证 + Evidence writeback
   │
   ├─ 验收满足 ─────▶ 结束当前阶段
   │                    │
   │                    └─ Goal 仍 active ─▶ 创建 Successor Vision
   │
   ├─ 证据不足 ─────▶ 保持目标 active，创建 Successor Todo
   │
   ├─ 路线失效 ─────▶ Replan + Path Delta
   │
   └─ 需要人判断 ───▶ User Gate / Wait / Safe Fallback
```

LoopX 管理这个循环中的长期状态。Agent runtime 负责读取文件、调用工具、修改
代码或生成产物。

## 第一层：Goal、Vision 和 Acceptance

### Goal：长期目标

Goal 描述最终希望达到的结果，例如：

> 让一个软件维护 Agent 可以长期推进项目，并在目标、证据或权限发生变化时
> 安全地纠正路线。

Goal 可以跨越很多个 Turn。它的生命周期长于单个 Todo、单个 Agent 会话和单个
PR。

### Vision：当前阶段方向

Goal 往往太大，无法直接执行。Vision 把它压缩成某个 Agent 当前阶段的方向：

```text
vision_summary:
建立长任务中的目标偏移检测和重新规划闭环。

role_scope:
负责控制面规则与验证；不改变 benchmark 评分或生产权限。
```

Vision 应保持简短。详细背景放在文档或 evidence artifact 中，控制面只保留当前
执行所需的摘要和引用。这样下一轮可以快速恢复方向，不必重新阅读完整聊天记录。

### Acceptance：验收标准

Acceptance 回答“做到什么才算完成”：

```text
acceptance_summary:
错误的 Goal、Agent 或 Todo 绑定会被拒绝；阶段结束后仍能根据证据选择结束、
继续或重新规划，并有自动化测试覆盖。
```

Acceptance 是防止“忙了很久就算完成”的关键边界。它应该描述结果和证明方式，
避免只写“继续优化”“持续关注”之类无法判断的目标。

## 第二层：Todo、Frontier 和 Claim

### Todo：一个可执行步骤

Todo 是当前可以交给 Agent 的具体工作，例如：

- 修复一次错误的任务选择；
- 为一个状态转换补充测试；
- 调查 CI 失败原因；
- 等待外部 PR 合并；
- 根据新证据更新方案。

一个 Todo 只代表长期目标中的一小步。

### Frontier：当前可推进边界

Frontier 是当前真正可执行或需要决策的工作集合：

```text
可以立即执行：修复 Agent 身份校验
等待外部条件：CI 完成后重新验证
需要用户判断：是否允许发布
已被其他 Agent 认领：运行 benchmark
```

LoopX 根据 Frontier 选择下一步，而不是简单取 Todo 列表第一项。

### Claim：任务认领

多 Agent 场景中，`claimed_by` 表示谁正在负责 Todo。Claim 可以减少重复工作和
相互覆盖：

```text
todo-fix-binding     claimed_by=coding-agent
todo-run-benchmark   claimed_by=evaluation-agent
```

每个 Agent 看到的是自己的可执行 Frontier。另一个 Agent 正在等待，不能自动让
当前 Agent 也进入等待。

## 第三层：Turn

Turn 是一次完整、有边界的 Agent 工作循环：

```text
Decide → Prepare → Execute → Validate → Commit
```

### 1. Decide

LoopX 根据实时状态决定本轮动作：

- 执行一个 Todo；
- 重新规划；
- 修复控制面；
- 等待外部条件；
- 请求用户决策；
- 安静停止，不消耗执行资源。

### 2. Prepare

执行前固定本轮身份和边界：

- `goal_id`：服务哪个长期目标；
- `agent_id`：由哪个 Agent 执行；
- `todo_id`：处理哪个工作单元；
- workspace：在哪个仓库或工作树工作；
- capability：当前环境真实具备哪些能力；
- acceptance：本轮需要证明什么。

如果恢复的会话与当前 Goal、Agent 或 Todo 不一致，Turn 应拒绝继续。

### 3. Execute

Host adapter 启动 Codex CLI、OpenCode 或其他 Agent runtime，只执行当前
bounded segment。Agent 可以读写文件、运行命令或访问获准的外部资源，但不能
自行改变控制面中的权限和完成标准。

### 4. Validate

验证器检查真实结果，例如：

- 测试是否通过；
- 文件是否真的产生；
- API 或外部状态是否满足条件；
- 只读任务是否得到可追溯的结论；
- 改动是否符合工作树、权限和公开边界。

Agent 进程退出码为零，只能说明进程正常结束。它不能单独证明工作完成。

### 5. Commit

验证通过后，LoopX 才把结果写回长期状态：

- 记录 compact evidence；
- 更新或完成 Todo；
- 创建 Successor Todo；
- 更新 Vision checkpoint；
- 消耗本轮 quota；
- 决定下一次调度。

这里的 Commit 指 Turn 结果进入持久化状态，不一定是 Git commit。

## 第四层：Evidence、Checkpoint 和 Successor

### Evidence：可检查的完成依据

常见 Evidence 包括：

- 修改后的文件或 commit；
- 测试、编译或 verifier 结果；
- PR、issue 或公开资料链接；
- 外部系统的状态引用；
- blocker 和恢复条件；
- 新旧方案之间的差异记录。

长期状态保存紧凑结论和引用。原始日志、完整 transcript、凭证、私有路径和
benchmark 原始材料不应复制进公共状态。

### Checkpoint：每个实质阶段都要重新对齐

一次有实质产出的 Turn 结束时，需要回答：

- 当前 Vision 是否仍然适用？
- Acceptance 是否已经满足？
- 如果没有满足，还缺什么证据？
- 应该创建后续 Todo、重新规划，还是等待？

Checkpoint 可以记录：

- `patched`：Vision 已更新；
- `unchanged_with_reason`：Vision 仍适用，并说明原因；
- `retired_or_superseded`：当前方向已结束或被替代；
- `missing_required`：本轮没有完成必要的 Vision 判断。

缺失 Checkpoint 会成为控制面中的 acceptance gap，而不会只留下一条聊天提醒。

### Successor：显式的下一步

Todo 完成后，如果 Acceptance 尚未满足，应创建 Successor Todo。

一个阶段 Vision 完成后，如果长期 Goal 仍然 active，应建立 Successor Vision。

```text
Goal：验证长任务可以持续纠偏

Vision 1：实现目标和执行身份校验
  └─ Successor Vision 2：运行多轮回归并统计偏移
       └─ Successor Vision 3：根据失败样本修正规则
```

这条规则防止一个局部阶段完成后，长期任务静默停止。

## 第五层：Replan 和 Path Delta

路线需要改变时，LoopX 触发 Replan。常见触发条件包括：

- 当前 Frontier 已经耗尽，但 Acceptance 仍未满足；
- monitor 多次检查仍没有实质变化；
- 用户修改了目标或验收标准；
- 新证据推翻了原假设；
- handoff 已结束，却没有 Successor；
- 当前 Todo 链太长，需要重新压缩方向。

Replan 需要产生实际变化。只有“已重新规划”的 ACK 不能清除义务。

### Path Delta：记录路线为什么变化

Path Delta 用结构化方式保存回看结果：

```text
prior_assumption:
继续等待，外部检查会自动完成。

observed_reality:
两次有边界的检查都没有产生状态变化。

retained:
保留已验证的目标和现有 evidence。

changed:
创建一个调查阻塞原因的可执行 Todo。

stopped:
停止把重复轮询当作进展。
```

Path Delta 允许路线变化，同时保持目标、证据和决策过程可追溯。

## Gate、Safe Fallback、Monitor 和 Quota

### User Gate

某些决定必须由人完成，例如：

- 是否发布；
- 是否使用高风险权限；
- 是否改变产品目标；
- 是否接受成本较高的实验。

Gate 应写清楚阻塞哪条路线、需要回答什么、用户暂时不回答时还有哪些安全工作。

### Safe Fallback

主路线被 Gate 阻塞时，不依赖该决定的安全工作可以继续。例如发布正在等待批准，
Agent 仍可以运行本地测试或整理公开文档。

Fallback 的进展不能被写成主路线已经解除阻塞。

### Monitor

Monitor Todo 只观察外部变化，例如 CI、PR 或远程任务状态。没有变化时，它应该
安静等待；发现变化后，再恢复对应 Successor。

### Quota

Quota 决定本轮是否值得消耗计算资源和用户注意力。它关心：

- 是否存在可执行 Frontier；
- 是否有未处理的 Gate；
- 当前动作能否产生可验证状态转移；
- 是否只是在重复 status 或 monitor poll；
- 当前预算和 cadence 是否允许继续。

Heartbeat 可以定期唤醒系统，Quota 决定唤醒后是否真的运行 Agent。

## 多 Agent 如何保持一致

LoopX 中的多个 Agent 是平级工作者。它们共享同一个 Goal 的事实源，但通过
`agent_id`、Claim 和 Lane 查看各自的工作范围。

这里的几个术语分别表示：

- **Lane**：一个 Agent 持续负责的工作方向，例如编码、评测或 review；
- **agent-scoped**：状态只对指定 `agent_id` 生效，不能被其他 Agent 错用；
- **ACK**：Agent 对一个控制面义务的确认记录；它只能证明“已经响应”，不能单独
  证明目标完成；
- **Material Frontier**：能够产生代码、证据、决策或其他实质结果的当前工作，
  不包含没有状态变化的重复轮询。

```text
Goal: 提升长任务可靠性
├─ coding-agent
│  └─ Vision: 修复控制面和 Turn 实现
├─ evaluation-agent
│  └─ Vision: 运行回归与 benchmark
└─ review-agent
   └─ Vision: 检查证据、边界和完成声明
```

关键隔离规则：

- A 的 Replan ACK 不能清除 B 的 acceptance gap；
- A 的等待 Todo 不能让 B 自动进入等待；
- 一个 Goal 的 Material Frontier 不能污染另一个 Goal；
- Review、权限和 workspace 要求来自任务与仓库策略；
- Dashboard 只是状态投影，不能成为第二个事实源。

## 一个“持续 200 小时”的例子

假设 Goal 是：

> 让一个维护 Agent 在 200 小时观察窗口内持续改进项目，并保持目标、权限和完成
> 判断可追溯。

开始前，控制面先写下可复用的初始状态：

```text
Goal:
在 200 小时观察窗口中交付三个经过验证的可靠性改进，并保持所有高风险操作
需要人工批准。

coding-agent Vision:
修复一个可以复现的长任务控制面问题。

evaluation-agent Vision:
对每个候选修复运行回归，并记录有效状态转移、重复动作和错误完成声明。

Acceptance:
三个改进分别有代码、测试和 review 证据；没有绕过权限；未完成问题有明确
Successor、Blocker 或 no-follow-up 原因。

Initial Frontier:
1. coding-agent 调查已知的重复执行问题；
2. evaluation-agent 建立只读基线；
3. 发布动作停在 User Gate。
```

LoopX 不会创建一个持续运行 200 小时的巨大模型调用。下面的时间线展示状态如何
在多次 bounded Turn 之间演化：

| 时间 | 控制面状态 | Turn 与证据 | 下一步 |
| --- | --- | --- | --- |
| 0h | Goal、两个 Agent Vision 和 Acceptance 已建立 | 尚未执行；Quota 选择第一个 coding Todo | coding-agent Claim 调查任务 |
| 2h | coding Todo active | Agent 复现重复执行；Evidence 指向失败测试 | Checkpoint 保持 Vision，创建修复 Todo |
| 8h | 修复 Todo active | 修改完成，独立测试通过；Turn Commit 写回代码和测试证据 | 创建 evaluation Successor |
| 24h | evaluation Todo active | 回归发现 CI 在特定环境失败 | Acceptance 未满足，不能关闭 Goal |
| 26h | 原方案与现实不一致 | Replan 写入 Path Delta：保留修复，停止重复 CI 重试，增加环境诊断 | 创建诊断 Todo |
| 40h | 外部 CI 尚未恢复 | Monitor 记录确切恢复条件；没有变化时 Quota 返回 wait | Agent 安静等待，不重复消耗 |
| 72h | CI 状态变化，`resume_when` 满足 | evaluation-agent 恢复 Successor，验证第一项改进 | Checkpoint 关闭阶段 Vision，并创建下一个 Vision |
| 120h | 第二项改进通过本地验证 | 发布仍需要 User Gate；Safe Fallback 继续第三项只读调查 | Gate 保持可见，未被旁路进展掩盖 |
| 160h | 用户批准发布范围 | Turn 校验当前 Goal、Agent、Todo 和权限后执行发布前检查 | review-agent 检查证据与公开边界 |
| 200h | 三项改进均有验证，或仍有明确未完成项 | Final Checkpoint 对照 Acceptance 汇总证据 | 满足则关闭 Goal；否则保留 Blocker/Successor，不虚报完成 |

这个例子中的关键状态变化可以进一步展开。

### 证据不足时

24 小时的 CI 失败说明“代码已经修改”，但不能证明改进已经交付。Todo 可以记录
实现完成，长期 Acceptance 仍保持 open，并由 evaluation Successor 继续验证。

### 路线失效时

26 小时的 Replan 不会覆盖旧历史。Path Delta 记录原假设、实际失败、保留内容和
新路线，后续 Agent 能知道为什么停止重复 CI 重试。

### 外部等待时

40 小时的 Monitor 保存确切恢复条件。Heartbeat 可以继续唤醒控制面，但只要外部
状态没有变化，Quota 就不启动昂贵的执行 Turn。

### 人工决定未完成时

120 小时的 User Gate 只阻塞发布路线。第三项只读调查属于 Safe Fallback，可以
继续推进，但它的进展不能把发布 Gate 标成已解决。

### 会话被替换时

任何时刻都可以由新 Agent 会话接手。新会话重新读取当前 Goal、Vision、Todo、
Gate 和 Evidence，然后校验 `goal_id + agent_id + todo_id` 再执行。旧聊天线程
不是恢复工作的唯一入口。

### 到达 200 小时时

时间到达 200 小时不自动等于成功。Final Checkpoint 仍然按照 Acceptance 判断：

- 证据完整：关闭 Goal；
- 外部条件仍阻塞：写明 Blocker 和恢复条件；
- 仍有安全工作：创建 Successor；
- 目标已经不值得继续：记录明确的 no-follow-up 或 superseding Vision。

因此，200 小时描述的是观察窗口和循环寿命，不是一次模型调用的长度，也不是自动
完成标准。

## “不偏移”能够承诺什么

LoopX 不能保证模型永远不会提出错误方案。它提供的是一组可执行约束：

- 重要目标和决定不只存在于聊天记忆；
- 每轮执行绑定明确的 Goal、Agent 和 Todo；
- 完成声明需要 Evidence 和 Acceptance 审核；
- 局部 Todo 完成不会自动结束长期 Goal；
- 路线变化需要 Replan 和可追溯 Delta；
- 旧状态、跨 Agent 状态和无效 ACK 可以被检测或拒绝；
- 没有可验证进展时，Quota 可以停止资源消耗。

因此，更准确的表述是：

> LoopX 让长期任务的偏移可见、可拒绝、可纠正，并让后续 Agent 能从持久化状态
> 恢复工作。

要证明真实模型在特定任务上连续 200 小时保持稳定，还需要实际的 soak benchmark，
并持续统计：

- acceptance drift；
- 错误完成声明；
- 无效或重复 Replan；
- 重复动作比例；
- 人工纠偏次数；
- 验证通过的有效状态转移；
- token cost 和 user attention cost。

控制面机制是长时间实验的基础，真实运行证据决定最终能够做多强的产品声明。

## 最小设计检查表

设计一个新的长任务时，至少回答以下问题：

1. Goal 的最终结果是什么？
2. 当前 Vision 的范围和 Acceptance 是什么？
3. 当前 Frontier 中有哪些可执行 Todo？
4. 多 Agent 场景中由谁 Claim？
5. 每个 Turn 用什么独立证据验证？
6. Todo 完成后怎样生成 Successor？
7. 哪些情况触发 Replan？
8. Path Delta 保存哪些变化？
9. 哪些操作需要 User Gate？
10. 没有状态变化时，Monitor 和 Quota 怎样停止无效消耗？
11. 新会话怎样从持久化状态恢复？
12. 最终用什么证据关闭 Goal？

如果这些问题没有明确答案，长任务很可能仍然依赖某个聊天线程或某个 Agent 的临时
记忆。

## 相关文档

- [Loop Engineering 原则与常见坑](../product/loop-engineering-principles-and-pitfalls.zh.md)
- [LoopX Turn 协议](../reference/protocols/loopx-turn-v0.md)
- [Goal Vision Replan 协议](../reference/protocols/goal-vision-replan-contract-v0.md)
- [长期 Agent 状态协议](../reference/protocols/long-horizon-agent-state-protocol-v0.md)
- [Quota 分配](../quota-allocation.md)
- [Heartbeat 自动化](../heartbeat-automation-prompt.md)
- [项目 Agent Todo 协议](../project-agent-todo-contract.md)
- [公开与私有边界](../public-private-boundary.md)
