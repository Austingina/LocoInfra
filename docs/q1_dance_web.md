# Q1 行走 / 舞蹈统一控制

本分支把发布的 `dual_v3b` 速度行走与已有 `q1_smooth_dance` 单段策略放进同一个低层控制进程：

```text
实体手柄或手机网页
        ↓
输入超时、限速、状态机
        ↓
站立 / WALK ↔ DANCE / 起身 / 趴下
        ↓
唯一的 Session 与 PrimeBotBackend
        ↓
/aima/hal/joint/command
```

## 离线验证

安装仓库原有 CPU 部署依赖后，在仓库根目录运行：

```bash
infra/bin/q1py -m apps.dance_web.sim_check
infra/bin/q1py -m apps.dance_web --sim-start-standing
```

第二条命令会打印带随机控制令牌的地址。手机和运行程序的电脑在同一局域网时，把地址里的
`<本机或机器人IP>` 换成实际 IP 即可。这里的“手机”是 Safari / Chrome 浏览器，不是启元官方 APP。
网页提供前后、横移、转向、起身、舞蹈、正常停止、趴下和阻尼；真机安全配置会锁住未允许的按钮。

实体手柄仍可使用原入口：

```bash
infra/bin/q1py -m apps.teleop
```

新增按键为 `L2+X`：先自动把速度降为零并等待站稳，再进入舞蹈；舞蹈完整播放后自动切回站立。

## 切换规则

- 行走与站立都由基础模型 `policy_tele.onnx` 完成，不换控制模式。
- 舞蹈是独立 `SkillModel`。它与基础模型的关节顺序、默认站姿、动作缩放、观测历史和 PD 参数完全一致。
- 两个策略不共享潜变量，所以切换时对关节目标做 0.4 秒线性过渡。
- 正常停止只把速度命令按加速度限制降到零；阻尼是独立的紧急处置。
- 未开启安全配置时，手机超过 0.5 秒不再发速度心跳，机器人端只把速度输入归零。
- 开启安全配置时，网页自动维持控制心跳，不需要双指按住授权；锁屏、断网、关闭网页或默认 0.3 秒无心跳会在机器人端请求阻尼。
- 页面底部常驻红色“立即阻尼急停”，触发后网页锁住动作，需点击“急停后重新连接控制”才能继续。
- 网页及它的断联阻尼仍然不是独立硬件急停。

## 真机安全配置

真机访问真实电机话题时强制选择以下配置之一，不允许使用 `off`：

- `getup`：只允许起身、正常站立和阻尼；行走、趴下和舞蹈全部锁住。
- `walk`：在 `getup` 基础上只开放前后行走；前进命令上限硬限为 0.08 m/s，横移、转向和舞蹈仍锁住。
- `full`：全功能，起身、行走和舞蹈可直接操作；只在前两阶段真机验收后使用。

首次起身的启动参数形式为：

```bash
infra/bin/q1py -m apps.dance_web --backend real --enable-real --safety-profile getup
```

充电期间只做离线测试和假话题演练，不执行上述真实电机命令。

## 折叠趴卧起身

Q1 收储后的折叠趴卧与 `getup_facedown` 的平趴首帧不是同一姿态。程序会同时检查
骨盆 IMU 与双侧髋关节；检测到折叠姿态而未安装专用技能时，会停留在 `LYING` 并明确拒绝，
不会把髋关节从约 -2.9 rad 直接插值到平趴参考约 -0.12 rad。

正确的统一低层路线是单独训练一条 `FOLDED_GETUP`（折叠趴卧→站立）技能：

1. 暂时退出 APP 的“低层运控开发”，切到保留原厂高层运控的开发模式并按厂家流程重启；
2. 在吊带、独立急停和空旷场地保护下，先只做公开 SDK 预检：

   ```bash
   python3 -m apps.teleop.capture_native_folded_getup
   ```

3. 预检确认原生 MC、`SetMcAction/GetMcAction`、关节、IMU 与里程计均有效后，人工确认执行一次原生起身并记录：

   ```bash
   python3 -m apps.teleop.capture_native_folded_getup --execute-native-action
   ```

4. 在训练机的 SuperMimic-Q1 环境把成功记录转为 50 Hz 参考；转换器要求 `/odom` 覆盖率至少 95%，
   不会凭关节角猜骨盆高度：

   ```bash
   infra/SuperMimic-Q1/.venv/bin/python q1/getup/native_capture_to_motion.py \
     q1/getup/data/native_folded_getup_<时间>.npz
   ```

5. 使用 `getup_folded_native` 单段参考训练、仿真验收并导出策略包；重新进入低层模式后，通过
   `--folded-skill <策略包>` 接入网页/手柄状态机。状态机会自动选择 `FOLDED_GETUP` 或现有 `GETUP`，
   完成后统一进入 `WALK`，不需要在两个自有策略之间重启。

低层模式会关闭原厂高层运控，所以不能在当前低层进程内直接调用原生 `STAND_UP`；也不能用放宽
关节跟踪保护的方式代替折叠起身技能。

## 真机门禁

网页入口默认只跑仿真。真机必须显式同时给 `--backend real --enable-real`，并继续受以下门禁约束：

1. 厂商 APP 已进入“低层运控开发”，`mc_app_main` 不在运行；
2. PrimeBot SDK / `aimdk_msgs` 已安装且话题、关节和 IMU 契约核实；
3. 吊带、现场独立急停、空旷区域和操作员均已就位；
4. 先使用 `--dry-run` 假话题，再做站姿保持和低速行走，最后才测试舞蹈；
5. 发布权重目前只有仿真验收，不能把仿真通过视为真机放行。

先跑无网页的自动假话题检查：

```bash
bash apps/dance_web/q1_dry_run.sh --seconds 3
```

它读真实关节与 IMU、执行站立零速度 E_tele 推理，但硬编码只向
`/kabutack/dry_run/joint/command` 发布，不使用 `/aima/hal/joint/command`。通过标准是传感器连续、
策略 50 Hz 无严重超时，假指令频率不低于 400 Hz。

当前网页不依赖官方 APP 摇杆，也没有实现音乐播放；音乐同步应在真机运动链路通过后单独接入。
