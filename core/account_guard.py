"""Account-level concentration guard shared by live engine and backtester.

Two jobs:

1. Never hold two positions that express the *same* risk factor in opposite
   directions (that is a synthetic hedge which pays the spread twice and
   distorts realized P/L attribution).
2. Never stack more than the user-configured number of correlated
   same-direction positions and never exceed the matching aggregate risk
   budget.

Correlation is expressed with coarse "risk factors" instead of a fitted
correlation matrix so the rule is deterministic, explainable and stable across
brokers/symbol suffixes.
"""

from __future__ import annotations

_FX_MAJORS = ('USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD')
_GOLD_TAGS = ('XAU', 'GOLD')
_INDEX_TAGS = ('US30', 'US100', 'NAS', 'SPX', 'USTEC', 'US500', 'GER', 'DAX',
               'DE40', 'UK100', 'JP225', 'JPX')


def _upper(symbol):
    return str(symbol or '').upper()


def leg_codes(symbol):
    """Return ``(base, quote)`` currency codes (suffix-safe, best effort)."""
    upper = _upper(symbol)
    for tag in _GOLD_TAGS + _INDEX_TAGS:
        if tag in upper:
            return (tag,)
    letters = ''.join(ch for ch in upper if ch.isalpha())
    for start in range(0, max(0, len(letters) - 5)):
        base, quote = letters[start:start + 3], letters[start + 3:start + 6]
        if base in _FX_MAJORS and quote in _FX_MAJORS and base != quote:
            return (base, quote)
    codes = [tag for tag in _FX_MAJORS if tag in upper]
    return tuple(codes[:2]) if codes else (upper[:3],)


def exposure(symbol, side):
    """Signed risk-factor exposure of one position.

    FX quotes are ``BASE/QUOTE``: buying the pair is long base / short quote.
    Metals/index CFDs are quoted against USD, so buying them is long the
    instrument and short USD.  Two positions are a synthetic hedge when they
    share a leg with opposite signs, e.g. EURUSD SELL (long USD) against
    USDCHF SELL (short USD).
    """
    sign = 1.0 if str(getattr(side, 'value', side)).upper() == 'BUY' else -1.0
    upper = _upper(symbol)
    legs = leg_codes(upper)
    if is_gold(upper) or is_index(upper):
        return {legs[0]: sign, 'USD': -sign}
    if len(legs) >= 2:
        return {legs[0]: sign, legs[1]: -sign}
    return {legs[0]: sign}


def conflicting(a_symbol, a_side, b_symbol, b_side):
    """True when the two positions share a leg with opposite sign."""
    a = exposure(a_symbol, a_side)
    b = exposure(b_symbol, b_side)
    for leg in set(a) & set(b):
        if a[leg] * b[leg] < 0:
            return True
    return False


def is_gold(symbol):
    upper = _upper(symbol)
    return any(tag in upper for tag in _GOLD_TAGS)


def is_index(symbol):
    upper = _upper(symbol)
    return any(tag in upper for tag in _INDEX_TAGS)


def correlated(a, b):
    """True when two symbols express an overlapping risk factor."""
    upper_a, upper_b = _upper(a), _upper(b)
    if upper_a == upper_b:
        return True
    if is_gold(upper_a) or is_gold(upper_b) or is_index(upper_a) or is_index(upper_b):
        # Metals and index CFDs are USD-quoted: they always share the USD
        # factor with each other and with any USD pair.
        return True
    return bool(set(leg_codes(upper_a)) & set(leg_codes(upper_b)))


def _side_of(position):
    side = position.get('side') if isinstance(position, dict) else getattr(position, 'side', None)
    if side is None:
        return ''
    return str(getattr(side, 'value', side)).upper()


def _num(position, name):
    value = position.get(name) if isinstance(position, dict) else getattr(position, name, None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def check(symbol, side, open_positions, max_correlated_positions=1, risk_pct=0.0, equity=0.0):
    """Return ``(allowed, reason, detail)`` for a prospective entry."""
    if not open_positions:
        return True, 'OK', {}
    side = str(getattr(side, 'value', side)).upper()
    limit = int(max_correlated_positions or 0)
    if limit <= 0:
        return True, 'OK', {}

    same_direction = 0
    correlated_risk = 0.0
    for position in open_positions:
        other_symbol = (position.get('symbol') if isinstance(position, dict)
                        else getattr(position, 'symbol', '')) or ''
        if not correlated(symbol, other_symbol):
            continue
        other_side = _side_of(position)
        if other_side and side and conflicting(symbol, side, other_symbol, other_side):
            return False, 'CORRELATED_OPPOSITE_SIDE', {
                'existing_symbol': other_symbol, 'existing_side': other_side, 'side': side}
        same_direction += 1
        correlated_risk += _num(position, 'risk_cash')

    if same_direction >= limit:
        return False, 'CORRELATED_LIMIT', {
            'correlated_same_side': same_direction, 'limit': limit, 'side': side}
    budget = equity * float(risk_pct or 0.0) * limit / 100.0
    new_risk = equity * float(risk_pct or 0.0) / 100.0
    if equity > 0 and budget > 0 and correlated_risk + new_risk > budget + max(0.01, budget * 0.05):
        return False, 'CORRELATED_RISK_BUDGET', {
            'correlated_risk_cash': correlated_risk, 'new_risk_cash': new_risk,
            'budget_cash': budget, 'limit': limit}
    return True, 'OK', {'correlated_same_side': same_direction}
