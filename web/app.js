const $ = id => document.getElementById(id);
let latest = null;
const labels = {create:'Create job',claim:'Acquire lease',begin_step:'Begin step',commit_step:'Commit checkpoint',commit_done:'Commit final result'};
$('run-button').addEventListener('click', async () => {
  const button = $('run-button');
  button.disabled = true;
  document.body.classList.add('running');
  $('announcement').textContent = '';
  $('run-label').textContent = 'Running the real recovery scenario…';
  $('run-detail').textContent = 'Starting A, killing its process, waiting for expiry, then recovering with B.';
  button.textContent = 'Running…';
  if (latest) $('run-id').textContent = `PREVIOUS RUN · ${latest.run_id}`;
  try {
    const response = await fetch('/api/run', {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The run failed. Try again.');
    latest = result;
    $('run-id').textContent = `VERIFIED RUN · ${result.run_id}`;
    $('receipt-count').textContent = result.receipt_count;
    $('final-state').textContent = result.status;
    $('replay-state').textContent = result.replay_unchanged ? 'Unchanged' : 'Changed';
    $('elapsed').textContent = result.elapsed_seconds.toFixed(2) + 's';
    $('a-state').textContent = 'CHECKPOINT SAVED';
    $('kill-state').textContent = 'PROCESS KILLED';
    $('b-state').textContent = `DONE · GEN ${result.generation}`;
    ['a-state','kill-state','b-state'].forEach(id => $(id).classList.add('done'));
    $('receipt-json').textContent = JSON.stringify(result.receipt,null,2);
    $('event-count').textContent = `${result.history.length} DURABLE EVENTS`;
    $('history-body').replaceChildren();
    for (const event of result.history) {
      const tr = document.createElement('tr');
      if (event.event === 'claim' && event.generation === 2) tr.className = 'takeover';
      const values = [event.seq, labels[event.event] || event.event, event.generation, event.new_status];
      values.forEach((value,index) => {
        const td = document.createElement('td');
        if (index === 3) {const span = document.createElement('span');span.className='state-pill';span.textContent=value;td.append(span);}
        else td.textContent = value;
        if (index === 1 && event.step) {const small=document.createElement('small');small.textContent=event.step;td.append(small);}
        tr.append(td);
      });
      $('history-body').append(tr);
    }
    $('empty-history').hidden = true;
    $('history-wrap').hidden = false;
    $('download').disabled = false;
    $('run-label').textContent = 'Recovery verified. Nothing repeated.';
    $('run-detail').textContent = 'A was killed. B finished. One receipt, with unchanged records on replay.';
  } catch (error) {
    $('announcement').textContent = error.message || 'Unable to reach the demo. Please try again.';
    $('run-label').textContent = 'This run could not be verified.';
    $('run-detail').textContent = latest ? 'The results below are from the previous successful run.' : 'No result is being claimed. You can safely try again.';
  } finally {
    document.body.classList.remove('running');
    button.disabled = false;
    button.textContent = latest ? 'Run again ↗' : 'Run the recovery demo ↗';
  }
});
$('download').addEventListener('click', () => {
  if (!latest) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(latest,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=`handoff-run-${latest.run_id}.json`;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
