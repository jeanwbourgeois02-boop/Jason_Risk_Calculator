"""Multi-leg option structures: combining several option positions into one.

THE IDEA
--------
A structure (straddle, risk reversal, collar, ...) is just multiple option
legs held at once. Its combined price and Greeks are simply the sum of
each leg's price and Greeks, each leg's contribution scaled by +1 if you're
long it or -1 if you're short it. No new pricing math is needed here --
every leg is priced by an existing pricer in equity/ or fx/; this module
only adds them together correctly.

`combine()` is the generic, asset-class-agnostic machinery. The named
builders (straddle, strangle, risk_reversal, collar) live in
equity/structures.py and fx/structures.py, since which pricer function they
call and which rate arguments they take differs by asset class exactly the
way it does everywhere else in this package.
"""


def combine(*legs):
    """Combine option legs into one position's price and Greeks.

    Each leg is a (result_dict, quantity) tuple:
      - result_dict: the dict returned by any pricer in this package.
        Every pricer includes at least {price, delta, gamma, theta, vega,
        rho}; some include extra fields (e.g. FX pricers also add
        'rho_foreign' and 'delta_premium_adjusted', equity pricers add
        'rho_dividend').
      - quantity: +1.0 for a long leg, -1.0 for a short leg (or any other
        signed multiple, e.g. 2.0 for two contracts long)

    Returns a dict covering the UNION of keys present across all legs, not
    a fixed set -- so extra fields like 'rho_foreign' survive being
    combined into a structure or portfolio position instead of being
    silently dropped. A leg that doesn't have a given key (e.g. an equity
    leg has no 'rho_foreign') contributes 0 for it, which is the correct
    answer: an equity position genuinely has no foreign-rate risk.

    Every field here is a linear combination of each leg's own values (all
    of delta, gamma, theta, vega, rho, rho_foreign, rho_dividend, and
    delta_premium_adjusted are additive across legs on the SAME
    underlying/spot), so summing after scaling by quantity is correct, not
    an approximation.
    """
    all_keys = set()
    for result, _ in legs:
        all_keys.update(result.keys())

    combined = {key: 0.0 for key in all_keys}
    for result, quantity in legs:
        for key in all_keys:
            combined[key] += quantity * result.get(key, 0.0)
    return combined
