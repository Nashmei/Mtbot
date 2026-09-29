"""AI-native catalog of short-horizon scalping playbooks.

Every playbook fits a 2-10 minute holding thesis. Quality tier:
  A = highest edge (retest, sweep reclaim, momentum resume, pullback)
  B = solid edge with clearer setup (breakouts, range edges)
  C = opportunistic (must pass all six quality gates to be used)
"""

TIERS = {
    'A': [
        ("trend_ema_pullback", "TREND"),
        ("trend_vwap_pullback", "TREND"),
        ("trend_micro_pullback", "TREND"),
        ("trend_break_retest", "TREND"),
        ("trend_momentum_resume", "TREND"),
        ("trend_ema_stack_resume", "TREND"),
        ("trend_impulse_pullback", "TREND"),
        ("breakout_retest", "BREAKOUT"),
        ("breakout_failed_reclaim", "BREAKOUT"),
        ("reversion_failed_break", "RANGE"),
        ("liquidity_sweep_high", "VOLATILE"),
        ("liquidity_sweep_low", "VOLATILE"),
        ("liquidity_reclaim", "VOLATILE"),
        ("stop_run_reversal", "VOLATILE"),
        ("gold_trend_pullback", "TREND"),
        ("gold_break_retest", "BREAKOUT"),
        ("gold_liquidity_sweep", "VOLATILE"),
        ("fx_london_open_breakout", "BREAKOUT"),
        ("fx_ny_open_momentum", "TREND"),
    ],
    'B': [
        ("trend_micro_channel", "TREND"),
        ("trend_higher_low", "TREND"),
        ("trend_lower_high", "TREND"),
        ("breakout_micro_range", "BREAKOUT"),
        ("breakout_compression", "BREAKOUT"),
        ("breakout_inside_bar", "BREAKOUT"),
        ("breakout_momentum", "BREAKOUT"),
        ("breakout_atr_expansion", "BREAKOUT"),
        ("breakout_squeeze", "BREAKOUT"),
        ("reversion_vwap", "RANGE"),
        ("reversion_bollinger", "RANGE"),
        ("reversion_rsi_extreme", "RANGE"),
        ("reversion_range_edge", "RANGE"),
        ("reversion_wick", "RANGE"),
        ("volatility_expansion", "VOLATILE"),
        ("volatility_compression_break", "BREAKOUT"),
        ("impulse_follow", "VOLATILE"),
        ("micro_spike_follow", "VOLATILE"),
        ("gold_impulse_follow", "VOLATILE"),
        ("fx_overlap_expansion", "VOLATILE"),
    ],
    'C': [
        ("breakout_session_highlow", "BREAKOUT"),
        ("breakout_volume", "BREAKOUT"),
        ("reversion_zscore", "RANGE"),
        ("reversion_exhaustion", "RANGE"),
        ("reversion_double_test", "RANGE"),
        ("reversion_micro_divergence", "RANGE"),
        ("impulse_fade", "VOLATILE"),
        ("micro_spike_reclaim", "VOLATILE"),
        ("gold_range_reversion", "RANGE"),
        ("fx_session_retest", "BREAKOUT"),
    ],
}

PLAYBOOKS = []
for tier, items in TIERS.items():
    for name, regime in items:
        PLAYBOOKS.append((name, regime))

CATALOG = {
    name: {"id": name, "regime": regime, "tier": tier}
    for tier, items in TIERS.items()
    for name, regime in items
}

assert len(PLAYBOOKS) == 49, f'expected 49 playbooks, got {len(PLAYBOOKS)}'
assert len({x[0] for x in PLAYBOOKS}) == len(PLAYBOOKS), 'duplicate playbook id'
