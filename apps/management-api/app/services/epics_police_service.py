"""Epics Police decision logging and distillation engine.

Stores accept/reject/redirect decisions from the UI and skill,
then distills them into learned weights and thresholds using
Bayesian updating and signal effectiveness analysis.
"""

import math
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.epics_police import EpicsPoliceDecision
from app.schemas.epics_police import (
    ConfidenceBand,
    DecisionInput,
    LearningsResponse,
    SignalEffectiveness,
)
from app.services.config_service import get_config_value, upsert_config

# Default weights from SKILL.md — used as Bayesian priors
DEFAULT_WEIGHTS = {
    "label_overlap": 35.0,
    "title_overlap": 25.0,
    "description_overlap": 20.0,
    "squad_match": 10.0,
    "notion_match": 10.0,
}

DEFAULT_THRESHOLDS = {
    "pass1_lock": 70.0,
    "pass1_ambiguous_floor": 40.0,
    "feature_match": 60.0,
    "bug_matched": 70.0,
    "bug_suggested_floor": 40.0,
}

CONFIDENCE_BANDS = [
    ("0_39", 0, 39),
    ("40_59", 40, 59),
    ("60_79", 60, 79),
    ("80_100", 80, 100),
]

# Signals we track — must match the keys the skill writes into suggested_signals
SIGNAL_KEYS = list(DEFAULT_WEIGHTS.keys())

# Decay: half-life in weeks for tactical weight calculations
DECAY_HALF_LIFE_WEEKS = 8.0


# ── Decision storage ──────────────────────────────────────────────


async def store_decisions(
    db: AsyncSession, decisions: list[DecisionInput]
) -> list[EpicsPoliceDecision]:
    """Store a batch of decisions."""
    records = []
    for d in decisions:
        record = EpicsPoliceDecision(
            week_monday=d.week_monday,
            decided_at=d.decided_at,
            orphan_identifier=d.orphan_identifier,
            orphan_labels=d.orphan_labels,
            orphan_squad=d.orphan_squad,
            suggested_parent_id=d.suggested_parent_id,
            suggested_confidence=d.suggested_confidence,
            suggested_signals=d.suggested_signals,
            match_source=d.match_source,
            decision=d.decision,
            actual_parent_id=d.actual_parent_id,
            inferred=d.inferred,
        )
        db.add(record)
        records.append(record)
    await db.flush()
    return records


async def get_decisions(
    db: AsyncSession,
    week_monday: date | None = None,
    decision_type: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[EpicsPoliceDecision], int]:
    """Query decisions with optional filters."""
    query = select(EpicsPoliceDecision)
    count_q = select(EpicsPoliceDecision)

    if week_monday:
        query = query.where(EpicsPoliceDecision.week_monday == week_monday)
        count_q = count_q.where(EpicsPoliceDecision.week_monday == week_monday)
    if decision_type:
        query = query.where(EpicsPoliceDecision.decision == decision_type)
        count_q = count_q.where(EpicsPoliceDecision.decision == decision_type)

    from sqlalchemy import func

    total_result = await db.execute(
        select(func.count()).select_from(count_q.subquery())
    )
    total = total_result.scalar()

    query = query.order_by(EpicsPoliceDecision.decided_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all()), total


# ── Distillation engine ──────────────────────────────────────────


def _decay_weight(weeks_ago: float) -> float:
    """Exponential decay: halves every DECAY_HALF_LIFE_WEEKS weeks."""
    return math.pow(0.5, weeks_ago / DECAY_HALF_LIFE_WEEKS)


def _is_positive(decision: str) -> bool:
    return decision in ("accepted", "manual")


def _is_negative(decision: str) -> bool:
    return decision in ("rejected", "redirected")


async def distill_learnings(db: AsyncSession) -> LearningsResponse:
    """Compute learned weights and patterns from all stored decisions.

    Uses Bayesian updating with SKILL.md defaults as priors.
    Applies time decay for tactical weights; structural patterns use full history.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    # Load all decisions
    result = await db.execute(
        select(EpicsPoliceDecision).order_by(EpicsPoliceDecision.decided_at)
    )
    all_decisions = list(result.scalars().all())

    if not all_decisions:
        return LearningsResponse(
            total_decisions=0,
            last_distilled=now,
            learned_weights=dict(DEFAULT_WEIGHTS),
            learned_thresholds=dict(DEFAULT_THRESHOLDS),
            sufficient_data=False,
        )

    # Count unique weeks
    weeks_seen = set()
    for d in all_decisions:
        weeks_seen.add(d.week_monday)

    total = len(all_decisions)
    weeks_covered = len(weeks_seen)

    # ── Decision counts ──
    decision_counts: dict[str, int] = defaultdict(int)
    for d in all_decisions:
        decision_counts[d.decision] += 1

    # ── Confidence calibration ──
    calibration: dict[str, dict] = {}
    for band_name, lo, hi in CONFIDENCE_BANDS:
        band_decisions = [
            d for d in all_decisions
            if d.suggested_confidence is not None
            and lo <= d.suggested_confidence <= hi
        ]
        accepted = sum(1 for d in band_decisions if _is_positive(d.decision))
        rejected = sum(1 for d in band_decisions if d.decision == "rejected")
        redirected = sum(1 for d in band_decisions if d.decision == "redirected")
        band_total = accepted + rejected + redirected
        precision = accepted / band_total if band_total > 0 else 0.0
        calibration[band_name] = ConfidenceBand(
            accepted=accepted,
            rejected=rejected,
            redirected=redirected,
            precision=round(precision, 3),
        )

    # ── Signal effectiveness (with decay) ──
    signal_sums_pos: dict[str, float] = defaultdict(float)
    signal_sums_neg: dict[str, float] = defaultdict(float)
    signal_weight_pos: dict[str, float] = defaultdict(float)
    signal_weight_neg: dict[str, float] = defaultdict(float)

    for d in all_decisions:
        if not d.suggested_signals:
            continue
        weeks_ago = max(0, (today - d.week_monday).days / 7.0)
        w = _decay_weight(weeks_ago)

        positive = _is_positive(d.decision)
        negative = _is_negative(d.decision)

        for key in SIGNAL_KEYS:
            val = d.suggested_signals.get(key, 0.0)
            if val is None:
                val = 0.0
            if positive:
                signal_sums_pos[key] += val * w
                signal_weight_pos[key] += w
            elif negative:
                signal_sums_neg[key] += val * w
                signal_weight_neg[key] += w

    signal_effectiveness: dict[str, SignalEffectiveness] = {}
    for key in SIGNAL_KEYS:
        avg_pos = (
            signal_sums_pos[key] / signal_weight_pos[key]
            if signal_weight_pos[key] > 0
            else 0.0
        )
        avg_neg = (
            signal_sums_neg[key] / signal_weight_neg[key]
            if signal_weight_neg[key] > 0
            else 0.0
        )
        # Cap lift to prevent a single extreme ratio from dominating weights.
        # A lift of 10 already means "10x more predictive when accepted" — beyond
        # that, diminishing returns. Log-scale would also work but capping is simpler.
        lift = min(avg_pos / (avg_neg + 1e-6), 10.0)
        signal_effectiveness[key] = SignalEffectiveness(
            avg_accepted=round(avg_pos, 2),
            avg_rejected=round(avg_neg, 2),
            lift=round(lift, 2),
        )

    # ── Bayesian weight update ──
    # Blend factor: prior dominates early, data dominates with volume
    n_with_signals = sum(
        1 for d in all_decisions if d.suggested_signals
    )
    blend = 1.0 / (1.0 + math.log(max(1, n_with_signals)))

    raw_weights: dict[str, float] = {}
    for key in SIGNAL_KEYS:
        prior = DEFAULT_WEIGHTS[key]
        eff = signal_effectiveness.get(key)
        if eff and (signal_weight_pos[key] + signal_weight_neg[key]) > 0:
            data_weight = eff.lift * prior
            raw_weights[key] = prior * blend + data_weight * (1 - blend)
        else:
            raw_weights[key] = prior

    # Normalize to sum to 100
    total_raw = sum(raw_weights.values())
    learned_weights = {
        k: round(v / total_raw * 100, 1) for k, v in raw_weights.items()
    }

    # ── Threshold calibration ──
    learned_thresholds = dict(DEFAULT_THRESHOLDS)

    # Adjust pass1_lock: find where precision >= 0.90
    for band_name, lo, hi in reversed(CONFIDENCE_BANDS):
        band = calibration.get(band_name)
        if band and (band.accepted + band.rejected + band.redirected) >= 3:
            if band.precision >= 0.90:
                learned_thresholds["pass1_lock"] = float(lo)
                break

    # Adjust feature_match: find where precision >= 0.60
    for band_name, lo, hi in reversed(CONFIDENCE_BANDS):
        band = calibration.get(band_name)
        if band and (band.accepted + band.rejected + band.redirected) >= 3:
            if band.precision >= 0.60:
                learned_thresholds["feature_match"] = float(lo)
                break

    # ── Structural patterns (no decay — full history) ──
    structural_patterns = _compute_structural_patterns(all_decisions)

    # ── Store learnings in config ──
    learnings = LearningsResponse(
        total_decisions=total,
        weeks_covered=weeks_covered,
        last_distilled=now,
        confidence_calibration=calibration,
        signal_effectiveness=signal_effectiveness,
        learned_weights=learned_weights,
        learned_thresholds=learned_thresholds,
        structural_patterns=structural_patterns,
        decision_counts=dict(decision_counts),
        sufficient_data=total >= 1,
    )

    await upsert_config(
        db,
        "epics_police",
        "learnings",
        learnings.model_dump(mode="json"),
    )

    return learnings


def _compute_structural_patterns(
    decisions: list[EpicsPoliceDecision],
) -> list[dict]:
    """Extract structural patterns from full decision history.

    These accumulate over time and inform higher-level recommendations.
    No decay applied — patterns get stronger with more data.
    """
    patterns = []

    # Pattern: cross-squad redirect rate
    cross_squad_total = 0
    cross_squad_redirected = 0
    for d in decisions:
        if d.suggested_parent_id and d.orphan_squad and d.decision in ("accepted", "redirected"):
            cross_squad_total += 1
            if d.decision == "redirected":
                cross_squad_redirected += 1

    if cross_squad_total >= 5:
        rate = cross_squad_redirected / cross_squad_total
        if rate > 0.3:
            patterns.append({
                "type": "cross_squad_redirect",
                "redirect_rate": round(rate, 3),
                "sample_size": cross_squad_total,
                "description": (
                    f"Cross-squad suggestions redirected {rate:.0%} of the time "
                    f"({cross_squad_redirected}/{cross_squad_total})"
                ),
            })

    # Pattern: epic absorption — which epics receive the most suggestions?
    epic_receives: dict[str, dict] = defaultdict(lambda: {"accepted": 0, "rejected": 0, "redirected": 0})
    for d in decisions:
        if d.suggested_parent_id:
            epic_receives[d.suggested_parent_id][d.decision] = (
                epic_receives[d.suggested_parent_id].get(d.decision, 0) + 1
            )
    for epic_id, counts in epic_receives.items():
        total_for_epic = sum(counts.values())
        if total_for_epic >= 5:
            reject_rate = counts.get("rejected", 0) / total_for_epic
            if reject_rate > 0.5:
                patterns.append({
                    "type": "epic_over_suggested",
                    "epic_identifier": epic_id,
                    "reject_rate": round(reject_rate, 3),
                    "total_suggestions": total_for_epic,
                    "description": (
                        f"Epic {epic_id} has {reject_rate:.0%} rejection rate "
                        f"across {total_for_epic} suggestions — may be too broad"
                    ),
                })

    # Pattern: redirect destinations — when we suggest X, users pick Y instead
    redirect_flows: dict[tuple, int] = defaultdict(int)
    for d in decisions:
        if d.decision == "redirected" and d.suggested_parent_id and d.actual_parent_id:
            redirect_flows[(d.suggested_parent_id, d.actual_parent_id)] += 1

    for (from_epic, to_epic), count in redirect_flows.items():
        if count >= 3:
            patterns.append({
                "type": "redirect_flow",
                "suggested_epic": from_epic,
                "actual_epic": to_epic,
                "count": count,
                "description": (
                    f"Tickets suggested for {from_epic} redirected to {to_epic} "
                    f"{count} times"
                ),
            })

    # Pattern: label-based false positives
    label_decisions: dict[str, dict] = defaultdict(lambda: {"positive": 0, "negative": 0})
    for d in decisions:
        if not d.orphan_labels:
            continue
        for label in d.orphan_labels:
            if _is_positive(d.decision):
                label_decisions[label]["positive"] += 1
            elif _is_negative(d.decision):
                label_decisions[label]["negative"] += 1

    for label, counts in label_decisions.items():
        total_for_label = counts["positive"] + counts["negative"]
        if total_for_label >= 5:
            neg_rate = counts["negative"] / total_for_label
            if neg_rate > 0.6:
                patterns.append({
                    "type": "label_false_positive",
                    "label": label,
                    "negative_rate": round(neg_rate, 3),
                    "sample_size": total_for_label,
                    "description": (
                        f"Tickets with label '{label}' have {neg_rate:.0%} rejection rate — "
                        f"label may be misleading for matching"
                    ),
                })

    return patterns


async def get_stored_learnings(db: AsyncSession) -> LearningsResponse | None:
    """Retrieve the latest stored learnings from config."""
    value = await get_config_value(db, "epics_police", "learnings")
    if value is None:
        return None
    return LearningsResponse.model_validate(value)
