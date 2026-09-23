import numpy as np
from .models import Regime, Signal, Side

# هذا الملف هو المصدر المركزي للاستراتيجيات.
# كل استراتيجية لها قسم مستقل: الإعدادات ثم منطق BUY/SELL الخاص بها.
# لا توجد هنا إدارة مخاطرة أو تنفيذ أوامر MT5.

# ============================================================
# استراتيجية: SCALP BREAKOUT
# ============================================================
SCALP_BREAKOUT = {
    'name': 'scalp_breakout',
    'score_base': 76,
    'score_cap': .92,
    'retest_atr': .15,
    'retest_min_points': 3.0,
    'sl_atr_min': .65,
    'sl_atr_max': 1.7,
    'sl_range_factor': .30,
}

def scalp_breakout(ctx):
    rates=ctx['rates']; have=ctx['have']
    if not have or len(rates)<25:
        return None, None, None, None
    point=ctx['point']; live=ctx['live']; atrp=ctx['atrp']
    tick_momentum_fast=ctx['tick_momentum_fast']; micro_trend=ctx['micro_trend']
    h5=np.asarray(rates['high'],float); l5=np.asarray(rates['low'],float); c5=np.asarray(rates['close'],float)
    closed_index=ctx['m5_breakout_closed_index']
    prior_hi=float(np.max(h5[-22:closed_index])); prior_lo=float(np.min(l5[-22:closed_index]))
    closed_hi=float(h5[closed_index]); closed_lo=float(l5[closed_index]); closed_close=float(c5[closed_index])
    retest_tol=max(SCALP_BREAKOUT['retest_min_points']*point,atrp*SCALP_BREAKOUT['retest_atr']*point)
    broke_up=closed_hi>prior_hi and closed_close>prior_hi
    broke_dn=closed_lo<prior_lo and closed_close<prior_lo
    retest_up=(live>=prior_hi-retest_tol and live<=prior_hi+retest_tol and tick_momentum_fast>0 and micro_trend>0)
    retest_dn=(live<=prior_lo+retest_tol and live>=prior_lo-retest_tol and tick_momentum_fast<0 and micro_trend<0)
    breakout_up=broke_up and retest_up and ctx['context_up'] and ctx['htf_allows'](Side.BUY)
    breakout_dn=broke_dn and retest_dn and ctx['context_dn'] and ctx['htf_allows'](Side.SELL)
    retest_level=prior_hi if broke_up else (prior_lo if broke_dn else None)
    if breakout_up or breakout_dn:
        side=Side.BUY if breakout_up else Side.SELL
        score=SCALP_BREAKOUT['score_base']+min(10,abs(ctx['tick_momentum'])/max(atrp,1)*14)+min(6,ctx['micro_gap']/max(atrp,1)*9)
        slp=max(10.,min(SCALP_BREAKOUT['sl_atr_max']*atrp,max(SCALP_BREAKOUT['sl_atr_min']*atrp,ctx['tick_range']*SCALP_BREAKOUT['sl_range_factor'])))
        return Signal(side,SCALP_BREAKOUT['name'],min(SCALP_BREAKOUT['score_cap'],score/100.),slp,'M5 breakout + direct retest + M15/H1 trend confirmation'),Regime.BREAKOUT,'scalp_breakout_retest',retest_level
    if broke_up or broke_dn:
        return None,Regime.BREAKOUT,'breakout_wait_retest_or_htf',retest_level
    return None,None,None,retest_level


# ============================================================
# استراتيجية: EMA CROSS SCALP
# ============================================================
EMA_CROSS_SCALP = {
    'name': 'ema_cross_scalp',
    'fast_period': 9,
    'slow_period': 21,
    'score_base': 72,
    'score_cap': .88,
    'sl_atr_min': .60,
    'sl_atr_max': 1.6,
    'sl_range_factor': .28,
}

def ema_cross_scalp(ctx):
    rates_m1=ctx['rates_m1']; rates=ctx['rates']
    cross_rates=rates_m1 if rates_m1 is not None and len(rates_m1)>=30 else rates
    required=30 if ctx['is_gold'] else 31
    if cross_rates is None or len(cross_rates)<required:
        return None, None, 0.0
    cc=np.asarray(cross_rates['close'],float); ema=ctx['ema']
    ema9_now=ema(cc[-24:],9); ema21_now=ema(cc[-30:],21)
    ema9_prev=ema(cc[-25:-1],9)
    ema21_prev=ema(cc[-30:-1] if ctx['is_gold'] else cc[-31:-1],21)
    cross_up=ema9_prev<=ema21_prev and ema9_now>ema21_now
    cross_dn=ema9_prev>=ema21_prev and ema9_now<ema21_now
    gap=abs(ema9_now-ema21_now)/ctx['point']
    tf='M1' if cross_rates is rates_m1 else 'M5'
    recent_move=(ctx['live']-float(ctx['mid'][-10]))/ctx['point']
    buy_live_ok=ctx['tick_momentum_fast']>0 and recent_move>0 and ctx['live']>=ctx['micro_fast']
    sell_live_ok=ctx['tick_momentum_fast']<0 and recent_move<0 and ctx['live']<=ctx['micro_fast']
    side=None
    if cross_up and ctx['m15_bias']>=0 and buy_live_ok:
        side=Side.BUY
    elif cross_dn and ctx['m15_bias']<=0 and sell_live_ok:
        side=Side.SELL
    if ctx['is_gold'] or side is None or not ctx['strategy_allowed'](EMA_CROSS_SCALP['name'],side):
        return None, tf, gap
    atrp=ctx['atrp']
    score=EMA_CROSS_SCALP['score_base']+min(10,gap/max(atrp*.05,1.)*4)+min(8,abs(ctx['tick_momentum_fast'])/max(atrp,1)*12)
    slp=max(10.,min(EMA_CROSS_SCALP['sl_atr_max']*atrp,max(EMA_CROSS_SCALP['sl_atr_min']*atrp,ctx['tick_range']*EMA_CROSS_SCALP['sl_range_factor'])))
    return Signal(side,EMA_CROSS_SCALP['name'],min(EMA_CROSS_SCALP['score_cap'],score/100.),slp,f'EMA 9/21 {tf} closed-candle cross + live momentum + M15 guard'),tf,gap


# ============================================================
# استراتيجية: SCALP TREND
# ============================================================
SCALP_TREND = {
    'name': 'scalp_trend',
    'confirmation_required': 3,
    'momentum_floor': .8,
    'acceleration_floor': .4,
    'momentum_atr_factor': .03,
    'acceleration_atr_factor': .015,
    'pullback_atr': .22,
    'pullback_min_points': 4.0,
    'score_base': 70,
    'score_cap': .92,
    'sl_atr_min': .70,
    'sl_atr_max': 1.8,
    'sl_range_factor': .33,
}

def scalp_trend(ctx):
    if ctx['is_gold'] or not (ctx['bull'] or ctx['bear']) or not ctx['have'] or not ctx['strategy_allowed'](SCALP_TREND['name']):
        return None, None, None
    side=Side.BUY if ctx['bull'] else Side.SELL
    gap_ok=ctx['micro_gap']>=ctx['momentum_min']
    momentum_ok=abs(ctx['tick_momentum'])>=ctx['momentum_min']
    acceleration=((side==Side.BUY and ctx['tick_momentum_fast']>=ctx['acceleration_min']) or
                  (side==Side.SELL and ctx['tick_momentum_fast']<=-ctx['acceleration_min']))
    rates=ctx['rates']; c5=np.asarray(rates['close'],float); h5=np.asarray(rates['high'],float); l5=np.asarray(rates['low'],float)
    ema20_5=ctx['ema'](c5[-30:],20)
    pull_tol=max(ctx['atrp']*SCALP_TREND['pullback_atr']*ctx['point'],SCALP_TREND['pullback_min_points']*ctx['point'])
    pullback=((side==Side.BUY and l5[-1]<=ema20_5+pull_tol and ctx['live']>ema20_5) or
              (side==Side.SELL and h5[-1]>=ema20_5-pull_tol and ctx['live']<ema20_5))
    htf_ok=ctx['htf_allows'](side)
    confirmations={'gap':bool(gap_ok),'momentum':bool(momentum_ok),'acceleration':bool(acceleration),'pullback':bool(pullback)}
    confirmation_score=sum(confirmations.values())
    checks={**confirmations,'htf':bool(htf_ok)}
    pullback_distance=((ctx['live']-ema20_5)/ctx['point'] if side==Side.BUY else (ema20_5-ctx['live'])/ctx['point'])
    values={'side':side.value,'confirmation_score':confirmation_score,'confirmation_required':SCALP_TREND['confirmation_required'],'micro_gap':round(float(ctx['micro_gap']),2),'momentum':round(float(ctx['tick_momentum']),2),'momentum_abs':round(abs(float(ctx['tick_momentum'])),2),'momentum_min':round(float(ctx['momentum_min']),2),'acceleration':round(float(ctx['tick_momentum_fast']),2),'acceleration_abs':round(abs(float(ctx['tick_momentum_fast'])),2),'acceleration_min':round(float(ctx['acceleration_min']),2),'ema20_m5':round(float(ema20_5),8),'live':round(float(ctx['live']),8),'pullback_distance_points':round(float(pullback_distance),2),'pullback_tolerance_points':round(float(pull_tol/ctx['point']),2),'m15_bias':ctx['m15_bias'],'h1_bias':ctx['h1_bias']}
    if htf_ok and confirmation_score>=SCALP_TREND['confirmation_required']:
        atrp=ctx['atrp']
        score=SCALP_TREND['score_base']+min(12,abs(ctx['micro_trend'])/max(atrp,1)*20)+min(10,abs(ctx['tick_momentum'])/max(atrp,1)*15)
        slp=max(10.,min(SCALP_TREND['sl_atr_max']*atrp,max(SCALP_TREND['sl_atr_min']*atrp,ctx['tick_range']*SCALP_TREND['sl_range_factor'])))
        sig=Signal(side,SCALP_TREND['name'],min(SCALP_TREND['score_cap'],score/100.),slp,f"Trend confirmation {confirmation_score}/4 + M15/H1 direction guard")
        return sig,checks,values
    return None,checks,values


# ============================================================
# استراتيجية: GOLD SCALP
# ============================================================
GOLD_SCALP = {
    'name': 'gold_scalp',
    'momentum_floor': 8.0,
    'acceleration_floor': 5.0,
    'momentum_atr_factor': .03,
    'acceleration_atr_factor': .015,
    'confirmation_required': 3,
    'range_atr': .35,
    'range_floor_factor': .60,
    'score_base': 72,
    'score_cap': .92,
    'sl_atr_min': .70,
    'sl_atr_max': 1.7,
    'sl_range_factor': .32,
}

def gold_scalp(ctx):
    if not ctx['is_gold']:
        return None, None, None
    gold_up=ctx['micro_trend']>0 and ctx['tick_momentum']>0 and ctx['tick_momentum_fast']>0 and ctx['context_up']
    gold_dn=ctx['micro_trend']<0 and ctx['tick_momentum']<0 and ctx['tick_momentum_fast']<0 and ctx['context_dn']
    gold_gap=ctx['micro_gap']>=ctx['momentum_min']
    gold_momentum=abs(ctx['tick_momentum'])>=ctx['momentum_min']
    gold_fast=abs(ctx['tick_momentum_fast'])>=ctx['acceleration_min']
    side=Side.BUY if gold_up else (Side.SELL if gold_dn else None)
    range_min=max(5.0,ctx['atrp']*GOLD_SCALP['range_atr'])
    range_ok=ctx['tick_range']>=range_min
    range_floor=ctx['tick_range']>=range_min*GOLD_SCALP['range_floor_factor']
    htf=side is not None and ctx['htf_allows'](side)
    confirmations={'gap':bool(gold_gap),'momentum':bool(gold_momentum),'acceleration':bool(gold_fast),'expansion_range':bool(range_ok)}
    score_n=sum(confirmations.values())
    checks={'direction':bool(gold_up or gold_dn),**confirmations,'htf':bool(htf),'range_floor':bool(range_floor)}
    values={'side':side.value if side is not None else 'NONE','confirmation_score':score_n,'confirmation_required':GOLD_SCALP['confirmation_required'],'micro_gap':round(float(ctx['micro_gap']),2),'gap_min':round(float(ctx['momentum_min']),2),'momentum':round(float(ctx['tick_momentum']),2),'momentum_abs':round(abs(float(ctx['tick_momentum'])),2),'momentum_min':round(float(ctx['momentum_min']),2),'acceleration':round(float(ctx['tick_momentum_fast']),2),'acceleration_abs':round(abs(float(ctx['tick_momentum_fast'])),2),'acceleration_min':round(float(ctx['acceleration_min']),2),'tick_range':round(float(ctx['tick_range']),2),'expansion_range_min':round(float(range_min),2),'expansion_range_floor':round(float(range_min*GOLD_SCALP['range_floor_factor']),2),'micro_trend':round(float(ctx['micro_trend']),2),'m15_bias':ctx['m15_bias'],'h1_bias':ctx['h1_bias']}
    if (gold_up or gold_dn) and side is not None and htf and range_floor and score_n>=GOLD_SCALP['confirmation_required']:
        atrp=ctx['atrp']
        score=GOLD_SCALP['score_base']+min(12,abs(ctx['tick_momentum'])/max(atrp,1)*18)+min(8,ctx['micro_gap']/max(atrp,1)*12)
        slp=max(12.,min(GOLD_SCALP['sl_atr_max']*atrp,max(GOLD_SCALP['sl_atr_min']*atrp,ctx['tick_range']*GOLD_SCALP['sl_range_factor'])))
        return Signal(side,GOLD_SCALP['name'],min(GOLD_SCALP['score_cap'],score/100.),slp,f"XAUUSD confirmation {score_n}/4 + direction/HTF/range guards"),checks,values
    return None,checks,values


# ============================================================
# استراتيجية: M5 REVERSAL
# ============================================================
M5_REVERSAL = {
    'name': 'scalp_m5_reversal',
    'doji_body_ratio': .12,
    'wick_body_ratio': 2.0,
    'opposite_wick_max': .8,
    'support_atr_tolerance': .18,
    'sideways_range_ratio': .55,
    'sideways_overlap_required': 4,
    'score': .76,
    'sl_atr_min': .65,
    'sl_atr_max': 1.6,
    'sl_range_factor': .30,
}

def m5_reversal_candidate(ctx):
    rates=ctx['rates']
    if not ctx['have'] or len(rates)<25:
        return None,'',False,False
    point=ctx['point']; o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
    l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
    i=ctx['m5_reversal_closed_index']
    body=abs(c[i]-o[i]); candle_range=max(h[i]-l[i],point)
    upper=h[i]-max(o[i],c[i]); lower=min(o[i],c[i])-l[i]
    doji=body<=candle_range*M5_REVERSAL['doji_body_ratio']
    hammer=body>0 and lower>=body*M5_REVERSAL['wick_body_ratio'] and upper<=body*M5_REVERSAL['opposite_wick_max'] and c[i]>=o[i]
    shooting=body>0 and upper>=body*M5_REVERSAL['wick_body_ratio'] and lower<=body*M5_REVERSAL['opposite_wick_max'] and c[i]<=o[i]
    prev_body_hi=max(o[i-1],c[i-1]); prev_body_lo=min(o[i-1],c[i-1])
    bull_engulf=c[i]>o[i] and c[i-1]<o[i-1] and o[i]<=prev_body_lo and c[i]>=prev_body_hi
    bear_engulf=c[i]<o[i] and c[i-1]>o[i-1] and o[i]>=prev_body_hi and c[i]<=prev_body_lo
    prior_end=-1
    prior_low=float(np.min(l[-22:prior_end])); prior_high=float(np.max(h[-22:prior_end]))
    sr_tol=max(ctx['atrp']*M5_REVERSAL['support_atr_tolerance']*point,4*point)
    at_support=l[i]<=prior_low+sr_tol; at_resistance=h[i]>=prior_high-sr_tol
    recent_ranges=h[-8:prior_end]-l[-8:prior_end]
    overlap=sum(1 for j in ctx['m5_sideways_overlap_indices'] if h[j]>=l[j-1] and l[j]<=h[j-1])
    sideways=(float(np.mean(recent_ranges))/max(ctx['atrp']*point,point)<M5_REVERSAL['sideways_range_ratio'] and overlap>=M5_REVERSAL['sideways_overlap_required'])
    side=None; reason=''
    if not doji and not sideways:
        if at_support and (hammer or bull_engulf):
            side=Side.BUY; reason='M5 support + '+('hammer' if hammer else 'bullish engulfing')
        elif at_resistance and (shooting or bear_engulf):
            side=Side.SELL; reason='M5 resistance + '+('shooting star' if shooting else 'bearish engulfing')
    return side,reason,doji,sideways

def scalp_m5_reversal(ctx,side,reason):
    strong_trend=(ctx['have'] and ctx['adx']>=25) or abs(ctx['context_trend'])>max(2.0,ctx['atrp']*.18)
    if side is None or not ctx['range_ok'] or strong_trend or not ctx['strategy_allowed'](M5_REVERSAL['name'],side):
        return None
    atrp=ctx['atrp']
    slp=max(10.,min(M5_REVERSAL['sl_atr_max']*atrp,max(M5_REVERSAL['sl_atr_min']*atrp,ctx['tick_range']*M5_REVERSAL['sl_range_factor'])))
    return Signal(side,M5_REVERSAL['name'],M5_REVERSAL['score'],slp,reason+'; range-only reversal on new candle')


# ============================================================
# استراتيجية: MEAN REVERSION
# ============================================================
MEAN_REVERSION = {
    'name': 'scalp_reversion',
    'z_min': 1.80,
    'fast_momentum_min': 1.25,
    'score_base': 69,
    'score_cap': .88,
    'sl_atr_min': .65,
    'sl_atr_max': 1.5,
    'sl_range_factor': .28,
}

def scalp_reversion(ctx):
    if (ctx['bull'] or ctx['bear']) or not ctx['range_ok'] or abs(ctx['micro_z'])<MEAN_REVERSION['z_min'] or abs(ctx['tick_momentum_fast'])<MEAN_REVERSION['fast_momentum_min'] or not ctx['strategy_allowed'](MEAN_REVERSION['name']):
        return None
    side=Side.SELL if ctx['micro_z']>0 and ctx['tick_momentum_fast']<0 else (Side.BUY if ctx['micro_z']<0 and ctx['tick_momentum_fast']>0 else None)
    if side is None:
        return None
    score=MEAN_REVERSION['score_base']+min(13,(abs(ctx['micro_z'])-MEAN_REVERSION['z_min'])*13)
    atrp=ctx['atrp']
    slp=max(10.,min(MEAN_REVERSION['sl_atr_max']*atrp,max(MEAN_REVERSION['sl_atr_min']*atrp,ctx['tick_range']*MEAN_REVERSION['sl_range_factor'])))
    return Signal(side,MEAN_REVERSION['name'],min(MEAN_REVERSION['score_cap'],score/100.),slp,'strong short-term extreme + confirmed live reversal')
