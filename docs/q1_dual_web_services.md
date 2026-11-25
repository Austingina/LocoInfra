# Q1 原线路 B / BIGBANG 新网页常驻切换

两个网页的代码、端口、令牌和 systemd 服务相互独立，但真机低层命令只允许一个控制器持有。两个启动器共用 `/tmp/q1-lowlevel-web-controller.lock`。不得尝试同时启动。

任何切换都必须先让当前控制器进入 `LYING` 或 `DAMPED`。服务管理器在其他状态会拒绝停止。

## 选择原线路 B（8768）并设为开机常驻

```bash
cd /home/run/LocoInfra_Q1_Dance

# 如新服务在运行，只会在 LYING/DAMPED 停止
python3 -m apps.new_motion_web.robot_service stop

systemctl --user disable q1-new-motion-web.service
systemctl --user enable q1-line-b-web.service

python3 -m apps.line_b_web.robot_service start \
  --confirm-not-charging \
  --confirm-low-level \
  --confirm-protected

python3 -m apps.line_b_web.robot_service url
```

## 选择 BIGBANG 新舞蹈 + 连续行走（8769）并设为开机常驻

```bash
cd /home/run/LocoInfra_Q1_Dance

# 原线路 B 只会在 LYING/DAMPED 停止
python3 -m apps.line_b_web.robot_service stop

systemctl --user disable q1-line-b-web.service

# 每次选择新服务时都重验固定包哈希、完整 MuJoCo 报告和精确代码哈希
python3 -m apps.new_motion_web.robot_service install \
  --project /home/run/LocoInfra_Q1_Dance \
  --release /home/run/q1_policy_packages/current \
  --dynamic-report /home/run/LocoInfra_Q1_Dance/apps/new_motion_web/bigbang_lineb_dynamic_report.json \
  --port 8769 \
  --enable-at-boot

python3 -m apps.new_motion_web.robot_service start \
  --confirm-not-charging \
  --confirm-low-level \
  --confirm-protected

python3 -m apps.new_motion_web.robot_service url
```

`--confirm-protected` 表示启动者已在现场确认吊带/稳定支撑、独立急停和无障碍场地。代码不会代替该物理确认。

## 只查看状态与地址

```bash
systemctl --user is-active q1-line-b-web.service q1-new-motion-web.service
systemctl --user is-enabled q1-line-b-web.service q1-new-motion-web.service
python3 -m apps.line_b_web.robot_service url
python3 -m apps.new_motion_web.robot_service url
```

机器人已配置 `Linger=yes`，终端退出不会结束已启动的 user service。开机常驻只对当前选中的一套服务启用。
