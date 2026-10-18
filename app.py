import csv,hashlib,hmac,io,json,os,secrets,uuid
from datetime import datetime,timedelta,timezone
from functools import wraps
from pathlib import Path
from flask import Flask,abort,flash,redirect,render_template,request,session,url_for,Response
from sqlalchemy import create_engine,MetaData,Table,Column,String,Integer,Text,Float,select,update,func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash,check_password_hash
from tasks import VERSION,ARMS,ARM_LABELS,COUNTS,make_task,public_task

def utcnow():return datetime.now(timezone.utc)
def stamp(dt):return dt.isoformat()
def parse(s):return datetime.fromisoformat(s)
def tokenhash(s):return hashlib.sha256(s.encode()).hexdigest()

def create_app(overrides=None):
    app=Flask(__name__)
    local={}
    if not os.environ.get('RENDER') and Path('instance/local_config.json').exists():
        local=json.loads(Path('instance/local_config.json').read_text())
    app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY',local.get('SECRET_KEY')),
        ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD',local.get('ADMIN_PASSWORD')),
        DATABASE_URL=os.environ.get('DATABASE_URL',local.get('DATABASE_URL','sqlite:///instance/study.db')),
        STUDY_CONTACT=os.environ.get('STUDY_CONTACT',local.get('STUDY_CONTACT','Contact the researcher who invited you.')),
        LIVE_RECRUITMENT=os.environ.get('LIVE_RECRUITMENT','false').lower()=='true',
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER')),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),MAX_CONTENT_LENGTH=32768,
        CLOCK=utcnow,TESTING=False)
    if overrides:app.config.update(overrides)
    if not app.config['SECRET_KEY'] or not app.config['ADMIN_PASSWORD']:
        raise RuntimeError('Configure SECRET_KEY and ADMIN_PASSWORD. Locally run: python setup_local.py')
    if len(app.config['SECRET_KEY'])<32 or len(app.config['ADMIN_PASSWORD'])<12:
        raise RuntimeError('SECRET_KEY must have 32+ characters; ADMIN_PASSWORD must have 12+.')
    dburl=app.config['DATABASE_URL']
    if dburl.startswith('postgres://'):dburl=dburl.replace('postgres://','postgresql+psycopg://',1)
    elif dburl.startswith('postgresql://'):dburl=dburl.replace('postgresql://','postgresql+psycopg://',1)
    if os.environ.get('RENDER') and not dburl.startswith('postgresql+psycopg://'):
        raise RuntimeError('Render requires persistent PostgreSQL DATABASE_URL. Local SQLite is disabled on Render.')
    Path('instance').mkdir(exist_ok=True)
    engine=create_engine(dburl,pool_pre_ping=True)
    md=MetaData()
    people=Table('participants',md,Column('id',String(40),primary_key=True),Column('token_hash',String(64),unique=True,nullable=False),
        Column('arm',String(40),nullable=False),Column('mode',String(10),nullable=False),Column('batch',String(40),nullable=False),
        Column('seed',String(64),nullable=False),Column('version',String(40),nullable=False),Column('created_at',String(40),nullable=False),
        Column('consent_at',String(40)),Column('background',Text),Column('phase',String(20),nullable=False),
        Column('baseline_at',String(40)),Column('retention_at',String(40)),Column('change_revealed_at',String(40)),
        Column('finished_at',String(40)),Column('debrief',Text))
    attempts=Table('attempts',md,Column('id',String(40),primary_key=True),Column('pid',String(40),nullable=False),
        Column('phase',String(20),nullable=False),Column('round',Integer,nullable=False),Column('task_json',Text,nullable=False),
        Column('displayed_at',String(40),nullable=False),Column('answered_at',String(40)),Column('choice',String(40)),
        Column('correct',Integer),Column('elapsed_seconds',Float))
    from sqlalchemy import UniqueConstraint
    attempts.append_constraint(UniqueConstraint('pid','phase','round',name='unique_participant_task'))
    logins=Table('login_failures',md,Column('id',String(40),primary_key=True),Column('key',String(64)),Column('at',String(40)))
    md.create_all(engine)
    app.extensions['study']=dict(engine=engine,people=people,attempts=attempts,logins=logins)
    adminhash=generate_password_hash(app.config['ADMIN_PASSWORD'])
    def now():return app.config['CLOCK']()
    def person():
        with engine.connect() as c:
            p=c.execute(select(people).where(people.c.id==session.get('pid',''))).mappings().first()
        if not p:abort(403,description='Open your individual invitation link to continue.')
        if p['version']!=VERSION:abort(409,description='Study version changed. Contact the researcher before continuing.')
        return dict(p)
    def admin(f):
        @wraps(f)
        def wrapped(*a,**kw):
            if not session.get('admin'):return redirect(url_for('login'))
            return f(*a,**kw)
        return wrapped
    def csrf():
        if 'csrf' not in session:session['csrf']=secrets.token_hex(24)
        return session['csrf']
    app.jinja_env.globals.update(csrf_token=csrf,ARM_LABELS=ARM_LABELS,VERSION=VERSION,ARABIC={'Other adult age':'عمر آخر للبالغين','Aswan':'أسوان','Elsewhere in Egypt':'مكان آخر في مصر','Outside Egypt':'خارج مصر','Less than 2 years':'أقل من عامين','2–5 years':'عامان إلى خمسة أعوام','More than 5 years':'أكثر من خمسة أعوام','Working':'أعمل حاليًا','Unemployed and seeking work':'لا أعمل وأبحث عن عمل','Other':'أخرى','Little experience':'خبرة قليلة','Some experience':'بعض الخبرة','Frequent use':'استخدام متكرر','Phone':'هاتف','Tablet':'جهاز لوحي','Computer':'حاسوب','Easy':'سهل','Manageable':'مقبول','Difficult':'صعب','Yes':'نعم','No':'لا','Unsure':'غير متأكد'})
    @app.before_request
    def protect():
        if request.method=='POST' and not hmac.compare_digest(str(session.get('csrf','')),str(request.form.get('csrf',''))) :abort(400,description='Session expired. Reload the page and try again.')
        if request.method=='POST' and not session.get('csrf'):abort(400)
    @app.after_request
    def headers(r):
        r.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"})
        return r
    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def error(e):return render_template('error.html',message=e.description),e.code
    @app.get('/health')
    def health():
        with engine.connect() as c:c.execute(select(func.count()).select_from(people))
        return {'status':'ok','version':VERSION}
    @app.get('/')
    def home():return render_template('home.html')
    @app.get('/join/<token>')
    def join(token):
        with engine.connect() as c:p=c.execute(select(people.c.id).where(people.c.token_hash==tokenhash(token))).scalar_one_or_none()
        if not p:abort(404,description='Invitation not found. Check the link with the researcher.')
        session['pid']=p;session.permanent=True
        return redirect(url_for('study'))
    @app.route('/study',methods=['GET','POST'])
    def study():
        p=person()
        if not p['consent_at']:
            if request.method=='POST':
                if request.form.get('agree')!='yes':return render_template('error.html',message='You have not consented. You may close this page.')
                with engine.begin() as c:c.execute(update(people).where(people.c.id==p['id']).values(consent_at=stamp(now())))
                return redirect(url_for('study'))
            return render_template('consent.html',p=p,contact=app.config['STUDY_CONTACT'])
        if not p['background']:
            if request.method=='POST':
                fields={'age':['25–34','35–44','45–55','Other adult age'], 'location':['Aswan','Elsewhere in Egypt','Outside Egypt'],
                    'experience':['Less than 2 years','2–5 years','More than 5 years'], 'status':['Working','Unemployed and seeking work','Other'],
                    'digital':['Little experience','Some experience','Frequent use'], 'device':['Phone','Tablet','Computer']}
                data={k:request.form.get(k,'') for k in fields}
                if any(data[k] not in fields[k] for k in fields):abort(400,description='Please answer every background question.')
                with engine.begin() as c:c.execute(update(people).where(people.c.id==p['id']).values(background=json.dumps(data)))
                return redirect(url_for('study'))
            return render_template('background.html',p=p)
        if request.method=='POST':abort(400)
        phase=p['phase']
        if phase=='intro':return render_template('instructions.html',stage='original',p=p)
        if phase=='waiting':
            base=parse(p['baseline_at']);demo=p['mode']=='test'
            due=base+timedelta(days=7)
            with engine.connect() as c:
                counts={ph:c.execute(select(func.count()).select_from(attempts).where(attempts.c.pid==p['id'],attempts.c.phase==ph,attempts.c.answered_at.is_not(None))).scalar() for ph in ['practice1','practice2']}
            practice=[]
            if p['arm'] in ('refresher','refresher_task_change'):
                for ph,day in [('practice1',2),('practice2',4)]:
                    if (demo or base+timedelta(days=day)<=now()<due) and counts[ph]<COUNTS[ph]:practice.append(ph)
            return render_template('waiting.html',p=p,practice=practice,counts=counts,due=due,ready=demo or now()>=due)
        if phase=='work_intro':return render_template('instructions.html',stage='changed' if p['arm'] in ('task_change','refresher_task_change') else 'stable',p=p)
        if phase=='debrief':return render_template('debrief.html',p=p)
        if phase=='done':return render_template('done.html',p=p)
        if phase not in COUNTS:abort(409)
        with engine.begin() as c:
            # Lock the participant so parallel requests cannot create different tasks.
            current=c.execute(select(people.c.phase).where(people.c.id==p['id']).with_for_update()).scalar_one()
            if current!=phase:return redirect(url_for('study'))
            if phase in ('practice1','practice2') and p['mode']!='test' and now()>=parse(p['baseline_at'])+timedelta(days=7):
                c.execute(update(people).where(people.c.id==p['id']).values(phase='waiting'))
                return redirect(url_for('study'))
            n=c.execute(select(func.count()).select_from(attempts).where(attempts.c.pid==p['id'],attempts.c.phase==phase,attempts.c.answered_at.is_not(None))).scalar()
            a=c.execute(select(attempts).where(attempts.c.pid==p['id'],attempts.c.phase==phase,attempts.c.round==n)).mappings().first()
            if not a:
                task=make_task(p['seed'],phase,n,changed=p['arm'] in ('task_change','refresher_task_change'))
                aid=str(uuid.uuid4());c.execute(attempts.insert().values(id=aid,pid=p['id'],phase=phase,round=n,task_json=json.dumps(task),displayed_at=stamp(now())))
                a=dict(id=aid,task_json=json.dumps(task))
            task=json.loads(a['task_json'])
        return render_template('task.html',p=p,phase=phase,n=n,total=COUNTS[phase],task=public_task(task),aid=a['id'])
    @app.post('/start')
    def start():
        p=person()
        if not p['consent_at'] or not p['background']:abort(403)
        if p['phase']=='intro':new='tutorial'
        elif p['phase']=='work_intro':new='work'
        else:return redirect(url_for('study'))
        with engine.begin() as c:c.execute(update(people).where(people.c.id==p['id'],people.c.phase==p['phase']).values(phase=new))
        return redirect(url_for('study'))
    @app.post('/practice/<which>')
    def practice(which):
        p=person()
        if p['phase']!='waiting' or which not in ('practice1','practice2') or p['arm'] not in ('refresher','refresher_task_change'):abort(403)
        base=parse(p['baseline_at']);day=2 if which=='practice1' else 4
        if p['mode']!='test' and not(base+timedelta(days=day)<=now()<base+timedelta(days=7)):abort(403,description='This practice opportunity is not available now.')
        with engine.begin() as c:
            n=c.execute(select(func.count()).select_from(attempts).where(attempts.c.pid==p['id'],attempts.c.phase==which,attempts.c.answered_at.is_not(None))).scalar()
            if n>=COUNTS[which]:return redirect(url_for('study'))
            c.execute(update(people).where(people.c.id==p['id'],people.c.phase=='waiting').values(phase=which))
        return redirect(url_for('study'))
    @app.post('/followup')
    def followup():
        p=person()
        if p['phase']!='waiting':return redirect(url_for('study'))
        if p['mode']!='test' and now()<parse(p['baseline_at'])+timedelta(days=7):abort(403,description='Your follow-up session is not due yet.')
        with engine.begin() as c:c.execute(update(people).where(people.c.id==p['id'],people.c.phase=='waiting').values(phase='retention',retention_at=stamp(now())))
        return redirect(url_for('study'))
    @app.post('/answer')
    def answer():
        p=person();aid=request.form.get('attempt','');choice=request.form.get('choice','')
        feedback=None
        with engine.begin() as c:
            locked=c.execute(select(people).where(people.c.id==p['id']).with_for_update()).mappings().one()
            a=c.execute(select(attempts).where(attempts.c.id==aid,attempts.c.pid==p['id']).with_for_update()).mappings().first()
            if not a:abort(400)
            if a['answered_at']:return redirect(url_for('study'))
            if a['phase']!=locked['phase']:abort(409,description='This task is no longer active. Return to your current page.')
            if a['phase'] in ('practice1','practice2') and p['mode']!='test' and now()>=parse(p['baseline_at'])+timedelta(days=7):
                c.execute(update(people).where(people.c.id==p['id']).values(phase='waiting'))
                return redirect(url_for('study'))
            task=json.loads(a['task_json'])
            if choice not in [x['value'] for x in task['options']]:abort(400,description='Please select an answer.')
            correct=int(choice==task['answer']);elapsed=max(0,(now()-parse(a['displayed_at'])).total_seconds())
            c.execute(update(attempts).where(attempts.c.id==aid,attempts.c.answered_at.is_(None)).values(choice=choice,correct=correct,answered_at=stamp(now()),elapsed_seconds=elapsed))
            phase=a['phase'];n=c.execute(select(func.count()).select_from(attempts).where(attempts.c.pid==p['id'],attempts.c.phase==phase,attempts.c.answered_at.is_not(None))).scalar()
            if n==COUNTS[phase]:
                vals={}
                if phase=='tutorial':vals['phase']='baseline'
                elif phase=='baseline':vals.update(phase='waiting',baseline_at=stamp(now()))
                elif phase in ('practice1','practice2'):vals['phase']='waiting'
                elif phase=='retention':vals.update(phase='work_intro',change_revealed_at=stamp(now()))
                elif phase=='work':vals['phase']='debrief'
                c.execute(update(people).where(people.c.id==p['id']).values(**vals))
            if phase in ('tutorial','practice1','practice2'):feedback=f"{'Correct.' if correct else 'Not quite.'} The correct total is {task['answer']} EGP."
        if feedback:flash(feedback)
        return redirect(url_for('study'))
    @app.post('/debrief')
    def debrief():
        p=person()
        if p['phase']!='debrief':return redirect(url_for('study'))
        data={k:request.form.get(k,'')[:1000] for k in ['difficulty','outside_practice','device_changed','comment']}
        if data['difficulty'] not in ('Easy','Manageable','Difficult') or data['outside_practice'] not in ('Yes','No','Unsure') or data['device_changed'] not in ('Yes','No'):abort(400)
        with engine.begin() as c:c.execute(update(people).where(people.c.id==p['id'],people.c.phase=='debrief').values(debrief=json.dumps(data),phase='done',finished_at=stamp(now())))
        return redirect(url_for('study'))
    @app.route('/admin/login',methods=['GET','POST'])
    def login():
        if request.method=='POST':
            key=hmac.new(app.config['SECRET_KEY'].encode(),request.remote_addr.encode(),hashlib.sha256).hexdigest()
            with engine.begin() as c:
                c.execute(logins.delete().where(logins.c.at<stamp(now()-timedelta(hours=1))))
                n=c.execute(select(func.count()).select_from(logins).where(logins.c.key==key,logins.c.at>stamp(now()-timedelta(minutes=15)))).scalar()
                if n>=5:return render_template('error.html',message='Too many login attempts. Wait 15 minutes.'),429
                if check_password_hash(adminhash,request.form.get('password','')):
                    session['admin']=True;session['csrf']=secrets.token_hex(24);session.permanent=True
                    return redirect(url_for('dashboard'))
                c.execute(logins.insert().values(id=str(uuid.uuid4()),key=key,at=stamp(now())))
            flash('Password not recognised.')
        return render_template('login.html')
    @app.post('/admin/logout')
    @admin
    def logout():session.pop('admin',None);return redirect(url_for('home'))
    @app.get('/admin')
    @admin
    def dashboard():
        with engine.connect() as c:
            ps=[dict(x) for x in c.execute(select(people).order_by(people.c.created_at)).mappings()]
            for p in ps:p['answers']=c.execute(select(func.count()).select_from(attempts).where(attempts.c.pid==p['id'],attempts.c.answered_at.is_not(None))).scalar()
        return render_template('admin.html',ps=ps,live=app.config['LIVE_RECRUITMENT'])
    @app.post('/admin/create')
    @admin
    def create_invites():
        try:n=int(request.form.get('number','0'))
        except ValueError:abort(400)
        mode=request.form.get('mode','test')
        if mode not in ('test','live') or not 1<=n<=100:abort(400)
        if mode=='live' and not app.config['LIVE_RECRUITMENT']:abort(403,description='Live recruitment is closed. Configure LIVE_RECRUITMENT=true only when the study is ready.')
        batch=str(uuid.uuid4());links=[]
        # Complete blocks of four; exact balance within blocks, randomized leftovers.
        import random
        assignments=[]
        while len(assignments)<n:
            block=list(ARMS);random.SystemRandom().shuffle(block);assignments+=block
        with engine.begin() as c:
            for arm in assignments[:n]:
                pid=str(uuid.uuid4());token=secrets.token_urlsafe(32)
                c.execute(people.insert().values(id=pid,token_hash=tokenhash(token),arm=arm,mode=mode,batch=batch,seed=secrets.token_hex(24),version=VERSION,created_at=stamp(now()),phase='intro'))
                links.append(dict(id=pid,mode=mode,link=url_for('join',token=token,_external=True)))
        return render_template('invites.html',links=links)
    def csv_response(rows,columns,name):
        def safe(v):
            s='' if v is None else str(v)
            return "'"+s if s.startswith(('=','+','-','@','\t','\r')) else s
        out=io.StringIO();w=csv.writer(out);w.writerow(columns)
        for row in rows:w.writerow([safe(row.get(k)) for k in columns])
        return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename={name}'})
    @app.get('/admin/export/<kind>')
    @admin
    def export(kind):
        mode=request.args.get('mode','live')
        if mode not in ('live','test'):abort(400)
        with engine.connect() as c:
            ps=[dict(x) for x in c.execute(select(people).where(people.c.mode==mode)).mappings()]
            if kind=='participants':
                cols=['id','arm','mode','batch','version','created_at','consent_at','background','phase','baseline_at','retention_at','change_revealed_at','finished_at','debrief']
                return csv_response(ps,cols,f'participants_{mode}.csv')
            if kind=='attempts':
                rs=[dict(x) for x in c.execute(select(attempts,people.c.arm,people.c.mode,people.c.version).join(people,people.c.id==attempts.c.pid).where(people.c.mode==mode)).mappings()]
                return csv_response(rs,['id','pid','arm','mode','version','phase','round','task_json','displayed_at','answered_at','choice','correct','elapsed_seconds'],f'attempts_{mode}.csv')
            if kind=='summary':
                rs=[]
                for p in ps:
                    row={k:p[k] for k in ['id','arm','mode','phase','baseline_at','retention_at','finished_at']}
                    row['refresher']=int(p['arm'] in ('refresher','refresher_task_change'));row['changed']=int(p['arm'] in ('task_change','refresher_task_change'))
                    for ph in COUNTS:
                        aa=c.execute(select(attempts.c.correct,attempts.c.elapsed_seconds).where(attempts.c.pid==p['id'],attempts.c.phase==ph,attempts.c.answered_at.is_not(None))).all()
                        row[ph+'_n']=len(aa);row[ph+'_accuracy']=sum(x[0] for x in aa)/len(aa) if len(aa)==COUNTS[ph] else None
                        row[ph+'_seconds']=sum(x[1] for x in aa) if len(aa)==COUNTS[ph] else None
                    row['retention_change']=None if row['retention_accuracy'] is None or row['baseline_accuracy'] is None else row['retention_accuracy']-row['baseline_accuracy']
                    rs.append(row)
                cols=['id','arm','mode','phase','refresher','changed','baseline_at','retention_at','finished_at']+[ph+suf for ph in COUNTS for suf in ['_n','_accuracy','_seconds']]+['retention_change']
                return csv_response(rs,cols,f'summary_{mode}.csv')
        abort(404)
    return app

if __name__=='__main__':create_app().run(host='127.0.0.1',port=int(os.environ.get('PORT',5000)),debug=False)
