# Conjecta 数学基准套件（Benchmark Suite）

> 一套分层、可追溯来源的评测体系，用于量化 Conjecta Math Agent 在
> “计算 → 竞赛推理 → 奥赛上限 → Lean 形式化 → 研究级规划”各能力维度上的表现。
> 所有 `data/benchmarks/` 下的数据集由 `scripts/build_benchmark_suite.py` 从权威来源自动生成，
> 每个文件的来源、许可证、条数与注意事项记录在 `data/benchmarks/manifest.json`。

---

## 1. 分层体系

| Tier | 名称 | 数据集 | 规模 | 测什么 |
| --- | --- | --- | --- | --- |
| tier0 | 冒烟 | `data/eval_smoke.jsonl` | ~10 | 端到端链路是否可用（秒级） |
| tier1 | 基础 | `data/eval/fast.jsonl` | 40+ | 基础计算与代数、简单形式化 |
| tier2 | 竞赛数值 | `data/benchmarks/competition/*.jsonl` | 982 | 竞赛推理（AIME 1983–2025、HMMT Feb 2025），数值判分 |
| tier3 | 奥赛上限 | `data/benchmarks/olympiad/*.jsonl` | 450 | 难题上限（Omni-MATH 分层采样、OlympiadBench 英文纯文本） |
| tier4 | 形式化基线 | `data/eval/formal.jsonl` + `data/benchmarks/formal/minif2f_*.jsonl` | 487+ | Lean 4 形式化与证明搜索基线（miniF2F valid/test） |
| tier5 | 形式化高难 | `data/benchmarks/formal/{putnam,compfiles_imo,combibench}.jsonl` | 1000 | 高难形式化（Putnam、IMO、组合数学） |
| tier6 | 研究级 | `data/eval/formal_hard.jsonl`（内部冒烟用，7 题） + `data/eval/research.jsonl` | 30+ | 研究级形式化难度（人工精选）；走普通 `auto`/`react` + 按需 formal escalation |

能力维度对应关系：

- **基础计算**（tier0–1）：exact/numeric 判分，验证 harness 与基本推理。
- **竞赛推理**（tier2）：多步推理 + 精确数值答案；AIME 答案为 000–999 整数。
- **难题上限**（tier3）：Omni-MATH 按难度 1–9 分层（tag `d1`…`d9`），可做难度分维度准确率。
- **Lean 形式化与证明搜索**（tier4–5）：judge 为 `formal`，只有通过 Lean 验证（`verification_status == "verified"`）才计分。
- **研究级形式化**（tier6）：同一 ReAct 路径；需要形式验证时由 Supervisor 按需 escalation（`escalation_*`），不再有独立 `--mode research`。

## 2. 数据来源与许可证

| 文件 | 来源 | 许可证 | 条数 |
| --- | --- | --- | --- |
| `competition/aime_1983_2024.jsonl` | [gneubig/aime-1983-2024](https://huggingface.co/datasets/gneubig/aime-1983-2024) | CC0-1.0 | 932 |
| `competition/aime_2025.jsonl` | [MathArena/aime_2025](https://huggingface.co/datasets/MathArena/aime_2025) | CC-BY-NC-SA-4.0（非商用） | 30 |
| `competition/hmmt_feb_2025.jsonl` | [MathArena/hmmt_feb_2025](https://huggingface.co/datasets/MathArena/hmmt_feb_2025) | CC-BY-NC-SA-4.0（非商用） | 20 |
| `olympiad/omni_math.jsonl` | [KbsdJames/Omni-MATH](https://github.com/KbsdJames/Omni-MATH) | Apache-2.0 | 250 |
| `olympiad/olympiadbench_text.jsonl` | [Hothan/OlympiadBench](https://huggingface.co/datasets/Hothan/OlympiadBench) | Apache-2.0 | 200 |
| `formal/minif2f_valid.jsonl` / `formal/minif2f_test.jsonl` | [yangky11/miniF2F-lean4](https://github.com/yangky11/miniF2F-lean4) + [cat-searcher/minif2f-lean4](https://huggingface.co/datasets/cat-searcher/minif2f-lean4)（非形式化题面） | MIT | 243 / 244 |
| `formal/putnam.jsonl` | [trishullab/PutnamBench](https://github.com/trishullab/PutnamBench) | Apache-2.0（题面经 MAA 授权用于基准） | 672 |
| `formal/compfiles_imo.jsonl` | [dwrensha/compfiles](https://github.com/dwrensha/compfiles) | Apache-2.0 | 228 |
| `formal/combibench.jsonl` | [MoonshotAI/CombiBench](https://github.com/MoonshotAI/CombiBench) | MIT | 100 |

> PutnamBench 说明：非形式化题面经美国数学协会（MAA）授权用于基准评测；
> 作者要求不要公开发布模型生成的证明。归属：PutnamBench, trishullab/PutnamBench。

## 3. 构建与刷新流程

```bash
# 一键重建（幂等：覆盖 data/benchmarks/ 下的产物；原始下载缓存在 data/benchmarks/_src/，已 gitignore）
.venv/bin/python scripts/build_benchmark_suite.py
```

- 采样确定性：所有采样使用 `random.Random(20260805)`。
- 单源容错：任一来源失败只记警告，不影响其他来源；产物用 `math_agent.evaluation.load_cases` 逐一校验。
- 校验测试：

```bash
.venv-dev/bin/python -m pytest tests/test_benchmark_datasets.py -q
```

**新增一个数据源的步骤：**
1. 在 `scripts/build_benchmark_suite.py` 中新增 `build_<name>()`，返回 `(cases, caveats)`；
2. 在 `FILE_SPECS` 登记输出路径、tier、track、来源与许可证；
3. 重跑构建脚本与 `tests/test_benchmark_datasets.py`；
4. 更新本文档与 `manifest.json`（由脚本自动生成）。

## 4. 外部验收门槛：miniF2F v2（Lean 4）

`data/eval/minif2f_valid.jsonl` / `data/eval/minif2f_test.jsonl` 是里程碑验收的
**外部基准**（区别于 `data/benchmarks/formal/minif2f_*.jsonl` 的自然语言题面版本）：
每题的 `problem` 内嵌上游 miniF2F-lean4 的 Lean 4 命题原文，import 为精确模块集
（非 umbrella `import Mathlib`——本机 14GB 内存无法加载伞形导入，Lean critic 也会拒绝），
每条语句在收录前都已对照本项目锁定的 mathlib4 v4.30.0 工具链实际 elaboration 验证过。
`judge="formal"`，`require_formal_verification=true`，`expected` 为目标定理名
（验证通过的证明里必须出现该定理名，防止用无关平凡定理骗取 verified）。
内部精选的 `formal_hard.jsonl`（7 题）仅作冒烟，不作为验收依据。

当前收录：**valid 244 条 / test 244 条**（上游 488 条全部通过 mathlib4 v4.30.0
elaboration 验证，0 条丢弃；未使用回退 import）。验证进度持久化在
`data/benchmarks/_src/minif2f_verify_cache.jsonl`（每条一行，完成即落盘），
构建中断后重跑 build 命令会从断点续验，不会从零开始。

运行方式（`scripts/run_minif2f.sh`）：

```bash
# 迭代：valid 划分，默认每题 1 次（看 pass@1）
scripts/run_minif2f.sh

# 里程碑验收：test 划分，每题 8 次（看 pass@1 + pass@8）
scripts/run_minif2f.sh --acceptance
```

摘要行（results 文件末尾 `{"type":"summary", ...}`）中的口径：

- `pass_at_1`：逐题成功率（c/n）的平均，即 pass@1；
- `pass_at_k`：无偏估计 1 − C(n−c,k)/C(n,k) 的逐题平均；k 由 `--pass-k` 控制（默认 8），
  当 k ≥ 每题试验数时退化为“至少一次通过”，与旧的 pass@3 口径一致；
- `pass_k`：本次汇总实际使用的 k。

重建数据集（需要本地 Lean 工具链，逐条重新验证，jobs=2 时约 8 分钟）：

```bash
.venv-dev/bin/python -m math_agent.evaluation.minif2f build --jobs 2
```

丢弃的语句与使用的回退 import 记录在 `data/eval/minif2f_build_report.json`。
若需替换/升级上游 miniF2F 来源，把新的 Lean 4 源树放到
`data/benchmarks/_src/miniF2F-lean4/MiniF2F/{Valid,Test}/*.lean`
（每文件一个 `theorem ... := by sorry`），重跑上面的 build 命令即可。

## 5. 运行方式

```bash
# 冒烟（秒级）
uv run python scripts/evaluate_math_agent.py \
  --dataset data/eval_smoke.jsonl \
  --output data/eval-results/smoke.jsonl

# 竞赛数值轨（AIME 全量；--trials 控制每题重复次数，用于 pass@k）
uv run python scripts/evaluate_math_agent.py \
  --dataset data/benchmarks/competition/aime_1983_2024.jsonl \
  --trials 3 \
  --output data/eval-results/aime_full.jsonl

# 奥赛上限轨
uv run python scripts/evaluate_math_agent.py \
  --dataset data/benchmarks/olympiad/omni_math.jsonl \
  --output data/eval-results/omni_math.jsonl

# 形式化轨（需要 Lean 工具链；formal judge 只认验证通过的证明）
# --mode 只接受 auto/react；形式化失败时由 Supervisor 按需 escalation（见 docs/research-mode.md）。
uv run python scripts/evaluate_math_agent.py \
  --dataset data/benchmarks/formal/minif2f_valid.jsonl \
  --mode auto \
  --output data/eval-results/minif2f_valid.jsonl
```

常用消融开关：

- `--mode auto|react`：同一求解栈；形式化升级不是模式，由 `require_formal_verification` + `escalation_*` 触发。
- `--planning on|off`：规划模块 A/B 消融。
- `--direct-react`：绕过 Supervisor 直接评估 ReActAgent（遗留消融）。
- `--with-mcp`：启用 MCP 工具（默认关闭以保证可复现）。

## 6. 评分口径

评测结果由 `math_agent.evaluation` 汇总，主要指标：

- **accuracy**：单次尝试正确率（numeric 判分为精确有理数感知，支持 `\frac{a}{b}`）。
- **pass@1 / pass@k**：`pass_at_1` 为逐题成功率；`pass_at_k` 为无偏估计
  1 − C(n−c,k)/C(n,k) 的逐题平均，k 由 `--pass-k` 控制（默认 8，k ≥ 试验数时
  退化为“至少一次通过”，与旧的 pass@3 口径一致）。
- **by_tag 分维度准确率**：按 tag 切片（如 `d1`…`d9` 难度、领域、`formal`/`olympiad` 等）。
- **false_verified_rate**：形式化轨中“判对但未通过 Lean 验证”的比例，衡量判分可信度。
- **token / 时延**：`average_total_tokens`、`median_total_tokens`、`average_latency_seconds`、`p95_latency_seconds`。

## 7. 已知局限

- **规则判分只认数值答案**：当前 `numeric` judge 不支持表达式等价判断，因此 Omni-MATH / OlympiadBench 仅收录答案为整数、小数或简单分数的题目（Omni-MATH 4428 题中约 2000 题答案为表达式而被剔除；官方 Omni-Judge 基于 GPT，未采用）。HMMT Feb 2025 的 30 题中 10 题因答案非数值被跳过。
- **AIME 覆盖缺口**：上游 CSV 实际只有 933 行（数据集卡片宣称 ~2250），缺失近年部分场次；另有 1 题（2022-II-8，080/081 双答案均被官方接受）被剔除。
- **miniF2F 非形式化题面来源**：`yangky11/miniF2F-lean4` 只含 Lean 4 命题（每文件一个定理，无注释），题面按定理名（大小写不敏感回退）从 `cat-searcher/minif2f-lean4`（openai/miniF2F 的镜像）关联；该镜像缺 `imo_2006_p3` 的题面，故 valid 集为 243 条。仓库锁定的 mathlib4 工具链版本较旧，本套件不重新构建它。验收用的 `data/eval/minif2f_{valid,test}.jsonl` 不依赖题面关联：命题直接取自源树 `.lean` 文件并逐条对照 mathlib4 v4.30.0 elaboration 验证（缺题面的 `imo_2006_p3` 以通用引导语收录）。
- **Compfiles**：从 `problem_file` 之后的模块文档串提取 IMO 题面，228 个 `Imo*.lean` 文件全部提取成功。
- **CombiBench**：从每个定理/引理前的 `/-- ... -/` 文档串提取题面（100 题全部成功）。
- **FrontierMath 不可得**：Epoch AI 的 FrontierMath 不公开，本套件无法收录。
- **OlympiadBench**：仅使用英文纯文本数学配置（`OE_TO_maths_en_COMP`、`TP_TO_maths_en_COMP`），剔除含图、多答案、非数值题。
