"""Additional datatype-aware patterns used by the existing model generator."""
import math
import random
from decimal import Decimal
from datetime import datetime,timedelta,timezone

NUMERIC=('constant','increment','decrement','random','ramp','step','sine','csv')
BOOL=('constant','toggle','random','csv')
TEXT=('constant','text_sequence','random_text','csv')
DATES=('current_timestamp','fixed_timestamp','increment_timestamp','csv')


def patterns(kind):return BOOL if kind=='Boolean' else TEXT if kind=='String' else DATES if kind=='DateTime' else NUMERIC


def validate(tag,s):
    from model import cast
    kind=tag['dtype'];p=s.get('pattern','sine')
    if kind in ('String','DateTime') or p in ('toggle','increment','decrement','step'):
        if p not in patterns(kind):raise ValueError(f'{p} is incompatible with {kind}')
        if kind=='String':
            for k in ('value','text_prefix','text_suffix'):cast(s.get(k,''),'String')
            if len(s.get('text_prefix',''))+len(s.get('text_suffix',''))+32>4096:raise ValueError('Text affixes are too long')
            if p=='csv':
                seq=s.get('sequence',[])
                if not isinstance(seq,list) or not 1<=len(seq)<=100000:raise ValueError('Sequence needs 1..100000 values')
                for v in seq:cast(v,kind)
        elif kind=='DateTime':
            if p!='current_timestamp':cast(s.get('value','2026-01-01T00:00:00Z'),kind)
            if p=='increment_timestamp':
                step=float(s.get('step',1))
                if not math.isfinite(step) or not 0<=step<=86400:raise ValueError('Timestamp step must be 0..86400 seconds')
            if p=='csv':
                seq=s.get('sequence',[])
                if not isinstance(seq,list) or not 1<=len(seq)<=100000:raise ValueError('Sequence needs 1..100000 values')
                for v in seq:cast(v,kind)
        elif kind=='Boolean':
            cast(s.get('value',False),kind)
        else:
            lo=cast(s.get('minimum',0),kind);hi=cast(s.get('maximum',100),kind)
            if lo>hi:raise ValueError('Minimum exceeds maximum')
            step=Decimal(str(s.get('step',1)))
            if not step.is_finite() or step<0:raise ValueError('Step must be finite and nonnegative')
            if kind not in ('Float','Double') and step!=step.to_integral_value():raise ValueError('Integer step must be whole')
            cast(s.get('value',lo),kind)
        period=s.get('period',20)
        if isinstance(period,bool) or not math.isfinite(float(period)) or float(period)!=int(float(period)) or not 2<=int(period)<=1000000:raise ValueError('Period must be 2..1000000 whole cycles')
        value(tag,s,0);value(tag,s,int(period)-1)
        return True
    return False


def value(tag,s,slot):
    from model import cast
    kind=tag['dtype'];p=s.get('pattern','sine');i=int(tag.get('index',1));period=int(s.get('period',20));n=slot%period
    if kind=='String':
        prefix=s.get('text_prefix','');suffix=s.get('text_suffix','')
        if p=='constant':v=s.get('value','')
        elif p=='text_sequence':v=prefix+str(n)+suffix
        elif p=='random_text':
            rng=random.Random(f"{s.get('seed',1)}:{slot}:{i}");v=prefix+''.join(rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(12))+suffix
        elif p=='csv':v=s['sequence'][slot%len(s['sequence'])]
        else:raise ValueError('Incompatible String pattern')
        return cast(v,kind)
    if kind=='DateTime':
        if p=='current_timestamp':v=datetime.now(timezone.utc)
        elif p=='fixed_timestamp':v=cast(s.get('value','2026-01-01T00:00:00Z'),kind)
        elif p=='increment_timestamp':
            try:v=cast(s.get('value','2026-01-01T00:00:00Z'),kind)+timedelta(seconds=float(s.get('step',1))*n)
            except OverflowError:raise ValueError('Timestamp sequence exceeds DateTime range')
        elif p=='csv':v=s['sequence'][slot%len(s['sequence'])]
        else:raise ValueError('Incompatible DateTime pattern')
        return cast(v,kind)
    if p=='toggle':return cast(bool(slot%2),kind)
    if p in ('increment','decrement','step'):
        lo=Decimal(str(s.get('minimum',0)));hi=Decimal(str(s.get('maximum',100)));step=Decimal(str(s.get('step',1)))
        if p=='increment':v=min(hi,lo+step*n)
        elif p=='decrement':v=max(lo,hi-step*n)
        else:v=lo if n<period//2 else hi
        return cast(v,kind)
    return NotImplemented
