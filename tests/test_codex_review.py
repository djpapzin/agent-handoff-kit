"""Independent Codex regression checks from implementation review."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from handoff_kit import worker
from handoff_kit.db import open_and_init
from handoff_kit.models import Status, InconsistentState

RAW = '{"schema_version":1,"task":"build-sample","artifact":"sample.txt"}'


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'review.db')

    def test_unknown_schema_rejected_without_any_file_write(self):
        c = sqlite3.connect(self.db)
        c.execute('CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT)')
        c.execute("INSERT INTO schema_meta VALUES ('version','999')")
        c.commit()
        c.close()
        before = Path(self.db).read_bytes()
        with self.assertRaises(Exception):
            c = open_and_init(self.db)
            c.close()
        self.assertEqual(before, Path(self.db).read_bytes())
        self.assertFalse(Path(self.db + '-wal').exists())

    def test_schema_rejects_infinite_active_lease(self):
        worker.create(self.db, 'job', RAW)
        c = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE jobs SET status='CLAIMED',owner='a',lease_expiry=? WHERE job_id='job'", (float('inf'),))
        finally:
            c.close()

    def test_malformed_checkpoint_is_held_without_guessing(self):
        cases = [
            '{"schema_version":true,"completed_steps":[],"results":{}}',
            '{"schema_version":1,"completed_steps":[],"results":{"ghost":{}}}',
            '{"schema_version":1,"completed_steps":["validate"],"results":{"validate":null}}',
            '{"schema_version":1,"completed_steps":["validate","create_receipt","summarize","extra"],"results":{}}',
        ]
        for i, checkpoint in enumerate(cases):
            with self.subTest(checkpoint=checkpoint):
                job_id = 'bad-' + str(i)
                worker.create(self.db, job_id, RAW)
                c = open_and_init(self.db)
                c.execute('UPDATE jobs SET checkpoint_json=? WHERE job_id=?', (checkpoint, job_id))
                c.close()
                job = worker.run_job(self.db, job_id, RAW, 'review-worker-' + str(i))
                self.assertEqual(job.status, Status.NEEDS_REVIEW)
                self.assertEqual(job.checkpoint_json, checkpoint)

    def test_done_run_is_logically_read_only(self):
        worker.create(self.db, 'job', RAW)
        worker.run_job(self.db, 'job', RAW, 'a')
        def snapshot():
            c = sqlite3.connect(self.db)
            try:
                return {t: c.execute('SELECT * FROM ' + t).fetchall() for t in ('jobs','receipts','history')}
            finally:
                c.close()
        before = snapshot()
        self.assertEqual(worker.run_job(self.db, 'job', RAW, 'b').status, Status.DONE)
        self.assertEqual(before, snapshot())

    def test_status_missing_database_does_not_create_one(self):
        with self.assertRaises(Exception):
            worker.get_status(self.db, 'missing')
        self.assertFalse(Path(self.db).exists())

    def test_clock_exception_rolls_back_transaction(self):
        from handoff_kit.db import run_immediate
        c = open_and_init(self.db)
        try:
            def broken():
                raise RuntimeError('clock failed')
            with self.assertRaisesRegex(RuntimeError, 'clock failed'):
                run_immediate(c, lambda *_: None, clock=broken)
            self.assertFalse(c.in_transaction)
        finally:
            c.close()

    def test_uncertain_commit_halts_and_observes_both_outcomes(self):
        from handoff_kit.db import run_immediate, CommitUncertain
        worker.create(self.db, 'job', RAW)
        for committed in (False, True):
            with self.subTest(committed=committed):
                c = open_and_init(self.db)
                class ConnectionProxy:
                    def __getattr__(self, key):
                        return getattr(c, key)
                    def execute(self, sql, *args):
                        if sql == 'COMMIT':
                            if committed:
                                c.execute(sql)
                            raise sqlite3.OperationalError('injected commit acknowledgement failure')
                        return c.execute(sql, *args)
                calls = []
                def body(conn, now):
                    calls.append(now)
                    conn.execute("UPDATE jobs SET updated_at=42 WHERE job_id='job'")
                with self.assertRaises(CommitUncertain) as error:
                    run_immediate(ConnectionProxy(), body)
                self.assertEqual(len(calls), 1)
                self.assertIn('job', error.exception.evidence)
                c = sqlite3.connect(self.db)
                value = c.execute("SELECT updated_at FROM jobs").fetchone()[0]
                c.close()
                self.assertEqual(value == 42, committed)

    def test_genuine_lock_retry_and_post_lock_clock(self):
        from handoff_kit.db import run_immediate
        from unittest.mock import patch
        worker.create(self.db, 'job', RAW)
        holder = open_and_init(self.db)
        contender = open_and_init(self.db)
        contender.execute('PRAGMA busy_timeout=1')
        holder.execute('BEGIN IMMEDIATE')
        clocks = []
        def release(_):
            self.assertEqual(clocks, [])
            holder.execute('ROLLBACK')
        try:
            with patch('handoff_kit.db.time.sleep', side_effect=release):
                run_immediate(contender, lambda *_: None, clock=lambda: clocks.append(1) or 42)
            self.assertEqual(clocks, [1])
        finally:
            holder.close()
            contender.close()

    def test_mismatch_active_expired_and_done_leaves_every_table_unchanged(self):
        from demo import snapshot
        from handoff_kit.models import InputHashMismatch
        wrong = RAW.replace('sample.txt', 'other.txt')
        for i, status in enumerate(('PENDING', 'CLAIMED', 'RUNNING', 'CHECKPOINT', 'DONE')):
            job = 'mismatch-' + str(i)
            worker.create(self.db, job, RAW, clock=lambda: 100)
            if status == 'DONE':
                worker.run_job(self.db, job, RAW, 'a')
            elif status != 'PENDING':
                row = worker.claim(self.db, job, RAW, 'a', clock=lambda: 100)
                if status in ('RUNNING', 'CHECKPOINT'):
                    worker.begin_step(self.db, job, 'a', row.generation, 'validate', clock=lambda: 100)
                if status == 'CHECKPOINT':
                    worker.commit_step(self.db, job, 'a', row.generation, 'validate', clock=lambda: 100)
            for now in (101, 131):
                before = snapshot(self.db)
                with self.assertRaises(InputHashMismatch):
                    if status == 'DONE':
                        worker.run_job(self.db, job, wrong, 'b')
                    else:
                        worker.claim(self.db, job, wrong, 'b', clock=lambda: now)
                self.assertEqual(snapshot(self.db), before)


def crash_child(db, step, phase):
    import os
    owner = 'crash-worker'
    row = worker.claim(db, 'job', RAW, owner, ttl=1, clock=lambda: 100)
    for s in ('validate', 'create_receipt', 'summarize'):
        worker.begin_step(db, 'job', owner, row.generation, s, clock=lambda: 100)
        def hook(at):
            if s == step and at == phase:
                os._exit(77)
        worker.commit_step(db, 'job', owner, row.generation, s, fault_hook=hook, clock=lambda: 100)


def claimant_child(db, gate, pipe, owner):
    from handoff_kit.models import NotClaimable
    gate.wait(5)
    try:
        row = worker.claim(db, 'job', RAW, owner, clock=lambda: 200)
        pipe.send(('won', row.generation))
    except NotClaimable:
        pipe.send(('lost', None))
    finally:
        pipe.close()


class RealProcessRegressions(unittest.TestCase):
    setUp = ReviewRegressions.setUp

    def test_crash_before_and_after_every_commit(self):
        import multiprocessing as mp
        from demo import snapshot, stop
        for step in ('validate', 'create_receipt', 'summarize'):
            for phase in ('before_commit', 'after_commit'):
                with self.subTest(step=step, phase=phase):
                    db = str(Path(self.tmp.name) / (step + phase + '.db'))
                    worker.create(db, 'job', RAW, clock=lambda: 100)
                    p = mp.get_context('spawn').Process(target=crash_child, args=(db, step, phase))
                    try:
                        p.start(); p.join(10)
                        self.assertEqual(p.exitcode, 77)
                        state = worker.get_status(db, 'job')
                        index = ('validate','create_receipt','summarize').index(step)
                        count = index + (phase == 'after_commit')
                        expected_last = ('validate','create_receipt','summarize')[count-1] if count else None
                        self.assertEqual(state['last_completed_step'], expected_last)
                        job = worker.run_job(db, 'job', RAW, 'replacement', clock=lambda: 200)
                        self.assertEqual(job.status, Status.DONE)
                        snap = snapshot(db)
                        self.assertEqual(len(snap['receipts']), 1)
                        events = worker.get_history(db, 'job')
                        self.assertEqual([r.step for r in events if r.event in ('commit_step','commit_done')], ['validate','create_receipt','summarize'])
                    finally:
                        stop(p)

    def test_competing_claimants_pending_and_all_expired_states(self):
        import multiprocessing as mp
        from demo import stop
        ctx = mp.get_context('spawn')
        for status in ('PENDING', 'CLAIMED', 'RUNNING', 'CHECKPOINT'):
            with self.subTest(status=status):
                db = str(Path(self.tmp.name) / (status + '.db'))
                worker.create(db, 'job', RAW, clock=lambda: 100)
                if status != 'PENDING':
                    row = worker.claim(db, 'job', RAW, 'old', clock=lambda: 100)
                    if status in ('RUNNING','CHECKPOINT'):
                        worker.begin_step(db, 'job', 'old', row.generation, 'validate', clock=lambda: 100)
                    if status == 'CHECKPOINT':
                        worker.commit_step(db, 'job', 'old', row.generation, 'validate', clock=lambda: 100)
                gate = ctx.Event()
                pipes = [ctx.Pipe(False), ctx.Pipe(False)]
                procs = [ctx.Process(target=claimant_child, args=(db,gate,pipes[i][1],str(i))) for i in range(2)]
                try:
                    for p in procs: p.start()
                    gate.set()
                    results = []
                    for read, write in pipes:
                        write.close()
                        self.assertTrue(read.poll(10))
                        results.append(read.recv())
                    self.assertEqual(sorted(r[0] for r in results), ['lost','won'])
                    for p in procs:
                        p.join(5); self.assertEqual(p.exitcode, 0)
                finally:
                    for p in procs: stop(p)
                    for pair in pipes:
                        for pipe in pair: pipe.close()
