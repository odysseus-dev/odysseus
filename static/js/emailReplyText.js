/** Keep explicit model control text out of an email composer. */
export function cleanEmailReplyText(text, { userHint = '', currentDraft = '' } = {}) {
  if (typeof text !== 'string') return '';
  let reply = text
    .replace(/<(think|thinking|analysis|reasoning)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi, '')
    .replace(/<\/(?:think|thinking|analysis|reasoning)\s*>/gi, '')
    .trim();

  const marked = /<<<\s*REPLY\s*>>>/i.exec(reply);
  if (marked) {
    const rest = reply.slice(marked.index + marked[0].length);
    const end = /<<<\s*END\s*>>>/i.exec(rest);
    // A partial answer is not a finished email draft.
    if (!end) return '';
    reply = rest.slice(0, end.index).trim();
  }
  const textualTranscript = !marked && /^(?:system|user|assistant)\s*:/i.test(reply) &&
    /^assistant\s*:/im.test(reply) && /^(?:system|user)\s*:/im.test(reply);
  const planning = !marked && /^(?:the user\s+(?:wants|asks(?: for)?|requested)\s+(?:a|the|an)\s+(?:(?:short|concise|brief|polished|email)\s+)?(?:reply|response|email|draft)\b|I\s+(?:need to|should|must)\s+(?:draft|write|compose|generate)\s+(?:a|the|an)\s+(?:(?:short|concise|brief|polished|email)\s+)?(?:reply|response|email|draft)\b|(?:writing style|identity rules?)\s*:|(?:analysis|reasoning|thinking)\s*:\s*(?:I\b|we\b|the user\b|need\b|let['’]?s\b))/i.test(reply);
  if (/<<<\s*(?:REPLY|SUMMARY|OUTPUT|END)\s*>>>/i.test(reply) ||
      /<\/?\|[^>\n]*\|>?/i.test(reply) || textualTranscript || planning) {
    return '';
  }
  reply = reply.replace(/^```(?:text|plaintext|markdown)?\s*\n([\s\S]*?)\n```\s*$/i, '$1');
  // The composer already contains the original quote; keep only the reply.
  reply = reply.replace(/\n+(?:On\b[^\n]*\bwrote:\s*|Den\b[^\n]*\bskrev\b[^\n]*:\s*|---------- Previous message ----------)[\s\S]*$/i, '').trim();
  const status = /^(done|completed|finished|drafted|drafting|ready)[.!…]*$/i.exec(reply);
  if (status) {
    const hint = String(userHint || '').trim();
    const word = status[1].toLowerCase();
    const requested = /(?:\b(?:say|reply|respond|answer|write|return)\s+(?:(?:with|only|just|exactly|the (?:word|text))\s+)*["'“]?(done|completed|finished|drafted|drafting|ready)\b|^(?:just|only)\s*[:,-]?\s*["'“]?(done|completed|finished|drafted|drafting|ready)\b)/i.exec(hint);
    const prohibited = new RegExp(`\\b(?:do not|don't|never|avoid)\\s+(?:(?:say|reply|respond|answer|write|return|use|include|the|word|with|only|just)\\s+)*["'“]?${word}\\b`, 'i').test(hint);
    const intentional = hint
      ? !prohibited && !!requested && (requested[1] || requested[2]).toLowerCase() === word
      : String(currentDraft || '').trim().replace(/[.!…]+$/, '').toLowerCase() === word;
    if (!intentional) return '';
  }
  if (/^(?:\[AI reply draft will appear here\]|Drafting AI reply(?:\.{3}|…)?)[.!]?$/i.test(reply)) {
    return '';
  }
  return reply;
}
