"""Usability study scoring (rubric item 4; docs/usability_test_v0.md).

SUS: odd items contribute (answer - 1), even items (5 - answer); the sum times 2.5 gives 0-100.
Task success counts only unassisted completions. Exact Decimal arithmetic; inputs are never modified.
"""
from decimal import Decimal

from .meal_metrics import _median, _ratio, _tier

TASKS = ('T1', 'T2', 'T3', 'T4')
OUTCOMES = ('completed', 'assisted', 'failed')
CHECKLIST = ('no_raw_json_on_main_screen', 'chinese_reasons_and_next_steps', 'phone_width_t1_without_side_scroll')
MIN_PARTICIPANTS = 5


def sus_score(answers):
    """answers: ten integers 1-5 in questionnaire order."""
    if len(answers) != 10 or any(type(a) is not int or not 1 <= a <= 5 for a in answers):
        raise ValueError('sus_needs_ten_answers_1_to_5')
    return Decimal(sum((a - 1) if index % 2 == 0 else (5 - a) for index, a in enumerate(answers))) * Decimal('2.5')


def _int(value, code):
    try:
        return int(str(value).strip())
    except ValueError:
        raise ValueError(code) from None


def summarize_usability(sus_rows, task_rows, *, latency_p95_seconds=None, checklist=None):
    """sus_rows: participant_id,q1..q10; task_rows: participant_id,task,outcome,seconds."""
    sus, participants = [], set()
    for row in sus_rows:
        if row['participant_id'] in participants:
            raise ValueError('duplicate_sus_participant')
        participants.add(row['participant_id'])
        sus.append(sus_score([_int(row[f'q{i}'], 'invalid_sus_answer') for i in range(1, 11)]))
    attempts, done, seen, t1_times = 0, 0, set(), []
    for row in task_rows:
        if row['task'] not in TASKS or row['outcome'] not in OUTCOMES:
            raise ValueError('invalid_task_row')
        key = (row['participant_id'], row['task'])
        if key in seen:
            raise ValueError('duplicate_task_row')
        seen.add(key)
        attempts += 1
        done += row['outcome'] == 'completed'
        if row['task'] == 'T1' and row['outcome'] == 'completed':
            seconds = Decimal(str(row['seconds']).strip())
            if not seconds.is_finite() or seconds < 0:
                raise ValueError('invalid_seconds')
            t1_times.append(seconds)
    checks = dict(checklist or {})
    if set(checks) - set(CHECKLIST):
        raise ValueError('unknown_checklist_item')
    return {'participants': len(participants), 'sus_mean': sum(sus) / len(sus) if sus else None,
            'task_attempts': attempts, 'task_success_rate': _ratio(done, attempts),
            't1_median_seconds': _median(t1_times) if t1_times else None,
            'latency_p95_seconds': None if latency_p95_seconds is None else Decimal(str(latency_p95_seconds)),
            'checklist': {k: bool(checks.get(k, False)) for k in CHECKLIST}}


def score_usability(summary):
    d = Decimal
    enough = summary['participants'] >= MIN_PARTICIPANTS
    sus = _tier(summary['sus_mean'], ((d(80), 5), (d(68), 3), (d(51), 1)), higher_is_better=True) if enough else 0
    tasks = _tier(summary['task_success_rate'], ((d('0.90'), 4), (d('0.75'), 2)), higher_is_better=True) if enough else 0
    speed_hits = sum([summary['t1_median_seconds'] is not None and summary['t1_median_seconds'] <= 60,
                      summary['latency_p95_seconds'] is not None and summary['latency_p95_seconds'] <= 5])
    speed = {2: 3, 1: 1, 0: 0}[speed_hits]
    checklist = sum(summary['checklist'].values())
    notes = [] if enough else [f'fewer than {MIN_PARTICIPANTS} participants: 4a and 4b scored 0']
    return {'4a_sus': sus, '4b_task_success': tasks, '4c_speed': speed, '4d_checklist': checklist,
            'subtotal': sus + tasks + speed + checklist, 'max': 15, 'notes': notes}


def latency_p95(seconds):
    """Nearest-rank 95th percentile of measured durations."""
    ordered = sorted(Decimal(str(s)) for s in seconds)
    if not ordered:
        raise ValueError('no_latency_samples')
    rank = -(-95 * len(ordered) // 100)  # ceil(0.95 n)
    return ordered[rank - 1]
