import re,json,sys,csv,io
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import select,func
from app import create_app
from tasks import make_task,ARMS,COUNTS
@pytest.fixture
def env(tmp_path):
    clock=[datetime(2026,1,1,9,tzinfo=timezone.utc)]
    a=create_app(dict(TESTING=True,SECRET_KEY='x'*48,ADMIN_PASSWORD='test-admin-password-long',DATABASE_URL=f'sqlite:///{tmp_path}/study.db',LIVE_RECRUITMENT=True,CLOCK=lambda:clock[0]))
    return a,a.test_client(),clock

def csrf(client,path):
    client.get(path)
    with client.session_transaction() as s:return s['csrf']
def post(c,path,data=None):
    with c.session_transaction() as s:token=s.get('csrf')
    return c.post(path,data=dict(data or {},csrf=token),follow_redirects=False)
def login(c):csrf(c,'/admin/login');assert post(c,'/admin/login',{'password':'test-admin-password-long'}).status_code==302

def invites(c,n=4,mode='test'):
    login(c);r=post(c,'/admin/create',{'number':n,'mode':mode});assert r.status_code==200
    return re.findall(r'value="(http[^\"]+/join/[^\"]+)"',r.text)
def enroll(c,link):
    r=c.get(link,follow_redirects=True);assert 'Before you begin' in r.text
    assert post(c,'/study',{'agree':'yes'}).status_code==302
    c.get('/study');assert post(c,'/study',dict(age='35–44',location='Aswan',experience='More than 5 years',status='Unemployed and seeking work',digital='Some experience',device='Computer')).status_code==302
    c.get('/study');post(c,'/start')
def getperson(a,c):
    with c.session_transaction() as s:pid=s['pid']
    db=a.extensions['study']
    with db['engine'].connect() as co:return dict(co.execute(select(db['people']).where(db['people'].c.id==pid)).mappings().one())
def solvephase(a,c,phase):
    db=a.extensions['study'];seen=[]
    for k in range(COUNTS[phase]):
        r=c.get('/study');assert r.status_code==200
        aid=re.search(r'name="attempt" value="([^"]+)"',r.text).group(1)
        # Answer not in HTML; task JSON is used only by the test harness.
        with db['engine'].connect() as co:task=json.loads(co.execute(select(db['attempts'].c.task_json).where(db['attempts'].c.id==aid)).scalar_one())
        seen.append(task['kind']);r=post(c,'/answer',{'attempt':aid,'choice':task['answer']});assert r.status_code==302
        # Duplicate posts cannot add observations or change phase.
        p=getperson(a,c)['phase'];assert post(c,'/answer',{'attempt':aid,'choice':task['answer']}).status_code==302
        assert getperson(a,c)['phase']==p
    return seen

def test_all_four_arms_end_to_end_and_exports(env):
    a,c,clock=env;links=invites(c);assert len(links)==4
    arms=[]
    for link in links:
        enroll(c,link);solvephase(a,c,'tutorial');solvephase(a,c,'baseline');p=getperson(a,c);arms.append(p['arm']);assert p['phase']=='waiting'
        for ph in ['practice1','practice2']:
            r=post(c,'/practice/'+ph)
            if p['arm'] in ('refresher','refresher_task_change'):
                assert r.status_code==302;solvephase(a,c,ph)
            else:assert r.status_code==403
        post(c,'/followup');assert getperson(a,c)['phase']=='retention'
        assert set(solvephase(a,c,'retention'))=={'original'}
        assert getperson(a,c)['phase']=='work_intro';page=c.get('/study')
        expected='changed' if 'task_change' in p['arm'] else 'original';post(c,'/start');assert set(solvephase(a,c,'work'))=={expected}
        c.get('/study');assert post(c,'/debrief',dict(difficulty='Manageable',outside_practice='No',device_changed='No',comment='')).status_code==302
        assert 'Thank you' in c.get('/study').text
        # The same invitation resumes the same finished participant.
        assert 'Thank you' in c.get(link,follow_redirects=True).text
    assert set(arms)==set(ARMS)
    for kind in ['participants','attempts','summary']:
        rows=list(csv.DictReader(io.StringIO(c.get('/admin/export/'+kind+'?mode=test').text)));assert rows
        live=list(csv.DictReader(io.StringIO(c.get('/admin/export/'+kind+'?mode=live').text)));assert live==[]
    rows=list(csv.DictReader(io.StringIO(c.get('/admin/export/summary?mode=test').text)))
    assert len(rows)==4 and all(float(r['retention_accuracy'])==1 for r in rows)

def test_live_schedule_concealment_and_partial_data(env):
    a,c,clock=env;links=invites(c,4,'live');ref=None
    for link in links:
        enroll(c,link)
        if getperson(a,c)['arm']=='refresher_task_change':ref=link;break
    solvephase(a,c,'tutorial');solvephase(a,c,'baseline');p=getperson(a,c);base=clock[0]
    assert post(c,'/followup').status_code==403
    assert post(c,'/practice/practice1').status_code==403
    clock[0]=base+timedelta(days=2)
    assert post(c,'/practice/practice1').status_code==302;solvephase(a,c,'practice1')
    assert post(c,'/practice/practice2').status_code==403
    clock[0]=base+timedelta(days=4)
    assert post(c,'/practice/practice2').status_code==302;solvephase(a,c,'practice2')
    clock[0]=base+timedelta(days=7)
    assert post(c,'/practice/practice1').status_code==403
    assert post(c,'/followup').status_code==302
    r=c.get('/study');assert 'System total' not in r.text and 'new booking workflow' not in r.text
    # Refresh preserves task ID and no score exists before an answer.
    r2=c.get('/study');assert re.search('name="attempt" value="([^"]+)"',r.text).group(1)==re.search('name="attempt" value="([^"]+)"',r2.text).group(1)
    rows=list(csv.DictReader(io.StringIO(c.get('/admin/export/attempts?mode=live').text)))
    assert any(x['phase']=='retention' and not x['answered_at'] for x in rows)
    summary=list(csv.DictReader(io.StringIO(c.get('/admin/export/summary?mode=live').text)))
    assert next(x for x in summary if x['id']==p['id'])['retention_accuracy']==''

def test_csrf_authorization_and_invitations(env):
    a,c,clock=env
    assert c.get('/admin').status_code==302
    assert c.post('/admin/create',data={'number':12}).status_code==400
    assert c.get('/study').status_code==403
    assert c.get('/join/not-a-token').status_code==404
    links=invites(c,12)
    db=a.extensions['study']
    with db['engine'].connect() as co:
        rows=list(co.execute(select(db['people'])).mappings());assert all(sum(r['arm']==arm for r in rows)==3 for arm in ARMS)
        assert all('/join/' not in str(r) for r in rows)
    # HTML form answer options exclude the correct answer field itself.
    enroll(c,links[0]);r=c.get('/study');assert 'task_json' not in r.text
    assert post(c,'/answer',dict(attempt='not-owned',choice='0')).status_code==400

def test_local_sqlite_persists_and_server_checks(env,tmp_path):
    a,c,clock=env;invites(c,1)
    a2=create_app(dict(TESTING=True,SECRET_KEY='x'*48,ADMIN_PASSWORD='test-admin-password-long',DATABASE_URL=a.config['DATABASE_URL']))
    db=a2.extensions['study']
    with db['engine'].connect() as co:assert co.execute(select(func.count()).select_from(db['people'])).scalar()==1
    assert a2.test_client().get('/health').status_code==200

def test_live_recruitment_closed_and_login_limit(env):
    a,c,clock=env;login(c);a.config['LIVE_RECRUITMENT']=False
    assert post(c,'/admin/create',{'number':4,'mode':'live'}).status_code==403
    assert post(c,'/admin/create',{'number':4,'mode':'test'}).status_code==200
    other=a.test_client();csrf(other,'/admin/login')
    for _ in range(5):assert post(other,'/admin/login',{'password':'wrong-password'}).status_code==200
    assert post(other,'/admin/login',{'password':'wrong-password'}).status_code==429

def test_task_keys_and_balance():
    for seed in ['a','b','c']:
        for phase in COUNTS:
            for i in range(COUNTS[phase]):
                t=make_task(seed,phase,i,changed=True);assert len(set(o['value'] for o in t['options']))==4
                assert t['answer'] in [o['value'] for o in t['options']]
        assert sorted(make_task(seed,'work',i,True)['answer'] for i in range(8))==['0','0','1','1','2','2','3','3']
