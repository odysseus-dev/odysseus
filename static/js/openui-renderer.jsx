import React from 'react';
import { createRoot } from 'react-dom/client';
import { Renderer } from '@openuidev/react-lang';
import { openuiLibrary } from '@openuidev/react-ui/genui-lib';
import '@openuidev/react-ui/defaults.css';
import '@openuidev/react-ui/styles/index.css';

const roots = new WeakMap();

function OpenUIPreview({ response, isStreaming, initialState, onState, onAction }) {
  return (
    <div className="odysseus-openui-render">
      <Renderer
        library={openuiLibrary}
        response={response || ''}
        isStreaming={!!isStreaming}
        initialState={initialState || undefined}
        onStateUpdate={(state) => {
          if (typeof onState === 'function') onState(state);
        }}
        onAction={(event) => {
          if (typeof onAction === 'function') onAction(event);
          window.dispatchEvent(new CustomEvent('odysseus-openui-action', { detail: event }));
        }}
        onError={(errors) => {
          if (errors && errors.length) console.warn('OpenUI render errors', errors);
        }}
      />
    </div>
  );
}

export function renderOpenUI(target, response, options = {}) {
  if (!target) return;
  let root = roots.get(target);
  if (!root) {
    root = createRoot(target);
    roots.set(target, root);
  }
  root.render(
    <OpenUIPreview
      response={response}
      isStreaming={options.isStreaming}
      initialState={options.initialState}
      onState={options.onState}
      onAction={options.onAction}
    />
  );
}

export function unmountOpenUI(target) {
  const root = target && roots.get(target);
  if (!root) return;
  root.unmount();
  roots.delete(target);
}
