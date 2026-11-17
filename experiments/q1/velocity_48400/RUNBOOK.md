# Stage2 velocity exploratory first trial

仅本次明确授权的一轮，源193726/ckpt_0044000.pt (44000/17520)。不是验收通过；后退漂移与足接触问题未闭环；不使用45000AB、不加净空、不改数据/奖励。

原生base_dual：4096，anchor orientation-only future4，tele distill/free/ref共训；三LR=1e-4，lr_from_resume=true，total_iterations145000。Native command ranges原封不动，侧移/原地转无数据覆盖，仅探索不验收。checkpoint cadence改为100/latest和500/numbered以防40分钟硬停止丢失。

原生产src与193726/MANIFEST逐文件hash完全一致，含修复replay_buffer；不用旧editable来源。数据直接使用193726/data且hash保护。CPU preflight.json给tele_ok覆盖及源checkpoint有限性。

原生load恢复WM、E_ref/decoder、ref critic和WM/ref critic optimizer；新增Etele无旧keypoint来源，随机初始化；整个policy optimizer按原生逻辑重建；distill/free critics及optimizer新建。tele critic warmup300 updates；不能把critic-only训练称actor更新。first_tele_actor_update.json记录actor_on和Etele实际参数delta。

Native run()在env.reset之后重置t_start，不含Trainer初始化。启动总目标38min：构造后扣除初始化再设native剩余max_hours；外层2390s TERM，2400s KILL（含初始化、恢复、编译）。保存、最终eval可能耗时，硬上限优先；退出非零仅从完整周期checkpoint后评估。145000与LR跨度不因时限收缩。

pipeline.py：全局nonblocking lock + source专属一次性claim，不可删除claim重跑。background notify_on_complete。train.exit独立；final_gate验证最新checkpoint计数/有限性/hash保护；ref_eval_video与tele_eval_video独立exit；媒体失败不训练。后处理不计入训练40min。

GPU launch前32607MiB总、32003MiB可用；stage1原生4096约20GB基线；允许共享不杀其他进程。stage2实际峰值尚待运行，不能宣称不会OOM。

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
