import re

with open('static/js/slashCommands.js', 'r') as f:
    content = f.read()

def replace_cmd_demo(code):
    pattern = r"async function _cmdDemo\(args, ctx\) \{"
    match = re.search(pattern, code)
    if not match:
        return code
    start_idx = match.start()
    
    brace_count = 0
    in_function = False
    for i in range(start_idx, len(code)):
        if code[i] == '{':
            brace_count += 1
            in_function = True
        elif code[i] == '}':
            brace_count -= 1
        
        if in_function and brace_count == 0:
            end_idx = i + 1
            break
            
    replacement = """async function _cmdDemo(args, ctx) {
  const hasModels = await _hasConfiguredModels();
  if (!hasModels) {
    await typewriterReply('Before the tour, add your first AI endpoint with /setup or in /settings.');
    return true;
  }

  const tour = new Tour();
  let _typedDraft = '';
  const _msgEl = document.getElementById('message');
  const _onTyped = () => { if (_msgEl) _typedDraft = _msgEl.value; };
  if (_msgEl) _msgEl.addEventListener('input', _onTyped);
  const _restoreIfCleared = () => {
    if (!_msgEl || !_typedDraft) return;
    if (_msgEl.value === '' && _typedDraft) {
      _msgEl.value = _typedDraft;
      _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };
  const _draftObserver = new MutationObserver(() => _restoreIfCleared());
  if (_msgEl) _draftObserver.observe(_msgEl, { attributes: true, attributeFilter: ['value'] });
  const _draftPoll = setInterval(_restoreIfCleared, 200);

  const _clearTour = () => {
    tour.clear();
    setTimeout(() => {
      if (_msgEl && _onTyped) _msgEl.removeEventListener('input', _onTyped);
      if (_draftObserver) _draftObserver.disconnect();
      if (_draftPoll) clearInterval(_draftPoll);
    }, 3000);
  };

  const sidebar = document.getElementById('sidebar');

  const steps = [
    { sel: '#sidebar-new-chat-btn', text: 'Start a new chat here. <b>Click it.</b> You can do it!', mode: 'click',
      before() { if (sidebar?.classList.contains('hidden')) sidebar.classList.remove('hidden'); } },
    { sel: '#model-picker-btn',   text: 'Pick your LLM, Local or API.', advanceOnClick: true },
    { sel: '#mode-agent-btn',     text: '<b>Agent mode</b> gives Odysseus more control of the app when your model supports tools: create a theme, download a model, make a daily task, organize things, and more.', mode: 'click' },
    { sel: '#web-toggle-btn',     text: 'Toggle tools like <b>web search</b>. Odysseus comes with private built-in <b>SearXNG</b> search.', mode: 'click' },
    { sel: '#overflow-plus-btn',  text: 'More tools can be found here, or in your sidebar. <b>Click to peek.</b>',
      advanceOnClick: true, pulseNext: true, afterDelay: 2200 },
    { sel: '#message',            text: 'Write your prompt here. Drag and drop files to attach them. <b>/prompt</b> for random prompt, <b>/help</b> for more.',
      finishLabel: true,
      before() { document.getElementById('overflow-menu')?.classList.add('hidden'); } },
  ];

  let i = 0;
  while (i < steps.length) {
    const step = steps[i];
    if (step.before) step.before();
    const res = await tour.showStep({
      sel: step.sel, text: step.text, mode: step.mode || 'next', isFirst: i === 0, isLast: false, 
      advanceOnClick: step.advanceOnClick, pulseNext: step.pulseNext, finishLabel: step.finishLabel
    });
    if (res === 'cancel' || res === 'skip') { _clearTour(); return true; }
    if (res === 'back') { if (i > 0) i--; continue; }
    i++;
    await new Promise(r => setTimeout(r, step.afterDelay || 750));
    if (step.sel === '#message' && _isStreamingFn()) {
      tour.clearHalos();
      document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
      if (tour.tooltip) tour.tooltip.style.display = 'none';
      await new Promise(r => {
        const check = setInterval(() => { if (!_isStreamingFn()) { clearInterval(check); r(); } }, 300);
      });
      await new Promise(r => setTimeout(r, 400));
    }
  }

  _clearTour();
  await typewriterReply('Odysseus is yours to explore, enjoy the voyage!');
  return true;
}"""
    
    return code[:start_idx] + replacement + code[end_idx:]

new_content = replace_cmd_demo(content)
with open('static/js/slashCommands.js', 'w') as f:
    f.write(new_content)
print("Replaced _cmdDemo successfully")
