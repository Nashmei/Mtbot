import csv,sys
import MetaTrader5 as mt5
sym=sys.argv[1]; out=sys.argv[2]
if not mt5.initialize(): raise SystemExit(str(mt5.last_error()))
r=mt5.copy_rates_from_pos(sym,mt5.TIMEFRAME_M5,1,400)
if r is None or len(r)<80: raise SystemExit('no bars '+sym)
with open(out,'w',newline='') as f:
 w=csv.writer(f); w.writerow(['timestamps','open','high','low','close','volume'])
 import datetime
 for x in r:w.writerow([datetime.datetime.utcfromtimestamp(int(x['time'])).isoformat(),x['open'],x['high'],x['low'],x['close'],x['tick_volume']])
mt5.shutdown()
