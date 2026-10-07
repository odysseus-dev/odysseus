#!/usr/bin/env node
/** Verify that a link tapped while its streaming DOM node is replaced still activates. */
import fs from 'node:fs';
import { chromium } from 'playwright';

const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const sessions = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(sessions).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);

const browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
try {
  const context = await browser.newContext({ serviceWorkers: 'block' });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const page = await context.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#chat-history');
  const result = await page.evaluate(async () => {
    const history = document.querySelector('#chat-history');
    const button = document.querySelector('#tool-notes-btn');
    if (!history || !button) return { passed: false, error: 'required DOM missing' };
    let activations = 0;
    button.addEventListener('click', () => { activations += 1; });
    const bubble = document.createElement('div');
    bubble.className = 'msg msg-ai streaming';
    bubble.innerHTML = '<a href="#notes">Open notes</a>';
    history.appendChild(bubble);
    const anchor = bubble.querySelector('a');
    anchor.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, pointerId: 77, clientX: 10, clientY: 10,
    }));
    bubble.innerHTML = '<span>next streamed token</span>';
    document.body.dispatchEvent(new PointerEvent('pointerup', {
      bubbles: true, pointerId: 77, clientX: 10, clientY: 10,
    }));
    await new Promise(resolve => setTimeout(resolve, 50));
    bubble.remove();
    return { passed: activations === 1, activations };
  });
  console.log(JSON.stringify(result));
  if (!result.passed) process.exitCode = 1;
} finally {
  await browser.close();
}
