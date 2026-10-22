"""Billing helpers for the subscription service."""


def compute_prorated_refund(plan_price: float, days_used: int, days_in_cycle: int) -> float:
    """Refund the unused part of a billing cycle."""
    unused = max(days_in_cycle - days_used, 0)
    return round(plan_price * unused / days_in_cycle, 2)


def applyLateFeePenalty(balance: float, days_overdue: int) -> float:
    """Charge a 1.5 percent monthly penalty on overdue balances."""
    return round(balance * (1 + 0.015 * (days_overdue // 30)), 2)
