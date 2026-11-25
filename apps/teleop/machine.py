"""手柄遥操作的状态机，每个控制周期 ``tick`` 一次（仿真和真机同一套）：

    LYING ──L2+A──▶ GETUP / FOLDED_GETUP（按实际趴姿选择技能）──放完──▶ WALK（基础模型 E_tele）
      ▲                                                        │ L2+B / L2+X
      └──放完、切阻尼── LIEDOWN（技能策略跟踪趴下参考）◀──站稳── STRAIGHTEN（命令回零、高度回站姿）
                                                               └──▶ DANCE（舞蹈技能）──放完──▶ WALK
    任意状态 BACK：急停切阻尼（DAMPED，之后可 L2+A 再起身）；WALK 中倾倒（骨盆 z 轴与竖直夹角 > 60°）自动切阻尼。
    手柄断开：趴着 / 阻尼时只提示，等重连；动作中按 pad_lost 处理——damp 立即切阻尼，
    liedown 摇杆当回中，起身 / 趴下照常放完，行走中转 STRAIGHTEN 自动趴下（重连后 BACK 仍可急停）。
    没有技能包（仅仿真）：从站姿开始直接 WALK，L2+A / START 回到站姿，L2+B 不可用。

技能策略与 E_tele 是两个网络，不能做 z 插值：切换时两边各算一次，关节目标在 blend_s 内线性过渡，
上一步 action 记过渡后的值（两个包的 default_joint_pos / action_scale 必须相同）。
PD 增益跟着策略走（各自包里 deploy.yaml 的 pd），过渡期间同样线性插值。
每次切状态（及拒绝起身）播一句语音提示，见 voice.py。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from apps.teleop.command import BACK, DH_RANGE, DH_RATE, FWD, SPEED_RANGE, SPEED_STEP, TURN, fill_goal, ramp, side_speed
from apps.teleop.gamepad import Pad
from apps.teleop.lying_pose import is_folded_prone
from apps.teleop.voice import Silent
from runtime import Backend, MuJoCoBackend, Package, Session, SkillModel, TeleGoal
from runtime.body_ref import BodyRef
from runtime.primebot_io import vendor_mc_running

LYING, FOLDED_GETUP, GETUP, WALK, STRAIGHTEN, LIEDOWN, DANCE, DAMPED = (
    "LYING", "FOLDED_GETUP", "GETUP", "WALK", "STRAIGHTEN", "LIEDOWN", "DANCE", "DAMPED"
)
FALL_UPRIGHT = 0.5
LYING_UPRIGHT = 0.5  # 真机起身前骨盆 z 轴与竖直的余弦须低于它：起身参考从趴姿开始
SETTLE_GYRO = 0.3  # rad/s：STRAIGHTEN 里骨盆角速度低于它才算站稳
READY_SAY = "准备就绪，按 L2 加 A 起身"
WALK_MSG = "左摇杆 vx / vy，右摇杆 yaw，十字键 ↑↓ 高度、←→ 速度，Y 高度回正；L2+X 跳舞，L2+B 趴下"
PAD_REMIND_S = 30.0  # 趴着 / 阻尼且没手柄时，每隔这么久提醒一次连接手柄


class Teleop:
    def __init__(self, base: Package, skill: Package | None, robot: Backend, getup_ref: Path, liedown_ref: Path,
                 speed_scale: float = 1.0, blend_s: float = 0.4, settle_s: float = 1.5, voice: Silent | None = None,
                 pad_lost: str = "damp", dance: Package | None = None, dance_ref: Path | None = None,
                 folded_skill: Package | None = None, folded_ref: Path | None = None):
        """skill：起身 / 趴下技能包（export_package.py 导出的单段动作包）；None 时只能在仿真里从站姿行走。"""
        if pad_lost not in ("damp", "liedown"):
            raise ValueError(f"pad_lost 只能是 damp / liedown：{pad_lost}")
        self.sim = isinstance(robot, MuJoCoBackend)
        if skill is None and not self.sim:
            raise ValueError("真机需要起身 / 趴下技能包")
        if skill is not None and not (np.allclose(skill.default_joint_pos, base.default_joint_pos)
                                      and np.allclose(skill.action_scale, base.action_scale)):
            raise ValueError("技能包与基础模型包的 default_joint_pos / action_scale 不同，不能共用观测历史")
        self.base, self.robot = base, robot
        self.s = Session(base, robot)
        self.goal = TeleGoal(base)
        self.pd_base = (base.kp, base.kd)
        self.has_skill = skill is not None
        if self.has_skill:
            self.skill = SkillModel(skill)
            self.getup = BodyRef(skill, getup_ref)
            self.liedown = BodyRef(skill, liedown_ref)
            self.pd_skill = (skill.kp, skill.kd)
            if not (np.allclose(base.kp, skill.kp) and np.allclose(base.kd, skill.kd)):
                print(f"[teleop] 技能包 PD 与基础模型不同（{skill.spec['pd'].get('preset', '?')}），起身 / 趴下用技能包的增益，切换时插值", flush=True)
        self.has_folded_skill = folded_skill is not None
        if self.has_folded_skill:
            if folded_ref is None:
                raise ValueError("给了折叠起身技能包但没有 folded_ref")
            if not (folded_skill.joint_names == base.joint_names
                    and np.allclose(folded_skill.default_joint_pos, base.default_joint_pos)
                    and np.allclose(folded_skill.action_scale, base.action_scale)
                    and np.isclose(folded_skill.control_dt, base.control_dt)):
                raise ValueError("折叠起身技能包与基础模型包的关节/默认姿态/动作缩放/控制周期不兼容")
            self.folded_skill = SkillModel(folded_skill)
            self.folded_getup = BodyRef(folded_skill, folded_ref)
            self.pd_folded = (folded_skill.kp, folded_skill.kd)
        self.has_dance = dance is not None
        if self.has_dance:
            if dance_ref is None:
                raise ValueError("给了舞蹈技能包但没有 dance_ref")
            if not (np.allclose(dance.default_joint_pos, base.default_joint_pos)
                    and np.allclose(dance.action_scale, base.action_scale)):
                raise ValueError("舞蹈包与基础模型包的 default_joint_pos / action_scale 不同，不能共用观测历史")
            self.dance_skill = SkillModel(dance)
            self.dance_ref = BodyRef(dance, dance_ref)
            self.pd_dance = (dance.kp, dance.kd)
        self.dt = base.control_dt
        self.speed = speed_scale
        self.blend_n = max(1, int(round(blend_s / self.dt)))
        self.settle_s = settle_s
        self.voice = voice or Silent()
        self.mode, self.msg = LYING, "趴在地上：L2+A 起身"
        self.cmd = np.zeros(6)
        self.dh = 0.0
        self.i = 0
        self.t_mode = 0.0
        self.fade = None  # (旧策略, 旧 PD, 剩余步数)
        self.after_straighten: str | None = None
        self.pad_lost = pad_lost
        self.pad_ok: bool | None = None  # 上一拍手柄是否连着，None = 还没 tick 过
        self.t_remind = 0.0
        self.target = self.kp = None  # 本周期下发的 PD 目标与 kp（不在跟踪为 None），给记录用
        self.recorder = None  # recorder.Recorder：每周期 tick 之后记一行
        if self.sim:
            self.place_lying() if self.has_skill else self.place_standing()

    # ---- 状态切换
    def _enter(self, mode: str, msg: str, say: str | None = None) -> None:
        self.mode, self.msg, self.t_mode = mode, msg, 0.0
        print(f"[teleop] {mode:10s} {msg}", flush=True)
        if say:
            self.voice.say(say)

    def place_lying(self, announce: bool = True) -> None:
        """仿真：把机器人摆成起身参考首帧（趴姿）。"""
        r = self.getup
        self.robot.set_state(r.root_pos[0], r.root_quat[0], r.joint_pos[0])
        if announce:
            self._enter(LYING, "趴在地上：L2+A 起身", READY_SAY)

    def place_standing(self) -> None:
        """仿真摆成默认站姿，直接行走。"""
        self.robot.reset()
        self.s.begin()
        self.cmd[:], self.dh, self.fade = 0.0, 0.0, None
        msg = WALK_MSG if self.has_skill else WALK_MSG.replace("，L2+B 趴下", "（没有技能包，不能趴下）")
        self._enter(WALK, msg, "可以行走")

    def damp(self, why: str, say: str | None = None) -> None:
        self.robot.damp()
        self.fade = None
        self._enter(DAMPED, f"{why}，已切阻尼：L2+A 起身" + ("，START 重置" if self.sim else ""), f"{say or why}，已切阻尼")

    def _start_getup(self) -> None:
        if not self.has_skill:
            return self.place_standing()
        r = self.getup
        if self.sim:
            self.place_lying(announce=False)
            self.robot.set_pd(*self.pd_skill)
        else:
            if vendor_mc_running():
                self.msg = "厂商运控 mc 在运行（APP 没选「低层运控开发」），不起身"
                print(f"[teleop] {self.msg}", flush=True)
                self.voice.say("高层运控正在运行，不能起身")
                return
            sensed = self.robot.sense()
            if self.robot.upright() > LYING_UPRIGHT:
                self.msg = f"upright {self.robot.upright():+.2f}：不是趴着，不起身"
                print(f"[teleop] {self.msg}", flush=True)
                self.voice.say("没有趴着，不能起身")
                return
            folded = is_folded_prone(self.base.joint_names, sensed.joint_pos)
            if folded and not self.has_folded_skill:
                self.msg = "检测到折叠趴卧：当前平趴起身包不适用，折叠起身技能尚未安装"
                print(f"[teleop] {self.msg}", flush=True)
                self.voice.say("检测到折叠趴卧，当前起身技能不适用")
                return
            self.voice.say("准备起身")
            if folded:
                r = self.folded_getup
                self.robot.set_pd(*self.pd_folded)
            else:
                self.robot.set_pd(*self.pd_skill)
            self.robot.reset(r.joint_pos[0])  # 只允许插值到与当前姿态兼容的技能首帧
        self.s.begin()
        r.start(self.robot.torso_quat())
        self.i = 0
        mode = FOLDED_GETUP if not self.sim and folded else GETUP
        self._enter(mode, f"起身 {(r.T - 1) * self.dt:.1f} s", "开始折叠起身" if mode == FOLDED_GETUP else "开始起身")

    def _start_liedown(self) -> None:
        self.liedown.start(self.robot.torso_quat())
        self.i = 0
        self.fade = (self._tele_policy, self.pd_base, self.blend_n)
        self._enter(LIEDOWN, f"趴下 {(self.liedown.T - 1) * self.dt:.1f} s", "开始趴下")

    def _start_dance(self) -> None:
        """只从已经停稳的 STRAIGHTEN 进入；技能与基础模型之间插值关节目标与 PD。"""
        self.dance_ref.start(self.robot.torso_quat())
        self.i = 0
        self.fade = (self._tele_policy, self.pd_base, self.blend_n)
        self._enter(DANCE, f"舞蹈 {(self.dance_ref.T - 1) * self.dt:.1f} s", "开始跳舞")

    def adjust_speed(self, steps: int) -> None:
        s = float(np.clip(round(self.speed + steps * SPEED_STEP, 2), *SPEED_RANGE))
        if steps == 0:
            return
        if s == self.speed:
            self.voice.say("已是最高速度" if steps > 0 else "已是最低速度")
            return
        self.speed = s
        print(f"[teleop] 速度系数 {s:.2f}", flush=True)
        self.voice.say(f"速度 {s:g}")

    # ---- 策略
    def _ref_policy(self, model: SkillModel, ref: BodyRef, i: int):
        return lambda obs: model.act(obs, ref.goal(i, self.robot.torso_quat()))

    def _tele_policy(self, obs: np.ndarray):
        fill_goal(self.goal, self.cmd)
        return self.s.model.act_tele(obs, self.goal)

    def _act(self, policy, pd: tuple[np.ndarray, np.ndarray]) -> None:
        obs = self.s.obs()
        action, target = policy(obs)
        kp, kd = pd
        if self.fade is not None:
            old, (kp_old, kd_old), left = self.fade
            _, q_old = old(obs)
            w = 1.0 - (left - 1) / self.blend_n
            target = (1.0 - w) * q_old + w * target
            action = (target - self.s.q0) / self.base.action_scale
            kp, kd = (1.0 - w) * kp_old + w * kp, (1.0 - w) * kd_old + w * kd
            self.fade = (old, (kp_old, kd_old), left - 1) if left > 1 else None
        self.robot.set_pd(kp, kd)
        self.s.apply(action, target)
        self.target, self.kp = target, kp

    def _idle(self) -> None:
        """不在跟踪：真机不发 PD（阻尼或未启动），只按控制周期等待；仿真里阻尼状态下推进物理。"""
        if self.sim:
            self.robot.damp()
            self.robot.step(self.robot.sense().joint_pos)
        else:
            time.sleep(self.dt)

    # ---- 每个控制周期
    def tick(self, pad: Pad, edges: set[str]) -> None:
        self.target = self.kp = None
        try:
            self._tick(pad, edges)
        except RuntimeError as e:  # 真机后端断流 / 超时已自行切阻尼
            self._enter(DAMPED, f"后端故障：{e}", "后端故障，已切阻尼")
        self.t_mode += self.dt
        if self.recorder is not None:
            self.recorder.step(self, pad, edges)

    def _pad_changed(self, pad: Pad) -> None:
        idle = self.mode in (LYING, DAMPED)
        first, self.pad_ok = self.pad_ok is None, pad.connected
        if pad.connected:
            if first:
                if not self.sim:
                    self.voice.say(READY_SAY)
            else:
                self.voice.say("手柄已连接" + ("，按 L2 加 A 起身" if idle else ""))
        elif idle:
            self.t_remind = PAD_REMIND_S
            self.voice.say("请连接手柄" if first else "手柄断开，请重新连接")
        elif self.pad_lost == "liedown" and self.mode != WALK:  # 行走在 _tick 里转 STRAIGHTEN 时播
            self.voice.say("手柄断开，起身后自动趴下" if self.mode in (FOLDED_GETUP, GETUP) else "手柄断开")

    def _tick(self, pad: Pad, edges: set[str]) -> None:
        if pad.connected != self.pad_ok:
            self._pad_changed(pad)
        if not pad.connected and self.mode in (LYING, DAMPED):
            self.t_remind -= self.dt
            if self.t_remind <= 0:
                self.t_remind = PAD_REMIND_S
                self.voice.say("请连接手柄")
        if not pad.connected and self.mode not in (LYING, DAMPED):
            if self.pad_lost == "damp" or not self.has_skill:
                return self.damp("手柄断开")
            if self.mode == WALK:
                self._enter(STRAIGHTEN, "手柄断开：站直后自动趴下", "手柄断开，自动趴下")
        if ("BACK" in edges or "DAMP" in edges) and self.mode not in (LYING, DAMPED):
            return self.damp("急停（BACK）", "急停")
        if self.sim and "START" in edges:
            return self.place_lying() if self.has_skill else self.place_standing()
        if "LEFT" in edges or "RIGHT" in edges:
            self.adjust_speed(("RIGHT" in edges) - ("LEFT" in edges))

        if self.mode in (LYING, DAMPED):
            if (pad.l2 and "A" in edges) or "GETUP" in edges:
                return self._start_getup()
            return self._idle()

        if self.mode in (FOLDED_GETUP, GETUP, LIEDOWN):
            if self.mode == FOLDED_GETUP:
                ref, model, pd = self.folded_getup, self.folded_skill, self.pd_folded
            else:
                ref, model, pd = (self.getup, self.skill, self.pd_skill) if self.mode == GETUP \
                    else (self.liedown, self.skill, self.pd_skill)
            self._act(self._ref_policy(model, ref, self.i), pd)
            self.i += 1
            if self.i < ref.T - 1:
                return
            if self.mode == LIEDOWN:
                self.robot.damp()
                return self._enter(LYING, "趴下完成，已切阻尼：L2+A 起身", "趴下完成，已切阻尼")
            self.cmd[:], self.dh = 0.0, 0.0
            self.fade = (self._ref_policy(model, ref, ref.T - 1), pd, self.blend_n)
            return self._enter(WALK, WALK_MSG, "起身完成，可以行走")

        if self.mode == DANCE:
            self._act(self._ref_policy(self.dance_skill, self.dance_ref, self.i), self.pd_dance)
            self.i += 1
            if self.robot.upright() < FALL_UPRIGHT:
                return self.damp("舞蹈中倾倒")
            if self.i < self.dance_ref.T - 1:
                return
            self.cmd[:], self.dh = 0.0, 0.0
            self.fade = (self._ref_policy(self.dance_skill, self.dance_ref, self.dance_ref.T - 1),
                         self.pd_dance, self.blend_n)
            return self._enter(WALK, WALK_MSG, "舞蹈完成，已回到站立")

        if self.mode == WALK:
            if (pad.l2 and "B" in edges) or "LIEDOWN" in edges:
                if not self.has_skill:
                    self.msg = "没有技能包，不能趴下"
                    self.voice.say("没有技能包，不能趴下")
                else:
                    self.after_straighten = "liedown"
                    return self._enter(STRAIGHTEN, "站直", "站直，准备趴下")
            if (pad.l2 and "X" in edges) or "DANCE" in edges:
                if not self.has_dance:
                    self.msg = "没有舞蹈技能包"
                    self.voice.say("没有舞蹈技能包")
                else:
                    self.after_straighten = "dance"
                    return self._enter(STRAIGHTEN, "站直", "站直，准备跳舞")
            if "Y" in edges:
                self.dh = 0.0
            self.dh = float(np.clip(self.dh + DH_RATE * self.dt * (pad.buttons["UP"] - pad.buttons["DOWN"]), *DH_RANGE))
            target = np.array([self.speed * (FWD if pad.ly > 0 else BACK) * pad.ly, side_speed(pad.lx, self.speed),
                               self.speed * TURN * pad.rx, self.dh, 0.0, 0.0])
        else:  # STRAIGHTEN
            target = np.zeros(6)
            self.dh = 0.0
        self.cmd = ramp(self.cmd, target, self.dt)
        self._act(self._tele_policy, self.pd_base)
        if self.robot.upright() < FALL_UPRIGHT:
            return self.damp("倾倒")
        if self.mode == STRAIGHTEN and self.t_mode >= self.settle_s and np.abs(self.cmd).max() < 1e-3 \
                and np.linalg.norm(self.robot.sense().base_ang_vel) < SETTLE_GYRO:
            action, self.after_straighten = self.after_straighten, None
            self._start_dance() if action == "dance" else self._start_liedown()

    def status(self) -> list[tuple[str, str]]:
        c = self.cmd
        rows = [("mode", self.mode), ("", self.msg)]
        if self.mode in (WALK, STRAIGHTEN):
            rows += [("vx vy wz", f"{c[0]:+.2f} {c[1]:+.2f} {c[2]:+.2f}"), ("dh m", f"{c[3]:+.3f}"), ("speed", f"{self.speed:.1f}")]
        elif self.mode in (FOLDED_GETUP, GETUP, LIEDOWN, DANCE):
            ref = self.folded_getup if self.mode == FOLDED_GETUP else \
                self.dance_ref if self.mode == DANCE else self.getup if self.mode == GETUP else self.liedown
            rows.append(("t", f"{self.i * self.dt:.1f} / {(ref.T - 1) * self.dt:.1f} s"))
        return rows
