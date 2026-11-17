
## 完成核验（2026-11-16）
- 唯一训练和后链已结束；/proc核查无本run训练/评估进程，历史PID3848393/3848399不存在。未启动新训练、续训、重启或额外rollout；未创建cron。
- completion.json中train/final_gate/ref_eval_video/tele_eval_video均exit0，原始train.exit保持不变。native max_hours reached；外层总2295.438s（38.257min）低于hard40min。日志末尾3.97h为累计口径，不能作为本轮耗时。
- source44000/17520 -> final48400/19264，tele_updates1744；CPU重新读取最终checkpoint，递归295张量全部有限；源码、资产、保护文件及数据SHA重新通过。见completion_audit.json。
- runtime exact恢复WM/E_ref/decoder/ref critic及WM/ref critic optimizer；新增Etele、新critics及policy optimizer fresh，非全optimizer续训。first actor_on=1在44792/tele update301，Etele最大参数变化9.9977478e-5，skipped_nonfinite=0。
- E_ref四动作全部完整、0/4失败，但归一化真实回报较source44000均轻微回退，不能宣称ref改善：

|动作|44000|48400|变化|步数|失败|
|---|---:|---:|---:|---:|---:|
|straight|0.9440|0.9391|-0.0048|130|0|
|backward|0.9639|0.9627|-0.0012|149|0|
|left|0.9655|0.9633|-0.0022|149|0|
|right|0.9701|0.9680|-0.0021|149|0|

- Etele九case真实physics，每case300步/6s，0/9摔倒；tele encoder forward hook300次，全部轨迹/action/速度有限，重新计算MAE一致。初态为backward参考片段（本库无站立片段），不是从静止站立泛化验收。
- 前进不足、侧移未实现（左移平均vy反向），移动转弯有响应但误差明显；原地转有后退漂移。切换2s/4s后有滞后，最后侧移段vy仅0.0128 vs目标0.3。侧移/原地转/含侧移切换无参考覆盖，只能标探索。
- 足proxy仅foot-link高度/平面差分速度，不是接触力/足底净空/验证滑移；不能据此判自然步态合格。没有用imagined reward代替真实结果。
- 既有14个媒体逐一ffprobe计帧/50fps、完整ffmpeg decode及源与active codexgina副本SHA匹配。仅媒体拼接九个真实Etele视频：2700帧/54s/1x，不重模拟，附件见下；不是E_ref冒充velocity。
- 状态：探索完成，未自然步态验收、未部署。保留source44000作为回退，不自动执行回退/训练。
- 原速velocity附件：/home/cityur/.hermes/profiles/codexgina/media/stage2_velocity_from44000_20261116_001106_tele_iter48400/velocity_9cases_1x.mp4
- 合集SHA256：d908fc65f9deee98bbb9ead3888945425734443a4fdee34d78ef05d9930b8d6f
