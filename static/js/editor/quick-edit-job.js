export function finishQuickEditJob(job, result) {
  if (job.cancelled || result?.cancelled) {
    job.cancelled = true;
    job.status = 'cancelled';
    job.detail = 'Cancelled';
  } else if (job.status === 'error' || result?.ok === false) {
    job.status = 'error';
    job.detail = result?.error || result?.message || (job.detail !== 'Working…' && job.detail) || 'Edit failed';
  } else {
    job.status = 'done';
    job.detail = result?.message || 'Done';
  }
}
