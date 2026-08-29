# MLS-Bench Lite v003 中期配置与使用记录

> 更新日期：2026-08-28  
> 当前状态：前置代码和共享环境变更计划已准备；共享环境实际安装、Smoke、API、单题和整轮运行尚待完成。  
> 本文所有 PowerShell 命令均从本地仓库 `D:\programe_language_study\gpt\mls-lite-runner` 执行。

## 0. 替换 Agent 说明（先读）

这里的“Agent 有自己的文件”是指：Agent 本体保存在独立源码目录中，可以单独修改、迭代出新版本，并在下一版 release 中彻底替换；不是指每道题给 Agent 复制一套源码，也不是让 Agent 任意修改 MLS-Bench。

当前 Agent 本体位于：

```text
平台：code/mls-lite-runner-v003/deps/mini-swe-agent-v2.4.6
本地适配：src/mls_agent/
```

需要区分两类替换：

### 0.1 只迭代 mini-SWE，且接口保持兼容

如果新 Agent 仍提供当前使用的 `minisweagent.agents.default.DefaultAgent`、`minisweagent.models.get_model` 和 environment 接口，主要替换 Agent 自己的仓库、commit/tag 和依赖。通常不需要改 MLS 题目、Docker 镜像或五轮 manifest；现有 `miniswe-bash` 适配层经测试兼容后可以继续使用。

需要同步检查或修改：

- `platform/qz_entry.sh`：Agent 仓库地址、固定 ref、源码目录名、导入检查和凭据记录。
- `scripts/submit-prepare-release.ps1`：允许并传递新的 Agent ref。
- `pyproject.toml`：若新 Agent 的依赖或 Python 版本约束发生变化则更新。
- 共享环境事务：重新 dry-run；只有计划确认后才授权安装依赖。

当前 v003 入口把官方仓库和 `v2.4.6` 写死。因此“换成自己的 Agent fork/新版本”尚不能只传一个命令参数完成，必须先在本地修改上述固定点并发布新 runner 版本。

### 0.2 换成接口或运行方法不同的 Agent

如果新 Agent 不再使用 mini-SWE 的 Bash 单工具循环，还要替换或新增适配层：

- `src/mls_agent/miniswe_bash_agent.py`：把 MLS 任务、模型、调用轮次接入新 Agent 主循环。
- `src/mls_agent/miniswe_bash_environment.py`：把 Agent 的执行动作转成受控 bash、`mls-test` 和 `mls-submit`。
- `src/mls_agent/selection.py`：仍需自动选择题目 Docker image 时保留或改造。
- `src/mls_lite_runner/config.py`：生成新 Agent 所需、但不含密钥的配置。
- `src/mls_lite_runner/mls.py`：向 MLS CLI 传入新 `agent-type`。
- `patches/mls-registration-clean.patch`：在 MLS CLI 中注册新的 Agent 名称和实现。
- `doctor`、单元测试和 smoke：更新导入、注册、安全边界及端到端断言。

无论哪种替换，都不建议直接覆盖已经标记完成的 `v003` 目录，因为其 commit、环境 receipt 和 `PREPARE_RELEASE_OK` 将不再代表实际内容。推荐创建不可变的新 tag 和新 release ID，例如 `mls-lite-v004` / `v004`。这样回退只需重新运行 v003，无需删除或恢复 MLS-Bench。

完整替换顺序为：

```text
在 Agent 自己的源码仓库修改并固定 commit/tag
→ 本地调整 runner 的版本固定点与适配层
→ validate、单元测试、simulate
→ 推送新的 runner tag
→ 上传与该 tag 一致的 qz_entry.sh
→ 平台用新的 release ID 准备源码
→ 审核共享环境 dry-run 计划
→ host smoke → API smoke → 单题 → 一轮 → 五轮
```

安全边界不随 Agent 替换而取消：默认题目 shell 仍应在无网络、只读根文件系统、丢弃 capabilities 的 Docker 中运行，只挂载该题工作区；必须继续通过独立的 `mls-test`、`mls-submit N` 网关记账。若新 Agent 要求宿主执行、额外挂载、`--privileged`、Docker 网络或宿主文件访问，视为新的安全设计，不能直接沿用 v003 的验证结论。

### 0.3 2026-08-29 Python 3.10 模型后端兼容修改

API smoke 在 Python 3.10.12 下加载 LiteLLM 1.98.0 时，因标准库 `typing` 缺少 `NotRequired` 而在真正调用 API 前失败。本地 runner 已增加进程级 PEP 655 兼容层：仅在 Python 低于 3.11 时，从 `typing_extensions` 向当前进程的 `typing` 模块补充 `NotRequired` 和 `Required`。

改动文件为 `src/mls_agent/typing_compat.py`、`src/mls_lite_runner/cli.py` 和 `src/mls_agent/miniswe_bash_agent.py`。兼容函数分别在 API smoke 和真实 Agent 加载 mini-SWE 模型前执行。该修改不降级或覆盖 LiteLLM，不修改共享 venv、MLS-Bench、mini-SWE-agent、Docker 镜像、API 配置或题目状态；作用在进程结束后自然消失。

## 1. 目标与当前结论

目标是在启智平台使用可联网的 4090 作业运行 MLS-Bench Lite 30 题，分成 5 轮，每轮 6 题。mini-SWE-agent 通过独立源码目录维护，runner 负责接入 MLS-Bench、任务预检、断点状态、逐题执行和汇报。

目前已经确认：

- 本地 runner 已完成五轮清单校验和中断恢复模拟。
- Git 基线为提交 `e27abd8766ef700a782a0fc64ff3d5dbfc7b3ac0`。
- `main`、本地 `mls-lite-v003` 和 `origin/main` 在记录时均指向该提交。
- 平台入口 `code/qz_entry.sh` 已通过官方 Python 上传客户端上传成功。
- 平台已有共享 venv，使用 Python 3.10.12，不再另建 release 私有 venv。
- v003 的 MLS-Bench、mini-SWE 和 runner 源码按 release 隔离；共享 venv 只承载公共第三方依赖。
- `shared001` 已成功完成依赖 dry-run，并生成变更计划；它因未授权修改共享 venv 而主动以退出码 2 停止。
- 下一步是显式授权一次共享环境安装，然后依次进行 host smoke、API smoke、单题、第一轮。

尚未证明完成：

- 共享环境实际依赖安装及安装后 `pip check`/导入验证。
- `PREPARE_RELEASE_OK` 完整标记生成。
- 4090 宿主 smoke、DeepSeek API smoke。
- 任一真实题目的成功运行。
- 第一轮及后续四轮。
- 本地 `scripts/submit-round-report.ps1` 仍写着平台不支持的 `--cpu-spec 1c4g`；修复前报告使用本文给出的 `4c16g` 直提命令。

## 2. 三个代码部分的关系

| 部分 | 平台位置 | 作用 | 如何替换 |
| --- | --- | --- | --- |
| MLS-Bench | `code/mls-lite-runner-v003/deps/MLS-Bench` | 官方 benchmark、scheduler、Docker 题目环境 | 修改固定 commit 或建立新 release；不要直接改共享旧副本 |
| mini-SWE-agent | `code/mls-lite-runner-v003/deps/mini-swe-agent-v2.4.6` | Agent 本体，便于独立修改和彻底替换 | 换版本/仓库后建立新 release，并重新做适配与 smoke |
| runner/适配层 | `code/mls-lite-runner-v003/src` | Agent 注册、bash 环境、预检、状态机、五轮编排 | 修改本仓库，提交新 commit/tag，再生成或更新 release |

三者不要求安装在同一个源码仓库。运行时由下面的 `PYTHONPATH` 同时选中：

```text
runner/src : MLS-Bench/src : mini-SWE-agent/src
```

公共依赖由共享 venv 的 Python 提供。这样可以升级或彻底替换 Agent 源码，而不需要把 Agent 文件塞进 MLS-Bench 仓库，也不使用 editable install 污染共享环境的源码指向。

## 3. 平台目录和存储周期

项目根目录：

```text
/inspire/hdd/project/long-working-agent/ky26299
├── code/
│   ├── qz_entry.sh                         # 平台上传入口
│   └── mls-lite-runner-v003/               # v003 release 源码胶囊
│       ├── deps/MLS-Bench/
│       ├── deps/mini-swe-agent-v2.4.6/
│       ├── src/
│       ├── manifests/
│       ├── runtime/                        # 本 release 配置、状态、记录
│       └── platform/qz_entry.sh
├── runtime/
│   ├── envs/mlsbench-lite-agent/           # 已有共享 venv
│   ├── env-registry/mlsbench-lite-agent/   # baseline、事务、消费者、状态
│   ├── locks/mlsbench-lite-agent.lock      # 环境修改/运行协调锁
│   └── cache/pip/                          # pip 缓存
└── runs/                                   # qz-job 作业日志目录
```

长期存储包括项目盘上的 `code`、共享 venv、环境总账、pip 缓存、release 配置、任务状态、结果和作业日志。它们跨作业保留。

短期状态包括当前作业进程的环境变量、`CUDA_VISIBLE_DEVICES`、临时目录和题目 Docker 容器。作业结束后不应把这些当作持久状态。

API Key 通过命令参数传入，只存在于进程环境而不写入 Git/config；但它仍可能出现在本地 PowerShell 历史、作业提交参数或平台审计元数据中。若平台以后提供 secret，应改用 secret。

## 4. 共享环境安全模型

共享 Python 固定为：

```text
/inspire/hdd/project/long-working-agent/ky26299/runtime/envs/mlsbench-lite-agent/bin/python
```

准备逻辑遵循以下顺序：

1. 获取共享环境独占锁，防止安装时其他 v003 作业同时运行。
2. 检查 Python、pip、磁盘空间和环境健康。
3. 首次保存 baseline，并为本次操作保存 before 快照。
4. 用 pip dry-run 生成准确依赖计划。
5. 默认不安装；若发现缺包，以退出码 2 停止并要求显式授权。
6. 仅在 `allow-change=1` 时安装，随后执行 `pip check` 和关键导入验证。
7. 保存 after 快照、事务记录、consumer receipt 和 release 完整标记。

这不会修改系统 `/usr/bin/python`、平台镜像、Conda base 或其他人的目录。会发生修改的范围仅是本项目的共享 venv 和对应环境总账。风险主要是共享 venv 中包的新增、升级或降级，因此必须保留 before/after 快照，并先验证单题再并发五轮。

题目 Docker 环境与宿主共享 venv 是两层：宿主 venv 运行 runner、Agent 和 MLS 调度器；MLS 再创建题目 Docker 环境。Agent 通过适配的 bash 环境在 MLS 允许的题目工作区内读写和执行，不等于直接任意修改平台宿主。

## 5. 已发生的版本和故障记录

### 5.1 旧私有 venv 设计

v002/早期 v003 曾计划在 `code/mls-lite-runner-v00x/runtime/env` 创建私有 venv。平台镜像缺少满足该方案的 Python 3.11 或 `ensurepip`，因此准备失败。随后已改为使用现有共享 venv。

这些失败没有修改平台系统 Python；也没有成功创建可用的 release 私有环境。

### 5.2 `probe 004` 的 shell 变量错误

在 `set -u` 下，同一条 `local` 声明引用了尚未完成赋值的变量，探测在读取或修改共享环境前退出。提交 `e27abd8` 已修复。该失败没有修改共享 venv。

### 5.3 `shared001` 的退出码 2

作业：

```text
mr3mh-prepare-v003-4090-shared001
```

它已经完成 clone、固定版本、仅在 v003 MLS 副本中应用可逆注册补丁、保存环境快照以及 pip dry-run。生成的计划为：

```text
/inspire/hdd/project/long-working-agent/ky26299/runtime/env-registry/mlsbench-lite-agent/transactions/v003-e27abd8766ef-mr3mh-prepare-v003-4090-shared001/pip-plan.json
```

日志结尾：

```text
ERROR: shared environment needs packages; inspect the plan and rerun with allow-change=1
__QZ_EXIT_CODE__=2
```

这是保护性停止：它证明“需要安装依赖”，但尚未向共享 venv 安装这些包，因此不是半安装损坏。

日志中对 `pypi.ngc.nvidia.com` 的 DNS retry 来自共享环境已有的 pip extra index。主索引仍完成了解析。下一次只对该作业设置 `PIP_RETRIES=0` 和较短 timeout，减少无意义等待；不改全局 pip 配置，也不强制破坏平台原有索引生态。

### 5.4 已上传入口

本地已成功执行：

```powershell
python D:\programe_language_study\gpt\qz-client\QZ_CLIENT.py upload .\platform\qz_entry.sh code/ --overwrite
$LASTEXITCODE
```

结果为 `uploaded code/qz_entry.sh` 且退出码 0。`qz-upload` 不是本机命令，也不能通过 `ssh qz-gpu qz-upload` 使用；平台仅允许 `qz-job` 和受限文件协议。

## 6. 当前固定配置

| 配置项 | 值 |
| --- | --- |
| runner GitHub | `https://github.com/3MH-Dr/mls-lite-runner.git` |
| runner ref | `mls-lite-v003` |
| runner commit | `e27abd8766ef700a782a0fc64ff3d5dbfc7b3ac0` |
| release ID | `v003` |
| MLS-Bench commit | `cfd57a7e0139c72753e32e31bca593719b098717` |
| mini-SWE-agent | `v2.4.6` |
| model | `deepseekflash` |
| API key variable | `DEEPSEEK_API_KEY` |
| API Base URL | `http://106.15.124.164:4000/v1` |
| GPU profile | `4090` |
| nodes | `1` |

Base URL 已由 runner 配置生成器和平台入口显式设置，不需要在本地手改生成后的 YAML。

## 7. 后续操作：从当前位置继续

### 7.1 可选：再次核对远端 tag

```powershell
git ls-remote --tags origin refs/tags/mls-lite-v003 "refs/tags/mls-lite-v003^{}"
```

解引用后的 tag 应指向：

```text
e27abd8766ef700a782a0fc64ff3d5dbfc7b3ac0
```

### 7.2 实际配置共享环境

此步会修改共享 venv；这是下一步唯一需要明确授权环境变更的操作。`PIP_RETRIES` 和 `PIP_DEFAULT_TIMEOUT` 仅作用于本次作业。

```powershell
$remoteCommand = @'
qz-job submit --profile 4090 --gpus 1 --nodes 1 --name mr3mh-prepare-v003-4090-shared002 --minutes 120 --command 'PIP_RETRIES=0 PIP_DEFAULT_TIMEOUT=10 bash /inspire/hdd/project/long-working-agent/ky26299/code/qz_entry.sh prepare-release /inspire/hdd/project/long-working-agent/ky26299 https://github.com/3MH-Dr/mls-lite-runner.git mls-lite-v003 v003 cfd57a7e0139c72753e32e31bca593719b098717 v2.4.6 1'
'@
ssh qz-gpu $remoteCommand.Trim()
```

只查这个作业，不使用会列出大量作业的 `list`：

```powershell
ssh qz-gpu qz-job status mr3mh-prepare-v003-4090-shared002
ssh qz-gpu qz-job logs mr3mh-prepare-v003-4090-shared002 --tail 500
```

成功必须同时看到：

```text
SHARED_ENV_IMPORTS_OK
ENVIRONMENT_CHANGE=packages-installed
ENVIRONMENT_TRANSACTION=...
PREPARE_RELEASE_OK
__QZ_EXIT_CODE__=0
```

若没有 `PREPARE_RELEASE_OK`，不要继续运行题目。

### 7.3 4090 宿主 smoke

```powershell
.\scripts\submit-4090-smoke.ps1 -Gpus 1 -ReleaseId v003 -JobSuffix shared001 -Execute
ssh qz-gpu qz-job status mr3mh-4090-smoke-v003-1-shared001
ssh qz-gpu qz-job logs mr3mh-4090-smoke-v003-1-shared001 --tail 300
```

预期包含 `HOST_SMOKE_OK` 和退出码 0。它检查 release、共享凭据、Python 导入、Docker 和可见 GPU，不调用模型 API。

### 7.4 API smoke

把下面占位符只替换为真实 key，不要把 key 写进仓库文件：

```powershell
$ApiKey = "在这里填写真实API_KEY"
.\scripts\submit-api-smoke.ps1 -ApiKey $ApiKey -Model deepseekflash -ReleaseId v003 -JobSuffix shared001 -Execute
ssh qz-gpu qz-job status mr3mh-api-smoke-v003-shared001
ssh qz-gpu qz-job logs mr3mh-api-smoke-v003-shared001 --tail 300
```

预期包含 `API_SMOKE_OK`、`API_JOB_OK` 和退出码 0。日志不得打印完整 key。

### 7.5 先跑一道人为选择的题

推荐先跑第一轮的一道题；任务名由命令参数提供，并未硬编码：

```powershell
.\scripts\submit-run-task.ps1 -Task optimization-variance-reduction -ApiKey $ApiKey -Model deepseekflash -ReleaseId v003 -JobSuffix first001 -Minutes 360 -Execute
ssh qz-gpu qz-job status mr3mh-task-v003-optimization-variance-reduction-first001
ssh qz-gpu qz-job logs mr3mh-task-v003-optimization-variance-reduction-first001 --tail 500
```

要换题，只修改 `-Task`。脚本会从 manifest 查出该题属于哪一轮及所需作业 GPU 数。

`optimization-multi-objective` 有已知上游 host-side `dgp.py/import` 缺口，当前标记为 `review_required`；不要用它作为第一个无条件 smoke 题。

### 7.6 跑第一轮 6 题

```powershell
$Round1Tasks = @(
  "optimization-multi-objective",
  "optimization-variance-reduction",
  "ml-clustering-algorithm",
  "ml-dimensionality-reduction",
  "causal-discovery-discrete",
  "graph-generation"
)
.\scripts\submit-run-round.ps1 -Round 1 -Tasks $Round1Tasks -ApiKey $ApiKey -Model deepseekflash -ReleaseId v003 -JobSuffix first001 -Minutes 1440 -Execute
ssh qz-gpu qz-job status mr3mh-mls-v003-r1-first001
ssh qz-gpu qz-job logs mr3mh-mls-v003-r1-first001 --tail 500
```

一个 round 作业内部按给定顺序逐题运行；不是把 6 题同时塞给一张卡。每题的 MLS scheduler 在该题开始时再根据题目资源表调度其 Docker 执行。

生成轮次报告：

```powershell
$remoteCommand = @'
qz-job submit --profile cpu --cpu-spec 4c16g --name mr3mh-report-v003-r1-first001 --minutes 10 --command 'bash /inspire/hdd/project/long-working-agent/ky26299/code/mls-lite-runner-v003/platform/qz_entry.sh report /inspire/hdd/project/long-working-agent/ky26299 v003 1'
'@
ssh qz-gpu $remoteCommand.Trim()
ssh qz-gpu qz-job status mr3mh-report-v003-r1-first001
ssh qz-gpu qz-job logs mr3mh-report-v003-r1-first001 --tail 300
```

当前不要直接运行 `scripts/submit-round-report.ps1`：该包装脚本仍使用无效的 `1c4g`。这只影响报告作业的提交包装，不影响题目运行、状态文件或报告核心逻辑；改为上面的合法 `4c16g` 命令即可。

`RUN_ROUND_ORCHESTRATION_OK` 仅表示循环走到末尾。真正完成还必须看到：

```text
ROUND_COMPLETE=yes
ROUND_COUNTS=...succeeded=6...
```

## 8. 五轮 GPU 与任务划分

| 轮次 | 4090 数 | 执行方式 | 任务 |
| --- | ---: | --- | --- |
| 1 | 1 | 6 题顺序执行 | optimization 两题、clustering、dimensionality、causal、graph |
| 2 | 8 | 6 题顺序执行，题内可用多卡 | pooling、activation、membership、3dgs、dbm、vae |
| 3 | 4 | 6 题顺序执行，题内可用多卡 | 5 个 LLM 题、sparse attention |
| 4 | 1 | 6 题顺序执行 | JEPA、robotics、RL |
| 5 | 4 | 6 题顺序执行，题内可用多卡 | bio/sci/quant/time-series |

准确任务 ID 以 `manifests/lite30.json` 为唯一来源。第二轮的 `cv-dbm-sampler` 官方峰值为 12、最低 4 卡；当前设计允许在单个 8 卡节点上分波次执行其三个 4 卡项。此题仍需通过真实平台运行验证。

第一轮验证后，可以把五轮各自作为五个独立 qz-job 提交。五个作业能否同时进入运行态由平台当时的 4090 配额和调度决定；代码支持并存，但不保证平台立即同时分配 18 张卡。

## 9. 断点续跑与任务状态

状态含义：

- `pending`：尚未执行。
- `preflight_blocked`：缺少题目资产或预检条件；补齐后原命令重跑即可。
- `running`：本次作业正在处理。
- `succeeded`：成功；下次轮次运行会跳过。
- `failed`：运行失败；轮次重试需显式增加 `-RetryFailed`。

失败题重试示例：

```powershell
.\scripts\submit-run-round.ps1 -Round 1 -Tasks $Round1Tasks -ApiKey $ApiKey -Model deepseekflash -ReleaseId v003 -JobSuffix retry001 -Minutes 1440 -RetryFailed -Execute
ssh qz-gpu qz-job logs mr3mh-mls-v003-r1-retry001 --tail 500
```

预检缺失资产不会让整个 30 题状态丢失：该题记录为 blocked，其他题继续；补丁或资产补齐并留存哈希后可单独或按轮次重跑。

## 10. 常用平台查询指令

查单个作业：

```powershell
ssh qz-gpu qz-job status 作业名
ssh qz-gpu qz-job logs 作业名 --tail 300
```

查平台当前可申请资源：

```powershell
ssh qz-gpu qz-job resources
```

查某 profile 模板，而不是实际申请：

```powershell
ssh qz-gpu qz-job template-info --profile 4090 --gpus 8 --nodes 1
```

实际 GPU 可见性必须在已提交的 GPU 作业内部检查；profile 支持多卡参数不等于每次都能立刻调度到资源。

## 11. 清理与恢复原则

正常失败后不要先删除 release 或共享 venv；先看该作业 `status` 和 `logs`。任务状态、事务记录和结果正是续跑依据。

仅在明确要重建 v003 release 时，才删除精确 release 目录。下面命令具有破坏性，但不会删除共享 venv、MLS 其他副本或其他用户目录：

```powershell
$remoteCommand = @'
qz-job submit --profile cpu --cpu-spec 4c16g --name mr3mh-clean-v003-release-only-001 --minutes 10 --command 'set -euo pipefail; TARGET=/inspire/hdd/project/long-working-agent/ky26299/code/mls-lite-runner-v003; test "$TARGET" = /inspire/hdd/project/long-working-agent/ky26299/code/mls-lite-runner-v003; rm -rf -- "$TARGET"; echo CLEAN_V003_RELEASE_OK'
'@
ssh qz-gpu $remoteCommand.Trim()
```

注意 CPU profile 的合法小规格是 `4c16g`，不是 `1c4g`。此前使用 `1c4g` 的提交被参数校验拒绝，因此没有执行删除。

共享 venv 不应通过 `rm -rf` 恢复。需要回退时应依据环境总账中的 baseline、before/after 和 resolved constraints 制定逆向安装事务，执行后再次 `pip check` 和导入测试；这是可审计恢复，不是盲目覆盖。

## 12. 下一检查点

下一检查点只有一个：`mr3mh-prepare-v003-4090-shared002` 成功出现 `PREPARE_RELEASE_OK`。在此之前不要提交 API、单题或整轮任务。

通过后严格按以下顺序推进：

```text
共享环境安装成功
→ 4090 host smoke
→ API smoke
→ 人工选择的一道题
→ 第一轮 6 题及报告
→ 其余四轮
→ 汇总 blocked/failed/succeeded 并补跑
```
