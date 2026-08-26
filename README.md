# MLS-Bench Lite：独立 Agent、静态预检和 4090 直连运行器

本仓库包含两个逻辑独立部分：

- `mls_agent`：可整体替换的 mini-SWE Bash Agent。
- `mls_lite_runner`：30题预检、按题资产加载、五轮调度、状态恢复和报告。

MLS-Bench仍保存在自己的仓库中。接入只修改MLS的CLI注册入口，使其从外部 `mls_agent` 包导入Agent；不再复制Agent文件进MLS，也不使用CPU模型controller或 `QueueProxyModel`。

Agent迭代以release目录和Python环境双重隔离：例如 `mls-lite-runner-v001` 配 `mlsbench-lite-agent-v001`，v002使用另一组路径。MLS中的注册入口保持不变；回退时直接用旧ReleaseId提交即可。升级旧内置适配时，原来复制到MLS里的两个未跟踪文件不会被自动删除，但注册入口不再使用它们，避免误删已有工作。

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
code/MLS-Bench/                  题目、测试、评分、Docker/GPU调度
code/mini-swe-agent-v2.4.6/      固定版本的上游Agent循环依赖
code/mls-lite-runner-v001/       外部Agent + AgentRun
runtime/envs/mlsbench-lite-agent-v001 v001独立Python环境
runtime/configs/v001/            无密钥direct LiteLLM配置
runtime/state/v001/              30题持久状态
runtime/assets/receipts/v001/    按题资产加载记录
runtime/execution/v001/          workspace、日志和轮次报告
```

生成的Agent配置使用：

```yaml
model_class: litellm
```

模型名由运行参数提供。应传入本地已验证的真实LiteLLM模型标识；在确认前不要把“deepseekflash”擅自转换成其他名字。

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

## 五轮4090资源

| 轮次 | 题数 | 单节点4090 | 原因 |
|---:|---:|---:|---|
| 1 | 6 | 1 | 最高单命令1卡 |
| 2 | 6 | 8 | `cv-vae-loss`最低8卡 |
| 3 | 6 | 4 | `llm-pretrain-optimizer`最低4卡 |
| 4 | 6 | 1 | 六题均为单卡 |
| 5 | 6 | 4 | 峰值3卡，平台规格向上取4 |

所有轮次固定 `--nodes 1`。`cv-dbm-sampler` peak/minimum为12/4，MLS在8张可见卡上将同组命令分wave执行，不启动第二份AgentRun。

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

- `scripts/submit-bootstrap.ps1`：CPU下载固定release、记录MLS现状、选择接入/升级补丁、editable安装runner、生成direct配置和状态。
- `scripts/submit-install-environment.ps1`：CPU复用或创建Python 3.11共享环境，固定MLS commit与mini-SWE v2.4.6，并安装宿主依赖；不强制覆盖pip索引。
- `scripts/submit-4090-smoke.ps1`：验证指定数量4090、联网、Docker和Python导入。
- `scripts/submit-api-smoke.ps1`：在单卡4090上用同一配置发出一次最小模型请求，只报告成功与否，不打印回复或密钥。
- `scripts/submit-run-task.ps1`：按manifest为指定Lite题申请对应4090卡数，执行真实Agent/test/submit单题smoke；与整轮共用状态，成功后整轮自动跳过。
- `scripts/show-round-template.ps1`：检查对应轮次模板和资源。
- `scripts/submit-run-round.ps1`：联网4090直接调用模型API；每题先按MLS官方清单获取包、准备数据和构建镜像，再运行Agent。单题准备失败只阻塞该题并继续后续题。

所有PowerShell脚本默认只打印最终 `qz-job` 命令；只有加 `-Execute` 才会SSH提交。API Key不进Git或 `qz-job --command`，未来平台运行时从项目盘权限受限的 `runtime/secrets/deepseek.env` 加载。

## 尚需真实平台验证

- 平台MLS commit与本报告是否一致；
- 已安装mini-SWE版本与外部Agent真实导入；
- `deepseekflash`的准确LiteLLM模型名和环境变量；
- 4090作业内Docker daemon；
- 一次真实API tool call和完整 `mls-test`/`mls-submit`；
- 4090 48GB下各题实际显存。

这些属于上传后的平台smoke，本地准备阶段不执行SSH、不改平台、不上传GitHub。
