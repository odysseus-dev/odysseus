"""Exercise the stream-owned sidebar cleanup for terminal and detached paths."""
import json
import subprocess
from pathlib import Path


def test_sidebar_completion_clears_only_the_owning_finished_stream():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'static/js/chat.js').read_text()
    # The finalizer is a sibling of try, so its completion flag must be in
    # the shared outer scope alongside abortCtrl, not the SSE parser block.
    assert source.count('let _streamSawDone = false;') == 1
    assert source.index('let _streamSawDone = false;') < source.index('let abortCtrl = null;')
    start = source.index('      if (_ownsStreamState && _streamSawDone) {')
    end = source.index('      const _finallyRegistered', start)
    script = '''
      const cleanup = new Function('_ownsStreamState', '_streamSawDone', 'abortCtrl',
        'sessionModule', 'streamSessionId', BODY);
      const results = [];
      for (const [owner, done, reason] of [
        [true,true,null], [true,false,'user-stop'], [true,false,'detach'],
        [false,true,null], [false,false,'user-stop'], [true,false,null]
      ]) {
        const calls=[];
        cleanup(owner,done,{_reason:reason},{
          markStreamComplete: id=>calls.push('complete:'+id),
          clearStreaming: id=>calls.push('clear:'+id),
        },'chat');
        results.push(calls);
      }
      console.log(JSON.stringify(results));
    '''.replace('BODY', json.dumps(source[start:end]))
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [['complete:chat'], ['clear:chat'], [], [], [], []]


def test_tool_wait_indicator_cannot_reappear_after_completion_or_stop():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'static/js/chat.js').read_text()
    start = source.index('      let _toolPauseTimer = null;')
    end = source.index('      // Document streaming state', start)
    # The only scheduling call belongs to tool completion, not prose deltas.
    assert source.count('_scheduleToolWaitSpinner();') == 1
    tool_output = source.index("json.type === 'tool_output'", source.index("json.type === 'tool_start')"))
    assert source.index('_scheduleToolWaitSpinner();') > tool_output
    script = r'''
      const results = [];
      for (const mode of ['waiting', 'done', 'stopped', 'replaced', 'hidden', 'cancelled']) {
        let callback, shown = 0;
        const streamSessionId = 'chat';
        const abortCtrl = {signal: {aborted: mode === 'stopped'}};
        const _activeStreams = new Map([['chat', {abortCtrl: mode === 'replaced' ? {} : abortCtrl}]]);
        const sessionModule = {getCurrentSessionId: () => mode === 'hidden' ? 'other' : 'chat'};
        let _streamSawDone = false, _thinkingSpinnerEl = null, _cancelThinkingTimer;
        const _showThinkingSpinner = () => shown++;
        const _thinkingLabel = () => 'Thinking';
        const setTimeout = fn => {callback = fn; return 1;};
        const clearTimeout = () => {callback = null;};
        BODY
        _scheduleToolWaitSpinner();
        if (mode === 'done') _streamSawDone = true;
        if (mode === 'cancelled') _cancelThinkingTimer();
        callback?.();
        results.push(shown);
      }
      console.log(JSON.stringify(results));
    '''.replace('BODY', source[start:end])
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [1, 0, 0, 0, 0, 0]


def test_done_exits_reader_without_waiting_for_connection_close():
    source = (Path(__file__).resolve().parents[1] / 'static/js/chat.js').read_text()
    start = source.index("            if (data === '[DONE]') {")
    end = source.index('            try {\n              const json = JSON.parse(data);', start)
    body = source[start:end]
    script = r'''
      let reads = 0, cancellations = 0, _streamSawDone = false;
      const reader = {cancel: async () => {cancellations++;}};
      const _cancelThinkingTimer = () => {}, _removeThinkingSpinner = () => {};
      const _activeStreams = new Map(), _backgroundStreams = new Map();
      const streamSessionId = 'test', document = {visibilityState: 'visible'};
      const _closeOpenThinkingMarkup = () => {};
      const _isBg = false, isThinking = false;
      streamReadLoop:
      while (true) {
        reads++;
        if (reads > 1) throw Error('Waited for EOF after DONE');
        for (const data of ['[DONE]']) {
          BODY
        }
      }
      console.log(JSON.stringify({reads, cancellations, done: _streamSawDone}));
    '''.replace('BODY', body)
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {'reads': 1, 'cancellations': 1, 'done': True}
