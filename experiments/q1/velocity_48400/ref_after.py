"""Media-only completion stage; never launches training or retries."""
import json,subprocess,sys,shutil,hashlib
from pathlib import Path
O=Path(__file__).resolve().parent
E=O/'eval_replay'
def run(a): return subprocess.run(a,check=True)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
if '--preflight' in sys.argv:
 import torch,mujoco,numpy,PIL
 from mjlab.managers import TerminationManager
 from supermimic.eval.benchmark import BenchmarkRunner,load_policy_from_checkpoint
 from supermimic.robot import SPEC
 assert Path(SPEC.xml_path()).exists()
 assert Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf').exists()
 for x in ['ffmpeg','ffprobe']: assert shutil.which(x)
 print('MEDIA IMPORT PREFLIGHT PASS',flush=True)
 sys.exit(0)
assert (O/'final_gate.exit').read_text().strip()=='0'
run([sys.executable,str(E/'record.py')])
p=json.loads((E/'provenance.json').read_text()); it=p['iteration']
results=json.loads((E/'report.json').read_text()); assert len(results)==4
combined=E/'videos'/f'stage2_velocity_iter{it}_four_clips.mp4'
videos=[(combined,sum(r['steps']+1 for r in results))]; start=0
for r in results:
 count=r['steps']+1; dst=E/'videos'/f"stage2_velocity_iter{it}_{r['name']}.mp4"
 run(['ffmpeg','-y','-v','error','-i',str(combined),'-vf',f'trim=start_frame={start}:end_frame={start+count},setpts=PTS-STARTPTS','-an','-c:v','libx264','-threads','1','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(dst)])
 videos.append((dst,count)); start+=count
media=Path('/home/cityur/.hermes/profiles/codexgina/media')/f'{O.name}_iter{it}'
media.mkdir(parents=True,exist_ok=False)
verified=[]
for v,count in videos:
 meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(v)]))
 stream=next(s for s in meta['streams'] if s['codec_type']=='video')
 assert int(stream['nb_read_frames'])==count and stream['r_frame_rate']=='50/1'
 run(['ffmpeg','-v','error','-xerror','-i',str(v),'-f','null','-'])
 target=media/v.name; shutil.copy2(v,target); assert sha(v)==sha(target)
 verified.append(dict(path=str(v),attachment=str(target),sha256=sha(v),frames=count,decode_exit=0,probe=meta))
assert sha(Path(p['checkpoint']))==p['sha256']
manifest=dict(status='completed',iteration=it,num_updates=p['num_updates'],checkpoint=p['checkpoint'],checkpoint_sha256=p['sha256'],speed='1x 50fps; each clip includes independent initial frame and pre-reset terminal frame',videos=verified)
(E/'media_manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest,indent=2))
