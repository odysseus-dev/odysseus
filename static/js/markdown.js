// static/js/markdown.js

/**
 * Markdown rendering and content processing utilities
 */

import uiModule from './ui.js';

var escapeHtml = uiModule.esc;

function safeLinkUrl(rawUrl) {
  const url = String(rawUrl || '').trim();
  if (url.startsWith('#')) {
    return /^#[A-Za-z0-9_-]*$/.test(url) ? url : '';
  }
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch (_) {
    return '';
  }
  return '';
}

function linkHtml(text, url) {
  const safeUrl = safeLinkUrl(url);
  const safeText = escapeHtml(text);
  if (!safeUrl) return safeText;
  if (safeUrl.startsWith('#')) {
    return `<a href="${safeUrl}" class="chat-link">${safeText}</a>`;
  }
  return `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${safeText}</a>`;
}

/**
 * Check if text has unclosed think tag
 */
export function hasUnclosedThinkTag(text) {
  const openCount = (text.match(/<think(?:ing)?>/gi) || []).length;
  const closeCount = (text.match(/<\/think(?:ing)?>/gi) || []).length;
  return openCount > closeCount;
}

export function startsWithReasoningPrefix(text) {
  return /^\s*(?:thinking(?:\s+process)?\s*:|the user |i need |i should |i will |they are |the question |i can )/i.test(text || '');
}

function normalizePlainThinking(text) {
  if (!text || /<think/i.test(text)) return text;

  const trimmed = text.trimStart();
  if (!startsWithReasoningPrefix(trimmed)) return text;

  const replyStarts = [
    'Hey', 'Hi ', 'Hi!', 'Hello', 'Sure', 'Yes', 'No ', 'No,', 'Yo', 'OK',
    'Here', 'Absolutely', 'Of course', 'Great', 'Alright', 'Thanks', 'Welcome',
    'Good ', "I'm happy", "I'd be"
  ];
  const prefixRegex = /^(thinking(?:\s+process)?\s*:)\s*/i;
  const escapedReplyStarts = replyStarts.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const boundaryRegex = new RegExp(
    `^([\\s\\S]*?)(\\n\\n(?=${escapedReplyStarts.join('|')}|I |What|Let|This |As ))[\\s\\S]*$`,
    'i'
  );
  const boundaryMatch = boundaryRegex.exec(trimmed);

  if (boundaryMatch) {
    const thinkBlock = boundaryMatch[1].replace(prefixRegex, '').trim();
    const reply = trimmed.slice(boundaryMatch[1].length).trimStart();
    if (thinkBlock && reply) return `<think>${thinkBlock}</think>\n\n${reply}`;
  }

  const lines = trimmed.split('\n');
  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    if (replyStarts.some((prefix) => line.startsWith(prefix))) {
      const thinkBlock = lines.slice(0, index).join('\n').replace(prefixRegex, '').trim();
      const reply = lines.slice(index).join('\n').trim();
      if (thinkBlock && reply) return `<think>${thinkBlock}</think>\n${reply}`;
    }
  }

  const withoutPrefix = trimmed.replace(prefixRegex, '');
  for (const prefix of replyStarts) {
    const rx = new RegExp(`[.!?]\\s*(${prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`);
    const match = rx.exec(withoutPrefix);
    if (match && match.index > 20) {
      const thinkBlock = withoutPrefix.slice(0, match.index + 1).trim();
      const reply = withoutPrefix.slice(match.index + 1).trim();
      if (thinkBlock && reply) return `<think>${thinkBlock}</think>\n${reply}`;
    }
  }

  return text;
}

/**
 * Extract all complete thinking blocks and remaining content
 */
export function extractThinkingBlocks(text) {
  // Handle malformed patterns: <think></think>\n...actual thinking...\n</think>
  // Some models emit an empty <think></think> then put thinking text outside,
  // closed by a second orphaned </think>.
  let normalized = normalizePlainThinking(text);
  // Collapse <think>short</think>...real thinking...</think> into one block
  // Models sometimes emit a trivial first block then continue thinking outside tags
  normalized = normalized.replace(/<think(?:ing)?(?:\s+[^>]*)?>.{0,30}<\/think(?:ing)?>\s*([\s\S]*?)<\/think(?:ing)?>/gi, (m, content) => {
    return '<think>' + content.trim() + '</think>';
  });

  // Merge consecutive <think> blocks (some models split thinking across multiple tags)
  normalized = normalized.replace(/<\/think(?:ing)?>\s*<think(?:ing)?(?:\s+[^>]*)?>/gi, '\n\n');

  // Extract thinking time attribute if present
  const timeMatch = normalized.match(/<think(?:ing)?\s+time="([\d.]+)"/i);
  const thinkingTime = timeMatch ? timeMatch[1] : null;
  // Strip time attribute for content extraction
  normalized = normalized.replace(/<think(?:ing)?\s+time="[\d.]+"/gi, '<think');

  const thinkRegex = /<think(?:ing)?(?:\s+[^>]*)?>([\s\S]*?)<\/think(?:ing)?>/gi;
  const thinkingBlocks = [];
  let match;

  // Extract all complete thinking blocks
  while ((match = thinkRegex.exec(normalized)) !== null) {
    const content = match[1].trim();
    if (content) thinkingBlocks.push(content);
  }

  // Remove all complete <think>/<thinking> blocks
  let cleanContent = normalized.replace(thinkRegex, '');

  // If there's an unclosed tag, decide between two cases:
  // (a) Stray opener at the very start with no real reply before it — typical
  //     of quantized models (MiniMax-AWQ) that emit a literal `<think>` token
  //     at the start of every reply without ever closing it. Strip just the
  //     opener and keep the body as the reply, otherwise the bubble looks
  //     blank on reload (the body was being treated as collapsed thinking).
  // (b) Cut-off mid-generation — there's already real reply text before the
  //     opener. Drop from the tag onward as before (it's truncated thinking).
  if (hasUnclosedThinkTag(normalized)) {
    const strayOpener = cleanContent.match(/^\s*<think(?:ing)?(?:\s+[^>]*)?>([\s\S]*)$/i);
    if (strayOpener) {
      cleanContent = strayOpener[1];
    } else {
      cleanContent = cleanContent.replace(/<think(?:ing)?(?:\s+[^>]*)?>[\s\S]*$/gi, '');
    }
  }

  // Handle orphaned </think> with no opening tag — text before it is leaked thinking
  const orphanMatch = cleanContent.match(/^([\s\S]+?)<\/think(?:ing)?>/i);
  if (orphanMatch && orphanMatch[1].trim()) {
    thinkingBlocks.push(orphanMatch[1].trim());
    cleanContent = cleanContent.slice(orphanMatch[0].length);
  }

  // Strip any remaining orphaned closing tags
  cleanContent = cleanContent.replace(/<\/think(?:ing)?>/gi, '');

  // Merge all thinking blocks into one — no reason to show multiple dropdowns
  const mergedBlocks = thinkingBlocks.length > 1
    ? [thinkingBlocks.join('\n\n')]
    : thinkingBlocks;

  return {
    thinkingBlocks: mergedBlocks,
    content: cleanContent.trim(),
    thinkingTime,
  };
}

/**
 * Create a collapsible thinking section
 */
function createThinkingSection(thinkingContent, index = 0, thinkingTime = null) {
  const id = `thinking-${Date.now()}-${index}`;
  const timeHtml = thinkingTime ? `<span style="font-size:11px;opacity:0.4;font-variant-numeric:tabular-nums;">${thinkingTime}s</span>` : '';
  return `
    <div class="thinking-section">
      <div class="thinking-header" data-thinking-id="${id}">
        <div class="thinking-header-left">
          <span>View thinking process</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
          ${timeHtml}
          <span class="thinking-toggle" id="${id}-toggle"></span>
        </div>
      </div>
      <div class="thinking-content" id="${id}">
        <div class="thinking-content-inner">
          ${mdToHtml(thinkingContent)}
        </div>
      </div>
    </div>
  `;
}

/**
 * Process text and render with thinking sections
 */
// ── Emoji shortcode → unicode conversion ──
// Converts :blush: → 😊 etc. so the SVG pipeline below can pick them up.
// Covers GitHub-flavored shortcodes commonly emitted by LLMs.
const _E = {
  ':+1:':'\uD83D\uDC4D',':-1:':'\uD83D\uDC4E',':100:':'\uD83D\uDCAF',':1234:':'\uD83D\uDD22',
  ':8ball:':'\uD83C\uDFB1',':a:':'\uD83C\uDD70\uFE0F',':ab:':'\uD83C\uDD8E',':abacus:':'\uD83E\uDDEE',
  ':abc:':'\uD83D\uDD24',':abcd:':'\uD83D\uDD21',':accept:':'\uD83C\uDD51',':adhesive_bandage:':'\uD83E\uDE79',
  ':adult:':'\uD83E\uDDD1',':aerial_tramway:':'\uD83D\uDEA1',':afghanistan:':'\uD83C\uDDE6\uD83C\uDDEB',
  ':airplane:':'\u2708\uFE0F',':airplane_arriving:':'\uD83D\uDEEC',':airplane_departure:':'\uD83D\uDEEB',
  ':alarm_clock:':'\u23F0',':albania:':'\uD83C\uDDE6\uD83C\uDDF1',':alembic:':'\u2697\uFE0F',
  ':algeria:':'\uD83C\uDDE6\uD83C\uDDFF',':alien:':'\uD83D\uDC7D',':ambulance:':'\uD83D\uDE91',
  ':american_samoa:':'\uD83C\uDDE6\uD83C\uDDF8',':amphora:':'\uD83C\uDFFA',':anatomical_heart:':'\uD83E\uDEC0',
  ':anchor:':'\u2693',':andorra:':'\uD83C\uDDE6\uD83C\uDDE9',':angel:':'\uD83D\uDC7C',
  ':anger:':'\uD83D\uDCA2',':angola:':'\uD83C\uDDE6\uD83C\uDDF4',':angry:':'\uD83D\uDE20',
  ':anguished:':'\uD83D\uDE27',':ant:':'\uD83D\uDC1C',':antarctica:':'\uD83C\uDDE6\uD83C\uDDF6',
  ':antigua_barbuda:':'\uD83C\uDDE6\uD83C\uDDEC',':apple:':'\uD83C\uDF4E',':aquarius:':'\uD83D\uDD31',
  ':argentina:':'\uD83C\uDDE6\uD83C\uDDF7',':aries:':'\u2648\uFE0F',':armenia:':'\uD83C\uDDE6\uD83C\uDDF2',
  ':arrow_down:':'\u2B07\uFE0F',':arrow_left:':'\u2B05\uFE0F',':arrow_right:':'\u27A1\uFE0F',
  ':arrow_up:':'\u2B06\uFE0F',':art:':'\uD83C\uDFA8',':articulated_lorry:':'\uD83D\uDE9B',
  ':artist:':'\uD83E\uDDD1\u200D\uD83C\uDFA8',':astonished:':'\uD83D\uDE32',':athletic_shoe:':'\uD83D\uDC5F',
  ':atm:':'\uD83C\uDFE7',':atom_symbol:':'\u269B\uFE0F',':australia:':'\uD83C\uDDE6\uD83C\uDDFA',
  ':austria:':'\uD83C\uDDE6\uD83C\uDDF9',':avocado:':'\uD83E\uDD51',':axe:':'\uD83E\uDE93',
  ':azerbaijan:':'\uD83C\uDDE6\uD83C\uDDFF',':b:':'\uD83C\uDD71\uFE0F',':baby:':'\uD83D\uDC76',
  ':baby_bottle:':'\uD83C\uDF7C',':baby_chick:':'\uD83D\uDC24',':baby_symbol:':'\uD83D\uDEBC',
  ':back:':'\uD83D\uDD19',':bacon:':'\uD83E\uDD53',':badger:':'\uD83E\uDDBA',
  ':badminton:':'\uD83C\uDFF8',':bagel:':'\uD83E\uDD6F',':baggage_claim:':'\uD83D\uDEC4',
  ':baguette_bread:':'\uD83E\uDD56',':bahamas:':'\uD83C\uDDE7\uD83C\uDDF8',':bahrain:':'\uD83C\uDDE7\uD83C\uDDED',
  ':balance_scale:':'\u2696\uFE0F',':bald:':'\uD83E\uDDD2',':ballet_shoes:':'\uD83E\uDE70',
  ':balloon:':'\uD83C\uDF88',':ballot_box:':'\uD83D\uDDF3\uFE0F',':bamboo:':'\uD83C\uDF8D',
  ':banana:':'\uD83C\uDF4C',':bangbang:':'\u203C\uFE0F',':bangladesh:':'\uD83C\uDDE7\uD83C\uDDE9',
  ':bank:':'\uD83C\uDFE6',':bar_chart:':'\uD83D\uDCCA',':barbados:':'\uD83C\uDDE7\uD83C\uDDE7',
  ':barber:':'\uD83D\uDC88',':baseball:':'\u26BE',':basket:':'\uD83E\uDDFA',':basketball:':'\uD83C\uDFC0',
  ':bat:':'\uD83E\uDD87',':bath:':'\uD83D\uDEC0',':bathtub:':'\uD83D\uDEC1',':battery:':'\uD83D\uDD0B',
  ':beach_umbrella:':'\uD83C\uDFD6\uFE0F',':bear:':'\uD83D\uDC3B',':bearded_person:':'\uD83E\uDDD4',
  ':beaver:':'\uD83E\uDDAB',':bed:':'\uD83D\uDECF\uFE0F',':bee:':'\uD83D\uDC1D',':beer:':'\uD83C\uDF7A',
  ':beers:':'\uD83C\uDF7B',':beetle:':'\uD83D\uDC1E',':beginner:':'\uD83D\uDD30',
  ':belarus:':'\uD83C\uDDE7\uD83C\uDDFE',':belgium:':'\uD83C\uDDE7\uD83C\uDDEA',
  ':belize:':'\uD83C\uDDE7\uD83C\uDDFF',':bell:':'\uD83D\uDD14',':bell_pepper:':'\uD83E\uDED1',
  ':bellhop_bell:':'\uD83D\uDECE\uFE0F',':benin:':'\uD83C\uDDE7\uD83C\uDDEF',':bento:':'\uD83C\uDF71',
  ':bermuda:':'\uD83C\uDDE7\uD83C\uDDF2',':bhutan:':'\uD83C\uDDE7\uD83C\uDDF9',
  ':bicyclist:':'\uD83D\uDEB4',':bike:':'\uD83D\uDEB2',':bikini:':'\uD83D\uDC59',
  ':billed_cap:':'\uD83E\uDDE2',':biohazard:':'\u2622\uFE0F',':bird:':'\uD83D\uDC26',
  ':birthday:':'\uD83C\uDF82',':bison:':'\uD83E\uDDAC',':black_cat:':'\uD83D\uDC08\u200D\u2B1B',
  ':black_circle:':'\u26AB',':black_flag:':'\uD83C\uDFF4',':black_heart:':'\uD83D\uDDA4',
  ':black_square:':'\u2B1B',':blond_haired:':'\uD83D\uDC71',':blonde_woman:':'\uD83D\uDC71\u200D\u2640\uFE0F',
  ':blossom:':'\uD83C\uDF3C',':blowfish:':'\uD83D\uDC21',':blue_book:':'\uD83D\uDCD8',
  ':blue_car:':'\uD83D\uDE99',':blue_heart:':'\uD83D\uDC99',':blueberries:':'\uD83E\uDED0',
  ':blush:':'\uD83D\uDE0A',':boar:':'\uD83D\uDC17',':boat:':'\u26F5',':bolivia:':'\uD83C\uDDE7\uD83C\uDDF4',
  ':bomb:':'\uD83D\uDCA3',':bone:':'\uD83E\uDDB4',':book:':'\uD83D\uDCD6',':bookmark:':'\uD83D\uDD16',
  ':bookmark_tabs:':'\uD83D\uDCD1',':books:':'\uD83D\uDCDA',':boom:':'\uD83D\uDCA5',
  ':boomerang:':'\uD83E\uDE96',':boot:':'\uD83D\uDC62',':bosnia_herzegovina:':'\uD83C\uDDE7\uD83C\uDDE6',
  ':botswana:':'\uD83C\uDDE7\uD83C\uDDFC',':bouncing_ball:':'\u26F9\uFE0F',':bouquet:':'\uD83D\uDC90',
  ':bow_and_arrow:':'\uD83C\uDFF9',':bowl_with_spoon:':'\uD83E\uDD63',':bowling:':'\uD83C\uDFB3',
  ':boxing_glove:':'\uD83E\uDD4A',':boy:':'\uD83D\uDC66',':brain:':'\uD83E\uDDE0',
  ':brazil:':'\uD83C\uDDE7\uD83C\uDDF7',':bread:':'\uD83C\uDF5E',':breast_feeding:':'\uD83E\uDD31',
  ':brick:':'\uD83E\uDDF1',':bride_with_veil:':'\uD83D\uDC70',':bridge_at_night:':'\uD83C\uDF09',
  ':briefcase:':'\uD83D\uDCBC',':briefs:':'\uD83E\uDE72',':bright_button:':'\uD83D\uDD06',
  ':broccoli:':'\uD83E\uDD66',':broken_heart:':'\uD83D\uDC94',':broom:':'\uD83E\uDDF9',
  ':brown_heart:':'\uD83E\uDD0E',':brunei:':'\uD83C\uDDE7\uD83C\uDDF3',':bubble_tea:':'\uD83E\uDDCB',
  ':bubbles:':'\uD83E\uDEE7',':bucket:':'\uD83E\uDDF3',':bug:':'\uD83D\uDC1B',
  ':building_construction:':'\uD83C\uDFD7\uFE0F',':bulb:':'\uD83D\uDCA1',':bulgaria:':'\uD83C\uDDE7\uD83C\uDDEC',
  ':bullettrain_front:':'\uD83D\uDE85',':bullettrain_side:':'\uD83D\uDE84',':burkina_faso:':'\uD83C\uDDE7\uD83C\uDDEB',
  ':burrito:':'\uD83C\uDF2F',':burundi:':'\uD83C\uDDE7\uD83C\uDDEE',':bus:':'\uD83D\uDE8C',
  ':busstop:':'\uD83D\uDE8F',':bust_in_silhouette:':'\uD83D\uDC64',':busts_silhouette:':'\uD83D\uDC65',
  ':butter:':'\uD83E\uDEC8',':butterfly:':'\uD83E\uDD8B',':cactus:':'\uD83C\uDF35',
  ':cake:':'\uD83C\uDF70',':calendar:':'\uD83D\uDCC6',':call_me:':'\uD83E\uDD19',
  ':calling:':'\uD83D\uDCF2',':cambodia:':'\uD83C\uDDF0\uD83C\uDDED',':camel:':'\uD83D\uDC2B',
  ':camera:':'\uD83D\uDCF7',':camera_flash:':'\uD83D\uDCF8',':cameroon:':'\uD83C\uDDE8\uD83C\uDDF2',
  ':camping:':'\uD83C\uDFD5\uFE0F',':canada:':'\uD83C\uDDE8\uD83C\uDDE6',':canary_islands:':'\uD83C\uDDEE\uD83C\uDDE8',
  ':cancer:':'\u264B\uFE0F',':candle:':'\uD83D\uDEEF\uFE0F',':candy:':'\uD83C\uDF6C',
  ':canned_food:':'\uD83E\uDD6B',':canoe:':'\uD83D\uDEF6',':cape_verde:':'\uD83C\uDDE8\uD83C\uDDFB',
  ':capricorn:':'\u2651\uFE0F',':car:':'\uD83D\uDE97',':card_file_box:':'\uD83D\uDDC3\uFE0F',
  ':card_index:':'\uD83D\uDCC7',':card_index_dividers:':'\uD83D\uDDC2\uFE0F',':caribbean_netherlands:':'\uD83C\uDDE7\uD83C\uDDF6',
  ':carousel_horse:':'\uD83C\uDFA0',':carpentry_saw:':'\uD83E\uDE9A',':carrot:':'\uD83E\uDD55',
  ':cat:':'\uD83D\uDC31',':cat2:':'\uD83D\uDC08',':cd:':'\uD83D\uDCBF',':cayman_islands:':'\uD83C\uDDF0\uD83C\uDDFE',
  ':central_african_republic:':'\uD83C\uDDE8\uD83C\uDDEB',':ceuta_melilla:':'\uD83C\uDDEA\uD83C\uDDE6',
  ':chad:':'\uD83C\uDDF9\uD83C\uDDE9',':chains:':'\u26D3\uFE0F',':chair:':'\uD83E\uDE91',
  ':champagne:':'\uD83C\uDF7E',':chart:':'\uD83D\uDCB9',':chart_with_downwards_trend:':'\uD83D\uDCC9',
  ':chart_with_upwards_trend:':'\uD83D\uDCC8',':checkered_flag:':'\uD83C\uDFC1',
  ':cheese:':'\uD83E\uDDC0',':cherries:':'\uD83C\uDF52',':cherry_blossom:':'\uD83C\uDF38',
  ':chess_pawn:':'\u265F\uFE0F',':chestnut:':'\uD83C\uDF30',':chicken:':'\uD83D\uDC14',
  ':child:':'\uD83E\uDDD2',':children_crossing:':'\uD83D\uDEB8',':chile:':'\uD83C\uDDE8\uD83C\uDDF1',
  ':china:':'\uD83C\uDDE8\uD83C\uDDF3',':chipmunk:':'\uD83D\uDC3F\uFE0F',':chocolate_bar:':'\uD83C\uDF6B',
  ':chopsticks:':'\uD83E\uDD62',':christmas_island:':'\uD83C\uDDE8\uD83C\uDDFD',
  ':christmas_tree:':'\uD83C\uDF84',':church:':'\u26EA',':cinema:':'\uD83C\uDFA6',
  ':circus_tent:':'\uD83C\uDFAA',':city_sunrise:':'\uD83C\uDF07',':city_sunset:':'\uD83C\uDF06',
  ':cityscape:':'\uD83C\uDFD9\uFE0F',':cl:':'\uD83C\uDD82',':clap:':'\uD83D\uDC4F',
  ':clapper:':'\uD83C\uDFAC',':classical_building:':'\uD83C\uDFDB\uFE0F',':climbing:':'\uD83E\uDDD7',
  ':clinking_glasses:':'\uD83E\uDD42',':clipboard:':'\uD83D\uDCCB',':clock1:':'\uD83D\uDD50',
  ':clock2:':'\uD83D\uDD51',':clock3:':'\uD83D\uDD52',':clock4:':'\uD83D\uDD53',
  ':clock5:':'\uD83D\uDD54',':clock6:':'\uD83D\uDD55',':clock7:':'\uD83D\uDD56',
  ':clock8:':'\uD83D\uDD57',':clock9:':'\uD83D\uDD58',':clock10:':'\uD83D\uDD59',
  ':clock11:':'\uD83D\uDD5A',':clock12:':'\uD83D\uDD5B',':closed_book:':'\uD83D\uDCD5',
  ':closed_lock_with_key:':'\uD83D\uDD10',':closed_umbrella:':'\uD83C\uDF02',':cloud:':'\u2601\uFE0F',
  ':clown_face:':'\uD83E\uDD21',':clubs:':'\u2663\uFE0F',':cn:':'\uD83C\uDDE8\uD83C\uDDF3',
  ':coat:':'\uD83E\uDDE5',':cockroach:':'\uD83E\uDEB3',':cocktail:':'\uD83C\uDF78',
  ':coconut:':'\uD83E\uDD65',':cocos_islands:':'\uD83C\uDDE8\uD83C\uDDE8',':coffee:':'\u2615',
  ':coffin:':'\u26B0\uFE0F',':coin:':'\uD83E\uDE99',':cold_face:':'\uD83E\uDD76',
  ':cold_sweat:':'\uD83D\uDE30',':colombia:':'\uD83C\uDDE8\uD83C\uDDF4',':comet:':'\u2604\uFE0F',
  ':comoros:':'\uD83C\uDDF0\uD83C\uDDF2',':compass:':'\uD83E\uDDED',':computer:':'\uD83D\uDCBB',
  ':computer_mouse:':'\uD83D\uDDB1\uFE0F',':confetti_ball:':'\uD83C\uDF8A',':confounded:':'\uD83D\uDE16',
  ':confused:':'\uD83D\uDE15',':congo_brazzaville:':'\uD83C\uDDE8\uD83C\uDDEC',
  ':congo_kinshasa:':'\uD83C\uDDE8\uD83C\uDDE9',':congratulations:':'\u3297\uFE0F',
  ':construction:':'\uD83D\uDEA7',':construction_worker:':'\uD83D\uDE77',':control_knobs:':'\uD83C\uDF9B\uFE0F',
  ':convenience_store:':'\uD83C\uDFEA',':cook:':'\uD83E\uDDD1\u200D\uD83C\uDF73',
  ':cook_islands:':'\uD83C\uDDE8\uD83C\uDDF0',':cookie:':'\uD83C\uDF6A',':cool:':'\uD83C\uDD92',
  ':coral:':'\uD83E\uDEB8',':corn:':'\uD83C\uDF3D',':costa_rica:':'\uD83C\uDDE8\uD83C\uDDF7',
  ':cote_divoire:':'\uD83C\uDDE8\uD83C\uDDEE',':couch_lamp:':'\uD83D\uDECB\uFE0F',':couple:':'\uD83D\uDC6B',
  ':couple_with_heart:':'\uD83D\uDC91',':couplekiss:':'\uD83D\uDC8F',':cow:':'\uD83D\uDC04',
  ':cow2:':'\uD83D\uDC2E',':cowboy:':'\uD83E\uDD20',':crab:':'\uD83E\uDD80',
  ':crayon:':'\uD83D\uDD8D\uFE0F',':credit_card:':'\uD83D\uDCB3',':crescent_moon:':'\uD83C\uDF19',
  ':cricket:':'\uD83E\uDD97',':cricket_game:':'\uD83C\uDFCF',':croatia:':'\uD83C\uDDED\uD83C\uDDF7',
  ':crocodile:':'\uD83D\uDC0A',':croissant:':'\uD83E\uDD50',':crossed_fingers:':'\uD83E\uDD1E',
  ':crossed_flags:':'\uD83C\uDF8C',':crossed_swords:':'\u2694\uFE0F',':crown:':'\uD83D\uDC51',
  ':cruise_ship:':'\uD83D\uDEF3',':cry:':'\uD83D\uDE22',':crying_cat_face:':'\uD83D\uDE3F',
  ':crystal_ball:':'\uD83D\uDD2E',':cuba:':'\uD83C\uDDE8\uD83C\uDDFA',':cucumber:':'\uD83E\uDD52',
  ':cup_with_straw:':'\uD83E\uDD64',':cupcake:':'\uD83E\uDDC1',':cupid:':'\uD83D\uDC98',
  ':curacao:':'\uD83C\uDDE8\uD83C\uDDFC',':curling_stone:':'\uD83E\uDD4C',':curly_haired:':'\uD83E\uDDD1\u200D\uD83E\uDDB1',
  ':currency_exchange:':'\uD83D\uDCB1',':curry:':'\uD83C\uDF5B',':custard:':'\uD83C\uDF6E',
  ':customs:':'\uD83D\uDEC3',':cut_of_meat:':'\uD83E\uDD69',':cycling:':'\uD83D\uDEB4',
  ':cyprus:':'\uD83C\uDDE8\uD83C\uDDFE',':czech_republic:':'\uD83C\uDDE8\uD83C\uDDFF',
  ':dagger:':'\uD83D\uDDE1\uFE0F',':dancer:':'\uD83D\uDC83',':dancers:':'\uD83D\uDC6F',
  ':dango:':'\uD83C\uDF61',':dark_sunglasses:':'\uD83D\uDD76\uFE0F',':dart:':'\uD83C\uDFAF',
  ':dash:':'\uD83D\uDCA8',':date:':'\uD83D\uDCC5',':de:':'\uD83C\uDDE9\uD83C\uDDEA',
  ':deaf_person:':'\uD83E\uDDCF',':deciduous_tree:':'\uD83C\uDF33',':deer:':'\uD83E\uDD8C',
  ':denmark:':'\uD83C\uDDE9\uD83C\uDDF0',':department_store:':'\uD83C\uDFEC',':dependabot:':'\uD83E\uDEB4',
  ':desert:':'\uD83C\uDFDC\uFE0F',':desert_island:':'\uD83C\uDFDD\uFE0F',':desktop:':'\uD83D\uDDA5\uFE0F',
  ':detective:':'\uD83D\uDD75\uFE0F',':diamond_shape:':'\uD83D\uDD34',':diamonds:':'\u2666\uFE0F',
  ':disappointed:':'\uD83D\uDE1E',':disappointed_relieved:':'\uD83D\uDE25',':disguised_face:':'\uD83E\uDD78',
  ':diving_mask:':'\uD83E\uDD3F',':diya_lamp:':'\uD83E\uDE94',':dizzy:':'\uD83D\uDCA8',
  ':dizzy_face:':'\uD83D\uDE35',':djibouti:':'\uD83C\uDDE9\uD83C\uDDEF',':dna:':'\uD83E\uDDEC',
  ':do_not_litter:':'\uD83D\uDEAF',':dodo:':'\uD83E\uDDA4',':dog:':'\uD83D\uDC36',
  ':dog2:':'\uD83D\uDC15',':dollar:':'\uD83D\uDCB5',':dolls:':'\uD83C\uDF8E',
  ':dolphin:':'\uD83D\uDC2C',':dominican_republic:':'\uD83C\uDDE9\uD83C\uDDF4',
  ':dominica:':'\uD83C\uDDE9\uD83C\uDDF2',':door:':'\uD83D\uDEAA',
  ':doughnut:':'\uD83C\uDF69',':dove:':'\uD83D\uDD4A\uFE0F',':dragon:':'\uD83D\uDC09',
  ':dragon_face:':'\uD83D\uDC32',':dress:':'\uD83D\uDC57',':dromedary_camel:':'\uD83D\uDC2A',
  ':drool:':'\uD83E\uDD24',':droplet:':'\uD83D\uDCA7',':drum:':'\uD83E\uDD41',
  ':duck:':'\uD83E\uDD86',':dumpling:':'\uD83E\uDD5F',':dvd:':'\uD83D\uDCC0',
  ':eagle:':'\uD83E\uDD85',':ear:':'\uD83D\uDC42',':ear_of_rice:':'\uD83C\uDF3E',
  ':earth_africa:':'\uD83C\uDF0D',':earth_americas:':'\uD83C\uDF0E',':earth_asia:':'\uD83C\uDF0F',
  ':ecuador:':'\uD83C\uDDEA\uD83C\uDDE8',':egg:':'\uD83E\uDD5A',':eggplant:':'\uD83C\uDF46',
  ':egypt:':'\uD83C\uDDEA\uD83C\uDDEC',':eight:':'\u0038\uFE0F\u20E3',':eject:':'\u23CF\uFE0F',
  ':el_salvador:':'\uD83C\uDDF8\uD83C\uDDFB',':electric_plug:':'\uD83D\uDD0C',':elephant:':'\uD83D\uDC18',
  ':elevator:':'\uD83D\uDED7',':elf:':'\uD83E\uDDDD',':email:':'\uD83D\uDCE7',
  ':end:':'\uD83D\uDD1A',':england:':'\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC65\uDB40\uDC6E\uDB40\uDC67\uDB40\uDC7F',
  ':equatorial_guinea:':'\uD83C\uDDEC\uD83C\uDDF6',':eritrea:':'\uD83C\uDDEA\uD83C\uDDF7',
  ':es:':'\uD83C\uDDEA\uD83C\uDDF8',':estonia:':'\uD83C\uDDEA\uD83C\uDDEA',
  ':eswatini:':'\uD83C\uDDF8\uD83C\uDDFF',':ethiopia:':'\uD83C\uDDEA\uD83C\uDDF9',
  ':eu:':'\uD83C\uDDEA\uD83C\uDDFA',':euro:':'\uD83D\uDCB6',':european_castle:':'\uD83C\uDFF0',
  ':european_post_office:':'\uD83C\uDFE4',':evergreen_tree:':'\uD83C\uDF32',':exclamation:':'\u2757',
  ':exploding_head:':'\uD83E\uDD2F',':expressionless:':'\uD83D\uDE11',':eye:':'\uD83D\uDC41\uFE0F',
  ':eyeglasses:':'\uD83D\uDC53',':eyes:':'\uD83D\uDC40',':face_vomiting:':'\uD83E\uDD2E',
  ':face_with_hand_over_mouth:':'\uD83E\uDD2D',':face_with_monocle:':'\uD83E\uDDD0',
  ':face_with_raised_eyebrow:':'\uD83E\uDD28',':face_with_symbols_over_mouth:':'\uD83E\uDD2C',
  ':factory:':'\uD83C\uDFED',':fairy:':'\uD83E\uDDDA',':falkland_islands:':'\uD83C\uDDEB\uD83C\uDDF0',
  ':fallen_leaf:':'\uD83C\uDF42',':family:':'\uD83D\uDC6A',':farmer:':'\uD83E\uDDD1\u200D\uD83C\uDF3E',
  ':faroe_islands:':'\uD83C\uDDEB\uD83C\uDDF4',':fast_forward:':'\u23E9',':fax:':'\uD83D\uDCE0',
  ':fearful:':'\uD83D\uDE28',':feather:':'\uD83E\uDEB6',':feet:':'\uD83D\uDC3E',
  ':female_sign:':'\u2640\uFE0F',':ferris_wheel:':'\uD83C\uDFA1',':ferry:':'\u26F4\uFE0F',
  ':field_hockey:':'\uD83C\uDFD1',':fiji:':'\uD83C\uDDEB\uD83C\uDDEF',':file_cabinet:':'\uD83D\uDDC4\uFE0F',
  ':file_folder:':'\uD83D\uDCC1',':film_frames:':'\uD83C\uDF9E\uFE0F',':film_projector:':'\uD83D\uDCFD\uFE0F',
  ':fingers_crossed:':'\uD83E\uDD1E',':finland:':'\uD83C\uDDEB\uD83C\uDDEE',':fire:':'\uD83D\uDD25',
  ':fire_engine:':'\uD83D\uDE92',':fire_extinguisher:':'\uD83E\uDDEF',':firecracker:':'\uD83E\uDEA8',
  ':firefighter:':'\uD83E\uDDD1\u200D\uD83D\uDE92',':fireworks:':'\uD83C\uDF86',':first_quarter_moon:':'\uD83C\uDF13',
  ':first_quarter_moon_with_face:':'\uD83C\uDF1B',':fish:':'\uD83D\uDC1F',':fish_cake:':'\uD83C\uDF65',
  ':fishing_pole_and_fish:':'\uD83C\uDFA3',':fist:':'\u270A',':five:':'\u0035\uFE0F\u20E3',
  ':flag_black:':'\uD83C\uDFF4',':flag_white:':'\uD83C\uDFF3\uFE0F',':flags:':'\uD83C\uDF8F',
  ':flamingo:':'\uD83E\uDDA2',':flashlight:':'\uD83D\uDD26',':flat_shoe:':'\uD83E\uDD7F',
  ':flatbread:':'\uD83E\uDD50',':fleur_de_lis:':'\u269C\uFE0F',':flexed_biceps:':'\uD83D\uDCAA',
  ':flight_arrival:':'\uD83D\uDEEC',':flight_departure:':'\uD83D\uDEEB',':flipper:':'\uD83D\uDC2C',
  ':floppy_disk:':'\uD83D\uDCBE',':flower_playing_cards:':'\uD83C\uDFB4',':flushed:':'\uD83D\uDE33',
  ':flute:':'\uD83E\uDE92',':fly:':'\uD83E\uDEB0',':flying_disc:':'\uD83E\uDDF8',
  ':flying_saucer:':'\uD83D\uDEF8',':fog:':'\uD83C\uDF2B\uFE0F',':foggy:':'\uD83C\uDF01',
  ':fondue:':'\uD83E\uDD63',':foot:':'\uD83E\uDDB6',':football:':'\uD83C\uDFC8',
  ':footprints:':'\uD83D\uDC63',':fork_and_knife:':'\uD83C\uDF74',':fortune_cookie:':'\uD83E\uDD60',
  ':fountain:':'\u26F2',':fountain_pen:':'\uD83D\uDD8B\uFE0F',':four:':'\u0034\uFE0F\u20E3',
  ':four_leaf_clover:':'\uD83C\uDF40',':fox:':'\uD83E\uDD8A',':fr:':'\uD83C\uDDEB\uD83C\uDDF7',
  ':framed_picture:':'\uD83D\uDDBC\uFE0F',':free:':'\uD83C\uDD93',':french_guiana:':'\uD83C\uDDEC\uD83C\uDDEB',
  ':french_polynesia:':'\uD83C\uDDF5\uD83C\uDDEB',':french_southern_territories:':'\uD83C\uDDF9\uD83C\uDDEB',
  ':fried_egg:':'\uD83C\uDF73',':fried_shrimp:':'\uD83C\uDF64',':fries:':'\uD83C\uDF5F',
  ':frog:':'\uD83D\uDC38',':frowning:':'\uD83D\uDE26',':frowning_face:':'\u2639\uFE0F',
  ':fuelpump:':'\u26FD',':full_moon:':'\uD83C\uDF15',':full_moon_with_face:':'\uD83C\uDF1D',
  ':funeral_urn:':'\u26B1\uFE0F',':gabon:':'\uD83C\uDDEC\uD83C\uDDE6',':gambia:':'\uD83C\uDDEC\uD83C\uDDF2',
  ':game_die:':'\uD83C\uDFB2',':garlic:':'\uD83E\uDDC4',':gb:':'\uD83C\uDDEC\uD83C\uDDE7',
  ':gear:':'\u2699\uFE0F',':gem:':'\uD83D\uDC8E',':gemini:':'\u264A\uFE0F',
  ':genie:':'\uD83E\uDDDE',':georgia:':'\uD83C\uDDEC\uD83C\uDDEA',':ghana:':'\uD83C\uDDEC\uD83C\uDDED',
  ':ghost:':'\uD83D\uDC7B',':gibraltar:':'\uD83C\uDDEC\uD83C\uDDEE',':gift:':'\uD83C\uDF81',
  ':gift_heart:':'\uD83D\uDC9D',':giraffe:':'\uD83E\uDD92',':girl:':'\uD83D\uDC67',
  ':globe_with_meridians:':'\uD83C\uDF10',':gloves:':'\uD83E\uDDE4',':goal:':'\uD83E\uDD45',
  ':goat:':'\uD83D\uDC10',':goggles:':'\uD83E\uDD7D',':golf:':'\u26F3',
  ':golfing:':'\uD83C\uDFCC\uFE0F',':goryeo_dynasty:':'\uD83C\uDFF0',':gorilla:':'\uD83E\uDD8D',
  ':grace:':'\uD83C\uDF3F',':grapes:':'\uD83C\uDF47',':greece:':'\uD83C\uDDEC\uD83C\uDDF7',
  ':green_apple:':'\uD83C\uDF4F',':green_book:':'\uD83D\uDCD7',':green_heart:':'\uD83D\uDC9A',
  ':greenland:':'\uD83C\uDDEC\uD83C\uDDF1',':grenada:':'\uD83C\uDDEC\uD83C\uDDE9',
  ':grey_exclamation:':'\u2755',':grey_question:':'\u2754',':grimacing:':'\uD83D\uDE2C',
  ':grin:':'\uD83D\uDE01',':grinning:':'\uD83D\uDE00',':guadeloupe:':'\uD83C\uDDEC\uD83C\uDDF5',
  ':guam:':'\uD83C\uDDEC\uD83C\uDDFA',':guard:':'\uD83D\uDC82',':guatemala:':'\uD83C\uDDEC\uD83C\uDDF9',
  ':guernsey:':'\uD83C\uDDEC\uD83C\uDDEC',':guinea:':'\uD83C\uDDEC\uD83C\uDDF3',
  ':guinea_bissau:':'\uD83C\uDDEC\uD83C\uDDFC',':guitar:':'\uD83C\uDFB8',':gun:':'\uD83D\uDD2B',
  ':guyana:':'\uD83C\uDDEC\uD83C\uDDFE',':haircut:':'\uD83D\uDC87',':haiti:':'\uD83C\uDDED\uD83C\uDDF9',
  ':hamburger:':'\uD83C\uDF54',':hammer:':'\uD83D\uDD28',':hammer_and_pick:':'\u2692\uFE0F',
  ':hammer_and_wrench:':'\uD83D\uDEE0\uFE0F',':hamster:':'\uD83D\uDC39',':hand_over_mouth:':'\uD83E\uDD2D',
  ':handbag:':'\uD83D\uDC5C',':handshake:':'\uD83E\uDD1D',':haiti:':'\uD83C\uDDED\uD83C\uDDF9',
  ':hash:':'\u0023\uFE0F\u20E3',':hatched_chick:':'\uD83D\uDC25',':hatching_chick:':'\uD83D\uDC23',
  ':head_bandage:':'\uD83E\uDD15',':headphones:':'\uD83C\uDFA7',':headstone:':'\uD83E\uDEA6',
  ':health_worker:':'\uD83E\uDDD1\u200D\u2695\uFE0F',':hear_no_evil:':'\uD83D\uDE49',
  ':heart:':'\u2764\uFE0F',':heart_decoration:':'\uD83D\uDC9F',':heart_eyes:':'\uD83D\uDC8D',
  ':heart_eyes_cat:':'\uD83D\uDE3B',':heart_on_fire:':'\u2764\uFE0F\u200D\uD83D\uDD25',
  ':heartbeat:':'\uD83D\uDC93',':heartpulse:':'\uD83D\uDC97',':hearts:':'\u2665\uFE0F',
  ':heavy_check_mark:':'\u2705',':heavy_division_sign:':'\u2797',':heavy_dollar_sign:':'\uD83D\uDCB2',
  ':heavy_minus_sign:':'\u2796',':heavy_plus_sign:':'\u2795',':hedgehog:':'\uD83E\uDD94',
  ':helicopter:':'\uD83D\uDE81',':herb:':'\uD83C\uDF3F',':hibiscus:':'\uD83C\uDF3A',
  ':high_brightness:':'\uD83D\uDD06',':high_heel:':'\uD83D\uDC60',':hiking_boot:':'\uD83E\uDD7E',
  ':hindu_temple:':'\uD83D\uDED5',':hippopotamus:':'\uD83E\uDD9B',':hocho:':'\uD83D\uDD2A',
  ':hockey:':'\uD83C\uDFD2',':hole:':'\uD83D\uDD73\uFE0F',':honduras:':'\uD83C\uDDED\uD83C\uDDF3',
  ':honey_pot:':'\uD83C\uDF6F',':honeybee:':'\uD83D\uDC1D',':hong_kong:':'\uD83C\uDDED\uD83C\uDDF0',
  ':hook:':'\uD83E\uDE9D',':horizontal_traffic_light:':'\uD83D\uDEA5',':horse:':'\uD83D\uDC34',
  ':horse_racing:':'\uD83C\uDFC7',':hospital:':'\uD83C\uDFE5',':hot_face:':'\uD83E\uDD75',
  ':hot_pepper:':'\uD83C\uDF36\uFE0F',':hotdog:':'\uD83C\uDF2D',':hotel:':'\uD83C\uDFE8',
  ':hotsprings:':'\u2668\uFE0F',':hourglass:':'\u231B',':hourglass_flowing_sand:':'\u23F3',
  ':house:':'\uD83C\uDFE0',':house_with_garden:':'\uD83C\uDFE1',':houses:':'\uD83C\uDFD8\uFE0F',
  ':hugging:':'\uD83E\uDD17',':hungary:':'\uD83C\uDDED\uD83C\uDDFA',':hushed:':'\uD83D\uDE2F',
  ':hut:':'\uD83C\uDFD6',':ice_cream:':'\uD83C\uDF68',':ice_cube:':'\uD83E\uDDCA',
  ':ice_hockey:':'\uD83C\uDFD2',':ice_skate:':'\u26F8\uFE0F',':iceland:':'\uD83C\uDDEE\uD83C\uDDF8',
  ':id:':'\uD83C\uDD94',':ideograph_advantage:':'\uD83C\uDD50',':imp:':'\uD83D\uDC7F',
  ':inbox_tray:':'\uD83D\uDCE5',':incoming_envelope:':'\uD83D\uDCE8',':india:':'\uD83C\uDDEE\uD83C\uDDF3',
  ':indonesia:':'\uD83C\uDDEE\uD83C\uDDE9',':infinity:':'\u267E\uFE0F',':information_desk_person:':'\uD83D\uDC81',
  ':information_source:':'\u2139\uFE0F',':innocent:':'\uD83D\uDE07',':interrobang:':'\u2049\uFE0F',
  ':iphone:':'\uD83D\uDCF1',':iran:':'\uD83C\uDDEE\uD83C\uDDF7',':iraq:':'\uD83C\uDDEE\uD83C\uDDF6',
  ':ireland:':'\uD83C\uDDEE\uD83C\uDDEA',':isle_of_man:':'\uD83C\uDDEE\uD83C\uDDF2',
  ':israel:':'\uD83C\uDDEE\uD83C\uDDF1',':it:':'\uD83C\uDDEE\uD83C\uDDF9',':izakaya_lantern:':'\uD83C\uDFEE',
  ':jack_o_lantern:':'\uD83C\uDF83',':jamaica:':'\uD83C\uDDEF\uD83C\uDDF2',':japan:':'\uD83D\uDDFE\uFE0F',
  ':japanese_castle:':'\uD83C\uDFEF',':japanese_goblin:':'\uD83D\uDC7A',':japanese_ogre:':'\uD83D\uDC79',
  ':jeans:':'\uD83D\uDC56',':jersey:':'\uD83C\uDDEF\uD83C\uDDEA',':jigsaw:':'\uD83E\uDDE9',
  ':jordan:':'\uD83C\uDDEF\uD83C\uDDF4',':joy:':'\uD83D\uDE02',':joy_cat:':'\uD83D\uDE39',
  ':joystick:':'\uD83D\uDD79\uFE0F',':jp:':'\uD83C\uDDEF\uD83C\uDDF5',':judge:':'\uD83E\uDDD1\u200D\u2696\uFE0F',
  ':juggling:':'\uD83E\uDD39',':juice_box:':'\uD83E\uDD64',':kaaba:':'\uD83D\uDD4B',
  ':kangaroo:':'\uD83E\uDD98',':kazakhstan:':'\uD83C\uDDF0\uD83C\uDDFF',':kenya:':'\uD83C\uDDF0\uD83C\uDDEA',
  ':key:':'\uD83D\uDD11',':keyboard:':'\u2328\uFE0F',':keycap_ten:':'\uD83D\uDD1F',
  ':kick_scooter:':'\uD83D\uDEF4',':kimono:':'\uD83D\uDC58',':kiribati:':'\uD83C\uDDF0\uD83C\uDDEE',
  ':kiss:':'\uD83D\uDC8B',':kissing:':'\uD83D\uDE17',':kissing_cat:':'\uD83D\uDE3D',
  ':kissing_closed_eyes:':'\uD83D\uDE1A',':kissing_heart:':'\uD83D\uDE18',':kissing_smiling_eyes:':'\uD83D\uDE19',
  ':kite:':'\uD83E\uDE81',':kiwi_fruit:':'\uD83E\uDD5D',':kneeling:':'\uD83E\uDDCE',
  ':knife:':'\uD83D\uDD2A',':knot:':'\uD83E\uDEA2',':koala:':'\uD83D\uDC28',
  ':koko:':'\uD83C\uDD95',':kosovo:':'\uD83C\uDDFD\uD83C\uDDF0',':kuwait:':'\uD83C\uDDF0\uD83C\uDDFC',
  ':kyrgyzstan:':'\uD83C\uDDF0\uD83C\uDDEC',':lab_coat:':'\uD83E\uDD7C',':label:':'\uD83C\uDFF7\uFE0F',
  ':lacrosse:':'\uD83E\uDD4D',':ladder:':'\uD83E\uDE9C',':ladybug:':'\uD83D\uDC1E',
  ':laos:':'\uD83C\uDDF1\uD83C\uDDE6',':laptop:':'\uD83D\uDCBB',':large_blue_circle:':'\uD83D\uDD35',
  ':large_blue_diamond:':'\uD83D\uDD37',':large_orange_diamond:':'\uD83D\uDD36',
  ':last_quarter_moon:':'\uD83C\uDF17',':last_quarter_moon_with_face:':'\uD83C\uDF1C',
  ':latin_cross:':'\u271D\uFE0F',':latvia:':'\uD83C\uDDF1\uD83C\uDDFB',':laughing:':'\uD83D\uDE06',
  ':laurel:':'\uD83E\uDED5',':lavatory:':'\uD83D\uDEBD',':law:':'\u2696\uFE0F',
  ':leafy_green:':'\uD83E\uDD6C',':leather:':'\uD83E\uDEBA',':lebanon:':'\uD83C\uDDF1\uD83C\uDDE7',
  ':ledger:':'\uD83D\uDCD2',':left_fist:':'\uD83E\uDD1B',':left_luggage:':'\uD83D\uDEC5',
  ':left_right_arrow:':'\u2194\uFE0F',':left_speech_bubble:':'\uD83D\uDDE8\uFE0F',
  ':leg:':'\uD83E\uDDB5',':lemon:':'\uD83C\uDF4B',':leo:':'\u264C\uFE0F',
  ':leopard:':'\uD83D\uDC06',':lesotho:':'\uD83C\uDDF1\uD83C\uDDF8',':level_slider:':'\uD83C\uDF9A\uFE0F',
  ':liberia:':'\uD83C\uDDF1\uD83C\uDDF7',':libra:':'\u264E\uFE0F',':libya:':'\uD83C\uDDF1\uD83C\uDDFE',
  ':liechtenstein:':'\uD83C\uDDF1\uD83C\uDDEE',':light_rail:':'\uD83D\uDE88',':link:':'\uD83D\uDD17',
  ':lion_face:':'\uD83E\uDD81',':lips:':'\uD83D\uDC44',':lipstick:':'\uD83D\uDC84',
  ':lithuania:':'\uD83C\uDDF1\uD83C\uDDF9',':lizard:':'\uD83E\uDD8E',':llama:':'\uD83E\uDD99',
  ':lobster:':'\uD83E\uDD9E',':lock:':'\uD83D\uDD12',':lock_with_ink_pen:':'\uD83D\uDD0F',
  ':lollipop:':'\uD83C\uDF6D',':long_drum:':'\uD83E\uDE98',':loop:':'\u27BF',
  ':lotion_bottle:':'\uD83E\uDDF4',':lotus_position:':'\uD83E\uDDD8',':loud_sound:':'\uD83D\uDD0A',
  ':loudspeaker:':'\uD83D\uDCE2',':love_hotel:':'\uD83C\uDFE9',':love_letter:':'\uD83D\uDC8C',
  ':low_brightness:':'\uD83D\uDD05',':luggage:':'\uD83E\uDEF3',':luxembourg:':'\uD83C\uDDF1\uD83C\uDDFA',
  ':lying_face:':'\uD83E\uDD25',':macau:':'\uD83C\uDDF2\uD83C\uDDF4',':macedonia:':'\uD83C\uDDF2\uD83C\uDDF0',
  ':madagascar:':'\uD83C\uDDF2\uD83C\uDDEC',':mag:':'\uD83D\uDD0D',':mag_right:':'\uD83D\uDD0E',
  ':mage:':'\uD83E\uDDD9',':magic_wand:':'\uD83E\uDE84',':magnet:':'\uD83E\uDEA2',
  ':malawi:':'\uD83C\uDDF2\uD83C\uDDFC',':malaysia:':'\uD83C\uDDF2\uD83C\uDDFE',
  ':maldives:':'\uD83C\uDDF2\uD83C\uDDFB',':mali:':'\uD83C\uDDF2\uD83C\uDDF1',':malta:':'\uD83C\uDDF2\uD83C\uDDF9',
  ':man:':'\uD83D\uDC68',':man_in_business_suit:':'\uD83D\uDC68\u200D\uD83D\uDCBC',
  ':man_in_tuxedo:':'\uD83E\uDD35',':mandarin:':'\uD83C\uDF4A',':mango:':'\uD83E\uDD6D',
  ':mans_shoe:':'\uD83D\uDC5E',':mantelpiece_clock:':'\uD83D\uDD70\uFE0F',':manual_wheelchair:':'\uD83E\uDDBC',
  ':maple_leaf:':'\uD83C\uDF41',':marathon:':'\uD83C\uDFC3',':marble:':'\uD83E\uDEA7',
  ':marshall_islands:':'\uD83C\uDDF2\uD83C\uDDED',':martinique:':'\uD83C\uDDF2\uD83C\uDDF6',
  ':mask:':'\uD83D\uDE37',':massage:':'\uD83D\uDC86',':mauritania:':'\uD83C\uDDF2\uD83C\uDDF7',
  ':mauritius:':'\uD83C\uDDF2\uD83C\uDDFA',':mayotte:':'\uD83C\uDDF2\uD83C\uDDF9',
  ':meat_on_bone:':'\uD83C\uDF56',':mechanic:':'\uD83E\uDDD1\u200D\uD83D\uDD27',
  ':mechanical_arm:':'\uD83E\uDDEE',':mechanical_leg:':'\uD83E\uDDBF',':medal:':'\uD83C\uDF96\uFE0F',
  ':medical_symbol:':'\u2695\uFE0F',':mega:':'\uD83D\uDCE3',':melon:':'\uD83C\uDF48',
  ':memo:':'\uD83D\uDCDD',':men_wrestling:':'\uD83E\uDD3C',':mending_heart:':'\u2764\uFE0F\u200D\uD83E\uDE79',
  ':menorah:':'\uD83D\uDD4E',':mens:':'\uD83D\uDEB9',':mermaid:':'\uD83E\uDDDC\u200D\u2640\uFE0F',
  ':merman:':'\uD83E\uDDDC\u200D\u2642\uFE0F',':merperson:':'\uD83E\uDDDC',':metal:':'\uD83E\uDD18',
  ':metro:':'\uD83D\uDE87',':mexico:':'\uD83C\uDDF2\uD83C\uDDFD',':micronesia:':'\uD83C\uDDEB\uD83C\uDDF2',
  ':microphone:':'\uD83C\uDFA4',':microscope:':'\uD83D\uDD2C',':middle_finger:':'\uD83D\uDD95',
  ':military_helmet:':'\uD83E\uDE96',':milk:':'\uD83E\uDD5B',':milky_way:':'\uD83C\uDF0C',
  ':minibus:':'\uD83D\uDE90',':mini_disc:':'\uD83D\uDCBD',':minus:':'\u2796',
  ':mirror:':'\uD83E\uDE9E',':moai:':'\uD83D\uDDFF',':moldova:':'\uD83C\uDDF2\uD83C\uDDE9',
  ':monaco:':'\uD83C\uDDF2\uD83C\uDDE8',':mongolia:':'\uD83C\uDDF2\uD83C\uDDF3',
  ':monkey:':'\uD83D\uDC35',':monkey_face:':'\uD83D\uDC12',':monocle:':'\uD83E\uDDD0',
  ':monorail:':'\uD83D\uDE9D',':montenegro:':'\uD83C\uDDF2\uD83C\uDDEA',':montserrat:':'\uD83C\uDDF2\uD83C\uDDF8',
  ':moon:':'\uD83C\uDF14',':moon_cake:':'\uD83E\uDD6E',':morocco:':'\uD83C\uDDF2\uD83C\uDDE6',
  ':mortar_board:':'\uD83C\uDF93',':mosque:':'\uD83D\uDD4C',':mosquito:':'\uD83E\uDE9F',
  ':motor_boat:':'\uD83D\uDEE5\uFE0F',':motor_scooter:':'\uD83D\uDEF5',':motorcycle:':'\uD83C\uDFCD\uFE0F',
  ':motorized_wheelchair:':'\uD83E\uDDBD',':motorway:':'\uD83D\uDEE3\uFE0F',':mount_fuji:':'\uD83D\uDDFB',
  ':mountain:':'\u26F0\uFE0F',':mountain_bicyclist:':'\uD83D\uDEB5',':mountain_cableway:':'\uD83D\uDEA0',
  ':mountain_railway:':'\uD83D\uDE9E',':mountain_snow:':'\uD83C\uDFD4\uFE0F',':mouse:':'\uD83D\uDC2D',
  ':mouse2:':'\uD83D\uDC01',':mouse_trap:':'\uD83E\uDEA4',':mouth:':'\uD83D\uDC44',
  ':movie_camera:':'\uD83C\uDFA5',':mozambique:':'\uD83C\uDDF2\uD83C\uDDFF',
  ':myanmar:':'\uD83C\uDDF2\uD83C\uDDF2',':nail_care:':'\uD83D\uDC85',':name_badge:':'\uD83D\uDCDB',
  ':namibia:':'\uD83C\uDDF3\uD83C\uDDE6',':nauru:':'\uD83C\uDDF3\uD83C\uDDF7',':navy:':'\uD83D\uDEA2',
  ':nepal:':'\uD83C\uDDF3\uD83C\uDDF5',':netherlands:':'\uD83C\uDDF3\uD83C\uDDF1',
  ':neutral_face:':'\uD83D\uDE10',':new:':'\uD83C\uDD95',':new_caledonia:':'\uD83C\uDDF3\uD83C\uDDE8',
  ':new_moon:':'\uD83C\uDF11',':new_moon_with_face:':'\uD83C\uDF1A',':new_zealand:':'\uD83C\uDDF3\uD83C\uDDFF',
  ':newspaper:':'\uD83D\uDCF0',':next_track:':'\u23ED\uFE0F',':nicaragua:':'\uD83C\uDDF3\uD83C\uDDEE',
  ':niger:':'\uD83C\uDDF3\uD83C\uDDEA',':nigeria:':'\uD83C\uDDF3\uD83C\uDDEC',':night_with_stars:':'\uD83C\uDF03',
  ':nine:':'\u0039\uFE0F\u20E3',':niue:':'\uD83C\uDDF3\uD83C\uDDFA',':no_bell:':'\uD83D\uDD15',
  ':no_bicycles:':'\uD83D\uDEB3',':no_entry:':'\u26D4',':no_entry_sign:':'\uD83D\uDEAB',
  ':no_good:':'\uD83D\uDE45',':no_mobile_phones:':'\uD83D\uDCF5',':no_mouth:':'\uD83D\uDE36',
  ':no_pedestrians:':'\uD83D\uDEB7',':no_smoking:':'\uD83D\uDEAD',':non-potable_water:':'\uD83D\uDEB1',
  ':norfolk_island:':'\uD83C\uDDF3\uD83C\uDDEB',':north_korea:':'\uD83C\uDDF0\uD83C\uDDF5',
  ':northern_mariana_islands:':'\uD83C\uDDF2\uD83C\uDDF5',':norway:':'\uD83C\uDDF3\uD83C\uDDF4',
  ':nose:':'\uD83D\uDC43',':notebook:':'\uD83D\uDCD3',':notebook_with_decorative_cover:':'\uD83D\uDCD4',
  ':notes:':'\uD83C\uDFB6',':nut_and_bolt:':'\uD83D\uDD29',':o:':'\u2B55',
  ':o2:':'\uD83C\uDD7E\uFE0F',':ocean:':'\uD83C\uDF0A',':octopus:':'\uD83D\uDC19',
  ':oden:':'\uD83C\uDF62',':office:':'\uD83C\uDFE2',':oil_drum:':'\uD83D\uDEE2\uFE0F',
  ':ok:':'\uD83C\uDD96',':ok_hand:':'\uD83D\uDC4C',':ok_woman:':'\uD83D\uDE46',
  ':older_adult:':'\uD83E\uDDD3',':older_man:':'\uD83D\uDC74',':older_woman:':'\uD83D\uDC75',
  ':om:':'\uD83D\uDD49\uFE0F',':on:':'\uD83D\uDD1B',':oncoming_automobile:':'\uD83D\uDE98',
  ':oncoming_bus:':'\uD83D\uDE8D',':oncoming_police_car:':'\uD83D\uDE94',':oncoming_taxi:':'\uD83D\uDE96',
  ':one:':'\u0031\uFE0F\u20E3',':one_piece:':'\uD83E\uDEE6',':onion:':'\uD83E\uDDC5',
  ':open_book:':'\uD83D\uDCD6',':open_file_folder:':'\uD83D\uDCC2',':open_hands:':'\uD83D\uDC50',
  ':open_mouth:':'\uD83D\uDE2E',':ophiuchus:':'\u26CE',':orange_book:':'\uD83D\uDCD9',
  ':orange_heart:':'\uD83E\uDDE1',':orangutan:':'\uD83E\uDDA7',':orthodox_cross:':'\u2626\uFE0F',
  ':otter:':'\uD83E\uDDA6',':outbox_tray:':'\uD83D\uDCE4',':owl:':'\uD83E\uDD89',
  ':ox:':'\uD83D\uDC02',':oyster:':'\uD83E\uDDAA',':package:':'\uD83D\uDCE6',
  ':page_facing_up:':'\uD83D\uDCC4',':page_with_curl:':'\uD83D\uDCC3',':pager:':'\uD83D\uDCDF',
  ':paintbrush:':'\uD83D\uDD8C\uFE0F',':pakistan:':'\uD83C\uDDF5\uD83C\uDDF0',':palau:':'\uD83C\uDDF5\uD83C\uDDFC',
  ':palestinian_territories:':'\uD83C\uDDF5\uD83C\uDDF8',':palm_tree:':'\uD83C\uDF34',
  ':palms_up_together:':'\uD83E\uDD32',':panama:':'\uD83C\uDDF5\uD83C\uDDE6',
  ':pancakes:':'\uD83E\uDD5E',':panda_face:':'\uD83D\uDC3C',':paperclip:':'\uD83D\uDCCE',
  ':papua_new_guinea:':'\uD83C\uDDF5\uD83C\uDDEC',':parachute:':'\uD83E\uDE82',
  ':paraguay:':'\uD83C\uDDF5\uD83C\uDDFE',':parasol_on_ground:':'\u26F1\uFE0F',':parking:':'\uD83C\uDD7F\uFE0F',
  ':parrot:':'\uD83E\uDD9C',':part_alternation_mark:':'\u303D\uFE0F',':partly_sunny:':'\u26C5',
  ':partying_face:':'\uD83E\uDD73',':passenger_ship:':'\uD83D\uDEF3\uFE0F',':passport_control:':'\uD83D\uDEC2',
  ':pasta:':'\uD83C\uDF5D',':pastry:':'\uD83E\uDD50',':patio:':'\uD83E\uDEB9',
  ':pause_button:':'\u23F8\uFE0F',':paw_prints:':'\uD83D\uDC3E',':peace:':'\u262E\uFE0F',
  ':peach:':'\uD83C\uDF51',':peacock:':'\uD83E\uDD9A',':peanuts:':'\uD83E\uDD5C',
  ':pear:':'\uD83C\uDF50',':pen:':'\uD83D\uDD8A\uFE0F',':pencil:':'\uD83D\uDCDD',
  ':penguin:':'\uD83D\uDC27',':pensive:':'\uD83D\uDE14',':people_holding_hands:':'\uD83E\uDDD1\u200D\uD83E\uDDD1',
  ':people_with_bunny_ears:':'\uD83D\uDC6F',':performing_arts:':'\uD83C\uDFAD',':persevere:':'\uD83D\uDE23',
  ':person:':'\uD83E\uDDD1',':person_biking:':'\uD83D\uDEB4',':person_bouncing_ball:':'\u26F9\uFE0F',
  ':person_bowing:':'\uD83D\uDE47',':person_climbing:':'\uD83E\uDDD7',':person_doing_cartwheel:':'\uD83E\uDD38',
  ':person_frowning:':'\uD83D\uDE4D',':person_golfing:':'\uD83C\uDFCC\uFE0F',':person_in_lotus_position:':'\uD83E\uDDD8',
  ':person_in_steamy_room:':'\uD83E\uDDD6',':person_juggling:':'\uD83E\uDD39',
  ':person_kneeling:':'\uD83E\uDDCE',':person_mountain_biking:':'\uD83D\uDEB5',
  ':person_playing_handball:':'\uD83E\uDD3E',':person_playing_water_polo:':'\uD83E\uDD3D',
  ':person_pouting:':'\uD83D\uDE4E',':person_raising_hand:':'\uD83D\uDE4B',':person_rowing:':'\uD83D\uDEA3',
  ':person_running:':'\uD83C\uDFC3',':person_surfing:':'\uD83C\uDFC4',':person_swimming:':'\uD83C\uDFCA',
  ':person_taking_bath:':'\uD83D\uDEC0',':person_tipping_hand:':'\uD83D\uDC81',
  ':person_walking:':'\uD83D\uDEB6',':person_wearing_turban:':'\uD83D\uDC73',
  ':person_with_blond_hair:':'\uD83D\uDC71',':person_with_headscarf:':'\uD83E\uDDD5',
  ':person_with_probing_cane:':'\uD83E\uDDD1\u200D\uD83E\uDDAF',':person_with_skullcap:':'\uD83D\uDC72',
  ':person_with_turban:':'\uD83D\uDC73',':person_with_veil:':'\uD83D\uDC70',
  ':peru:':'\uD83C\uDDF5\uD83C\uDDEA',':petri_dish:':'\uD83E\uDDEB',':philippines:':'\uD83C\uDDF5\uD83C\uDDED',
  ':phone:':'\u260E\uFE0F',':pick:':'\u26CF\uFE0F',':pickup_truck:':'\uD83D\uDEFB',
  ':pie:':'\uD83E\uDD67',':pig:':'\uD83D\uDC37',':pig2:':'\uD83D\uDC16',
  ':pig_nose:':'\uD83D\uDC3D',':pill:':'\uD83D\uDC8A',':pilot:':'\uD83E\uDDD1\u200D\u2708\uFE0F',
  ':pinata:':'\uD83E\uDE85',':pinched_fingers:':'\uD83E\uDD0C',':pinching_hand:':'\uD83E\uDD0F',
  ':pineapple:':'\uD83C\uDF4D',':ping_pong:':'\uD83C\uDFD3',':pirate_flag:':'\uD83C\uDFF4\u200D\u2620\uFE0F',
  ':pisces:':'\u2653\uFE0F',':pizza:':'\uD83C\uDF55',':placard:':'\uD83E\uDEA7',
  ':place_of_worship:':'\uD83D\uDED0',':plate_with_cutlery:':'\uD83C\uDF7D\uFE0F',
  ':play_button:':'\u25B6\uFE0F',':play_or_pause_button:':'\u23EF\uFE0F',
  ':pleading_face:':'\uD83E\uDD7A',':plunger:':'\uD83E\uDEA0',':plus:':'\u2795',
  ':point_down:':'\uD83D\uDC47',':point_left:':'\uD83D\uDC48',':point_right:':'\uD83D\uDC49',
  ':point_up:':'\u261D\uFE0F',':point_up_2:':'\uD83D\uDC46',':poland:':'\uD83C\uDDF5\uD83C\uDDF1',
  ':polar_bear:':'\uD83E\uDD9F',':police_car:':'\uD83D\uDE93',':police_officer:':'\uD83D\uDC6E',
  ':poodle:':'\uD83D\uDC29',':pool_8_ball:':'\uD83C\uDFB1',':poop:':'\uD83D\uDCA9',
  ':popcorn:':'\uD83C\uDF7F',':portugal:':'\uD83C\uDDF5\uD83C\uDDF9',':post_office:':'\uD83C\uDFE3',
  ':postal_horn:':'\uD83D\uDCEF',':postbox:':'\uD83D\uDCEE',':potable_water:':'\uD83D\uDEB0',
  ':potato:':'\uD83E\uDD54',':potted_plant:':'\uD83E\uDED4',':pouch:':'\uD83D\uDC5D',
  ':poultry_leg:':'\uD83C\uDF57',':pound:':'\uD83D\uDCB7',':pouting_cat:':'\uD83D\uDE3E',
  ':pray:':'\uD83D\uDE4F',':prayer_beads:':'\uD83D\uDCFF',':pregnant_woman:':'\uD83E\uDD30',
  ':pretzel:':'\uD83E\uDD68',':previous_track:':'\u23EE\uFE0F',':prince:':'\uD83E\uDD34',
  ':princess:':'\uD83D\uDC78',':printer:':'\uD83D\uDDA8\uFE0F',':probing_cane:':'\uD83E\uDDAF',
  ':puerto_rico:':'\uD83C\uDDF5\uD83C\uDDF7',':punch:':'\uD83D\uDC4A',':purple_heart:':'\uD83D\uDC9C',
  ':purse:':'\uD83D\uDC5B',':pushpin:':'\uD83D\uDCCC',':put_litter_in_its_place:':'\uD83D\uDEAE',
  ':qatar:':'\uD83C\uDDF6\uD83C\uDDE6',':question:':'\u2753',':rabbit:':'\uD83D\uDC30',
  ':rabbit2:':'\uD83D\uDC07',':raccoon:':'\uD83E\uDD9D',':racehorse:':'\uD83D\uDC0E',
  ':racing_car:':'\uD83C\uDFCE\uFE0F',':racing_motorcycle:':'\uD83C\uDFCD\uFE0F',
  ':radio:':'\uD83D\uDCFB',':radio_button:':'\uD83D\uDD18',':radioactive:':'\u2622\uFE0F',
  ':rage:':'\uD83D\uDE21',':railway_car:':'\uD83D\uDE83',':railway_track:':'\uD83D\uDEE4\uFE0F',
  ':rainbow:':'\uD83C\uDF08',':rainbow_flag:':'\uD83C\uDFF3\uFE0F\u200D\uD83C\uDF08',
  ':raised_back_of_hand:':'\uD83E\uDD1A',':raised_eyebrow:':'\uD83E\uDD28',
  ':raised_hand:':'\u270B',':raised_hand_with_fingers_splayed:':'\uD83D\uDD90\uFE0F',
  ':raised_hands:':'\uD83D\uDE4C',':raising_hand:':'\uD83D\uDE4B',':ram:':'\uD83D\uDC0F',
  ':ramen:':'\uD83C\uDF5C',':rat:':'\uD83D\uDC00',':razor:':'\uD83E\uDE92',
  ':receipt:':'\uD83E\uDDFE',':record_button:':'\u23FA\uFE0F',':recycle:':'\u267B\uFE0F',
  ':red_car:':'\uD83D\uDE97',':red_circle:':'\uD83D\uDD34',':red_envelope:':'\uD83E\uDDE7',
  ':red_haired:':'\uD83E\uDDD0',':red_square:':'\uD83D\uDFE5',':regional_indicator_a:':'\uD83C\uDDE6',
  ':regional_indicator_b:':'\uD83C\uDDE7',':registered:':'\u00AE\uFE0F',':relaxed:':'\u263A\uFE0F',
  ':relieved:':'\uD83D\uDE0C',':reminder_ribbon:':'\uD83C\uDF97\uFE0F',':repeat:':'\uD83D\uDD01',
  ':repeat_one:':'\uD83D\uDD02',':restroom:':'\uD83D\uDEBB',':reunion:':'\uD83C\uDDF7\uD83C\uDDEA',
  ':revolving_hearts:':'\uD83D\uDC9E',':rewind:':'\u23EA',':rhinoceros:':'\uD83E\uDD8F',
  ':ribbon:':'\uD83C\uDF80',':rice:':'\uD83C\uDF5A',':rice_ball:':'\uD83C\uDF59',
  ':rice_cracker:':'\uD83C\uDF58',':rice_scene:':'\uD83C\uDF91',':right_fist:':'\uD83E\uDD1C',
  ':right_anger_bubble:':'\uD83D\uDDEF\uFE0F',':ring:':'\uD83D\uDC8D',':ring_buoy:':'\uD83D\uDEFD',
  ':ringed_planet:':'\uD83E\uDE90',':robot:':'\uD83E\uDD16',':rock:':'\uD83E\uDEA8',
  ':rocket:':'\uD83D\uDE80',':roll_of_paper:':'\uD83E\uDDFB',':rolled_up_newspaper:':'\uD83D\uDDDE\uFE0F',
  ':roller_coaster:':'\uD83C\uDFA2',':roller_skate:':'\uD83E\uDDFC',':rolling_eyes:':'\uD83D\uDE44',
  ':romania:':'\uD83C\uDDF7\uD83C\uDDF4',':rooster:':'\uD83D\uDC13',':rose:':'\uD83C\uDF39',
  ':rosette:':'\uD83C\uDFF5\uFE0F',':rotating_light:':'\uD83D\uDEA8',':round_pushpin:':'\uD83D\uDCCD',
  ':rowboat:':'\uD83D\uDEA3',':ru:':'\uD83C\uDDF7\uD83C\uDDFA',':rugby_football:':'\uD83C\uDFC9',
  ':runner:':'\uD83C\uDFC3',':running_shirt_with_sash:':'\uD83C\uDFBD',':rwanda:':'\uD83C\uDDF7\uD83C\uDDFC',
  ':sa:':'\uD83C\uDDF8\uD83C\uDDE6',':sad_cry:':'\uD83D\uDE22',':sagittarius:':'\u2650\uFE0F',
  ':sailboat:':'\u26F5',':sake:':'\uD83C\uDF76',':salad:':'\uD83E\uDD57',
  ':salmon:':'\uD83E\uDD9F',':salt:':'\uD83E\uDDC2',':saluting_face:':'\uD83E\uDD1B',
  ':samoa:':'\uD83C\uDDFC\uD83C\uDDF8',':san_marino:':'\uD83C\uDDF8\uD83C\uDDF2',
  ':sandal:':'\uD83D\uDC61',':sandwich:':'\uD83E\uDD6A',':santa:':'\uD83C\uDF85',
  ':sao_tome_principe:':'\uD83C\uDDF8\uD83C\uDDF9',':sari:':'\uD83E\uDD7B',':satellite:':'\uD83D\uDCE1',
  ':sauropod:':'\uD83E\uDD95',':saxophone:':'\uD83C\uDFB7',':scales:':'\u2696\uFE0F',
  ':scarf:':'\uD83E\uDDE3',':school:':'\uD83C\uDFEB',':school_satchel:':'\uD83C\uDF92',
  ':scissors:':'\u2702\uFE0F',':scooter:':'\uD83D\uDEF4',':scorpio:':'\u264F\uFE0F',
  ':scorpion:':'\uD83E\uDD82',':scotland:':'\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC73\uDB40\uDC63\uDB40\uDC74\uDB40\uDC7F',
  ':scream:':'\uD83D\uDE31',':scream_cat:':'\uD83D\uDE40',':screwdriver:':'\uD83E\uDE9B',
  ':scroll:':'\uD83D\uDCDC',':seal:':'\uD83E\uDDAD',':seat:':'\uD83D\uDCBA',
  ':secret:':'\u3299\uFE0F',':see_no_evil:':'\uD83D\uDE48',':seedling:':'\uD83C\uDF31',
  ':selfie:':'\uD83E\uDD33',':senegal:':'\uD83C\uDDF8\uD83C\uDDF3',':serbia:':'\uD83C\uDDF7\uD83C\uDDF8',
  ':service_dog:':'\uD83D\uDC15\u200D\uD83E\uDDBA',':seven:':'\u0037\uFE0F\u20E3',
  ':seychelles:':'\uD83C\uDDF8\uD83C\uDDE8',':shallow_pan_of_food:':'\uD83E\uDD58',
  ':shamrock:':'\u2618\uFE0F',':shark:':'\uD83E\uDD88',':shaved_ice:':'\uD83C\uDF67',
  ':sheep:':'\uD83D\uDC11',':shell:':'\uD83D\uDC1A',':shield:':'\uD83D\uDEE1\uFE0F',
  ':shinto_shrine:':'\u26E9\uFE0F',':ship:':'\uD83D\uDEA2',':shirt:':'\uD83D\uDC55',
  ':shit:':'\uD83D\uDCA9',':shoe:':'\uD83D\uDC5E',':shopping_bags:':'\uD83D\uDECD\uFE0F',
  ':shopping_cart:':'\uD83D\uDED2',':shortcake:':'\uD83C\uDF70',':shorts:':'\uD83E\uDE73',
  ':shower:':'\uD83D\uDEBF',':shrimp:':'\uD83E\uDD90',':shuffle:':'\uD83D\uDD00',
  ':shushing_face:':'\uD83E\uDD2B',':sierra_leone:':'\uD83C\uDDF8\uD83C\uDDF1',
  ':sign_language:':'\uD83E\uDD32',':sign_of_the_horns:':'\uD83E\uDD18',':singapore:':'\uD83C\uDDF8\uD83C\uDDEC',
  ':sint_maarten:':'\uD83C\uDDF8\uD83C\uDDFD',':sister:':'\uD83D\uDC6D',':six:':'\u0036\uFE0F\u20E3',
  ':six_pointed_star:':'\uD83D\uDD2F',':skateboard:':'\uD83D\uDEF9',':skeleton:':'\uD83D\uDC80',
  ':ski:':'\uD83C\uDFBF',':skier:':'\u26F7\uFE0F',':skull:':'\uD83D\uDC80',
  ':skull_and_crossbones:':'\u2620\uFE0F',':skunk:':'\uD83E\uDDA8',':sled:':'\uD83D\uDEF7',
  ':sleeping:':'\uD83D\uDE34',':sleeping_accommodation:':'\uD83D\uDECC',':sleepy:':'\uD83D\uDE2A',
  ':slight_frown:':'\uD83D\uDE41',':slight_smile:':'\uD83D\uDE42',':slightly_frowning_face:':'\uD83D\uDE41',
  ':slot_machine:':'\uD83C\uDFB0',':sloth:':'\uD83E\uDDAD',':slovakia:':'\uD83C\uDDF8\uD83C\uDDF0',
  ':slovenia:':'\uD83C\uDDF8\uD83C\uDDEE',':small_airplane:':'\uD83D\uDEE9\uFE0F',
  ':small_blue_diamond:':'\uD83D\uDD39',':small_orange_diamond:':'\uD83D\uDD38',
  ':small_red_triangle:':'\uD83D\uDD3A',':small_red_triangle_down:':'\uD83D\uDD3B',
  ':smile:':'\uD83D\uDE04',':smile_cat:':'\uD83D\uDE38',':smiley:':'\uD83D\uDE03',
  ':smiley_cat:':'\uD83D\uDE3A',':smiling_face_with_tear:':'\uD83E\uDD72',
  ':smiling_imp:':'\uD83D\uDE08',':smirk:':'\uD83D\uDE0F',':smirk_cat:':'\uD83D\uDE3C',
  ':smoking:':'\uD83D\uDEAC',':snail:':'\uD83D\uDC0C',':snake:':'\uD83D\uDC0D',
  ':sneeze:':'\uD83E\uDD27',':snowboarder:':'\uD83C\uDFC2',':snowflake:':'\u2744\uFE0F',
  ':snowman:':'\u2603\uFE0F',':snowman_with_snow:':'\u2603\uFE0F',':soap:':'\uD83E\uDDFC',
  ':sob:':'\uD83D\uDE2D',':soccer:':'\u26BD',':socks:':'\uD83E\uDDE6',':softball:':'\uD83E\uDD4E',
  ':solomon_islands:':'\uD83C\uDDF8\uD83C\uDDE7',':somalia:':'\uD83C\uDDF8\uD83C\uDDF4',
  ':soon:':'\uD83D\uDD1C',':sos:':'\uD83C\uDD98',':sound:':'\uD83D\uDD09',
  ':south_africa:':'\uD83C\uDDFF\uD83C\uDDE6',':south_georgia:':'\uD83C\uDDEC\uD83C\uDDE8',
  ':south_sudan:':'\uD83C\uDDF8\uD83C\uDDEA',':space_invader:':'\uD83D\uDC7E',':spade:':'\u2660\uFE0F',
  ':spaghetti:':'\uD83C\uDF5D',':sparkle:':'\u2747\uFE0F',':sparkler:':'\uD83C\uDF87',
  ':sparkles:':'\u2728',':sparkling_heart:':'\uD83D\uDC96',':speak_no_evil:':'\uD83D\uDE4A',
  ':speaker:':'\uD83D\uDD08',':speaking_head:':'\uD83D\uDDE3\uFE0F',':speech_balloon:':'\uD83D\uDCAC',
  ':speedboat:':'\uD83D\uDEA4',':spider:':'\uD83D\uDD77\uFE0F',':spider_web:':'\uD83D\uDD78\uFE0F',
  ':spiral_calendar:':'\uD83D\uDDD3\uFE0F',':spiral_notepad:':'\uD83D\uDDD2\uFE0F',
  ':sponge:':'\uD83E\uDDFD',':spoon:':'\uD83E\uDD44',':sport_utility_vehicle:':'\uD83D\uDE99',
  ':sports_medal:':'\uD83C\uDFC5',':spouting_whale:':'\uD83D\uDC33',':squid:':'\uD83E\uDD91',
  ':sri_lanka:':'\uD83C\uDDF1\uD83C\uDDF0',':st_barthelemy:':'\uD83C\uDDE7\uD83C\uDDF1',
  ':st_helena:':'\uD83C\uDDF8\uD83C\uDDE8',':st_kitts_nevis:':'\uD83C\uDDF0\uD83C\uDDF3',
  ':st_lucia:':'\uD83C\uDDF1\uD83C\uDDE8',':st_martin:':'\uD83C\uDDF2\uD83C\uDDEB',
  ':st_pierre_miquelon:':'\uD83C\uDDF5\uD83C\uDDF2',':st_vincent_grenadines:':'\uD83C\uDDFB\uD83C\uDDE8',
  ':stadium:':'\uD83C\uDFDF\uFE0F',':standing:':'\uD83E\uDDCD',':star:':'\u2B50',
  ':star2:':'\uD83C\uDF1F',':star_and_crescent:':'\u262A\uFE0F',':star_struck:':'\uD83E\uDD29',
  ':starfish:':'\uD83E\uDDA3',':stars:':'\uD83C\uDF20',':station:':'\uD83D\uDE89',
  ':statue_of_liberty:':'\uD83D\uDDFD',':steam_locomotive:':'\uD83D\uDE82',':stethoscope:':'\uD83E\uDE7A',
  ':stew:':'\uD83C\uDF72',':stop_button:':'\u23F9\uFE0F',':stop_sign:':'\uD83D\uDED1',
  ':stopwatch:':'\u23F1\uFE0F',':straight_ruler:':'\uD83D\uDCCF',':strawberry:':'\uD83C\uDF53',
  ':stuck_out_tongue:':'\uD83D\uDE1B',':stuck_out_tongue_closed_eyes:':'\uD83D\uDE1D',
  ':stuck_out_tongue_winking_eye:':'\uD83D\uDE1C',':sudan:':'\uD83C\uDDF8\uD83C\uDDE9',
  ':sun_with_face:':'\uD83C\uDF1E',':sunflower:':'\uD83C\uDF3B',':sunglasses:':'\uD83D\uDE0E',
  ':sunny:':'\u2600\uFE0F',':sunrise:':'\uD83C\uDF05',':sunrise_over_mountains:':'\uD83C\uDF04',
  ':surfer:':'\uD83C\uDFC4',':suriname:':'\uD83C\uDDF8\uD83C\uDDF7',':sushi:':'\uD83C\uDF63',
  ':suspension_railway:':'\uD83D\uDE9F',':svalbard:':'\uD83C\uDDF8\uD83C\uDDEF',
  ':swan:':'\uD83E\uDDA2',':swaziland:':'\uD83C\uDDF8\uD83C\uDDFF',':sweat:':'\uD83D\uDE13',
  ':sweat_drops:':'\uD83D\uDCA6',':sweat_smile:':'\uD83D\uDE05',':sweden:':'\uD83C\uDDF8\uD83C\uDDEA',
  ':sweet_potato:':'\uD83C\uDF60',':swim:':'\uD83C\uDFCA',':switzerland:':'\uD83C\uDDE8\uD83C\uDDED',
  ':symbol_over_sound:':'\uD83D\uDD07',':synagogue:':'\uD83D\uDD4D',':syria:':'\uD83C\uDDF8\uD83C\uDDFE',
  ':syringe:':'\uD83D\uDC89',':t_rex:':'\uD83E\uDD96',':table_tennis:':'\uD83C\uDFD3',
  ':taco:':'\uD83C\uDF2E',':taiwan:':'\uD83C\uDDF9\uD83C\uDDFC',':tajikistan:':'\uD83C\uDDF9\uD83C\uDDEF',
  ':takeout_box:':'\uD83E\uDD61',':tamale:':'\uD83E\uDD61',':tanzania:':'\uD83C\uDDF9\uD83C\uDDFF',
  ':taurus:':'\u2649\uFE0F',':taxi:':'\uD83D\uDE95',':tea:':'\uD83C\uDF75',
  ':teacup_without_handle:':'\uD83C\uDF75',':teapot:':'\uD83E\uDD76',':tear:':'\uD83D\uDE22',
  ':teeth:':'\uD83E\uDDB7',':telephone:':'\u260E\uFE0F',':telescope:':'\uD83D\uDD2D',
  ':ten:':'\uD83D\uDD1F',':tennis:':'\uD83C\uDFBE',':tent:':'\u26FA',':test_tube:':'\uD83E\uDDEA',
  ':thailand:':'\uD83C\uDDF9\uD83C\uDDED',':thermometer:':'\uD83C\uDF21\uFE0F',':thinking:':'\uD83E\uDD14',
  ':third_place_medal:':'\uD83C\uDF97\uFE0F',':thong_sandal:':'\uD83E\uDE74',':thought_balloon:':'\uD83D\uDCAD',
  ':thread:':'\uD83E\uDDF5',':three:':'\u0033\uFE0F\u20E3',':thumbsdown:':'\uD83D\uDC4E',
  ':thumbsup:':'\uD83D\uDC4D',':ticket:':'\uD83C\uDFAB',':tiger:':'\uD83D\uDC2F',
  ':tiger2:':'\uD83D\uDC05',':timer:':'\u23F2\uFE0F',':timor_leste:':'\uD83C\uDDF9\uD83C\uDDF1',
  ':tired_face:':'\uD83D\uDE2B',':tm:':'\u2122\uFE0F',':togo:':'\uD83C\uDDF9\uD83C\uDDEC',
  ':toilet:':'\uD83D\uDEBD',':tokelau:':'\uD83C\uDDF9\uD83C\uDDF0',':tokyo_tower:':'\uD83D\uDDFC',
  ':tomato:':'\uD83C\uDF45',':tonga:':'\uD83C\uDDF9\uD83C\uDDF4',':tongue:':'\uD83D\uDC45',
  ':toolbox:':'\uD83E\uDDF0',':tooth:':'\uD83E\uDDB7',':toothbrush:':'\uD83E\uDEA5',
  ':top:':'\uD83D\uDD1D',':tophat:':'\uD83C\uDFA9',':tornado:':'\uD83C\uDF2A\uFE0F',
  ':tr:':'\uD83C\uDDF9\uD83C\uDDF7',':trackball:':'\uD83D\uDDB2\uFE0F',':tractor:':'\uD83D\uDE9C',
  ':traffic_light:':'\uD83D\uDEA5',':train:':'\uD83D\uDE8B',':train2:':'\uD83D\uDE86',
  ':tram:':'\uD83D\uDE8A',':transgender_flag:':'\uD83C\uDFF3\uFE0F\u200D\u26A7\uFE0F',
  ':transgender_symbol:':'\u26A7\uFE0F',':trash:':'\uD83D\uDDD1\uFE0F',':tree:':'\uD83C\uDF33',
  ':trinidad_tobago:':'\uD83C\uDDF9\uD83C\uDDF9',':triumph:':'\uD83D\uDE24',':troll:':'\uD83E\uDDCC',
  ':trolleybus:':'\uD83D\uDE8E',':trophy:':'\uD83C\uDFC6',':tropical_drink:':'\uD83C\uDF79',
  ':tropical_fish:':'\uD83D\uDC20',':truck:':'\uD83D\uDE9A',':trumpet:':'\uD83C\uDFBA',
  ':tr:':'\uD83C\uDDF9\uD83C\uDDF7',':tshirt:':'\uD83D\uDC55',':tulip:':'\uD83C\uDF37',
  ':tumbler_glass:':'\uD83E\uDD43',':tunisia:':'\uD83C\uDDF9\uD83C\uDDF3',':turkey:':'\uD83E\uDD83',
  ':turkmenistan:':'\uD83C\uDDF9\uD83C\uDDF2',':turks_caicos:':'\uD83C\uDDF9\uD83C\uDDE8',
  ':turtle:':'\uD83D\uDC22',':tuvalu:':'\uD83C\uDDF9\uD83C\uDDFB',':tv:':'\uD83D\uDCFA',
  ':twisted_rightwards_arrows:':'\uD83D\uDD00',':two:':'\u0032\uFE0F\u20E3',
  ':two_hearts:':'\uD83D\uDC95',':two_men_holding_hands:':'\uD83D\uDC6C',
  ':two_women_holding_hands:':'\uD83D\uDC6D',':u6708:':'\uD83C\uDD37\uFE0F',':u6709:':'\uD83C\uDD36\uFE0F',
  ':u7121:':'\uD83C\uDD1A\uFE0F',':u7533:':'\uD83C\uDD38\uFE0F',':u7981:':'\uD83C\uDD32\uFE0F',
  ':u7a7a:':'\uD83C\uDD33\uFE0F',':uganda:':'\uD83C\uDDFA\uD83C\uDDEC',':uk:':'\uD83C\uDDEC\uD83C\uDDE7',
  ':ukraine:':'\uD83C\uDDFA\uD83C\uDDE6',':umbrella:':'\u2602\uFE0F',':umbrella_on_ground:':'\u26F1\uFE0F',
  ':unamused:':'\uD83D\uDE12',':underage:':'\uD83D\uDD1E',':unicorn:':'\uD83E\uDD84',
  ':united_arab_emirates:':'\uD83C\uDDE6\uD83C\uDDEA',':united_nations:':'\uD83C\uDDFA\uD83C\uDDF3',
  ':unlock:':'\uD83D\uDD13',':up:':'\uD83C\uDD97',':upside_down:':'\uD83D\uDE43',
  ':uruguay:':'\uD83C\uDDFA\uD83C\uDDFE',':us:':'\uD83C\uDDFA\uD83C\uDDF8',
  ':us_virgin_islands:':'\uD83C\uDDFB\uD83C\uDDEE',':uzbekistan:':'\uD83C\uDDFA\uD83C\uDDFF',
  ':v:':'\u270C\uFE0F',':vampire:':'\uD83E\uDDDB',':vanuatu:':'\uD83C\uDDFB\uD83C\uDDEA',
  ':vatican_city:':'\uD83C\uDDFB\uD83C\uDDE6',':venezuela:':'\uD83C\uDDFB\uD83C\uDDEA',
  ':vertical_traffic_light:':'\uD83D\uDEA6',':vhs:':'\uD83D\uDCFC',':vibration_mode:':'\uD83D\uDCF3',
  ':video_camera:':'\uD83D\uDCF9',':video_game:':'\uD83C\uDFAE',':vietnam:':'\uD83C\uDDFB\uD83C\uDDF3',
  ':violin:':'\uD83C\uDFBB',':virgo:':'\u264D\uFE0F',':volcano:':'\uD83C\uDF0B',
  ':volleyball:':'\uD83C\uDFD0',':vomiting:':'\uD83E\uDD2E',':vs:':'\uD83C\uDD9A',
  ':vulcan:':'\uD83D\uDD96',':waffle:':'\uD83E\uDDC7',':wales:':'\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC77\uDB40\uDC6C\uDB40\uDC73\uDB40\uDC7F',
  ':walking:':'\uD83D\uDEB6',':wallis_futuna:':'\uD83C\uDDFC\uD83C\uDDEB',
  ':waning_crescent_moon:':'\uD83C\uDF18',':waning_gibbous_moon:':'\uD83C\uDF16',
  ':warning:':'\u26A0\uFE0F',':wastebasket:':'\uD83D\uDDD1\uFE0F',':watch:':'\u231A',
  ':water_buffalo:':'\uD83D\uDC03',':water_polo:':'\uD83E\uDD3D',':watermelon:':'\uD83C\uDF49',
  ':wave:':'\uD83D\uDC4B',':waving_black_flag:':'\uD83C\uDFF4',':waving_white_flag:':'\uD83C\uDFF3\uFE0F',
  ':wavy_dash:':'\u3030\uFE0F',':waxing_crescent_moon:':'\uD83C\uDF12',
  ':waxing_gibbous_moon:':'\uD83C\uDF14',':wc:':'\uD83D\uDEBE',':weary:':'\uD83D\uDE29',
  ':wedding:':'\uD83D\uDC92',':weight_lifting:':'\uD83C\uDFCB\uFE0F',':western_sahara:':'\uD83C\uDDEA\uD83C\uDDED',
  ':whale:':'\uD83D\uDC33',':whale2:':'\uD83D\uDC0B',':wheel_of_dharma:':'\u2638\uFE0F',
  ':wheelchair:':'\u267F',':white_circle:':'\u26AA',':white_flag:':'\uD83C\uDFF3\uFE0F',
  ':white_flower:':'\uD83D\uDCAE',':white_heart:':'\uD83E\uDD0D',':white_square:':'\u2B1C',
  ':wilted_flower:':'\uD83E\uDD40',':wind_chime:':'\uD83C\uDF90',':wind_face:':'\uD83C\uDF2C\uFE0F',
  ':wine_glass:':'\uD83C\uDF77',':wink:':'\uD83D\uDE09',':wolf:':'\uD83D\uDC3A',
  ':woman:':'\uD83D\uDC69',':woman_with_headscarf:':'\uD83E\uDDD5',':womans_hat:':'\uD83D\uDC52',
  ':womens:':'\uD83D\uDEBA',':wood:':'\uD83E\uDEB5',':woozy_face:':'\uD83E\uDD74',
  ':world_map:':'\uD83D\uDDFA\uFE0F',':worm:':'\uD83E\uDEB1',':worried:':'\uD83D\uDE1F',
  ':wrench:':'\uD83D\uDD27',':wrestling:':'\uD83E\uDD3C',':writing_hand:':'\u270D\uFE0F',
  ':x:':'\u274C',':yarn:':'\uD83E\uDDF6',':yawning_face:':'\uD83E\uDD71',
  ':yellow_heart:':'\uD83D\uDC9B',':yemen:':'\uD83C\uDDFE\uD83C\uDDEA',':yen:':'\uD83D\uDCB4',
  ':yin_yang:':'\u262F\uFE0F',':yoyo:':'\uD83E\uDE80',':yum:':'\uD83D\uDE0B',
  ':zambia:':'\uD83C\uDDFF\uD83C\uDDF2',':zany_face:':'\uD83E\uDD2A',':zap:':'\u26A1',
  ':zebra:':'\uD83E\uDD93',':zero:':'\u0030\uFE0F\u20E3',':zimbabwe:':'\uD83C\uDDFF\uD83C\uDDFC',
  ':zipper_mouth:':'\uD83E\uDD10',':zombie:':'\uD83E\uDDDF',':zzz:':'\uD83D\uDCA4',
};

function _replaceShortcodes(text) {
  // Replace :shortcode: patterns with unicode emoji
  return text.replace(/:([a-z0-9_+\-]+):/g, (match, name) => {
    return _E[match] || match;
  });
}
// Replace colorful system/Twemoji emoji with single-color line icons tinted to
// the surrounding text color (project rule: never colorful emoji). Operates on
// rendered HTML: only touches text outside tags and skips <code>/<pre>.
const _EMOJI_RE = /\p{Extended_Pictographic}/u;
const _emojiSeg = (typeof Intl !== 'undefined' && Intl.Segmenter)
  ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;

function _emojiCodepoints(emoji) {
  // Twemoji filename rule: strip U+FE0F unless the sequence has a ZWJ (U+200D).
  const s = emoji.indexOf('‍') >= 0 ? emoji : emoji.replace(/️/g, '');
  const cps = [];
  for (const ch of s) { const c = ch.codePointAt(0); if (c) cps.push(c.toString(16)); }
  return cps.join('-');
}
function _emojiImg(emoji) {
  const code = _emojiCodepoints(emoji);
  if (!code) return emoji;
  // Monochrome line icon: the OpenMoji black SVG is used as a CSS mask filled
  // with the surrounding text color (currentColor), so emoji render as a single
  // theme-tinted line glyph — never colorful (project rule). If the proxy can't
  // supply the glyph it returns a transparent SVG, so the mask shows nothing.
  return `<span class="emoji" role="img" aria-label="${emoji}" style="--em:url('/api/emoji/${code}.svg')"></span>`;
}
function _svgifyText(text) {
  if (!_emojiSeg) return text;
  let out = '';
  for (const { segment } of _emojiSeg.segment(text)) {
    out += _EMOJI_RE.test(segment) ? _emojiImg(segment) : segment;
  }
  return out;
}
/** When "Text-only Emojis" is on, keep Unicode in HTML so deEmojify() can strip them. */
function _useSvgEmoji() {
  return typeof document === 'undefined' || !document.body?.classList.contains('text-emojis');
}

export function svgifyEmoji(html) {
  if (!_useSvgEmoji() || !html || !_EMOJI_RE.test(html)) return html;
  const parts = html.split(/(<[^>]*>)/);   // odd indices = tags
  let codeDepth = 0;
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const t = parts[i].toLowerCase();
      if (/^<(pre|code)[\s>]/.test(t)) codeDepth++;
      else if (/^<\/(pre|code)\s*>/.test(t)) codeDepth = Math.max(0, codeDepth - 1);
      continue;
    }
    if (codeDepth === 0 && _EMOJI_RE.test(parts[i])) parts[i] = _svgifyText(parts[i]);
  }
  return parts.join('');
}
/**
 * Generic collapsible section that reuses the thinking-dropdown styling and its
 * delegated toggle (any `.thinking-header[data-thinking-id]`). The label drives
 * the "View <label>" / "Hide <label>" text via data-label. Used e.g. for the
 * vision-model image description on a user's photo message.
 */
export function createCollapsible(contentMarkdown, label = 'details') {
  const id = `collapse-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  const safeLabel = escapeHtml(label);
  return `
    <div class="thinking-section">
      <div class="thinking-header" data-thinking-id="${id}">
        <div class="thinking-header-left"><span data-label="${safeLabel}">View ${safeLabel}</span></div>
        <div style="display:flex;align-items:center;gap:6px;"><span class="thinking-toggle" id="${id}-toggle"></span></div>
      </div>
      <div class="thinking-content" id="${id}"><div class="thinking-content-inner">${mdToHtml(contentMarkdown)}</div></div>
    </div>`;
}

export function processWithThinking(text) {
  const { thinkingBlocks, content, thinkingTime } = extractThinkingBlocks(text);

  let html = '';

  // Add thinking sections (collapsed by default)
  thinkingBlocks.forEach((block, index) => {
    html += createThinkingSection(block, index, thinkingTime);
  });

  // Add the actual content
  if (content) {
    html += mdToHtml(content);
  }

  return _useSvgEmoji() ? svgifyEmoji(html) : html;
}

/**
 * Convert markdown to HTML
 */
export function mdToHtml(src) {
  // CRITICAL: Extract allowed HTML blocks first (details/summary)
  const allowedHtmlBlocks = [];
  let s = (src ?? '');
  s = _replaceShortcodes(s);

  // Repair common ways the agent mangles the entity-anchor convention
  // (`[Name](#kind-<id>)`). Models reliably get the single-link case
  // right but slip into other formats when listing many in a table.
  // These regexes upgrade the broken forms to proper markdown links so
  // the standard `[text](url)` handler below picks them up.
  const ANCHOR_KIND = '(?:session|document|note|image|email|event|task|skill|research)';
  // Case A: `[Name] [#kind-id]` — agent put the URL in brackets, often
  // in a table cell next to the label. Pair them.
  s = s.replace(
    new RegExp(`\\[([^\\]\\n]+?)\\]\\s*\\[#(${ANCHOR_KIND}-[A-Za-z0-9_-]+)\\]`, 'g'),
    '[$1](#$2)',
  );
  // Case B: bare `[#kind-id]` with no preceding label — give it a
  // generic "→ open" link text so it still renders as a button.
  s = s.replace(
    new RegExp(`\\[#(${ANCHOR_KIND}-[A-Za-z0-9_-]+)\\]`, 'g'),
    '[→ open](#$1)',
  );
  // Case C: bare `#kind-id` in plain text — only when it's word-
  // boundary delimited and NOT already inside a markdown link or
  // anchor syntax. Use a lookbehind for `](` or `[` to skip those.
  s = s.replace(
    new RegExp(`(^|[^\\[(])#(${ANCHOR_KIND}-[A-Za-z0-9_-]+)\\b`, 'g'),
    '$1[#$2](#$2)',
  );

  // Convert markdown links [text](url) to clickable links
  // Internal #hash links navigate in-page; external links open in new tab
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
    return linkHtml(text, url);
  });

  // Autolink bare URLs (http/https). Skips URLs already inside <a> tags
  // (placed by markdown link replacement above) and URLs in backticks.
  s = s.replace(
    /(^|[\s(<])(https?:\/\/[^\s<>"'`\]]+[^\s<>"'`\].,;:!?])/g,
    (match, prefix, url) => `${prefix}${linkHtml(url, url)}`
  );

  // Autolink scheme-less domains the model often emits as plain text
  // (e.g. "techcrunch.com/ai", "perplexity.ai", "www.wired.com"). The TLD
  // allowlist keeps it from matching file names / versions ("package.json",
  // "node.js", "v1.2.3"); the required start/[\s(<] prefix means domains
  // already inside an http link (preceded by "//") or an email ("@") are
  // skipped. Trailing sentence punctuation is kept outside the link.
  s = s.replace(
    /(^|[\s(<])((?:www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|ai|co|dev|app|gov|edu|news|info|tech|xyz|me)(?:\/[^\s<>"'`\])]*)?)/gi,
    (match, prefix, domain) => {
      const trail = (domain.match(/[.,;:!?)]+$/) || [''])[0];
      const core = trail ? domain.slice(0, -trail.length) : domain;
      return `${prefix}${linkHtml(core, 'https://' + core)}${trail}`;
    }
  );

  // Extract <details>...</details> blocks and replace with placeholders
  // Default to open so agent output is visible
  s = s.replace(/<details>([\s\S]*?)<\/details>/gi, (match) => {
    const placeholder = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(match.replace(/<details>/i, '<details open>'));
    return placeholder;
  });

  // ALSO preserve <a> tags the same way (they're now in the HTML from markdown conversion)
  s = s.replace(/<a\s+[^>]*>.*?<\/a>/gi, (match) => {
    const placeholder = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(match);
    return placeholder;
  });

  // Now escape everything else
  s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  s = s.replace(/\n{3,}/g, '\n\n');

  // CRITICAL: Extract code blocks and replace with placeholders
  const codeBlocks = [];
  const mermaidBlocks = [];
  s = s.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const cleaned = code
      .replace(/\r\n/g, '\n')
      .replace(/[ \t]+$/gm, '')
      .replace(/^\s*\n+/, '')
      .replace(/\n+\s*$/g, '');

    // Mermaid diagrams: render as diagram instead of code block
    if (lang && lang.toLowerCase() === 'mermaid') {
      const mermaidId = 'mermaid-' + Date.now() + '-' + mermaidBlocks.length;
      const raw = cleaned.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
      const placeholder = `___MERMAID_BLOCK_${mermaidBlocks.length}___`;
      mermaidBlocks.push(`<div class="mermaid-container"><pre class="mermaid" id="${mermaidId}">${escapeHtml(raw)}</pre></div>`);
      return placeholder;
    }

    const escaped = cleaned.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    const placeholder = `___CODE_BLOCK_${codeBlocks.length}___`;

    const langClass = lang ? ` class="language-${lang}"` : '';
    const runnableLangs = ['python','py','javascript','js','html','bash','sh','shell','zsh'];
    const runBtn = (lang && runnableLangs.includes(lang.toLowerCase()))
      ? `<button type="button" class="run-code" data-code="${escapeHtml(escaped)}" data-lang="${lang}" title="Run code"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>`
      : '';
    const editBtn = `<button type="button" class="edit-code" title="Edit"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>`;
    codeBlocks.push(`<pre><code${langClass} data-lang="${lang || ''}">${escapeHtml(escaped)}</code>${runBtn}${editBtn}<button type="button" class="copy-code" data-code="${escapeHtml(escaped)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></pre>`);

    return placeholder;
  });

  // KaTeX math rendering (after code blocks are extracted, so math in code is safe)
  const mathBlocks = [];
  if (window.katex) {
    // Display math: \[ ... \]  — GPT-style delimiter (gpt-5.x, Claude, etc.).
    // Handle before $$/$ so all common delimiters render.
    s = s.replace(/\\\[([\s\S]*?)\\\]/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: true, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
    // Inline math: \( ... \)  — GPT-style inline delimiter. Single-line only
    // ([^\n]) so a stray escaped paren in prose can't swallow across lines.
    s = s.replace(/\\\(([^\n]*?)\\\)/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: false, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
    // Display math: $$...$$
    s = s.replace(/\$\$([\s\S]*?)\$\$/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: true, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
    // Inline math: $...$  (not preceded/followed by $ or digit, not spanning multiple lines)
    s = s.replace(/(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: false, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
  }

  // Handle pipe tables
  s = s.replace(/(?:^|\n)([^\n]*\|[^\n]*\|[^\n]*)(?:\n([^\n]*\|[^\n]*\|[^\n]*))*/g, (table) => {
    if (table.includes('___CODE_BLOCK_') || table.includes('___ALLOWED_HTML_')) return table;

    const rows = table.trim().split('\n');
    if (rows.length < 2) return table;

    let html = '<table style="border-collapse: collapse; width: 100%; margin: 10px 0;">';

    rows.forEach((row, idx) => {
      const cells = row.split('|').filter(cell => cell.trim() !== '');
      if (cells.length === 0) return;

      html += idx === 1 ? '<tbody>' : '';
      html += '<tr>';

      cells.forEach(cell => {
        const tag = idx === 0 ? 'th' : 'td';
        const style = idx === 1 ? 'style="border-top: 2px solid var(--red);"' : '';
        html += `<${tag} ${style} style="padding: 8px; text-align: left; border-bottom: 1px solid var(--border);">${cell.trim()}</${tag}>`;
      });

      html += '</tr>';
    });

    html += '</tbody></table>';
    return html;
  });

  // Inline code (but not placeholders)
  s = s.replace(/`([^`]+?)`/g, (match, code) => {
    if (code.startsWith('___CODE_BLOCK_') || code.startsWith('___ALLOWED_HTML_')) return match;
    return `<code>${code}</code>`;
  });

  // Horizontal rules (must come before bold/italic to avoid * conflicts)
  s = s.replace(/^(?:---|\*\*\*|___)\s*$/gm, '<hr>');

  // Bold, italic, strikethrough
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  // Headers
  s = s.replace(/^###### (.*)$/gm, '<h6>$1</h6>')
       .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
       .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
       .replace(/^### (.*)$/gm, '<h3>$1</h3>')
       .replace(/^## (.*)$/gm, '<h2>$1</h2>')
       .replace(/^# (.*)$/gm, '<h1>$1</h1>');

  // Ordered lists (1. 2. 3. etc.)
  s = s.replace(/^(\d+)\. (.*)$/gm, '<oli>$2</oli>');
  s = s.replace(/(?:^|\n)(<oli>[\s\S]*?)(?=\n(?!<oli>)|$)/g, m => `<ol>${m.trim().replace(/<\/?oli>/g, (t) => t === '<oli>' ? '<li>' : '</li>')}</ol>`);

  // Unordered lists
  s = s.replace(/^(?:- |\* )(.*)$/gm, '<li>$1</li>');
  s = s.replace(/(?:^|\n)(<li>[\s\S]*?)(?=\n(?!<li>)|$)/g, m => `<ul>${m.trim()}</ul>`);

  // Blockquotes
  s = s.replace(/^&gt; (.*)$/gm, '<bq>$1</bq>');
  s = s.replace(/(?:^|\n)(<bq>[\s\S]*?)(?=\n(?!<bq>)|$)/g, m =>
    `<blockquote>${m.trim().replace(/<\/?bq>/g, (t) => t === '<bq>' ? '<p>' : '</p>')}</blockquote>`);

  // Paragraphs - but NOT for code block placeholders or allowed HTML
  s = s.replace(/^(?!<h\d|<ul>|<ol>|<li>|<oli>|<pre>|<blockquote>|<bq>|<hr>|___CODE_BLOCK_|___ALLOWED_HTML_|___MATH_BLOCK_|___MERMAID_BLOCK_)([^\n]+)$/gm, '<p>$1</p>');

  // Line breaks within paragraphs
  s = s.replace(/<p>([\s\S]*?)<\/p>/g, (match, content) => {
    if (content.includes('___CODE_BLOCK_') || content.includes('___ALLOWED_HTML_') || content.includes('___MATH_BLOCK_') || content.includes('___MERMAID_BLOCK_')) return match;
    const withLineBreaks = content.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
    return `<p>${withLineBreaks}</p>`;
  });

  // Remove empty paragraphs
  s = s.replace(/<p><\/p>/g, '');

  // CRITICAL: Restore allowed HTML blocks first
  allowedHtmlBlocks.forEach((block, index) => {
    s = s.replace(`___ALLOWED_HTML_${index}___`, block);
  });

  // Restore math blocks
  mathBlocks.forEach((block, index) => {
    s = s.replace(`___MATH_BLOCK_${index}___`, block);
  });

  // Restore mermaid diagram blocks
  mermaidBlocks.forEach((block, index) => {
    s = s.replace(`___MERMAID_BLOCK_${index}___`, block);
  });

  // CRITICAL: Restore code blocks at the end
  codeBlocks.forEach((block, index) => {
    s = s.replace(`___CODE_BLOCK_${index}___`, block);
  });

  return _useSvgEmoji() ? svgifyEmoji(s) : s;
}

/**
 * Reduce excessive whitespace outside of code blocks
 */
export function squashOutsideCode(s) {
  if (!s) return "";
  const parts = String(s).split(/```/);
  for (let i = 0; i < parts.length; i += 2) {
    parts[i] = parts[i]
      .replace(/\r\n/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n');
  }
  return parts.join('```');
}

/**
 * Render content that may be text or array of content blocks
 */
export function renderContent(content) {
  if (Array.isArray(content)) {
    const texts = [];
    for (const blk of content) {
      if (blk.type === 'text') texts.push(blk.text);
      else if (blk.type === 'image_url') texts.push('[image]');
    }
    return texts.join('\n');
  }
  return content;
}

/**
 * Initialize any unprocessed Mermaid diagrams in a container (or whole document)
 */
export function renderMermaid(container) {
  if (!window.mermaid) return;
  initMermaid();
  const target = container || document;
  const pending = target.querySelectorAll('pre.mermaid:not([data-processed])');
  if (pending.length === 0) return;
  try {
    window.mermaid.run({ nodes: pending });
  } catch (e) {
    console.warn('Mermaid render error:', e);
  }
}

const markdownModule = {
  escapeHtml,
  mdToHtml,
  squashOutsideCode,
  renderContent,
  processWithThinking,
  createCollapsible,
  hasUnclosedThinkTag,
  extractThinkingBlocks,
  startsWithReasoningPrefix,
  renderMermaid
};

export default markdownModule;

// Mermaid is loaded async so it cannot delay the app shell.
function initMermaid() {
  if (!window.mermaid || window.__odysseusMermaidReady) return;
  window.mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
  window.__odysseusMermaidReady = true;
}
window.odysseusInitMermaid = initMermaid;
initMermaid();

// Persist which thinking sections were expanded across page refreshes.
// IDs are render-generated (Date.now-based) so we key by a stable hash of
// the inner text content instead — same content reproduces the same hash on
// reload. LocalStorage holds a Set of expanded hashes; we observe the chat
// history and re-expand matching sections as they're inserted.
const THINK_EXPANDED_KEY = 'odysseus-thinking-expanded';
function _loadExpandedSet() {
  try { return new Set(JSON.parse(localStorage.getItem(THINK_EXPANDED_KEY) || '[]')); }
  catch { return new Set(); }
}
function _saveExpandedSet(set) {
  try {
    const arr = [...set];
    // Bound storage growth — keep the most recent 200 entries.
    if (arr.length > 200) arr.splice(0, arr.length - 200);
    localStorage.setItem(THINK_EXPANDED_KEY, JSON.stringify(arr));
  } catch {}
}
function _hashThinkingContent(el) {
  if (!el) return '';
  const text = (el.textContent || '').trim();
  if (!text) return '';
  let h = 0;
  for (let i = 0; i < text.length; i++) {
    h = (h * 31 + text.charCodeAt(i)) | 0;
  }
  return String(h);
}
function _setThinkingExpanded(content, toggle, header, expanded) {
  if (!content || !toggle) return;
  content.classList.toggle('expanded', expanded);
  toggle.classList.toggle('expanded', expanded);
  const label_el = header?.querySelector('.thinking-header-left span');
  if (label_el) {
    const label = label_el.dataset.label || 'thinking process';
    label_el.textContent = expanded ? `Hide ${label}` : `View ${label}`;
  }
}

// Delegated click handler for thinking toggle (CSP-safe, no inline onclick)
document.addEventListener('click', function(e) {
  const header = e.target.closest('.thinking-header[data-thinking-id]');
  if (!header) return;
  const id = header.dataset.thinkingId;
  const content = document.getElementById(id);
  const toggle = document.getElementById(id + '-toggle');
  if (!content || !toggle) return;

  const willExpand = !content.classList.contains('expanded');
  _setThinkingExpanded(content, toggle, header, willExpand);

  // Persist by content hash so the choice survives a refresh.
  const hash = _hashThinkingContent(content);
  if (!hash) return;
  const set = _loadExpandedSet();
  if (willExpand) set.add(hash);
  else set.delete(hash);
  _saveExpandedSet(set);
});

// Watch the chat history; whenever a thinking section appears, expand it if
// its hash matches one the user previously expanded.
(function _watchThinking() {
  if (window._thinkingWatcherWired) return;
  window._thinkingWatcherWired = true;
  const _apply = (root) => {
    if (!root || !root.querySelectorAll) return;
    const sections = root.matches?.('.thinking-section')
      ? [root]
      : [...root.querySelectorAll('.thinking-section')];
    if (!sections.length) return;
    const set = _loadExpandedSet();
    if (!set.size) return;
    for (const sec of sections) {
      const content = sec.querySelector('.thinking-content');
      if (!content) continue;
      if (content.classList.contains('expanded')) continue;
      const hash = _hashThinkingContent(content);
      if (!hash || !set.has(hash)) continue;
      const header = sec.querySelector('.thinking-header[data-thinking-id]');
      const id = header?.dataset.thinkingId;
      const toggle = id ? document.getElementById(id + '-toggle') : null;
      _setThinkingExpanded(content, toggle, header, true);
    }
  };
  const start = () => {
    const root = document.body;
    if (!root) return;
    _apply(root);
    new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1) _apply(node);
        }
      }
    }).observe(root, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
