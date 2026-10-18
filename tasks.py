"""Deterministic fresh task sets. No real customer data or external AI calls."""
import random
VERSION = 'skills-retention-1.0'
ARMS = ('control','refresher','task_change','refresher_task_change')
ARM_LABELS = dict(zip(ARMS, ('No refresher / stable task','Refresher / stable task','No refresher / changed task','Refresher / changed task')))
COUNTS = dict(tutorial=2, baseline=8, practice1=4, practice2=4, retention=8, work=8)
def make_task(seed, phase, index, changed=False):
    r=random.Random(f'{VERSION}:{seed}:{phase}:{index}')
    nights=r.choice([2,3,4,5]);rate=r.choice([150,200,250]);fee=50
    total=nights*rate+fee-(100 if nights>=4 else 0)
    base=dict(version=VERSION,nights=nights,rate=rate,fee=fee,discount=100 if nights>=4 else 0)
    if phase=='work' and changed:
        # All four action classes appear twice, in randomized order fixed by participant seed.
        classes=[0,1,2,3]*2;random.Random(f'{seed}:work-classes').shuffle(classes)
        cls=classes[index];proof=not bool(cls&1);capacity=2;guests=3 if cls&2 else 2
        base.update(kind='changed',auto_total=total,prepaid=True,proof=proof,capacity=capacity,guests=guests,
                    options=[dict(value=str(k),label=v) for k,v in enumerate(['Confirm booking','Request payment evidence','Resolve room capacity','Resolve both issues'])],answer=str(cls))
    else:
        options=[total,total+50,total-50,total+100];r.shuffle(options)
        base.update(kind='original',options=[dict(value=str(x),label=f'{x} EGP') for x in options],answer=str(total))
    return base

def public_task(task):
    return {k:v for k,v in task.items() if k!='answer'}
