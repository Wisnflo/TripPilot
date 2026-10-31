# TripPilot

**Verifiable travel planning with grounded compilation and transactional replanning.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![uv](https://img.shields.io/badge/Dependencies-uv-DE5FE9)](uv.lock)
[![License: MIT](https://img.shields.io/badge/Code-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Wisnflo/TripPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/Wisnflo/TripPilot/actions/workflows/ci.yml)

TripPilot 是一个面向多约束旅行任务的 Agent：理解自然语言需求，查询工具事实，生成可验证的行程；当返程时间、交通或预算变化时，优先修复受影响的安排，遇到冲突则先澄清，再提交新的计划版本。

[快速开始](#快速开始) · [架构设计](docs/architecture.md) · [评测与复现](docs/evaluation.md) · [结果数据](evaluation/results.json)

## 核心能力

- **事实驱动的规划**：模型提取带原文证据的需求、选择公开候选 ID；编译器负责复制价格等事实、连接交通、安排时间并计算费用。
- **可验证的局部修复**：将变更转为类型化事件，在不可变快照上生成原子编辑，校验源版本后提交，保留修改记录与未受影响内容。
- **明确的冲突澄清**：例如“改为当天返回”与原住宿要求冲突时，生成可恢复的提问状态，由用户选择取消住宿或保留原计划。
- **有界的 Agent 执行**：规划、修复和模型回退共享预算；进程组监督提供任务总时限，主动取消会清理子进程与待执行任务。
- **可审计的评测与用量**：逐请求记录尝试、延迟、模型标识、token 和缓存用量；运行时工具事实与离线评分标注分离。

## 评测结果

在 ChinaTravel 旅行环境中，与 **ReAct + 运行时检查器**（代码标识 `b2`）对照。双方使用同一 `qwen3.8-reasoner` 服务模型、公开输入、工具接口及模型调用/时间上限。

| 任务 | TripPilot | ReAct + 检查器 | 已返回 token 减少 | 累计执行耗时减少 |
| --- | ---: | ---: | ---: | ---: |
| 首次规划：24 个父任务 | **17/24（70.8%）** | 9/24（37.5%） | **77.6%** | **42.7%** |
| 条件修复：11 个父任务、33 个事件 | **27/33（81.8%）** | 22/33（66.7%） | **38.9%** | **39.5%** |

首次规划覆盖 easy、medium、human 各 8 例；预先固定的 6 个父任务额外重复两次，TripPilot 均为 6/6。修复覆盖提前返程、交通不可用与预算下调；两方法从同一成功计划开始。

失败和超时保留在分母。动态结果是成功种子的**条件修复率**，不代表端到端成功率；当前为小样本、单服务模型对照，未证明总体显著领先或 SOTA。程序化工具查询的调度不同，未限制为相同工具调用次数。token 统计仅含已返回用量，实际账单可能不完整。详细协议、区间、失败分布及逐任务证据见[评测文档](docs/evaluation.md)。

## 工作流程

![TripPilot 工作流程](assets/trippilot-workflow.png)

模型负责语言理解与候选选择，程序负责可确定的事实处理和执行约束。候选搜索失败会回退或明确终止，不等同于证明全局无解。

## 快速开始

### 1. 安装

需要 [uv](https://docs.astral.sh/uv/) 与 Python 3.12。本仓库包含运行和离线验证所需的工具数据表与任务数据，无需为体验下载完整上游项目。

```sh
git clone https://github.com/Wisnflo/TripPilot.git
cd TripPilot
uv sync --locked
cp .env.example .env
```

在 `.env` 中填写 OpenAI-compatible API 的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`。模型通过命令行显式指定：

```sh
export MODEL_ID="qwen3.8-reasoner"  # 替换为当前服务可用的模型标识
```

### 2. 生成行程

```sh
uv run --locked python scripts/trip.py plan \
  --from-city 上海 --to-city 杭州 --days 2 --people 2 \
  --request "预算2000元，想去公园散步，住一间双床房" \
  --model "$MODEL_ID" --output-dir output/trip-first
```

成功后生成 `plan.json`、`task.json` 和 `record.json`。每次指定新的输出目录，避免覆盖现有计划。

### 3. 修改并澄清

```sh
uv run --locked python scripts/trip.py revise \
  --task output/trip-first/task.json --seed output/trip-first/plan.json \
  --change "提前一天回去" --model "$MODEL_ID" \
  --output-dir output/trip-change
```

若返回 `needs_clarification`，读取问题与选项，再明确答复：

```sh
uv run --locked python scripts/trip.py answer \
  --state output/trip-change/state.json --answer cancel_overnight \
  --model "$MODEL_ID" --output-dir output/trip-confirmed
```

`cancel_overnight` 表示取消住宿要求；`keep_original` 表示保留原行程。回答前会核对澄清状态与种子版本。仅在生成了待答复状态时运行 `answer`。

### 4. 验证与复现

以下检查不调用收费模型：

```sh
uv run --locked python -m unittest discover -s tests -q
uv run --locked python scripts/check_isolation.py
uv run --locked python scripts/verify_release.py
```

完整对照会调用模型；配置和断点恢复步骤见[评测与复现](docs/evaluation.md)。

## 项目结构

```text
src/trippilot/
├── planning/       # 条件提取、候选选择与草图编译
├── repair/         # 变更理解、澄清、事务编辑与增量修复
├── execution/      # 进程监督、取消与请求账本
├── agent/          # ReAct 循环与轨迹
├── tools/          # 只读工具白名单与双语实体链接
├── plan/           # 结构、事实与时间检查
└── models/         # API 配置、超时和重试
scripts/            # 产品 CLI、评测运行与独立判分
tests/              # 输入、规划、事务、澄清、超时与计费测试
evaluation/         # 协议、逐任务结果、请求账本与校验清单
data/               # 公开输入与仅供离线评分的标注
upstream/ChinaTravel/ # 保留归属的第三方环境与配套数据
```

## 运行边界

- 使用 ChinaTravel 示例数据，覆盖北京、上海、南京、杭州、苏州、深圳、成都、武汉、广州、重庆 10 座城市；不执行真实预订，也不提供实时营业或交通保证。
- `checked_ok` 表示公开运行时检查通过；完整 benchmark 约束由离线评分器独立判定。
- 首次规划默认 360 秒，修复默认 240 秒；每请求 90 秒、最多 2 次尝试，最多 16 次逻辑模型调用。客户端取消不能保证远端立即停止计费。
- 价格配置只用于已返回用量的估算。更换模型服务时需核对 `config/v3-pricing.json`，估算金额不能作为实际支付凭证。

## 许可证与致谢

TripPilot 原创代码采用 [MIT](LICENSE)。第三方 ChinaTravel 代码保留其 [MIT 许可证](upstream/ChinaTravel/LICENSE)；查询与 sandbox 数据采用 **CC BY 4.0**。

工具环境、配套数据与评分工具来自 [LAMDA-NeSy/ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel)，并非本项目自研。数据来源、版本、修改范围与引用方式见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
