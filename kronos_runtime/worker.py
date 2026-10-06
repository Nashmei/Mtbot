import argparse,json,os,sys,time
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'vendor'/'Kronos'))
from model import Kronos,KronosTokenizer,KronosPredictor

def main():
 p=argparse.ArgumentParser(); p.add_argument('--input',required=True); p.add_argument('--symbol',required=True); p.add_argument('--out',required=True); p.add_argument('--pred',type=int,default=12); a=p.parse_args()
 df=pd.read_csv(a.input); df['timestamps']=pd.to_datetime(df['timestamps'],unit='s',utc=True).dt.tz_localize(None); df=df.tail(400).reset_index(drop=True)
 tok=KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base'); model=Kronos.from_pretrained('NeoQuasar/Kronos-small'); pred=KronosPredictor(model,tok,max_context=512)
 x=df[['open','high','low','close','volume']].copy(); xt=df['timestamps']; step=xt.iloc[-1]-xt.iloc[-2]; yt=pd.Series([xt.iloc[-1]+step*(i+1) for i in range(a.pred)])
 paths=[]
 for _ in range(5): paths.append(pred.predict(x,xt,yt,a.pred,T=1.0,top_p=.9,sample_count=1,verbose=False))
 last=float(x.close.iloc[-1]); closes=[float(z.close.iloc[-1]) for z in paths]; ups=sum(v>last for v in closes); downs=5-ups; side='BUY' if ups>=4 else ('SELL' if downs>=4 else 'NONE'); consensus=max(ups,downs)/5
 all_hi=[float(z.high.max()) for z in paths]; all_lo=[float(z.low.min()) for z in paths]
 out={'generated_at':time.time(),'symbol':a.symbol,'timeframe':'M5','side':side,'confidence':round(consensus*100,1),'sample_count':5,'expected_close':sum(closes)/5,'expected_high':sum(all_hi)/5,'expected_low':sum(all_lo)/5}
 os.makedirs(os.path.dirname(a.out),exist_ok=True); tmp=a.out+'.tmp'; open(tmp,'w').write(json.dumps(out)); os.replace(tmp,a.out); print(json.dumps(out))
if __name__=='__main__': main()
