"""Durable operator-supervised cycles. These states are not machine telemetry."""
import json
from .job_history import now


TERMINAL = {'completed', 'aborted', 'failed'}
CUTTING = {'start_pending', 'awaiting_completion', 'interrupted'}


class SupervisedCycles:
    def __init__(self, history, session):
        self.history = history
        with history.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS supervised_cycles (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            # Preparation cannot survive a process restart. Never restart capture
            # or resend a native command when recovering an unfinished cycle.
            cycle = self._active(db)
            if cycle and cycle['state'] == 'processing' and cycle['session'] != session:
                cycle.update(state='interrupted', error='Application restarted during preparation. Inspect the machine and confirm it is stopped before clearing this cycle.')
                self._save(db, cycle, 'recovered_after_restart')

    @staticmethod
    def _active(db):
        rows = db.execute('SELECT document FROM supervised_cycles ORDER BY rowid DESC').fetchall()
        return next((c for row in rows if (c := json.loads(row[0]))['state'] not in TERMINAL), None)

    def status(self):
        with self.history.connect() as db:
            rows = db.execute('SELECT document FROM supervised_cycles ORDER BY rowid DESC LIMIT 30').fetchall()
        cycles = [json.loads(row[0]) for row in rows]
        return {'active': next((c for c in cycles if c['state'] not in TERMINAL), None), 'recent': cycles}

    def _save(self, db, cycle, event, detail=None):
        cycle['updated_at'] = now()
        db.execute('INSERT INTO supervised_cycles VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document', (cycle['id'], json.dumps(cycle)))
        db.execute('INSERT INTO events(job_id,timestamp,kind,detail) VALUES (?,?,?,?)',
                   (cycle.get('job_id'), now(), 'supervised_' + event, json.dumps({'cycle_id': cycle['id'], **(detail or {})})))
        return cycle

    def begin(self, cycle_id, session):
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._active(db):
                raise ValueError('Finish or explicitly abort the current supervised cycle before loading another sheath.')
            if db.execute('SELECT 1 FROM supervised_cycles WHERE id=?', (cycle_id,)).fetchone():
                raise ValueError('This preparation request was already submitted. Review its saved cycle; do not retry automatically.')
            cycle = {'id': cycle_id, 'session': session, 'state': 'processing', 'loaded_confirmed_at': now(),
                     'job_id': None, 'review': None, 'start_requested_at': None, 'operator_completed_at': None,
                     'controller_acknowledged': False, 'error': None}
            return self._save(db, cycle, 'sheath_loaded')

    def mutate(self, cycle_id, allowed, event, update, detail=None):
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cycle = self._active(db)
            if not cycle or cycle['id'] != cycle_id or cycle['state'] not in allowed:
                raise ValueError('The supervised cycle changed. Refresh and review its current stage.')
            cycle.update(update)
            return self._save(db, cycle, event, detail)

    def prepared(self, cycle_id, job):
        return self.mutate(cycle_id, {'processing'}, 'job_prepared',
                           {'state':'awaiting_review','job_id':job['id'],'filename':job['filename']})

    def failed(self, cycle_id, error):
        return self.mutate(cycle_id, {'processing'}, 'preparation_failed', {'state':'failed','error':str(error)})

    def review(self, cycle_id, job, panel, actor):
        snapshot = {k:panel.get(k) for k in ('window_id','process_id','window_title','communication_selection')}
        snapshot.update(job_id=job['id'], revision=job['revision'], sha256=job['sha256'])
        return self.mutate(cycle_id, {'awaiting_review','reviewed'}, 'operator_reviewed',
                           {'state':'reviewed','review':snapshot}, {'actor':actor,'review':snapshot,
                            'basis':'Operator confirmed LaserCAD download, settings, placement and machine readiness; no telemetry verification.'})

    def before_start(self, job, panel, request_id):
        cycle = self.status()['active']
        if not cycle:
            if any(c.get('job_id') == job['id'] for c in self.status()['recent']):
                raise ValueError('This supervised job has ended. Load a sheath and prepare a new cycle before another Start.')
            return None
        expected = {k:panel.get(k) for k in ('window_id','process_id','window_title','communication_selection')}
        expected.update(job_id=job['id'], revision=job['revision'], sha256=job['sha256'])
        if cycle['state'] != 'reviewed' or cycle.get('review') != expected:
            raise ValueError('Review this cycle in LaserCAD and confirm download, settings and placement before Start. Changed settings or window require a new review.')
        return self.mutate(cycle['id'], {'reviewed'}, 'start_intent',
                           {'state':'start_pending','start_request_id':str(request_id),'dispatch_claimed':False,
                            'start_requested_at':now(),'error':'Start outcome unconfirmed until request returns. Do not resend automatically.'})

    def claim_dispatch(self, cycle_id, request_id):
        with self.history.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cycle = self._active(db)
            if (not cycle or cycle['id'] != cycle_id or cycle['state'] != 'start_pending'
                or cycle.get('start_request_id') != str(request_id) or cycle.get('dispatch_claimed')):
                raise ValueError('Start was not approved for this cycle, or its delivery has already been attempted.')
            cycle['dispatch_claimed'] = True
            self._save(db, cycle, 'start_dispatch_claimed')

    def requested(self, cycle_id):
        return self.mutate(cycle_id, {'start_pending'}, 'start_requested',
                           {'state':'awaiting_completion','error':None})

    def interrupted(self, error):
        cycle = self.status()['active']
        if cycle and cycle['state'] in CUTTING:
            return self.mutate(cycle['id'], CUTTING, 'interrupted', {'state':'interrupted','error':str(error)})

    def finish(self, cycle_id, outcome, actor):
        allowed = {'start_pending','awaiting_completion'} if outcome == 'completed' else {'awaiting_review','reviewed',*CUTTING}
        return self.mutate(cycle_id, allowed, 'operator_' + outcome,
                           {'state':outcome,'operator_completed_at':now(),'completion_basis':'operator_reported'},
                           {'actor':actor,'basis':'Operator observed cutting completed and machine stopped.' if outcome == 'completed'
                            else 'Operator confirmed machine stopped; cycle aborted. This confirmation sends no Stop command.'})

    def require_idle(self):
        if self.status()['active']:
            raise ValueError('A supervised sheath cycle is active. Confirm completion or explicitly abort it before another capture/export.')

    def guard_action(self, action, job_id):
        cycle = self.status()['active']
        if not cycle:
            return
        if action == 'stop':
            return
        if cycle['state'] in CUTTING and action not in {'pause','resume'}:
            raise ValueError('Resolve the current cutting cycle before preparing, downloading or starting another job.')
        if cycle.get('job_id') != job_id:
            raise ValueError('This action does not belong to the active supervised cycle.')
