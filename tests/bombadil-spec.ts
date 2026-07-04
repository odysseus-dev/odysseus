/**
 * Bombadil spec for Void UI
 */
import { extract, always, eventually, now, actions } from "@antithesishq/bombadil";
export * from "@antithesishq/bombadil/defaults";

// ── Extractors (only place you can access the DOM) ──

const onLoginPage = extract((state) => {
  return state.document.querySelector("#username") !== null;
});

const loginElements = extract((state) => {
  const user = state.document.querySelector("#username") as HTMLElement | null;
  const pass = state.document.querySelector("#password") as HTMLElement | null;
  const btn = state.document.querySelector('button[type="submit"]') as HTMLElement | null;
  if (!user || !pass || !btn) return null;
  const ur = user.getBoundingClientRect();
  const pr = pass.getBoundingClientRect();
  const br = btn.getBoundingClientRect();
  return {
    user: { x: ur.left + ur.width / 2, y: ur.top + ur.height / 2 },
    pass: { x: pr.left + pr.width / 2, y: pr.top + pr.height / 2 },
    btn: { x: br.left + br.width / 2, y: br.top + br.height / 2 },
  };
});

const chatInput = extract((state) => {
  const el = state.document.querySelector("#message") as HTMLElement | null;
  if (!el || (el as any).offsetParent === null) return null;
  const rect = el.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, disabled: (el as any).disabled };
});

const pageHasContent = extract((state) => {
  return state.document.body && state.document.body.children.length > 0;
});

const visibleModals = extract((state) => {
  let count = 0;
  state.document.querySelectorAll(".modal").forEach((m: any) => {
    if (!m.classList.contains("hidden") && m.offsetParent !== null) count++;
  });
  return count;
});

const clickableElements = extract((state) => {
  const els: { name: string; x: number; y: number }[] = [];
  const selectors = "button:not([disabled]),.list-item,.icon-rail-btn,.section-header-flex,.send-btn,.sidebar-brand,input[type=checkbox]";
  state.document.querySelectorAll(selectors).forEach((el: any) => {
    if (el.offsetParent === null) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const name = el.id || el.tagName;
    els.push({ name, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  });
  return els;
});

// ── Login actions ──

export const login = actions(() => {
  const le = loginElements.current;
  if (!le) return [];
  return [
    { Click: { name: "username", point: le.user } },
    { TypeText: { text: "tester", delayMillis: 30 } },
    { Click: { name: "password", point: le.pass } },
    { TypeText: { text: "iloveass123", delayMillis: 30 } },
    { Click: { name: "submit", point: le.btn } },
  ];
});

// ── App exploration ──

export const explore = actions(() => {
  if (onLoginPage.current) return [];
  const acts: any[] = [];

  const els = clickableElements.current || [];
  for (const el of els) {
    acts.push({ Click: { name: el.name, point: { x: el.x, y: el.y } } });
  }

  const input = chatInput.current;
  if (input && !input.disabled) {
    acts.push({ Click: { name: "chat-input", point: { x: input.x, y: input.y } } });
    acts.push({ TypeText: { text: "hello", delayMillis: 50 } });
    acts.push({ PressKey: { code: 13 } });
  }

  acts.push({ ScrollDown: { origin: { x: 512, y: 400 }, distance: 300 } });
  acts.push({ ScrollUp: { origin: { x: 512, y: 400 }, distance: 300 } });
  acts.push("Wait");

  return acts;
});

// ── Properties ──

export const noBlankPage = always(() => pageHasContent.current === true);
export const noModalStacking = always(() => (visibleModals.current || 0) <= 2);
export const chatInputAppears = always(
  now(() => onLoginPage.current === false).implies(
    eventually(() => chatInput.current !== null).within(10, "seconds")
  )
);
