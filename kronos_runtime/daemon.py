import glob,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'kronos_runtime'/'data'; SIG=ROOT/'kronos_runtime'/'signals'; WORKER=ROOT/'kronos_runtime'/'worker.py'
seen={}
while True:
 for f in glob.glob(str(DATA/'*_M5.csv')):
  try:
   m=os.path.getmtime(f)
   if m<=seen.get(f,0): continue
   # "_M5.csv" is 7 chars.  Keep broker suffix/case exactly as exported.
   sym=Path(f).name[:-7]
   out=str(SIG/(sym+'_M5.json')); env=os.environ.copy(); env['HF_HOME']='/tmp/hf_kronos'
   print('KRONOS_RUN',sym,flush=True)
   r=subprocess.run(['/tmp/kronos_venv/bin/python',str(WORKER),'--input',f,'--symbol',sym,'--out',out,'--pred','12'],env=env,timeout=240)
   if r.returncode==0: seen[f]=m
   else: print('KRONOS_WORKER_EXIT',sym,r.returncode,flush=True)
  except Exception as e: print('KRONOS_ERROR',repr(e),flush=True)
 time.sleep(10)
