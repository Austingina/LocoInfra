# Q1 LocoInfra 假话题部署记录（2026-11-13）

## 已完成

- 部署目录：`/home/run/LocoInfra_Q1_Dance`
- 私有离线 Python 依赖：`/home/run/LocoInfra_Q1_Dance/.python`
- 已验证导入：MuJoCo 3.3.7、ONNX Runtime 1.23.2、NumPy 1.26.4、ROS 2 `rclpy`、`aimdk_msgs`
- 已加入自动假话题检查：`apps/dance_web/q1_dry_run.sh`
- 假命令话题硬编码为 `/kabutack/dry_run/joint/command`，真实电机话题是 `/aima/hal/joint/command`。
- 已修正原厂 MC 进程检测：当前固件的进程 comm 为 `mc_main`，可执行文件为 `mc_app_main`。
- 预检失败时已验证无 `dry_run_check` / `spawn_main` / `resource_tracker` 残留进程。

## 当前门禁

Q1 仍在原厂标准模式，`mc_main` 在运行，ROS graph 没有：

- `/aima/hal/joint/state` (`aimdk_msgs/msg/JointStateArray`)
- `/aima/hal/imu/state` (`sensor_msgs/msg/Imu`)

因此 1 秒预检在启动实时收发子进程前安全停止，没有进入策略循环，也没有发布关节指令。

## 下一步

1. 操作员在官方 APP 选择“低层运控开发”，按当前固件的厂商流程重启。
2. 重启后先只读核对 `mc_main` 已停止、两个状态话题已出现。
3. 运行 3 秒假话题 dry-run，验证传感器连续性、ONNX 50 Hz 推理、假指令≥400 Hz 和退出清理。
4. 只在现场明确确认吊带和独立急停就位后，才可进入真实电机话题的站立保持和低速行走验证。

## 重启后结果（14:20）

- APP 低层运控开发已生效：`mc_main` / `mc_app_main` 未运行。
- `/aima/hal/joint/state` 与 `/aima/hal/imu/state` 已出现并持续发布。
- 3 秒完整 dry-run 通过：
  - 策略：150 帧，49.5 Hz；
  - 关节状态：617 帧；
  - 假指令：1476 帧，487.2 Hz；
  - ONNX 推理：mean 9.345 ms，p99 22.579 ms；
  - 控制超时计数：8 / 150；
  - 指令话题：`/kabutack/dry_run/joint/command`；
  - 真实电机话题未使用，退出无残留进程。
- 当时姿态 `upright = 0.1955`，机器人仍接近趴卧。
- 真实命令话题当时 Publisher 0、Subscriber 1（`device_manager_node`）。
- 电池采样：26%，44.7 V，43.3 °C。真机起身/行走前应先继续充电。

## 充电期间软件安全加固

- 新增 `getup` / `walk` / `full` 三档断联保护，真实电机网页入口禁止关闭该保护。
- 网页自动维持控制心跳；手机锁屏、断网、关页或默认 0.3 秒心跳超时后，机器人端产生一次 `DAMP` 事件并清零速度。
- 页面底部常驻红色阻尼急停；触发后前端锁住动作，必须显式重新连接才能继续。
- `getup` 只开放起身/站立/阻尼；`walk` 只增加前后行走，前进上限 0.08 m/s。
- 控制逻辑、HTTP 令牌与断联保护回归测试共 10 项已通过；测试未连接机器人，未发布任何电机指令。

## 充电完成后恢复（15:05–15:09）

- SSH 已恢复，机器人本次开机时间 `2026-11-13 15:03:59 +08:00`。
- `mc_main` / `mc_app_main` 与旧网页控制器均未运行；关节、IMU 低层状态话题正常。
- 机器人热点为 `Q1-2610001106`，网页热点侧地址为 `192.168.88.88`；`p2p0` 工作在 AP 模式。
- 新版网页的五个文件经 SHA256 一致性校验后同步到 `/home/run/LocoInfra_Q1_Dance/apps/dance_web/`；旧版备份位于 `.codex_backups/20261113_1508_web_v1/`。
- 机器人本机 10 项网页控制测试全部通过。
- 3 秒假话题复测通过：策略 49.2 Hz、假指令 484.1 Hz、推理 mean 8.554 ms / p99 22.428 ms、真实电机话题未使用、`upright=0.195`。
- 曾在 8767 端口启动 `--dry-run --safety-profile getup` 网页供手机验证；随后已停止。

## 首次真实起身与折叠姿态诊断（15:20–15:52）

- 在用户确认充电线移开、吊带/独立急停/空旷场地就位后，真实网页以 `getup` 配置启动。
- 点击一次起身后，`PrimeBotBackend` 在进入策略前触发“关节实际位置偏离指令过大”，自动切阻尼；机器人未产生可见运动。
- 真实关节状态与 `getup_facedown` 首帧最大差为 2.79 rad（右髋 pitch）：当前双侧 hip pitch 约 -2.91 rad、hip yaw 约 ±1.47 rad，是收储式折叠趴卧；现有平趴参考首帧的 hip pitch 约 -0.12 rad、hip yaw 约 ±0.1 rad。
- 全部 455 帧现有起身参考中也没有接近该折叠姿态的帧：最近帧 RMS 仍为 0.98 rad，最大单关节差 2.52 rad。
- 官方 SDK 说明低层运控开发会关闭本体高层运控。实机低层模式虽能发现 `SetMcAction/GetMcAction` 名称，但只读 `GetMcAction` 没有可用响应端，因此不能在同一低层模式直接借用原生 `STAND_UP`。
- 真实网页已停止，8767 端口关闭，机器人保持阻尼；未再次尝试真实起身。

## 折叠起身路线的软件进展

- 新增 `apps/teleop/lying_pose.py`：用双侧 hip pitch/yaw 区分折叠趴卧与平趴。
- 状态机新增可选 `FOLDED_GETUP` 技能入口；未安装该技能时，折叠姿态会停留在 `LYING` 并明确拒绝，禁止进入旧策略首帧插值。
- `getup_test.py` 同样增加折叠姿态门禁，避免命令行测试重复触发后端偏差故障。
- 新增 `capture_native_folded_getup.py`：在保留原厂 MC 的开发模式下，通过公开 `SetMcAction(STAND_UP)` 记录关节、IMU、`/odom` 与 `/odom_torso`，且必须显式传入 `--execute-native-action` 并现场输入 `STAND`。
- 新增 `native_capture_to_motion.py`：成功记录转换为 50 Hz SuperMimic 参考；`/odom` 有效覆盖不足 95% 时拒绝生成，不猜测根轨迹。
- 机器人端16项姿态识别、网页控制和权重契约测试全部通过。
- 真机关节/IMU + 假命令话题验证通过：网页收到起身请求后保持 `LYING`，返回“检测到折叠趴卧：当前平趴起身包不适用，折叠起身技能尚未安装”；未调用 `reset()`、未进入策略、未写真实电机话题。
- 远端本轮备份：`/home/run/LocoInfra_Q1_Dance/.codex_backups/20261113_1545_folded_route/`。

## 下一项需要现场操作

在 APP 中暂时退出“低层运控开发”，选择保留原厂高层运控的开发模式并按厂家流程重新上下电。重启后先运行无动作的原生起身采集预检；预检通过后，再在同样的吊带与独立急停保护下记录一次原生折叠起身。采集前不启动低层网页控制器。
