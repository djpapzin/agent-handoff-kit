"""Real two-process crash/lease-expiry/recovery demo; synthetic data only."""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from handoff_kit.worker import create, claim, begin_step, commit_step, run_job, get_status, get_history, replay

JOB_ID = 'job-demo'


def worker_a(db, raw, ready):
    owner = 'A-' + uuid.uuid4().hex
    job = claim(db, JOB_ID, raw, owner, ttl=2)
    for step in ('validate', 'create_receipt'):
        begin_step(db, JOB_ID, owner, job.generation, step)
        commit_step(db, JOB_ID, owner, job.generation, step)
    ready.send((os.getpid(), job.generation))
    ready.close()
    # Parent kills this process while it still owns the unfinished job.
    time.sleep(30)


def worker_b(db, raw, result):
    try:
        job = run_job(db, JOB_ID, raw, 'B-' + uuid.uuid4().hex)
        result.send((os.getpid(), job.generation, job.status.value))
    except Exception as exc:
        result.send(('error', str(exc)))
    finally:
        result.close()


def snapshot(db):
    c = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        c.execute('BEGIN')
        return {t: c.execute('SELECT * FROM ' + t).fetchall() for t in ('jobs', 'receipts', 'history')}
    finally:
        c.close()


def stop(proc):
    if proc.pid is not None:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        if proc.is_alive():
            raise RuntimeError('Child did not stop')
        proc.close()


def run_demo(db, input_file):
    raw = Path(input_file).read_text()
    create(db, JOB_ID, raw)
    ctx = mp.get_context('spawn')
    ar, aw = ctx.Pipe(duplex=False)
    br, bw = ctx.Pipe(duplex=False)
    a = ctx.Process(target=worker_a, args=(db, raw, aw))
    b = ctx.Process(target=worker_b, args=(db, raw, bw))
    try:
        a.start()
        aw.close()
        if not ar.poll(10):
            raise RuntimeError('Worker A did not reach receipt checkpoint')
        pid_a, gen_a = ar.recv()
        a.kill()
        a.join(5)
        assert a.exitcode is not None and a.exitcode < 0
        state = get_status(db, JOB_ID)
        assert state['last_completed_step'] == 'create_receipt'
        print('A pid={} generation={} killed after receipt checkpoint'.format(pid_a, gen_a))
        delay = max(0, state['lease_expiry'] - time.time() + 0.05)
        if delay > 5:
            raise RuntimeError('Unexpected lease wait')
        print('Waiting {:.2f}s for actual lease expiry'.format(delay))
        time.sleep(delay)
        started = time.monotonic()
        b.start()
        bw.close()
        if not br.poll(10):
            raise RuntimeError('Worker B recovery timed out')
        result = br.recv()
        b.join(5)
        assert b.exitcode == 0, result
        pid_b, gen_b, status = result
        assert pid_a != pid_b and gen_b == gen_a + 1 and status == 'DONE', result
        elapsed = time.monotonic() - started
        before = snapshot(db)
        summary = replay(db, JOB_ID, raw)
        run_job(db, JOB_ID, raw, 'replay-' + uuid.uuid4().hex)
        assert snapshot(db) == before
        assert len(before['receipts']) == 1
        rows = get_history(db, JOB_ID)
        commits = [r.step for r in rows if r.event in ('commit_step', 'commit_done')]
        assert commits == ['validate', 'create_receipt', 'summarize'], commits
        print('B pid={} generation={} reached DONE in {:.3f}s (including process startup)'.format(pid_b, gen_b, elapsed))
        print('Receipt count: 1; replay: unchanged jobs, receipts and history')
        for row in rows:
            print('{} gen={} {} {} -> {}'.format(row.seq, row.generation, row.event, row.step or '-', row.new_status))
        print(json.dumps(summary, indent=2))
        print('PASS: real worker kill, expired-lease recovery, single receipt, read-only replay')
    finally:
        stop(a)
        stop(b)
        for pipe in (ar, aw, br, bw):
            pipe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', help='New database path; existing paths are rejected')
    parser.add_argument('--input', default=str(Path(__file__).parent / 'fixtures/input_v1.json'))
    args = parser.parse_args()
    if args.db:
        # Reserve an empty file exclusively; never delete/overwrite caller data.
        fd = os.open(args.db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        run_demo(args.db, args.input)
    else:
        with tempfile.TemporaryDirectory(prefix='handoff-demo-') as directory:
            run_demo(str(Path(directory) / 'demo.db'), args.input)


if __name__ == '__main__':
    main()
