# RSI harness loop：实施与提交验收说明

日期：2026-10-03  
状态：待实施。本文定义下一阶段工作，不表示相关功能已实现。  
对象：负责 rsi-loop 实现、CRC 运行和验收的同事。

## 1. 目标与现状

本阶段的改进对象始终是 harness：包含提示词、配置、AIDE 可调用的工具与执行包装，以及通过固定扩展接口实现的验证、恢复、搜索和提交控制逻辑。代码只是其中一种实现形式。完成一个可追溯、可测试、可回退的 harness 改进闭环，即可提交；不要求在本阶段证明官方分提升。

现有 notes/config 闭环已经验收通过，见 [acceptance_result_20261003.md](acceptance_result_20261003.md)。累计实验成本为 $17.71，验收结束时 CRC 无运行中的 job。新阶段必须使用新 state，不覆盖 `.state` 或 `.state-accept`。

当前实现中：

- `rsi/harness.py` 只允许 `notes.md` 和 `config.json`，digest 也只覆盖这两个文件。
- `rsi/improver.py` 只允许读写上述两个文件。
- `rsi/backend.py` 通过 notes、环境变量和固定 overlay 启动 AIDE，尚无可演化 harness 工具/hooks 的交付接口。
- `rsi/method.py` 冻结 verifier、improver、harness schema 和 improver 模型，尚未完整冻结执行基座、任务协议、观测与验收逻辑。
- `rsi/run.py` 已读取节点代码、父节点、分数、异常等信息，适合作为 dynamics 的初始数据源。

现有验收中，下一轮 25 个节点有 17 个失败，其中 16 个使用了 notes 所要求的 Pipeline 等结构。这是值得调查的现象，不是已经证明的因果关系；沿同一分支传播的错误也不是独立样本。

## 2. 完整闭环

```text
固定任务、执行基座、评估方法与预算
                  ↓
         AIDE 使用版本 H_t 运行
                  ↓
   收集产物、独立 verifier 与 dynamics
                  ↓
      带来源和缺失标记的证据写入 memory
                  ↓
 improver 提出原因假设并修改允许的 harness 文件
                  ↓
 独立边界检查、回归测试、真实入口的离线 smoke test
        ├─ 失败：保存拒绝记录，继续保留 H_t
        └─ 通过：保存不可变版本 H_{t+1}
                              ↓
             下一轮实际加载并执行 H_{t+1}
                              ↓
        相同评估方法 collect，记录行为与效果
```

notes/config、tools/hooks 都属于 harness，按证据选择改动，不要求每轮都改 Python 代码。本阶段需接通至少一个 notes 之外的 harness 工具或控制入口，并验证其在真实运行中执行；纯 notes 的送达已在上一阶段验收，不能替代新增入口的验收。新增未调用的 helper 或仅打印版本号也不算入口生效。

## 3. 明确 harness 的边界

improver 只修改版本化的 harness 文件，不修改 AIDE 核心源码、AIDE 生成的任务解题代码、任务环境或 loop 控制器。harness 通过固定适配层向 AIDE 提供工具、包装与 hooks。维护者可以在本阶段实现并测试必要的扩展接口，随后将接口及 AIDE 基座冻结；这不属于 improver 的可改范围。不能原地修改 CRC 共享 checkout、共享 overlay 或系统包。

### 3.1 允许改动的 harness 内容

| 范围 | 允许的改动 | 必须保留的边界 |
|---|---|---|
| 验证与预处理 | harness 提供的验证工具及执行包装，组织 fold 内预处理、训练/验证与最终 refit | 固定数据切分协议、指标定义与标签访问权限 |
| 搜索和节点选择 | 固定扩展接口暴露的策略配置、候选比较与提交选择 hook | 不增加总步数/时间/token 或费用额度，不读取官方测试反馈 |
| 错误恢复 | 针对明确异常的检查、有限重试、回退到已验证路径 | 重试计入原预算，不能掩盖失败或伪造成功 |
| 提交生成与检查 | 列名、顺序、行对应关系、概率值与格式验证 | 固定任务提交协议；不能改 grader 迁就输出 |
| notes/config | 提示词、工具使用说明及当前模式有效参数，可独立修改 | 不写入测试标签、官方分阈值等隐式答案 |

上述是允许扩展的范围，并非要求本次实现所有模块。本次先接入一个真实、可测试的修改入口；其他入口待明确接口后再加入白名单。

### 3.2 improver 不可修改的内容

- 原始训练/测试数据、标签、样本身份、官方任务划分和固定验证切分规则。允许在 fold 内对训练部分进行特征处理或重采样，但不能改源数据或验证成员身份。
- 官方评分器、独立 verifier 及其阈值、验收规则、固定回归测试。
- task notes 中的固定提交协议、测试信息访问权限、held-out 隔离规则。
- 预算管理、步数/时间/资源上限、计费与运行审计代码。
- improver 自身、其工具权限、观测采集器、memory 已有记录、方法冻结逻辑。
- AIDE 核心源码及其生成的任务解题代码、基础依赖、执行基座以及加载 harness 的可信适配层。任务解题代码仍由 AIDE 在 run 内生成和修改。

如发现这些固定部分存在缺陷，由维护者修复并建立新方法版本和新 state；不让 improver 在本轮里一并修改。

### 3.3 官方分的信息路径

延续现有任务级训练约定：训练任务官方分可以在 run 结束后进入外层 improver memory；AIDE 内层不能直接读取 grader、测试标签或官方分。

held-out 任务的官方分和 dynamics 均不进入改进 memory，也不能驱动 improve。若使用 held-out 反馈改方法，该任务就已参与开发，需要另外的独立评估。

训练任务官方分已被外层优化使用，因此不作为独立泛化证据。不能将这些分数转写进 harness 文件 作为“正常 AUC 应是多少”的判断规则。

## 4. 先接通一个小而真实的 harness 扩展入口

### 4.1 目标 repo 结构

保留现有平铺的 `rsi/` 模块，不进行大规模搬迁。按职责分成三部分：`rsi/` 是冻结的控制与评价代码，`harness/` 是可演化版本的初始模板，`.state-harness/` 是运行时状态。以下为目标结构，标注新增的部分需要实施：

```text
rsi-loop/
  README.md
  pyproject.toml
  rsi/                         # 固定控制面，improver 不可修改
    cli.py                     # 命令入口
    loop.py                    # collect → improve → check → publish → submit
    harness.py                 # 允许文件、版本读写、manifest/digest
    improver.py                # LLM 工具与修改协议
    llm.py                     # 模型调用与计费
    backend.py                 # CRC 提交、代码包交付
    harness_adapter.py         # 新增：真实执行入口与独立调用回执
    candidate.py               # 新增：候选检查、隔离执行、发布/拒绝
    dynamics.py                # 新增：观测提取、缺失与证据来源
    run.py                     # 运行产物读取
    memory.py                  # 改进证据存储
    method.py                  # 固定方法与依赖摘要
    budget.py                  # 成本与预留
    task.py                    # 固定任务定义加载
    verifiers/                 # 独立评价，保持冻结
  harness/H0/                  # 初始版本模板；运行时复制进 state
    notes.md
    config.json
    tools/                     # 新增：提供给 AIDE 的工具/执行包装
    hooks/                     # 新增：固定接口调用的控制逻辑
                               # 先实现其中一个入口，不预建空壳模块
  tasks/<task>/                # 固定数据准备、说明、提交与切分协议
  tests/                       # 固定回归/接口/隔离/smoke tests
  tools/                       # 验收与报告工具，不承载另一份 loop 逻辑
  docs/                        # 协议、结果、可共享的精简验收证据
  .state-harness/              # 新增且 gitignore；具体布局见下一节
```

`harness_adapter.py` 决定何时调用、给什么权限、怎样审计；可演化的 `harness/H*/tools/` 和 `hooks/` 实现允许范围内的工具与控制行为。二者不能混放，否则 improver 可以修改执行与验收边界。repo 根目录 `tools/` 是固定验收脚本，与版本内的可演化工具不同。

`candidate.py` 与 `harness.py` 分工：前者执行检查并决定是否发布，后者负责版本格式与存储。`dynamics.py` 只构造观测，不计算一套替代 verifier 的奖励。`tools/` 只调用已有模块，不复制业务逻辑。

实现时将 `.state-harness/` 加入 `.gitignore`，关键验收证据以脱敏小文件进入 `docs/`，不要提交整份实验状态或原始大日志。

### 4.2 实验状态与版本结构

以下目录和字段是拟新增接口，不是现有功能：

```text
<new-state>/
  method.json
  harness/H0/
    notes.md
    config.json
    tools/                   # 仅列入 manifest 的工具允许修改
    hooks/                   # 仅列入 manifest 的控制入口允许修改
    manifest.json            # 由控制器生成，improver 不可编辑
  candidates/<candidate-id>/
    harness/                 # 候选 notes/config/tools/hooks 的完整快照
    proposal.json
    validation.json
    diff.patch
  rounds/<task>/<round-id>/
    submit.json
    harness_receipt.json
    reward.json
    dynamics.json
    evidence.json
    improve.json
  memory.jsonl
```

manifest 至少包含：父版本 digest、允许路径及入口接口版本、每个文件的 SHA-256、基座/overlay digest、依赖标识、固定方法 digest。文件路径与字节内容共同参与摘要；顺序固定，版本发布后不得覆盖。

### 4.3 入口选择与实现要求

优先检查 job 1500133 中首次失败的节点代码、完整异常栈和父子关系，区分首发错误与复制传播，选一个可离线复现的具体问题作为首个修改目标。不要把“16 个节点使用 Pipeline”直接等同于“Pipeline 导致 16 个错误”。

维护者先实现一个固定适配层，将选定模块接到 AIDE 的真实调用路径；H0 提供与当前行为等价的基线实现。随后由 improver 根据 memory 修改该模块，产生 H1。

每个入口必须明确：

1. 输入输出类型及返回值由谁消费。
2. 调用时机、失败行为与超时处理。
3. 可读取的数据和可写入的产物。
4. 如何记录模块路径、版本摘要、调用事件与实际行为。

若首个入口选择验证/预处理，必须让真实评分路径消费该模块的结果。仅把 `validation.py` 放进工作目录、提示 AIDE “可以调用”不满足执行机制验收。无法接入真实评分路径时，先缩小到一个可强制执行的入口，不声称已保证所有生成代码的验证正确性。

不在本次默认加入在线节点淘汰、多个新策略或广泛代码重写。先证明一个 harness 改进闭环；额外策略分别立项验证。

## 5. Training dynamics：采集和使用方式

可以且应该作为 improve 的证据。它解释失败发生在何处、预算花在何处，不新增一个由 improver 自行定义的成功指标。

### 5.1 必须采集的 agent 搜索 dynamics

优先从已有 journal、stdout/stderr 和运行记录提取：

| 数据 | 最低要求 |
|---|---|
| 节点身份 | run_id、node_id、parent_id、step |
| 节点状态 | 是否执行、是否异常、是否成功评分；缺失状态单列 |
| 分数轨迹 | metric 名称、方向、值；best-so-far 仅比较可比的验证协议 |
| 错误 | 异常类型、关键栈位置、错误签名和原始证据引用 |
| 传播 | 同一分支中相同错误签名的重复；标为疑似传播，不自动断言因果 |
| 预算 | 节点耗时/token/cost 在有可靠记录时采集，缺失则 null |
| 汇总 | 节点总数、成功评分数、失败数、未知数、可用计时/计费覆盖率 |
| 提交 | 最终节点 ID、选择依据记录、其验证值与 verifier 结果 |

不能将 `is_buggy` 当作确认泄漏，也不能将“成功评分”当作验证无偏。原始最高分与未标记节点最高分分别报告，不能静默丢弃被标记节点。

### 5.2 有则采集的模型训练 dynamics

- epoch/iteration 对应的训练损失和验证指标；关联 node_id、模型、metric 和 split_id。
- 各 fold 指标、样本数、split 标识，以及同一协议下的均值和离散程度。
- early stopping、实际迭代数、运行时警告和训练耗时。
- 预处理 fit/refit 事件在固定适配层可观测时记录。

并非所有模型都有训练曲线。本阶段不为补齐曲线额外重训，不将 LLM 对日志的猜测写成测量值。不同指标或切分的数值不直接相减；不得凭空推断训练损失与 AUC 的 gap。

### 5.3 数据格式与证据来源

`dynamics.json` 包含 `schema_version`、run/harness/method digest、覆盖率、节点记录和汇总。每个非空观测包含来源，如 journal JSON pointer、日志行号、固定采集事件 ID。

缺失使用 `null` 并给出 `missing_reason`，如 `not_logged`、`unsupported_model`、`execution_failed`。零仅代表实际观测到零。

为具体证据分配稳定 evidence_id，例如：

```json
{
  "evidence_id": "runA:node06:error:1",
  "kind": "execution_error",
  "source": {"artifact": "logs/node06.stderr", "lines": [18, 24]},
  "observation": "实际异常摘要，必须来自原始日志",
  "confidence": "observed"
}
```

memory 保存结构化摘要与可解析的证据引用，原始日志保存在不可变产物中。improver 工具可以按 evidence_id 读取限定的原始片段，不提供任意文件系统读取权限。采集时不包含 API key、凭据或测试标签。

日志中由候选代码自行打印的曲线属于 self_reported 观测，不能当作独立验证。固定采集器负责 run/节点身份、退出状态、计时等可独立观察的事实。

## 6. Improver 的输出与权限

每次候选必须保存以下内容，而不只保存自然语言总结：

```text
evidence_ids       具体引用，不能只匹配 Logistic/random 等常见词
observation        观察到的现象
hypothesis         待验证的原因，明确不确定性
changed_paths      修改的允许路径
expected_behavior  下一轮期望出现的可观测行为
regression_risks   可能损害的行为，例如有效节点数或耗时
```

工具允许读当前版本、训练 memory 和指定证据，只写候选 harness 白名单内的 notes/config/tools/hooks，调用固定 `check`，最后 `finish`。不提供任意 shell、自由联网、修改测试或主动调度 CRC job 的能力。

文件白名单本身不能约束 Python 执行权限。候选检查及运行必须位于隔离进程/容器中，使用只读任务输入、独立输出目录、执行超时和资源限制；不向候选 harness 工具/hooks 暴露 improver 凭据、评分目录或可写 state。复用现有隔离机制前要确认这些边界实际成立。

固定控制器负责校验、发布、预算检查和调度；候选不得自行声明测试通过。允许因证据不足返回 no_change，不强制编造修改。

## 7. 候选检查、发布与回退

按顺序执行以下检查，全部通过才能发布：

1. **边界检查：** 路径白名单、禁止路径穿越/符号链接逃逸、固定文件摘要、合法配置。
2. **静态与接口检查：** Python 编译/import、入口签名、输出 schema；在受限进程执行 import。
3. **固定回归测试：** 保留原有正确行为并覆盖此次修复。
4. **真实入口离线 smoke test：** 使用合成小数据，通过与正式运行相同的适配层触发候选代码，检查输出和事件。
5. **版本发布：** 控制器生成 manifest，原子保存 H_{t+1} 和检查结果。相同版本内容不可被后续调用修改。

测试至少涵盖相关边界：fold 内 fit 合法、全量最终 refit 合法、全量监督预处理后 CV 不合法、未知数据流不宣称无泄漏；以及所选修改涉及的异常恢复、提交行对应或选择行为。

禁止用“分数低于 0.9”替代数据隔离测试。对于 fold 执行，可用合成样本身份和 spy transformer 验证 fit 未见验证行；对恢复策略，用故障注入验证重试有上限且计入预算。

失败候选保存 diff、原因和测试日志，但不改变当前有效版本。本阶段每次 improve 最多允许 2 次候选检查；再次失败即停止该会话，保留旧版本，不无限修复。

正式运行中的候选异常必须写入结果。若使用固定 fallback，记录触发原因和实际执行版本；不能把 fallback 的成绩归给新版本。运行失败不能删除或从分母中排除。

## 8. 方法冻结与运行送达

冻结的是“怎样产生、执行、观察和评价候选”的规则；H_t 的允许文件是规则内的变量，不应因其合法改变而触发方法不一致。

扩展 method manifest，固定以下内容：

- verifier、improver 提示词/工具/模型及模型参数。
- harness 工具/hooks 接口、路径白名单、固定测试、适配层及候选检查代码。
- dynamics schema/采集器、memory 构造规则。
- AIDE 模型配置、任务协议、数据版本与切分规则、基座/overlay/依赖版本。
- 预算及资源协议、官方分与 held-out 的信息边界。

用显式依赖清单建立摘要，不只 hash 单个入口文件而遗漏其依赖。验证 extend 后 freeze 不误阻止允许的 H_t 变化，同时能拒绝上述固定部分变化。

在 submit、collect、improve 和候选发布时检查方法版本。排队后才修改文件也不能改变已提交作业：submit 绑定不可变代码包，worker 启动时复核摘要。

`harness_receipt.json` 由固定运行器生成，至少记录：预期与实际摘要、基座摘要、实际加载路径、接口版本、调用事件、是否 fallback。验收同时要求：

- 预期版本与实际加载字节一致。
- 修改所在入口确实被调用。
- 至少一个调用产生了预期的机制行为，可回溯到输入、输出或固定观察事件。

只看到环境变量、import 成功、日志里打印了版本号，或 notes 出现了 81 次，都不能单独证明 harness 修改生效。

## 9. 按文件安排实施工作

| 文件/模块 | 任务 |
|---|---|
| `rsi/harness.py` | 支持 harness 文件白名单、递归内容摘要、不可变版本及 manifest；保留 notes/config |
| `rsi/improver.py` | 候选 harness 文件读写、evidence_id 引用、结构化原因假设、固定检查调用 |
| `rsi/backend.py` | 不可变代码包交付、固定适配层挂载、worker 送达回执 |
| `rsi/run.py` | 兼容旧日志，读取新增 harness 执行事件及已有 dynamics 字段 |
| 新增 dynamics 模块 | 确定性提取、缺失与覆盖率、证据来源，独立于可改 harness |
| `rsi/memory.py` | 摘要和证据引用；重复/held-out 不写入改进 memory |
| `rsi/method.py` | 扩大固定方法清单，区分方法 digest 与版本 digest |
| `rsi/loop.py` | 候选生命周期、失败保留、发布、送达与 collect 一致性 |
| `rsi/budget.py` | 新阶段独立记账；测试/重试/重训费用不遗漏，保留累计成本关联 |
| `rsi/cli.py` | 暴露必要入口并完善 help；未实现前不在文档宣称命令可执行 |
| `tests/` | 边界、冻结、回退、数据缺失、真实入口 smoke 和旧格式回归 |

实施顺序：先审计并复现一个错误 → 定义并接入单一入口 → dynamics 与证据 → improver 修改与独立检查 → 版本交付/回执 → 离线全链路验收 → 有预算时做真实 run。

## 10. 提交验收标准

| 检查 | 通过条件 |
|---|---|
| A：证据 | 真实 run 的 verifier 和至少搜索 dynamics 进入 memory；引用可解析，缺失明确 |
| B：harness 修改 | improver 根据具体证据修改允许的 harness 文件；原因假设、diff、预期行为完整，不要求每轮修改代码 |
| C：独立检查 | 固定测试与真实入口 smoke 通过；失败候选无法发布；回退测试通过 |
| D：实际执行 | 下一次真实 AIDE run 使用该 harness 版本，摘要一致；新增工具/控制入口被触发，且修改对应的配置、工具或 hook 有实际行为证据 |
| E：固定评价 | 同一方法收集 reward、dynamics、有效节点数、错误与成本；失败/fallback 未隐去 |

完整 harness 闭环验收要求 A–E 全部通过。只有离线测试通过时，报告为“实现完成，真实运行验收待执行”，不写闭环已通过。

分数提高、所有节点无错误、verifier 全部归零均不是本阶段硬性通过条件。若新增工具/控制入口没有被实际调用，则 D 不通过，即使官方分上升也不能替代。

### 最小真实实验

1. 离线检查全部通过后建立新 state，并冻结新方法。
2. 可以复用兼容的旧 H0 run 作为问题证据，但标明其旧方法/旧 harness 来源及缺失 dynamics；不冒充新基座下的 baseline。
3. 运行一次 improver，生成并测试一个 harness 候选。
4. 在预算允许时运行一次 H1，collect 并出具 A–E 验收表。
5. 若要比较 H0/H1 效果，另外运行匹配的新基座 H0；本次不因验收自动扩展成效果实验。

### 已授权的新阶段预算

用户已于 2026-10-03 为本阶段新增 **$15** 预算。新 `.state-harness/` 的阶段上限设为 $15，从零记录本阶段费用；另记录历史已花费 $17.71，报告累计实际支出。本阶段按历史支出加新增额度计算，累计实际支出上限为 **$32.71**；不自动叠加旧阶段未花完的 $2.29。

- 预算覆盖本阶段全部 improver 调用、AIDE run、失败尝试及付费重试；复用旧 run 不重复记费。
- 保留每个未完成 AIDE run 的 $3 预留；这是调度预留，不宣称实际调用费用必然小于 $3。启动前同时预留已承诺的 improver 调用额度，避免两者竞争同一余额。
- 顺序执行：离线实现与检查 → 一次 improver（首次会话最高 $0.50）→ 一次真实 H1 run → collect 与验收。旧 run 作为起始证据的兼容性按前述规则检查。
- 未通过验收时，先定位原因，再决定是否进行有限修复/重跑；每次重新检查剩余额度。不得因为预算还剩钱而自动增加实验轮数。
- A–E 通过即停止本阶段付费运行，记录未用预算。更多 baseline、held-out 或效果实验不属于本次必需范围。
- 本授权允许在实现与离线检查通过后开展上述必要的付费验收，无需再次询问是否允许使用这 $15；预计越过阶段上限时停止并报告。

## 11. 同事提交时必须提供的材料

- 实现代码及固定测试，README 中准确描述可修改范围和实际支持的 CLI。
- 新的实施结果文档：commit、method digest、H0/H1 digest、job/run ID、harness 入口及实际调用证据。
- 一条完整示例链：原始证据 → evidence_id → 原因假设 → diff → 检查 → 下一轮行为 → reward/dynamics。
- 错误首发/传播分析；模型训练曲线有则附上，无则说明日志缺失或模型不支持。
- 本阶段及累计费用、已结束/运行中的 job、失败候选和 fallback 记录。
- 可复现的实际命令及必要非敏感配置，不提交密钥或大体积原始数据；gitignored state 的关键验收材料另存为可共享的脱敏证据包并记录摘要。

最终结论区分三层：扩展接口是否已实现、harness 闭环是否已验收、性能是否已有证据。不得把一次真实 run 的成绩变化写成已证明的提升。

## 12. 本次明确不做

- 不同时开放任意仓库文件修改或系统环境管理。
- 不要求 improver 改 verifier 或自行定义更容易通过的评分标准。
- 不要求所有模型产出训练曲线，不为日志完整性额外重训旧 run。
- 不自动扩跑多任务、统计显著性实验或 held-out 补跑。
- 不在实现和离线检查通过前启动付费任务；通过后可使用已授权的 $15 完成必要验收。本文档编辑本身不启动任务。
- 不自动 push 或创建 PR；完成上述材料后交由负责人提交。
