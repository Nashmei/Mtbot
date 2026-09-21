import aiosqlite, json, time
from pathlib import Path
class DB:
 def __init__(self,path): self.path=path
 async def init(self):
  Path(self.path).parent.mkdir(parents=True,exist_ok=True)
  async with aiosqlite.connect(self.path) as d:
   await d.executescript('''CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,ts REAL,event TEXT,symbol TEXT,details TEXT); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);'''); await d.commit()
 async def log(self,event,symbol='',**details):
  async with aiosqlite.connect(self.path) as d:
   await d.execute('INSERT INTO audit(ts,event,symbol,details) VALUES(?,?,?,?)',(time.time(),event,symbol,json.dumps(details,ensure_ascii=False,default=str))); await d.commit()
 async def set(self,k,v):
  async with aiosqlite.connect(self.path) as d: await d.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(k,str(v))); await d.commit()
 async def get(self,k,default=None):
  async with aiosqlite.connect(self.path) as d:
   async with d.execute('SELECT value FROM settings WHERE key=?',(k,)) as c:
    r=await c.fetchone(); return r[0] if r else default
