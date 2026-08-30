# MLS-Bench Lite：独立 Agent、静态预检和 4090 直连运行器

本仓库包含两个逻辑独立部分：

- `mls_agent`：可整体替换的 mini-SWE Bash Agent。
- `mls_lite_runner`：30题预检、按题资产加载、五轮调度、状态恢复和报告。

MLS-Bench仍保存在自己的仓库中。接入只修改MLS的CLI注册入口，使其从外部 `mls_agent` 包导入Agent；不再复制Agent文件进MLS，也不使用CPU模型controller或 `QueueProxyModel`。

Agent迭代使用release源码胶囊隔离，宿主Python依赖由共享 `mlsbench-lite-agent` venv统一维护。共享环境可修改，但每次修改必须加独占锁、保存前后快照、登记消费者和事务；运行作业持共享锁，防止五轮运行期间环境变化。v001/v002保留为历史记录，v003标签按当前修正提交更新。

## 已绑定的版本和本地预检

本地静态分析读取现有WSL仓库：

```text
/home/mr_3mh/pro/auto research bench/MLS-Bench
MLS commit: cfd57a7e0139c72753e32e31bca593719b098717
```

报告位于：

- `reports/lite30-preflight.json`
- `reports/lite30-preflight.md`

本次结果为1题当前本地资源已齐的 `READY`、29题 `READY_WITH_WARNINGS`，没有静态硬阻塞。warning均已有自动准备路径：30个尚未获取的外部包引用由MLS官方 `vendor/packages.yaml` 固定URL/commit并在build时自动fetch，38项数据由已存在的官方prepare脚本生成；其中三题还会从同一MLS仓库中已跟踪的Harbor资产自动加载DGP：

- `optimization-variance-reduction`
- `ml-clustering-algorithm`
- `causal-discovery-discrete`

`optimization-multi-objective`也登记了DGP资产；本地目标文件已经存在且SHA正确。4个资产的来源、目标、MLS commit和SHA256记录在 `manifests/task-assets/`，实际资产不复制到本仓库。

扫描时WSL MLS工作树已有用户修改，因此报告同时记录 `mls_dirty` 和全部dirty paths；本runner没有修改该WSL仓库。

## 存储和配置关系

```text
code/mls-lite-runner-v003/                 完整、可整体替换的release
├── src/                                   外部Agent + AgentRun
├── deps/MLS-Bench/                        release专用MLS副本
├── deps/mini-swe-agent-v2.4.6/            release专用上游Agent依赖
└── runtime/
    ├── config/miniswe_bash.yaml           API smoke无密钥配置
    ├── records/environment-receipt.json   v003使用共享环境的消费者凭据
    ├── records/PREPARE_RELEASE_OK          原子写入的完整安装标记
    ├── rounds/round-N/config/              每轮独立无密钥配置
    ├── rounds/round-N/state.json           每轮独立状态
    ├── rounds/round-N/execution/           workspace和日志
    ├── assets/receipts/                   资产加载凭据
    └── locks/mls-prepare.lock             五作业共享准备锁
```

生成的Agent配置使用：

```yaml
model_class: litellm
```

模型名由运行参数提供。应传入本地已验证的真实LiteLLM模型标识；在确认前不要把“deepseekflash”擅自转换成其他名字。
配置同时写入 `api_base: "http://106.15.124.164:4000/v1"`；平台入口还会显式执行 `export LLMROUTER_BASE_URL="http://106.15.124.164:4000/v1"`。

v003固定使用 `/runtime/envs/mlsbench-lite-agent/bin/python`，要求Python >=3.10及pip可用。MLS、mini-SWE和runner源码不做editable安装，而由release专用PYTHONPATH选择；共享venv只提供公共依赖。准备过程先保存baseline和事务前快照，若依赖已齐则不修改；若缺失，只生成pip dry-run计划并停止，必须显式传入 `-AllowEnvironmentChange` 才在独占锁内安装并保存事务后快照。环境总账位于 `/runtime/env-registry/mlsbench-lite-agent`，缺少环境凭据或 `PREPARE_RELEASE_OK` 会阻止GPU作业。

## 单题真实链路

```text
AgentRun选择题目
→ 参考本地静态报告
→ 尝试加载该题已登记资产并重新预检
→ MLS CLI实例化外部MiniSWEBashAgent
→ 4090作业主Python进程通过LiteLLM直接请求模型API
→ 模型给出一个Bash action
→ Bash在无网络、只读root、仅/workspace持久可写（/tmp临时可写）的Agent Docker中执行
→ 独立命令mls-test由适配器截获并交给MLS WorkspaceTools
→ MLS启动官方测试Docker并分配CUDA_VISIBLE_DEVICES
→ mls-submit N提交
→ AgentRun写状态、日志和报告
```

Agent Bash容器不挂载项目根目录、API Key或GPU设备。题目允许修改的文件/行仍主要由prompt约束；MLS运行代码和Agent源码不在其可写范围内。

## 缺失、失败和恢复

状态包括：

```text
pending
preflight_blocked
running
succeeded
failed
```

题目本地资源缺失时：

- 不启动Agent；
- 不调用API；
- 不消耗测试次数；
- 不增加attempt；
- 标记 `preflight_blocked` 并继续下一题。

下次运行会自动重新检查blocked题。补齐后直接进入running；成功题永远跳过。单题实际执行失败会记录failed并继续后续题，使用 `--retry-failed` 明确重试。共享Python、Docker、GPU数量、Agent导入或direct配置错误属于基础设施错误，会在整轮开始前终止。

日志中的 `RUN_ROUND_ORCHESTRATION_OK` 只表示六题循环正常走到末尾；只有 `ROUND_COMPLETE=yes` 且 `ROUND_COUNTS` 为6个succeeded，才表示该轮全部完成。blocked题可在补齐后原命令重跑，failed题需加 `-RetryFailed`。

## 五轮4090资源

| 轮次 | 题数 | 单节点4090 | 原因 |
|---:|---:|---:|---|
| 1 | 6 | 1 | 最高单命令1卡 |
| 2 | 6 | 8 | `cv-vae-loss`最低8卡 |
| 3 | 6 | 4 | `llm-pretrain-optimizer`最低4卡 |
| 4 | 6 | 1 | 六题均为单卡 |
| 5 | 6 | 4 | 峰值3卡，平台规格向上取4 |

所有轮次固定 `--nodes 1`。不是30题都要8卡；第2轮整体申请8卡，是因为其中 `cv-vae-loss` 的最低需求为8。`cv-dbm-sampler` peak/minimum为12/4，MLS在8张可见卡上将同组命令分wave执行，不启动第二份AgentRun。

五轮可以提交成五个并发作业，共请求18张4090。每个作业内六题仍严格顺序执行；五个作业之间并行。每轮使用独立config、state、records和execution目录，共享MLS的package/data准备使用跨进程文件锁，避免并发写坏。提交前仍需用平台resources确认当时配额，并考虑8卡作业排队、共享文件系统锁语义、4090低利用率回收及API限流。

## 本地命令

```powershell
cd D:\programe_language_study\gpt\mls-lite-runner
$env:PYTHONPATH = (Resolve-Path src)
python -m unittest discover -s tests -v
python -m mls_lite_runner validate
python -m mls_lite_runner simulate --interrupt-after 11
```

重新读取WSL MLS并生成报告：

```bash
PYTHONPATH=/mnt/d/programe_language_study/gpt/mls-lite-runner/src \
python3 -m mls_lite_runner audit \
  --mls-root '/home/mr_3mh/pro/auto research bench/MLS-Bench'
```

## MLS可逆接入

`patches/mls-registration-clean.patch` 用于干净MLS；`patches/mls-registration-upgrade-v1.patch` 用于已经安装旧内置复制版Agent的MLS。二者都绑定上述MLS commit。

应用前必须保存：

```text
git rev-parse HEAD
git status --short
git diff --binary
所选patch的SHA256
```

先执行 `git apply --check`，再应用。撤销使用 `git apply -R --check` 和 `git apply -R`，禁止用破坏性reset清除用户已有修改。

## 平台脚本（当前仅本地准备，尚未执行）

4090上的真实host smoke、单题和整轮作业都显式提交 `--docker`。平台在用户命令前启动daemon；日志出现 `QZ_DOCKER_READY` 后，MLS才能拉取并运行题目镜像。作业使用单节点，按manifest申请1/2/4/8卡。Agent的API请求由作业宿主进程联网发出，Agent Bash沙箱继续使用 `--network none`；无需登录实例或自行启动Docker。

Docker镜像库只活在当前作业中。单题作业会在同一作业内由MLS自动补齐题包、拉取镜像并完成解题；整轮作业会在同一作业内依次准备并运行所选题。镜像拉取失败时，runner只重试题包准备（等待20/40秒，共3次），不会自动重试Agent或API。作业结束后镜像消失，但项目盘中的release、题包、数据、state、workspace和报告仍保留，因此不会丢失解题结果。

- `scripts/submit-shared-env-probe.ps1`：默认在最终4090镜像中检查共享venv的Python、pip、pip check、空间和关键导入，只写探测报告。
- `scripts/submit-prepare-release.ps1`：默认用单卡4090下载完整v003源码胶囊，应用并记录可逆MLS注册补丁，然后审计共享环境。依赖齐全时零环境修改；缺依赖时生成计划并停止，只有显式 `-AllowEnvironmentChange` 才执行有事务记录的配置。完成后生成环境凭据、完整标记和五组独立config/state。
- `scripts/submit-4090-smoke.ps1`：验证指定数量4090、联网、Docker和Python导入。
- `scripts/submit-api-smoke.ps1`：在单卡4090上用同一配置发出一次最小模型请求，只报告成功与否，不打印回复或密钥。
- `scripts/submit-run-task.ps1`：按manifest为指定Lite题申请对应4090卡数，执行真实单题。
- `scripts/show-round-template.ps1`：检查对应轮次模板和资源。
- `scripts/submit-run-round.ps1`：要求命令显式传入1至6个task；同一作业内顺序执行。五轮可分别提交并发运行。
- `scripts/submit-round-report.ps1`：不依赖交互shell，用CPU作业把指定轮报告打印到qz日志。

所有PowerShell脚本默认只打印最终 `qz-job` 命令；只有加 `-Execute` 才会SSH提交。按当前约定，API Key直接作为command参数传入，不写Git或release文件，但会出现在本机命令历史、平台作业command/元数据中。入口不会把key打印到日志。

## 尚需真实平台验证

- 平台MLS commit与本报告是否一致；
- 已安装mini-SWE版本与外部Agent真实导入；
- `deepseekflash`的准确LiteLLM模型名和环境变量；
- 4090作业内Docker daemon；
- 一次真实API tool call和完整 `mls-test`/`mls-submit`；
- 4090 48GB下各题实际显存。

这些属于上传后的平台smoke，本地准备阶段不执行SSH、不改平台、不上传GitHub。
