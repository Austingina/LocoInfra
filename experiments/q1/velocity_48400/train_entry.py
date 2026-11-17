import os,sys,json,hashlib,importlib,time
from pathlib import Path
from dataclasses import asdict
import torch,tyro
from supermimic.config import TrainConfig
O=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
m=json.loads((O/'MANIFEST.json').read_text())
for mod,p in m['imports'].items(): assert importlib.import_module(mod).__file__==p
for group in ['code_sha256','robot_assets_sha256','protected_sha256']:
 for p,h in m[group].items(): assert sha(p)==h,p
cfg=tyro.cli(TrainConfig,args=json.loads((O/'argv.json').read_text()))
assert cfg.tele.enabled and cfg.env.num_envs==4096 and cfg.schedule.total_iterations==145000 and cfg.schedule.lr_from_resume
(O/'parsed_config.json').write_text(json.dumps(asdict(cfg),indent=2,default=str))
def finite(x):
 if torch.is_tensor(x): return bool(torch.isfinite(x).all())
 if isinstance(x,dict): return all(finite(v) for v in x.values())
 if isinstance(x,(tuple,list)): return all(finite(v) for v in x)
 return True
def equal(a,b):
 if torch.is_tensor(a): return torch.equal(a,b)
 if isinstance(a,dict): return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
 if isinstance(a,(tuple,list)): return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
 return a==b
if '--preflight' in sys.argv:
 c=torch.load(cfg.resume,map_location='cpu',weights_only=False)
 assert (c['iteration'],c['num_updates'])==(44000,17520) and finite(c)
 assert not any(k.startswith('encoders.tele.') for k in c['policy'])
 assert not any(k in c for k in ['distill_critic','free_critic','tele_updates'])
 from supermimic.policy.nets import Policy,policy_cfg_from_dict
 pc=policy_cfg_from_dict(c['config']['policy'])
 print('SOURCE POLICY CONFIG',pc)
 for key in ['env','policy','wm','wm_trainer','policy_trainer']: print('SOURCE',key,c['config'].get(key))
 from supermimic.envs.mdp.motion_library import MotionLibrary,discover_npz
 from supermimic.envs.tracking_env_cfg import TRACKED_BODIES
 from supermimic.robot import NJ
 from supermimic.tele.labels import compute_labels
 lib=MotionLibrary(discover_npz(cfg.data.npz_dir),TRACKED_BODIES,torch.zeros(NJ),device='cpu')
 lab=compute_labels(lib)
 coverage=dict(frames=int(lab['tele_ok'].sum()),total_frames=lib.num_frames,bins=int(lab['tele_ok_bin'].sum()),total_bins=lib.num_bins)
 assert coverage['frames']>0 and coverage['bins']>0
 (O/'preflight.json').write_text(json.dumps(dict(source_finite=True,source_iteration=44000,source_updates=17520,coverage=coverage,code_identity='all stage1 manifest hashes exact including fixed replay_buffer',initialization='native load: WM/ref/decoder/ref critic exact; WM/ref critic optimizer restored; entire policy optimizer fresh due new Etele; new distill/free critics and optimizers fresh; tele_updates=0'),indent=2))
 print('PREFLIGHT PASS',coverage);sys.exit(0)
assert sys.argv[1:]==['--launch']
with (O/'TRAIN_STARTED').open('x') as f:f.write(str(os.getpid()))
(O/'train.pid').write_text(str(os.getpid()))
from supermimic.training.loop import Trainer
t=Trainer(cfg)
c=torch.load(cfg.resume,map_location=t.device,weights_only=False)
for key,obj in [('wm',t.wm),('policy',t.policy),('critic',t.critic)]:
 for k,v in c[key].items(): assert torch.equal(v,obj.state_dict()[k]),(key,k)
for k,o in [('wm_opt',t.wm_trainer.opt),('critic_opt',t.policy_trainer.critic_opt)]:assert equal(o.state_dict(),c[k]),k
assert not t.policy_trainer.opt.state and not t.policy_trainer.distill_critic_opt.state and not t.policy_trainer.free_critic_opt.state
assert (t.iteration,t.num_updates,t.policy_trainer.tele_updates)==(44000,17520,0)
(O/'runtime_resume_verified.json').write_text(json.dumps(dict(pid=os.getpid(),iteration=t.iteration,num_updates=t.num_updates,old_models_exact=True,wm_refcritic_optimizer_exact=True,policy_optimizer_fresh=True,new_critics_fresh=True,tele_updates=0)))
del c
# Native clock begins AFTER env.reset; subtract initialization from 38 minute total target.
elapsed=time.time()-float(os.environ['TRAIN_WALL_START'])
cfg.schedule.max_hours=max(1,(2280-elapsed))/3600
(O/'budget.json').write_text(json.dumps(dict(initialization_seconds=elapsed,native_remaining_hours=cfg.schedule.max_hours,hard_total_seconds=2400,includes_reset_in_external_budget=True)))
old=t.policy_trainer.update
initial={k:v.detach().clone() for k,v in t.policy.encoders['tele'].state_dict().items()}
def update(*a,**kw):
 result=old(*a,**kw)
 count=t.policy_trainer.tele_updates
 if not (O/'first_update.json').exists() or (result.get('tele/actor_on',0) and not (O/'first_tele_actor_update.json').exists()):
  delta=max(float((v-initial[k]).abs().max()) for k,v in t.policy.encoders['tele'].state_dict().items())
  row=dict(iteration=t.iteration,num_updates_before_increment=t.num_updates,tele_updates=count,tele_parameter_max_delta=delta,metrics=result)
  name='first_tele_actor_update.json' if result.get('tele/actor_on',0) else 'first_update.json'
  (O/name).write_text(json.dumps(row,indent=2,default=float));print('UPDATE_EVIDENCE',json.dumps(row,default=float),flush=True)
 return result
t.policy_trainer.update=update
print('RUNTIME RESUME VERIFIED; native dual training starts',flush=True)
t.run()
assert all(sha(p)==h for p,h in m['protected_sha256'].items())
(O/'preservation_after_train.json').write_text(json.dumps({'all_protected_unchanged':True}))
