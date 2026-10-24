"""Scheduling metadata for existing simulation workers; no second engine."""
from datetime import datetime,timezone
import math


def schedule_time(value):
    if value in (None,''):return None
    if not isinstance(value,str):raise ValueError('Schedule needs an ISO timestamp with timezone')
    try:d=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('Invalid scheduled timestamp')
    if d.tzinfo is None:raise ValueError('Scheduled timestamp needs a timezone')
    return d.astimezone(timezone.utc).timestamp()


def validate_schedule(job):
    stamp=schedule_time(job.get('schedule_at'));duration=job.get('duration_seconds')
    if (stamp is not None or duration is not None) and job['kind'] not in ('simulation','server'):raise ValueError('Scheduling/duration applies to simulation/server jobs')
    if duration is not None:
        if isinstance(duration,bool) or not math.isfinite(float(duration)) or not .1<=float(duration)<=604800:raise ValueError('Duration must be 0.1..604800 seconds')
