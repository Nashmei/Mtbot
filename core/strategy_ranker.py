"""Dynamic strategy ranking helpers.

Combines the current signal confidence with recent realized strategy performance.
Performance uses up to the latest 50 closed trades per strategy and gradually
reaches full influence after 30 samples.
"""

SIGNAL_WEIGHT = 0.70
PERFORMANCE_WEIGHT = 0.30
PERFORMANCE_WINDOW = 50
FULL_SAMPLE_SIZE = 30
NEUTRAL_PERFORMANCE_SCORE = 50.0


def _clamp(value, low, high):
    return max(low, min(high, value))


def normalize_average_points(avg_points):
    """Map average points [-1, 3] to a neutral-centered 0..100 score.

    -1 point/trade -> 0
     0 points/trade -> 50
    +3 points/trade -> 100
    """
    avg=float(avg_points or 0.0)
    avg=_clamp(avg,-1.0,3.0)
    if avg<=0:
        return (avg+1.0)*50.0
    return 50.0+(avg/3.0)*50.0


def effective_performance_score(stats):
    """Shrink small samples toward neutral until 30 trades are available."""
    stats=stats or {}
    trades=max(0,int(stats.get('trades',0) or 0))
    avg=float(stats.get('avg_points',0.0) or 0.0)
    raw=normalize_average_points(avg)
    reliability=min(1.0,trades/float(FULL_SAMPLE_SIZE))
    effective=NEUTRAL_PERFORMANCE_SCORE+(raw-NEUTRAL_PERFORMANCE_SCORE)*reliability
    return {
        'trades':trades,
        'points':float(stats.get('points',0.0) or 0.0),
        'avg_points':avg,
        'raw_score':round(raw,4),
        'reliability':round(reliability,4),
        'score':round(effective,4),
    }


def score_signal(signal, performance_stats=None):
    confidence=_clamp(float(signal.confidence)*100.0,0.0,100.0)
    perf=effective_performance_score(performance_stats)
    final=(SIGNAL_WEIGHT*confidence)+(PERFORMANCE_WEIGHT*perf['score'])
    return {
        'strategy':signal.strategy,
        'side':signal.side.value,
        'confidence':round(confidence,4),
        'performance_score':perf['score'],
        'performance_trades':perf['trades'],
        'performance_points':round(perf['points'],4),
        'performance_avg_points':round(perf['avg_points'],4),
        'performance_reliability':perf['reliability'],
        'final_score':round(final,4),
    }


def choose_best(candidates, performance_by_strategy=None, min_confidence=None):
    """Return (winner, ranked_rows).

    candidates: iterable of dicts containing at least signal, regime, decision,
    and legacy_priority.
    """
    performance_by_strategy=performance_by_strategy or {}
    ranked=[]
    for item in candidates:
        sig=item['signal']
        score=score_signal(sig,performance_by_strategy.get(sig.strategy))
        eligible=(min_confidence is None or score['confidence']>=float(min_confidence))
        ranked.append({**item,**score,'eligible':eligible})
    ranked.sort(
        key=lambda row:(
            row['final_score'],
            row['confidence'],
            -int(row.get('legacy_priority',999)),
        ),
        reverse=True,
    )
    eligible_rows=[row for row in ranked if row['eligible']]
    winner=eligible_rows[0] if eligible_rows else (ranked[0] if ranked else None)
    return winner, ranked
