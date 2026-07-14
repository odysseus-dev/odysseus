/**
 * Blocking overlay while the campaign job pipeline runs (Q11).
 * Shows spinner + human-readable step from GET /game/jobs poll.
 */

const STEP_LABELS = {
  interactive_turn: 'GM prepares response…',
  sd_generate: 'Generating scene…',
  scene_prompt_llm: 'Building image prompt…',
  location_population: 'Populating location…',
};

export function createPipelineWaitModal(root) {
  const backdrop = document.createElement('div');
  backdrop.className = 'fugassa-popup-backdrop fugassa-pipeline-backdrop';
  backdrop.hidden = true;
  backdrop.innerHTML = `
    <div class="fugassa-popup fugassa-pipeline-popup" role="dialog" aria-modal="true" aria-labelledby="fugassa-pipeline-title">
      <div class="fugassa-pipeline-spinner" aria-hidden="true"></div>
      <h4 id="fugassa-pipeline-title">Processing…</h4>
      <p class="fugassa-muted" data-pipeline-step>Please wait…</p>
      <p class="fugassa-pipeline-error fugassa-muted" data-pipeline-error hidden></p>
    </div>
  `;
  root.appendChild(backdrop);

  const stepEl = backdrop.querySelector('[data-pipeline-step]');
  const errorEl = backdrop.querySelector('[data-pipeline-error]');

  const show = ({ title = 'Processing…', step = 'Please wait…', error = '' } = {}) => {
    backdrop.querySelector('#fugassa-pipeline-title').textContent = title;
    stepEl.textContent = step;
    if (error) {
      errorEl.textContent = error;
      errorEl.hidden = false;
    } else {
      errorEl.hidden = true;
      errorEl.textContent = '';
    }
    backdrop.hidden = false;
  };

  const updateFromPipeline = (pipeline) => {
    const jobs = pipeline?.jobs || [];
    const interactive = jobs.find((j) => j.job_type === 'interactive_turn');
    const failedInteractive = jobs.find(
      (j) => j.status === 'failed' && j.job_type === 'interactive_turn',
    );
    const label =
      (interactive && interactive.status === 'running' && (pipeline?.current_job_label || STEP_LABELS.interactive_turn))
      || (interactive?.status === 'completed' ? 'Turn complete' : null)
      || pipeline?.current_job_label
      || STEP_LABELS[pipeline?.current_job_type || '']
      || pipeline?.blocking_phase
      || 'Working…';
    stepEl.textContent = label;
    if (failedInteractive?.error) {
      errorEl.textContent = failedInteractive.error;
      errorEl.hidden = false;
    }
  };

  const hide = () => {
    backdrop.hidden = true;
    errorEl.hidden = true;
    errorEl.textContent = '';
  };

  return { show, updateFromPipeline, hide, el: backdrop };
}

export function labelForJobType(jobType) {
  return STEP_LABELS[jobType] || jobType || 'Working…';
}
