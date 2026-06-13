
// static/js/personaSelector.js
// Gemini-style persona switcher for the main chat

export function initPersonaSelector() {
  // Create floating persona pill in the composer area
  const composer = document.querySelector('.composer, #composer, .chat-input-container');
  if (!composer) return;

  const pill = document.createElement('div');
  pill.id = 'persona-pill';
  pill.style.cssText = `
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    background: rgba(99,102,241,0.1);
    border: 1px solid rgba(99,102,241,0.3);
    border-radius: 999px;
    font-size: 0.8rem;
    cursor: pointer;
    margin-right: 8px;
    transition: all 0.2s ease;
  `;
  
  pill.innerHTML = `
    <span style="color:#a5b4fc">Persona:</span> 
    <span id="current-persona-name" style="font-weight:600;color:#c0c7d6">Default</span>
    <span style="font-size:10px;opacity:0.6">▼</span>
  `;

  pill.onclick = async () => {
    const res = await fetch('/api/personas');
    const personas = await res.json();
    
    const menu = document.createElement('div');
    menu.style.cssText = `
      position:absolute; background:#1e2937; border:1px solid #334155;
      border-radius:12px; padding:6px 0; min-width:220px; z-index:999;
      box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.3);
    `;
    
    personas.forEach(p => {
      const item = document.createElement('div');
      item.style.cssText = 'padding:8px 16px; cursor:pointer; display:flex; align-items:center; gap:8px;';
      item.innerHTML = `
        <div style="width:6px;height:6px;border-radius:50%;background:${p.status==='active' ? '#4ade80' : '#64748b'}"></div>
        <div>
          <div style="font-weight:600">${p.display_name}</div>
          <div style="font-size:0.7rem;opacity:0.6">${p.category}</div>
        </div>
      `;
      item.onclick = async () => {
        // In real app this would set the active persona for the session
        document.getElementById('current-persona-name').textContent = p.display_name;
        menu.remove();
        
        // Optional: send a system message to the chat
        if (window.addSystemMessage) {
          window.addSystemMessage(`Switched to persona: ${p.display_name}`);
        }
        
        // You can also call an API to set active persona for the current session here
      };
      menu.appendChild(item);
    });

    const rect = pill.getBoundingClientRect();
    menu.style.left = rect.left + 'px';
    menu.style.top = (rect.bottom + 8) + 'px';
    document.body.appendChild(menu);

    setTimeout(() => {
      document.addEventListener('click', function handler(e) {
        if (!menu.contains(e.target)) {
          menu.remove();
          document.removeEventListener('click', handler);
        }
      }, { once: true });
    }, 100);
  };

  // Insert the pill
  const inputArea = composer.querySelector('textarea')?.parentElement || composer;
  inputArea.style.display = 'flex';
  inputArea.style.alignItems = 'center';
  inputArea.prepend(pill);

  // Load current persona (stub)
  fetch('/api/personas').then(r => r.json()).then(list => {
    const active = list.find(p => p.status === 'active');
    if (active) {
      document.getElementById('current-persona-name').textContent = active.display_name;
    }
  });
}

// Auto init when imported
if (typeof window !== 'undefined') {
  setTimeout(() => {
    if (document.readyState === 'complete') {
      initPersonaSelector();
    } else {
      window.addEventListener('load', initPersonaSelector);
    }
  }, 1200);
}
