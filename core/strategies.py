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


# ============================================================
# استراتيجية: SWEEP REVERSAL
# ============================================================
SCALP_SWEEP_REVERSAL = {
    'name': 'scalp_sweep_reversal',
    'lookback': 20,
    'wick_body_ratio': 1.5,
    'score_base': 76,
    'score_cap': .90,
    'sl_atr_min': .65,
    'sl_atr_max': 1.7,
    'sl_range_factor': .30,
}

def scalp_sweep_reversal(ctx):
    rates=ctx['rates']
    if not ctx['have'] or rates is None or len(rates)<SCALP_SWEEP_REVERSAL['lookback']+2:
        return None
    point=ctx['point']; atrp=ctx['atrp']
    o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
    l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
    i=-1; n=SCALP_SWEEP_REVERSAL['lookback']
    prior_hi=float(np.max(h[-(n+1):-1])); prior_lo=float(np.min(l[-(n+1):-1]))
    body=abs(float(c[i]-o[i]))
    body_ref=max(body,point)
    upper=float(h[i]-max(o[i],c[i])); lower=float(min(o[i],c[i])-l[i])

    swept_low=l[i]<prior_lo and c[i]>prior_lo
    swept_high=h[i]>prior_hi and c[i]<prior_hi
    buy_reject=swept_low and lower>=body_ref*SCALP_SWEEP_REVERSAL['wick_body_ratio']
    sell_reject=swept_high and upper>=body_ref*SCALP_SWEEP_REVERSAL['wick_body_ratio']

    side=None
    if buy_reject and ctx['tick_momentum_fast']>0 and ctx['h1_bias'] in (0,1):
        side=Side.BUY
    elif sell_reject and ctx['tick_momentum_fast']<0 and ctx['h1_bias'] in (0,-1):
        side=Side.SELL
    if side is None or not ctx['strategy_allowed'](SCALP_SWEEP_REVERSAL['name'],side):
        return None

    wick=lower if side==Side.BUY else upper
    wick_ratio=wick/body_ref
    score=SCALP_SWEEP_REVERSAL['score_base']+min(8,(wick_ratio-SCALP_SWEEP_REVERSAL['wick_body_ratio'])*4)+min(6,abs(ctx['tick_momentum_fast'])/max(atrp,1)*10)
    slp=max(10.,min(SCALP_SWEEP_REVERSAL['sl_atr_max']*atrp,max(SCALP_SWEEP_REVERSAL['sl_atr_min']*atrp,ctx['tick_range']*SCALP_SWEEP_REVERSAL['sl_range_factor'])))
    return Signal(side,SCALP_SWEEP_REVERSAL['name'],min(SCALP_SWEEP_REVERSAL['score_cap'],score/100.),slp,'M5 failed breakout + wick rejection + live reversal confirmation')


# ============================================================
# استراتيجية: SQUEEZE EXPANSION
# ============================================================
SCALP_SQUEEZE_EXPANSION = {
    'name': 'scalp_squeeze_expansion',
    'short_atr': 14,
    'long_atr': 50,
    'squeeze_ratio': .65,
    'expansion_body_atr': 1.20,
    'score_base': 76,
    'score_cap': .90,
    'sl_atr_min': .65,
    'sl_atr_max': 1.7,
    'sl_range_factor': .30,
}

def diagnose_scalp_sweep_reversal(ctx):
    rates=ctx['rates']; n=SCALP_SWEEP_REVERSAL['lookback']
    out={'strategy':SCALP_SWEEP_REVERSAL['name']}
    if not ctx['have'] or rates is None or len(rates)<n+2:
        out.update({'stage':'insufficient_data','have':bool(ctx['have']),'bars':0 if rates is None else len(rates),'required_bars':n+2})
        return out
    point=ctx['point']
    o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
    l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
    prior_hi=float(np.max(h[-(n+1):-1])); prior_lo=float(np.min(l[-(n+1):-1]))
    body=abs(float(c[-1]-o[-1])); body_ref=max(body,point)
    upper=float(h[-1]-max(o[-1],c[-1])); lower=float(min(o[-1],c[-1])-l[-1])
    swept_low=bool(l[-1]<prior_lo and c[-1]>prior_lo)
    swept_high=bool(h[-1]>prior_hi and c[-1]<prior_hi)
    lower_ratio=float(lower/body_ref); upper_ratio=float(upper/body_ref)
    wick_required=float(SCALP_SWEEP_REVERSAL['wick_body_ratio'])
    buy_wick=bool(swept_low and lower_ratio>=wick_required)
    sell_wick=bool(swept_high and upper_ratio>=wick_required)
    fast=float(ctx['tick_momentum_fast']); h1=int(ctx['h1_bias'])
    buy_live=fast>0; sell_live=fast<0
    buy_h1=h1 in (0,1); sell_h1=h1 in (0,-1)
    buy_allowed=bool(ctx['strategy_allowed'](SCALP_SWEEP_REVERSAL['name'],Side.BUY))
    sell_allowed=bool(ctx['strategy_allowed'](SCALP_SWEEP_REVERSAL['name'],Side.SELL))
    candidate_side='BUY' if buy_wick and buy_live and buy_h1 and buy_allowed else ('SELL' if sell_wick and sell_live and sell_h1 and sell_allowed else None)
    failed=[]
    if not (swept_low or swept_high): failed.append('no_sweep')
    if swept_low and not buy_wick: failed.append('buy_wick_ratio')
    if swept_high and not sell_wick: failed.append('sell_wick_ratio')
    if buy_wick and not buy_live: failed.append('buy_live_momentum')
    if sell_wick and not sell_live: failed.append('sell_live_momentum')
    if buy_wick and not buy_h1: failed.append('buy_h1_guard')
    if sell_wick and not sell_h1: failed.append('sell_h1_guard')
    if buy_wick and buy_live and buy_h1 and not buy_allowed: failed.append('buy_strategy_allowed')
    if sell_wick and sell_live and sell_h1 and not sell_allowed: failed.append('sell_strategy_allowed')
    out.update({
        'stage':'candidate' if candidate_side else 'blocked','candidate_side':candidate_side,
        'swept_low':swept_low,'swept_high':swept_high,
        'lower_wick_ratio':round(lower_ratio,3),'upper_wick_ratio':round(upper_ratio,3),'wick_required':wick_required,
        'tick_momentum_fast':round(fast,3),'h1_bias':h1,
        'buy_allowed':buy_allowed,'sell_allowed':sell_allowed,'failed':failed,
    })
    return out


def scalp_squeeze_expansion(ctx):
    rates=ctx['rates']
    need=SCALP_SQUEEZE_EXPANSION['long_atr']+2
    if not ctx['have'] or rates is None or len(rates)<need:
        return None
    point=ctx['point']; atrp=ctx['atrp']
    o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
    l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)

    prev=c[:-1]
    tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-prev),np.abs(l[1:]-prev)))
    prior_tr=tr[:-1]
    short_n=SCALP_SQUEEZE_EXPANSION['short_atr']; long_n=SCALP_SQUEEZE_EXPANSION['long_atr']
    if len(prior_tr)<long_n:
        return None
    atr_short=float(np.mean(prior_tr[-short_n:]))
    atr_long=float(np.mean(prior_tr[-long_n:]))
    if atr_long<=0 or atr_short/atr_long>=SCALP_SQUEEZE_EXPANSION['squeeze_ratio']:
        return None

    body=float(c[-1]-o[-1])
    body_abs=abs(body)
    if body_abs<atr_short*SCALP_SQUEEZE_EXPANSION['expansion_body_atr']:
        return None

    side=None
    if body>0 and ctx['tick_momentum']>0 and ctx['micro_trend']>0 and ctx['context_up'] and ctx['h1_bias'] in (0,1):
        side=Side.BUY
    elif body<0 and ctx['tick_momentum']<0 and ctx['micro_trend']<0 and ctx['context_dn'] and ctx['h1_bias'] in (0,-1):
        side=Side.SELL
    if side is None or not ctx['strategy_allowed'](SCALP_SQUEEZE_EXPANSION['name'],side):
        return None

    compression=max(0.,1.-atr_short/atr_long)
    expansion=body_abs/max(atr_short,point)
    score=SCALP_SQUEEZE_EXPANSION['score_base']+min(7,compression*12)+min(7,max(0.,expansion-SCALP_SQUEEZE_EXPANSION['expansion_body_atr'])*5)
    slp=max(10.,min(SCALP_SQUEEZE_EXPANSION['sl_atr_max']*atrp,max(SCALP_SQUEEZE_EXPANSION['sl_atr_min']*atrp,ctx['tick_range']*SCALP_SQUEEZE_EXPANSION['sl_range_factor'])))
    return Signal(side,SCALP_SQUEEZE_EXPANSION['name'],min(SCALP_SQUEEZE_EXPANSION['score_cap'],score/100.),slp,'M5 volatility squeeze + closed-bar expansion + live momentum confirmation')


def diagnose_scalp_squeeze_expansion(ctx):
    rates=ctx['rates']; need=SCALP_SQUEEZE_EXPANSION['long_atr']+2
    out={'strategy':SCALP_SQUEEZE_EXPANSION['name']}
    if not ctx['have'] or rates is None or len(rates)<need:
        out.update({'stage':'insufficient_data','have':bool(ctx['have']),'bars':0 if rates is None else len(rates),'required_bars':need})
        return out
    point=ctx['point']
    o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
    l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
    prev=c[:-1]
    tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-prev),np.abs(l[1:]-prev)))
    prior_tr=tr[:-1]
    short_n=SCALP_SQUEEZE_EXPANSION['short_atr']; long_n=SCALP_SQUEEZE_EXPANSION['long_atr']
    if len(prior_tr)<long_n:
        out.update({'stage':'insufficient_tr','tr_bars':len(prior_tr),'required_tr_bars':long_n})
        return out
    atr_short=float(np.mean(prior_tr[-short_n:])); atr_long=float(np.mean(prior_tr[-long_n:]))
    ratio=float(atr_short/atr_long) if atr_long>0 else float('inf')
    squeeze_required=float(SCALP_SQUEEZE_EXPANSION['squeeze_ratio'])
    squeeze_ok=bool(atr_long>0 and ratio<squeeze_required)
    body=float(c[-1]-o[-1]); body_abs=abs(body)
    expansion_required=float(SCALP_SQUEEZE_EXPANSION['expansion_body_atr'])
    expansion_ratio=float(body_abs/max(atr_short,point))
    expansion_ok=bool(expansion_ratio>=expansion_required)
    tm=float(ctx['tick_momentum']); micro=float(ctx['micro_trend']); h1=int(ctx['h1_bias'])
    buy_direction=body>0; sell_direction=body<0
    buy_live=tm>0 and micro>0 and bool(ctx['context_up']) and h1 in (0,1)
    sell_live=tm<0 and micro<0 and bool(ctx['context_dn']) and h1 in (0,-1)
    buy_allowed=bool(ctx['strategy_allowed'](SCALP_SQUEEZE_EXPANSION['name'],Side.BUY))
    sell_allowed=bool(ctx['strategy_allowed'](SCALP_SQUEEZE_EXPANSION['name'],Side.SELL))
    candidate_side='BUY' if squeeze_ok and expansion_ok and buy_direction and buy_live and buy_allowed else ('SELL' if squeeze_ok and expansion_ok and sell_direction and sell_live and sell_allowed else None)
    failed=[]
    if not squeeze_ok: failed.append('squeeze_ratio')
    if not expansion_ok: failed.append('expansion_body')
    if buy_direction and not buy_live: failed.append('buy_live_context')
    if sell_direction and not sell_live: failed.append('sell_live_context')
    if buy_direction and buy_live and not buy_allowed: failed.append('buy_strategy_allowed')
    if sell_direction and sell_live and not sell_allowed: failed.append('sell_strategy_allowed')
    out.update({
        'stage':'candidate' if candidate_side else 'blocked','candidate_side':candidate_side,
        'squeeze_ratio':round(ratio,4) if np.isfinite(ratio) else None,'squeeze_required_lt':squeeze_required,'squeeze_ok':squeeze_ok,
        'expansion_ratio':round(expansion_ratio,4),'expansion_required_gte':expansion_required,'expansion_ok':expansion_ok,
        'body_direction':'BUY' if buy_direction else ('SELL' if sell_direction else 'FLAT'),
        'tick_momentum':round(tm,3),'micro_trend':round(micro,3),'context_up':bool(ctx['context_up']),'context_dn':bool(ctx['context_dn']),'h1_bias':h1,
        'buy_allowed':buy_allowed,'sell_allowed':sell_allowed,'failed':failed,
    })
    return out


# ============================================================
# EXPERIMENTAL INDICATOR STRATEGIES (branch: 5test)
# Scalp-oriented: M1 trigger when available, M5 context, live confirmation.
# ============================================================

def _series_ema(values, period):
    values=np.asarray(values,dtype=float)
    if len(values)==0:return np.asarray([],dtype=float)
    out=np.empty(len(values),dtype=float); out[0]=values[0]
    k=2.0/(period+1.0)
    for i in range(1,len(values)):
        out[i]=k*values[i]+(1.0-k)*out[i-1]
    return out

def _indicator_sl(ctx, min_atr=.55, max_atr=1.35, range_factor=.25):
    atrp=ctx['atrp']
    return max(8.,min(max_atr*atrp,max(min_atr*atrp,ctx['tick_range']*range_factor)))

def _scalp_rates(ctx, minimum=35):
    r=ctx.get('rates_m1')
    return r if r is not None and len(r)>=minimum else ctx['rates']

MACD_STRATEGY={'name':'macd_momentum','fast':8,'slow':21,'signal':5,'recent_bars':3,'score_base':75,'score_cap':.91}
def macd_momentum(ctx):
    rates=_scalp_rates(ctx,35)
    if rates is None or len(rates)<35:return None
    c=np.asarray(rates['close'],float)
    fast=_series_ema(c,MACD_STRATEGY['fast']); slow=_series_ema(c,MACD_STRATEGY['slow'])
    macd=fast-slow; sig=_series_ema(macd,MACD_STRATEGY['signal']); hist=macd-sig
    recent=MACD_STRATEGY['recent_bars']
    cross_up=any(macd[i-1]<=sig[i-1] and macd[i]>sig[i] for i in range(-recent,0))
    cross_dn=any(macd[i-1]>=sig[i-1] and macd[i]<sig[i] for i in range(-recent,0))
    buy=cross_up and hist[-1]>0 and hist[-1]>=hist[-2] and ctx['tick_momentum_fast']>0 and ctx['micro_trend']>0
    sell=cross_dn and hist[-1]<0 and hist[-1]<=hist[-2] and ctx['tick_momentum_fast']<0 and ctx['micro_trend']<0
    side=Side.BUY if buy else (Side.SELL if sell else None)
    if side is None or not ctx['strategy_allowed'](MACD_STRATEGY['name'],side):return None
    # M15 is a guard, but neutral is allowed for fast scalp entries.
    if ctx['m15_bias'] not in (0,1 if side==Side.BUY else -1):return None
    strength=abs(hist[-1])/max(ctx['atrp']*ctx['point'],ctx['point'])
    score=MACD_STRATEGY['score_base']+min(10,strength*18)+min(6,abs(ctx['tick_momentum_fast'])/max(ctx['atrp'],1)*12)
    return Signal(side,MACD_STRATEGY['name'],min(MACD_STRATEGY['score_cap'],score/100.),_indicator_sl(ctx,.55,1.30,.24),'Scalp MACD 8/21/5 recent cross + rising histogram + live momentum')

ALLIGATOR_STRATEGY={'name':'alligator_trend','jaw':13,'teeth':8,'lips':5,'score_base':75,'score_cap':.91}
def alligator_trend(ctx):
    rates=_scalp_rates(ctx,35)
    if rates is None or len(rates)<35:return None
    c=np.asarray(rates['close'],float)
    jaw=_series_ema(c,13)[-1]; teeth=_series_ema(c,8)[-1]; lips=_series_ema(c,5)[-1]
    gap=(max(jaw,teeth,lips)-min(jaw,teeth,lips))/ctx['point']
    min_gap=max(1.5,ctx['atrp']*.045)
    buy=lips>teeth>jaw and ctx['live']>lips and ctx['tick_momentum_fast']>0 and ctx['micro_trend']>0
    sell=lips<teeth<jaw and ctx['live']<lips and ctx['tick_momentum_fast']<0 and ctx['micro_trend']<0
    side=Side.BUY if buy else (Side.SELL if sell else None)
    if side is None or gap<min_gap or not ctx['strategy_allowed'](ALLIGATOR_STRATEGY['name'],side):return None
    # For scalping, require M15 agreement; H1 may be neutral/opposite because it is too slow for the trigger.
    wanted=1 if side==Side.BUY else -1
    if ctx['m15_bias'] not in (0,wanted):return None
    score=ALLIGATOR_STRATEGY['score_base']+min(10,gap/max(ctx['atrp'],1)*18)+min(6,abs(ctx['tick_momentum_fast'])/max(ctx['atrp'],1)*12)
    return Signal(side,ALLIGATOR_STRATEGY['name'],min(ALLIGATOR_STRATEGY['score_cap'],score/100.),_indicator_sl(ctx,.58,1.35,.26),'Scalp Alligator 5/8/13 alignment + M15 guard + live momentum')

MOVING_AVERAGE_STRATEGY={'name':'moving_average_trend','fast':9,'slow':21,'recent_bars':3,'score_base':74,'score_cap':.90}
def moving_average_trend(ctx):
    rates=_scalp_rates(ctx,35)
    if rates is None or len(rates)<35:return None
    c=np.asarray(rates['close'],float)
    f=_series_ema(c,MOVING_AVERAGE_STRATEGY['fast']); s=_series_ema(c,MOVING_AVERAGE_STRATEGY['slow'])
    recent=MOVING_AVERAGE_STRATEGY['recent_bars']
    cross_up=any(f[i-1]<=s[i-1] and f[i]>s[i] for i in range(-recent,0))
    cross_dn=any(f[i-1]>=s[i-1] and f[i]<s[i] for i in range(-recent,0))
    buy=cross_up and f[-1]>s[-1] and ctx['live']>f[-1] and ctx['tick_momentum_fast']>0 and ctx['micro_trend']>0
    sell=cross_dn and f[-1]<s[-1] and ctx['live']<f[-1] and ctx['tick_momentum_fast']<0 and ctx['micro_trend']<0
    side=Side.BUY if buy else (Side.SELL if sell else None)
    if side is None or not ctx['strategy_allowed'](MOVING_AVERAGE_STRATEGY['name'],side):return None
    wanted=1 if side==Side.BUY else -1
    if ctx['m15_bias'] not in (0,wanted):return None
    gap=abs(f[-1]-s[-1])/ctx['point']
    score=MOVING_AVERAGE_STRATEGY['score_base']+min(10,gap/max(ctx['atrp']*.04,1.)*3)+min(6,abs(ctx['tick_momentum_fast'])/max(ctx['atrp'],1)*12)
    return Signal(side,MOVING_AVERAGE_STRATEGY['name'],min(MOVING_AVERAGE_STRATEGY['score_cap'],score/100.),_indicator_sl(ctx,.55,1.30,.24),'Scalp EMA 9/21 recent cross + M15 guard + live momentum')

RSI_STRATEGY={'name':'rsi_reversal','period':7,'oversold':35.,'overbought':65.,'score_base':75,'score_cap':.90}
def _rsi(values, period=14):
    v=np.asarray(values,dtype=float)
    if len(v)<period+2:return 50.
    d=np.diff(v); gains=np.maximum(d,0.); losses=np.maximum(-d,0.)
    ag=float(np.mean(gains[-period:])); al=float(np.mean(losses[-period:]))
    if al<=1e-12:return 100.
    return 100.-100./(1.+ag/al)

def rsi_reversal(ctx):
    rates=_scalp_rates(ctx,25)
    if rates is None or len(rates)<25:return None
    c=np.asarray(rates['close'],float)
    p=RSI_STRATEGY['period']; prev=_rsi(c[:-1],p); now=_rsi(c,p)
    buy=prev<=RSI_STRATEGY['oversold'] and now>prev and ctx['tick_momentum_fast']>0 and ctx['micro_trend']>0
    sell=prev>=RSI_STRATEGY['overbought'] and now<prev and ctx['tick_momentum_fast']<0 and ctx['micro_trend']<0
    side=Side.BUY if buy else (Side.SELL if sell else None)
    if side is None or not ctx['strategy_allowed'](RSI_STRATEGY['name'],side):return None
    # Reversal remains range/mixed only; clear trends are still blocked by strategy_allowed.
    extreme=max(0.,RSI_STRATEGY['oversold']-prev) if side==Side.BUY else max(0.,prev-RSI_STRATEGY['overbought'])
    score=RSI_STRATEGY['score_base']+min(9,extreme*.65)+min(6,abs(ctx['tick_momentum_fast'])/max(ctx['atrp'],1)*12)
    return Signal(side,RSI_STRATEGY['name'],min(RSI_STRATEGY['score_cap'],score/100.),_indicator_sl(ctx,.52,1.25,.23),'Scalp RSI 7 reversal from 35/65 zone + live momentum')

BOLLINGER_STRATEGY={'name':'bollinger_reversion','period':20,'stddev':1.8,'score_base':75,'score_cap':.91}
def bollinger_reversion(ctx):
    rates=_scalp_rates(ctx,25)
    if rates is None or len(rates)<25:return None
    c=np.asarray(rates['close'],float); n=BOLLINGER_STRATEGY['period']; mult=BOLLINGER_STRATEGY['stddev']
    window=c[-n:]; mean=float(np.mean(window)); std=max(float(np.std(window)),ctx['point'])
    upper=mean+mult*std; lower=mean-mult*std
    prev_window=c[-(n+1):-1]; pm=float(np.mean(prev_window)); ps=max(float(np.std(prev_window)),ctx['point'])
    prev_upper=pm+mult*ps; prev_lower=pm-mult*ps
    # Scalp trigger: touch/pierce the previous band and show immediate live rejection.
    buy=c[-2]<=prev_lower and c[-1]>c[-2] and ctx['tick_momentum_fast']>0 and ctx['micro_trend']>0
    sell=c[-2]>=prev_upper and c[-1]<c[-2] and ctx['tick_momentum_fast']<0 and ctx['micro_trend']<0
    side=Side.BUY if buy else (Side.SELL if sell else None)
    if side is None or not ctx['strategy_allowed'](BOLLINGER_STRATEGY['name'],side):return None
    excursion=(prev_lower-c[-2])/std if side==Side.BUY else (c[-2]-prev_upper)/std
    score=BOLLINGER_STRATEGY['score_base']+min(9,max(0.,excursion)*9)+min(6,abs(ctx['tick_momentum_fast'])/max(ctx['atrp'],1)*12)
    return Signal(side,BOLLINGER_STRATEGY['name'],min(BOLLINGER_STRATEGY['score_cap'],score/100.),_indicator_sl(ctx,.52,1.25,.23),'Scalp Bollinger 20/1.8 band rejection + live reversal')
