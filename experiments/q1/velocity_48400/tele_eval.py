"""Bounded native eval_tele protocol, real Etele/physics; pre-reset capture, no retraining."""
import os
os.environ['MUJOCO_GL']='egl'
import sys,json,hashlib,subprocess,shutil
from pathlib import Path
import numpy as np
import torch,mujoco
from PIL import Image,ImageDraw,ImageFont
from mjlab.managers import TerminationManager
from supermimic.envs import make_env
from supermimic.envs.dr import policy_obs
from supermimic.eval.benchmark import deterministic_env_args,load_policy_from_checkpoint
from supermimic.tele import slots as S
from supermimic.tele.interface import TeleCfg,slots_from_bodies
from supermimic.robot import SPEC
O=Path(__file__).resolve().parent
# Finite named commands, not a parameter scan. Moving turns have data; side/spot are OOD exploration.
CASES=[('forward',(0.4,0,0),False),('backward',(-0.3,0,0),False),('side_left',(0,0.3,0),True),('side_right',(0,-0.3,0),True),('turn_left',(0.3,0,0.5),False),('turn_right',(0.3,0,-0.5),False),('spot_left',(0,0,0.5),True),('spot_right',(0,0,-0.5),True),('switch',(0.4,0,0),True)]
def goal(base):
 g=torch.cat([S.default_vector(),torch.zeros(S.NUM_SLOTS)])
 g[S.SLICE['base']]=torch.tensor(base)
 g[S.SLOT_DIM+S.SLOT_NAMES.index('base')]=1
 return g
if '--preflight' in sys.argv:
 assert len(CASES)==9 and all(goal(b).numel()==S.SLOT_DIM+S.NUM_SLOTS for _,b,_ in CASES)
 for name in ['ffmpeg','ffprobe']:assert shutil.which(name)
 print('TELE EVAL IMPORT/GOALS PREFLIGHT PASS');sys.exit(0)
assert (O/'final_gate.exit').read_text()=='0'
E=O/'eval_tele';E.mkdir(exist_ok=False)
C=O/'logs/stage2_velocity/ckpt_latest.pt'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
fin=json.loads((O/'final_checkpoint.json').read_text());assert sha(C)==fin['sha256']
m=json.loads((O/'MANIFEST.json').read_text())
for x in m['data']:assert sha(x['path'])==x['sha256']
torch.set_num_threads(1);torch.manual_seed(0);np.random.seed(0)
p,cfg=load_policy_from_checkpoint(C,'cuda:0');assert 'tele' in p.encoders
tele_calls=[0]
handle=p.encoders['tele'].register_forward_hook(lambda *args:tele_calls.__setitem__(0,tele_calls[0]+1))
tc=TeleCfg(**{k:tuple(v) if isinstance(v,list) else v for k,v in cfg['tele'].items()})
tc.enabled=True;tc.free_env_fraction=1.;tc.distill_env_fraction=0.;tc.resample_steps=10**9
files=[x['path'] for x in m['data']];n=len(CASES)
e=make_env(deterministic_env_args(files,n,future_frames=4,anchor_obs=True,anchor_ori_only=True,tele=tc),device='cuda:0')
cmd=e.command_manager.get_term('motion');lib=cmd.library
ci=int(np.argmin([np.abs(lib.ref_cmd[c.start:c.end].cpu().numpy()).mean() for c in lib.clips]))
cmd.forced_frame[:]=lib.clip_start[ci]
cmd.tele_forced=torch.stack([goal(b) for _,b,_ in CASES]).to('cuda:0')
obs,_=e.reset();rob=e.scene['robot'];d=rob.data
qa=torch.cat([rob.indexing.free_joint_q_adr,rob.indexing.joint_q_adr]).long()
initial=e.sim.data.qpos[:,qa].cpu().numpy().copy();initial[:,:3]-=e.scene.env_origins.cpu().numpy()
qs=[[] for _ in CASES];vel=[[] for _ in CASES];commands=[[] for _ in CASES];feet=[[] for _ in CASES];terms=[[] for _ in CASES];actions=[[] for _ in CASES]
done=[False]*n;feet_ids=[cmd.cfg.body_names.index(b) for b in S.FOOT_BODIES]
orig=TerminationManager.compute
current=None
# Capture immediately after physics and BEFORE auto reset; stop each trace at first failure.
def capture(self):
 out=orig(self)
 q=e.sim.data.qpos[:,qa].cpu().numpy().copy();q[:,:3]-=e.scene.env_origins.cpu().numpy()
 measured=slots_from_bodies(d.root_link_pos_w-e.scene.env_origins,d.root_link_quat_w,d.root_link_lin_vel_w,d.root_link_ang_vel_b,cmd.robot_body_pos_w,cmd.robot_body_quat_w,cmd.tele_idx)[:,S.SLICE['base']].cpu().numpy()
 fp=(cmd.robot_body_pos_w[:,feet_ids]-e.scene.env_origins[:,None,:]).cpu().numpy().copy()
 for i in range(n):
  if done[i]:continue
  ts={k:bool(self.get_term(k)[i]) for k in self.active_terms}
  qs[i].append(q[i]);vel[i].append(measured[i]);commands[i].append(current[i].copy());feet[i].append(fp[i]);terms[i].append(ts)
  done[i]=any(ts.values())
 return out
TerminationManager.compute=capture
try:
 with torch.no_grad():
  for step in range(300):
   # Two finite switches at 2s and 4s, held through resets; no resampling.
   base=CASES[-1][1] if step<100 else ((-0.3,0,0) if step<200 else (0,0.3,0))
   cmd.tele_forced[-1]=goal(base).to('cuda:0')
   cmd.tele_slots.copy_(cmd.tele_forced[:,:S.SLOT_DIM]);cmd.tele_slot_mask.copy_(cmd.tele_forced[:,S.SLOT_DIM:]);cmd.tele_from_ref.zero_()
   g=cmd.tele_goal;current=g[:,S.SLICE['base']].cpu().numpy().copy()
   a=p(policy_obs(e,obs),g,'tele')
   for i in range(n):
    if not done[i]:actions[i].append(a[i].cpu().numpy().copy())
   obs,_,_,_,_=e.step(a)
   if all(done):break
finally:TerminationManager.compute=orig
assert tele_calls[0]>0
rows=[]
for i,(name,base,ood) in enumerate(CASES):
 v=np.asarray(vel[i]);c=np.asarray(commands[i]);fp=np.asarray(feet[i]);err=v-c
 assert len(v)>0 and np.isfinite(v).all() and np.isfinite(qs[i]).all()
 speed=np.linalg.norm(np.diff(fp[:,:,:2],axis=0)/e.step_dt,axis=-1)
 row=dict(name=name,mode='native Etele base-only/free physics',no_data_coverage=ood,acceptance=False,steps=len(v),duration_s=len(v)*e.step_dt,fell=any(t.get('root_height',False) or t.get('root_tilt',False) for t in terms[i]),final_terms=terms[i][-1],mean_command=c.mean(0).tolist(),mean_measured=v.mean(0).tolist(),mae=np.abs(err).mean(0).tolist(),rmse=np.sqrt((err**2).mean(0)).tolist(),last_2s_mae=np.abs(err[-100:]).mean(0).tolist(),foot_proxy=dict(description='tracked foot-link height and finite-difference planar speed; NOT contact force or verified slip',height_min=fp[:,:,2].min(0).tolist(),height_max=fp[:,:,2].max(0).tolist(),mean_planar_speed=speed.mean(0).tolist() if len(speed) else None))
 rows.append(row)
 np.savez_compressed(E/(name+'_trajectory.npz'),initial_qpos=initial[i],qpos=qs[i],measured_vx_vy_wz=v,actual_command=c,error=err,foot_link_pos=fp,action=actions[i],dt=e.step_dt)
 (E/(name+'_terms.json')).write_text(json.dumps(terms[i]))
(E/'report.json').write_text(json.dumps(dict(checkpoint=fin,cases=rows,tele_encoder_forward_calls=tele_calls[0],initialization_clip=lib.clips[ci].name,initialization_note='native eval_tele lowest mean absolute reference command clip; local library has no standing clip',switch_schedule_s=[0,2,4],frame_capture='pre-reset; truncate at first failure',acceptance=False),indent=2))
handle.remove();e.close()
# Replay measured physical qpos, not a policy imagination. One 1x 50fps video per command.
model=mujoco.MjModel.from_xml_path(str(SPEC.xml_path()));data=mujoco.MjData(model)
model.vis.global_.offwidth=640;model.vis.global_.offheight=480
renderer=mujoco.Renderer(model,height=480,width=640);cam=mujoco.MjvCamera();cam.distance=1.8;cam.azimuth=135;cam.elevation=-15
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',14)
media=Path('/home/cityur/.hermes/profiles/codexgina/media')/(O.name+'_tele_iter'+str(fin['iteration']));media.mkdir(parents=True,exist_ok=False)
verified=[]
for i,(name,_,ood) in enumerate(CASES):
 video=E/(name+'.mp4')
 ff=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','640x480','-r','50','-i','-','-an','-c:v','libx264','-threads','1','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],stdin=subprocess.PIPE)
 for j,q in enumerate(qs[i]):
  data.qpos[:]=q;mujoco.mj_forward(model,data);cam.lookat[:]=[q[0],q[1],.38];renderer.update_scene(data,camera=cam)
  im=Image.fromarray(renderer.render());dr=ImageDraw.Draw(im);dr.rectangle((0,0,640,125),fill=(12,18,25))
  fmt=lambda a:' '.join(f'{x:+.2f}' for x in a)
  lines=[f'Etele BASE/FREE | ITER {fin["iteration"]} | 1x 50fps',f'{name} | t={(j+1)*.02:.2f}s | '+('NO DATA COVERAGE / EXPLORATORY' if ood else 'EXPLORATORY / NOT ACCEPTANCE'),f'actual cmd vx vy wz: {fmt(commands[i][j])}',f'measured   vx vy wz: {fmt(vel[i][j])}',('FALL / pre-reset terminal frame' if j==len(qs[i])-1 and rows[i]['fell'] else 'Real physics / saved qpos replay')]
  for k,line in enumerate(lines):dr.text((6,4+k*23),line,font=font,fill='white')
  ff.stdin.write(np.asarray(im).tobytes())
 ff.stdin.close();assert ff.wait()==0
 meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(video)]));st=next(s for s in meta['streams'] if s['codec_type']=='video')
 assert st['r_frame_rate']=='50/1' and int(st['nb_read_frames'])==len(qs[i])
 subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),'-f','null','-'],check=True)
 dst=media/video.name;shutil.copy2(video,dst);assert sha(video)==sha(dst)
 verified.append(dict(path=str(video),attachment=str(dst),sha256=sha(video),probe=meta,decode_exit=0))
renderer.close();assert sha(C)==fin['sha256']
(E/'media_manifest.json').write_text(json.dumps(dict(videos=verified,speed='1x 50fps',visually_reviewed=False),indent=2));print('TELE COMPLETE',json.dumps(rows),flush=True)
