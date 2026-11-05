// One UI for live and saved browser previews. Images stay out of model history.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const states = new Map();
const requests = new Map();
let client;
try {
  const reload = performance.getEntriesByType('navigation')[0]?.type === 'reload';
  client = reload && sessionStorage.getItem('browser-viewer-client') || crypto.randomUUID();
  sessionStorage.setItem('browser-viewer-client', client);
} catch (_) { client = crypto.randomUUID(); }
const sessionId = () => decodeURIComponent(location.hash.slice(1));
const leaseKey = sid => `browser-viewer-lease:${sid}`;
function lease(sid) { try { return sessionStorage.getItem(leaseKey(sid)) || ''; } catch (_) { return ''; } }
function remember(sid, token) { try { token ? sessionStorage.setItem(leaseKey(sid), token) : sessionStorage.removeItem(leaseKey(sid)); } catch (_) {} }

export function imagePoint(rect, width, height, x, y) {
  const scale = Math.min(rect.width / width, rect.height / height);
  const left = rect.left + (rect.width - width * scale) / 2;
  const top = rect.top + (rect.height - height * scale) / 2;
  const px = (x-left)/scale, py = (y-top)/scale;
  return scale > 0 && px >= 0 && py >= 0 && px < width && py < height ? {x:px,y:py} : null;
}

export function browserPreviewHtml(command='', screenshot='', url='', boundSessionId=null) {
  const sid = boundSessionId ?? sessionId();
  return `<div class="private-browser-preview browser-viewer" data-browser-session="${esc(sid)}" tabindex="0">
    <div class="private-browser-preview-header"><span class="private-browser-preview-title">Private Browser</span>
      <span class="private-browser-preview-status">Finished — connecting preview</span>
      <button type="button" data-browser-action="enlarge">Enlarge</button>
      <button type="button" class="private-browser-preview-fold" aria-label="Fold browser preview">×</button></div>
    <div class="browser-viewer-toolbar"><select aria-label="Browser tab" data-browser-tabs></select>
      <button type="button" data-browser-action="take">Take control</button>
      <button type="button" data-browser-action="release" hidden>Return control to Odysseus</button>
      <button type="button" data-browser-action="refresh">Refresh view</button></div>
    <div class="private-browser-preview-frame"><div class="private-browser-preview-empty">Preview unavailable until a browser session is open.</div>
      <img class="private-browser-preview-img" alt="Private browser page" ${screenshot ? `src="${esc(screenshot)}"` : 'hidden'} draggable="false"></div>
    <div class="browser-viewer-input" hidden><textarea autocomplete="off" spellcheck="false" aria-label="Text for focused browser field" placeholder="Click a field above, then type or paste here"></textarea>
      <button type="button" data-browser-action="text">Send text</button>
      <button type="button" data-browser-key="Enter">Enter</button><button type="button" data-browser-key="Tab">Tab</button>
      <button type="button" data-browser-key="Backspace">Backspace</button><button type="button" data-browser-key="Control+a">Select all</button>
      <select aria-label="Focused page dropdown" data-browser-options hidden></select></div>
    <div class="browser-viewer-message" role="status">${esc(url || '')}</div></div>`;
}

async function api(sid, op='state', args={}) {
  const response = await fetch(`/api/browser/${encodeURIComponent(sid)}/${op}`, op === 'state' ? {
    credentials:'same-origin', headers:{'X-Browser-Client':client,'X-Browser-Lease':lease(sid)}, cache:'no-store'
  } : {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...args,client,lease:lease(sid)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Browser view unavailable');
  if (result.lease) remember(sid,result.lease);
  states.set(sid,result);
  render(sid,result);
  return result;
}
function roots(sid) { return [...document.querySelectorAll('.browser-viewer')].filter(el => el.dataset.browserSession===sid); }
function render(sid,state) {
  for (const root of roots(sid)) {
    const img=root.querySelector('img'), empty=root.querySelector('.private-browser-preview-empty');
    const labels={agent:state.busy ? 'Odysseus is working — previous view' : 'Ready — Odysseus controls the browser',requested:'Taking control — finishing current action',human:state.controlling ? 'You’re in control — Odysseus is paused' : 'Another viewer is in control',paused:'Paused — take control to reconnect or resume'};
    root.querySelector('.private-browser-preview-status').textContent=labels[state.mode] || 'Session closed';
    const tabs=root.querySelector('[data-browser-tabs]');
    tabs.replaceChildren(...(state.tabs||[]).map(tab => {const opt=document.createElement('option');opt.value=tab.page;opt.textContent=`${tab.page} · ${tab.url}`;return opt;}));
    tabs.value=state.page;
    const owns=!!state.controlling;
    root.querySelector('[data-browser-action="take"]').hidden=owns;
    root.querySelector('[data-browser-action="take"]').disabled=state.mode==='requested'||state.mode==='human';
    root.querySelector('[data-browser-action="release"]').hidden=!owns;
    root.querySelector('.browser-viewer-input').hidden=!owns;
    tabs.disabled=!owns;
    root.classList.toggle('user-controlled',owns);
    if (state.frame_id) {
      const src=`/api/browser/${encodeURIComponent(sid)}/frame/${encodeURIComponent(state.incarnation)}/${encodeURIComponent(state.frame_id)}`;
      if (img.getAttribute('src')!==src) { img.src=src; img.hidden=false; }
      empty.hidden=true;
    } else { empty.hidden=false; empty.textContent='Finished — no preview available'; }
    root.querySelector('.browser-viewer-message').textContent=owns ? 'Click the page, scroll, or send text. Return control when finished.' : 'This is the same private browser used by this chat.';
    const options=root.querySelector('[data-browser-options]');
    options.hidden=!state.options?.length;
    if (state.options?.length) options.replaceChildren(...state.options.map(o => {const el=document.createElement('option');el.value=o.value;el.textContent=o.label;el.selected=o.selected;return el;}));
  }
}
function error(sid, message) {
  for (const root of roots(sid)) {
    root.querySelector('.private-browser-preview-status').textContent='Preview unavailable';
    root.querySelector('.browser-viewer-message').textContent=message;
    const empty=root.querySelector('.private-browser-preview-empty');
    if (!root.querySelector('img')?.getAttribute('src')) {empty.hidden=false;empty.textContent=message;}
  }
}
async function refresh(sid) {
  if (!sid) return;
  if (requests.has(sid)) return requests.get(sid);
  const promise=api(sid).catch(e => error(sid,e.message)).finally(() => requests.delete(sid));
  requests.set(sid,promise);return promise;
}
async function perform(root,op,args={}) {
  const sid=root.dataset.browserSession;
  if (requests.has(sid)) { root.querySelector('.browser-viewer-message').textContent='The browser view is updating. Try your input again when it finishes.'; return; }
  const promise=(async () => {
    try {
      const state=states.get(sid);
      if(op==='input' && (root.querySelector('img').dataset.loadedFrame !== state?.frame_id || (args.display_frame && args.display_frame !== state?.frame_id))) {
        root.querySelector('.browser-viewer-message').textContent='Wait for the new page image before interacting.';return;
      }
      const {display_frame,...payload}=args;
      const result=await api(sid,op,{...payload,page:args.page||state?.page||root.querySelector('[data-browser-tabs]').value,
        frame_id:state?.frame_id,incarnation:state?.incarnation,width:state?.width,height:state?.height});
      if(op==='release') remember(sid,'');
      return result;
    } catch(e) {await api(sid).catch(()=>{});error(sid,e.message);}
    finally {requests.delete(sid);}
  })();
  requests.set(sid,promise);return promise;
}
function hydrate() {
  for(const root of document.querySelectorAll('.browser-viewer:not([data-viewer-mounted])')) {
    root.dataset.viewerMounted='1';
    const sid=root.dataset.browserSession;
    if(states.has(sid)) render(sid,states.get(sid));
    refresh(sid);
    const img=root.querySelector('img');
    img.addEventListener('load',()=>{const state=states.get(sid);if(state && img.getAttribute('src')?.endsWith('/'+encodeURIComponent(state.frame_id)))img.dataset.loadedFrame=state.frame_id;});
    if(img.complete && img.naturalWidth && states.get(sid)?.frame_id) img.dataset.loadedFrame=states.get(sid).frame_id;
    img.addEventListener('error',()=>{error(sid,'Preview changed or expired. Refresh view to reconnect.');});
  }
}
export function observeBrowserPreview(preview) {
  if(preview?.session_id) refresh(preview.session_id);
}
function init() {
  new MutationObserver(hydrate).observe(document.body,{childList:true,subtree:true});
  document.addEventListener('click',async event=>{
    const root=event.target.closest('.browser-viewer');if(!root)return;
    const action=event.target.closest('[data-browser-action]')?.dataset.browserAction;
    const key=event.target.closest('[data-browser-key]')?.dataset.browserKey;
    if(action||key||event.target.closest('.private-browser-preview-frame')){event.preventDefault();event.stopImmediatePropagation();}
    if(action==='enlarge'){root.classList.toggle('browser-viewer-large');return;}
    if(action==='text') {const input=root.querySelector('textarea');const text=input.value;if(text && await perform(root,'input',{kind:'text',text})) input.value='';return;}
    if(key){await perform(root,'input',{kind:'key',key});return;}
    if(action==='refresh') {const state=states.get(root.dataset.browserSession);await (state?.controlling?perform(root,'refresh'):refresh(root.dataset.browserSession));return;}
    if(action){await perform(root,action);return;}
    const state=states.get(root.dataset.browserSession);
    if(event.target.matches('img')&&state?.controlling){
      const point=imagePoint(event.target.getBoundingClientRect(),state.width,state.height,event.clientX,event.clientY);
      if(!point)return;
      clearTimeout(root._browserClickTimer);
      const send=async kind=>{await perform(root,'input',{kind,...point,display_frame:state.frame_id});root.focus();};
      if(event.detail===2)send('double_click');
      else root._browserClickTimer=setTimeout(()=>send('click'),250);
    }
  },true);
  document.addEventListener('wheel',event=>{
    const root=event.target.closest('.browser-viewer');if(!root||!event.target.matches('img')||!states.get(root.dataset.browserSession)?.controlling)return;
    event.preventDefault();event.stopImmediatePropagation();
    perform(root,'input',{kind:'scroll',dx:Math.max(-2000,Math.min(2000,event.deltaX)),dy:Math.max(-2000,Math.min(2000,event.deltaY))});
  },{capture:true,passive:false});
  document.addEventListener('keydown',event=>{
    const root=event.target.closest('.browser-viewer');if(!root||event.target!==root||!states.get(root.dataset.browserSession)?.controlling)return;
    const keys=['Enter','Tab','Escape','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'];
    if(keys.includes(event.key)){event.preventDefault();event.stopImmediatePropagation();perform(root,'input',{kind:'key',key:event.shiftKey&&event.key==='Tab'?'Shift+Tab':event.key});}
  },true);
  document.addEventListener('change',event=>{
    const root=event.target.closest('.browser-viewer');if(!root)return;
    if(event.target.matches('[data-browser-tabs]')) perform(root,'refresh',{page:event.target.value});
    if(event.target.matches('[data-browser-options]')) perform(root,'input',{kind:'select',value:event.target.value});
  });
  setInterval(()=>{for(const [sid,state] of states) {
    const root=roots(sid)[0];if(!root || document.visibilityState!=='visible' || requests.has(sid))continue;
    if(state.controlling) perform(root,'refresh'); else if(state.mode==='requested')refresh(sid);
  }},2000);
  hydrate();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
