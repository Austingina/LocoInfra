# Q1 release runbook

## 状态与前提

本地候选已准备；尚无 GitHub Release/下载 URL。2026-11-17 检查：`gh` 不存在；`git credential fill` 退出 128、未返回凭据；GH_TOKEN/GITHUB_TOKEN 未设置；API 尝试返回 401，`git push --dry-run origin HEAD:main` 报 could not read Username。公开仓库 fetch 成功不等于有写权限。

认证必须由账号所有者在可信本机配置 Git credential helper 或环境令牌；不要把 token 发到聊天、放 remote URL、命令参数、日志或提交里。对目标仓库至少需要 Contents 写权限。后续 API 调用从 helper 捕获结果到内存，永不打印凭据。此 runbook 不修改全局认证。

## 发布检查

1. `git status --short`、`git fetch origin`，检查 upstream，禁止覆盖用户修改、禁止 force push。当前代码基于 2bfc9d26216e40622033bda8df2f5cb129e5bc85。从 detached HEAD 建立专用 release 分支，不直接改 main。
2. 保留 replay_buffer.py 和 test_replay_terminal_labels.py；只有本次授权路径入提交。`weights/dual_v3b` 必须无 diff。
3. 阅读 release notes 与资产 SHA；舞蹈五文件同步到本机 weights/dance_4x，但二进制不 git add。ZIP/训练 checkpoint 放仓库外；检查待提交新增 blob 大小。
4. 对候选 ZIP 做 CRC、路径/符号链接检查，解压到全新目录，以各 SHA256_MANIFEST 验证 inventory。使用 `python -B`，输出/venv 放包外。
5. 执行以下无真机测试；只舞蹈已授权的有界 sim_test 做物理积分，走路仅离线验证，不重跑 continuous sim.py / gate / long。

```sh
# PY 指已安装依赖的隔离解释器；BASE 是全新解压参考包，DELTA 是增量包。
PYTHONPATH=infra/SuperMimic-Q1/src:. "$PY" -B -m pytest -q -p no:cacheprovider \
  runtime/test_primebot_io.py infra/SuperMimic-Q1/tests/test_replay_terminal_labels.py
# 分别在完整舞蹈包目录运行：
"$PY" -B verify.py
"$PY" -B offline_test.py
"$PY" -B sim_test.py
# 在 reference_prepare 中：
"$PY" -B verify_integrity.py
"$PY" -B offline_reference.py
# 在 continuous_delta 中：
"$PY" -B verify_integrity.py
"$PY" -B build_scene.py --base "$BASE" --out "$OUT/native_scene.mjb"
"$PY" -B verify_offline.py --base "$BASE" --scene "$OUT/native_scene.mjb"
```

6. 扫描新增源码、文档与包内文本：不得包含私钥、真实访问令牌、密码、.env；历史 provenance 的主机路径不是凭据。核实磁盘剩余空间，不能因重复解压挤占活动训练的 checkpoint 空间。只能清理自己生成的副本，禁止删训练文件。
7. 测试通过后提交。若无用户 Git 身份，提交使用明确机器人身份 `Hermes Agent <hermes-agent@localhost>`，通过单次 `git -c` 设置，不改用户全局配置。记录完整 commit。
8. 凭据恢复后 `git push -u origin HEAD`（普通 push）；创建目标 commit 对应 tag `q1-20261117-research` 的 draft prerelease，`make_latest=false`。上传四资产与校验清单；逐项比对 size 和 API digest（或重新下载 SHA256）后再公开，避免半成品发布。
9. GitHub API GET release 和 git ls-remote 交叉核对 commit/tag/asset URL。把本页“尚未上传”改成已验证真实 URL，再提交文档更新。无认证时明确报告阻塞，不伪造 URL。

本地构建/日志目录：`/home/cityur/Gina/q1-github-release-20261117/`。四 ZIP 及 ASSETS.json 留在该目录，fresh 仅为测试副本。prepare.py 是本机审计构建脚本而非机器人安装入口。测试环境为存储 handoff 中现有 Python 3.12 venv；外部依赖未做干净安装验收。

## 不变约束

最新活动训练 checkpoint 不作为冻结制品；48400 仍 research only，不替换 dual_v3b。reference_prepare 与 continuous_delta 不是 velocity 控制器。不会连接真机、停止训练、启动服务、运行后台 rollout 或操作 G1。
