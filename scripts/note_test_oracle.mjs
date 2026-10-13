// Evaluation only: no runtime routing, permissions or model instructions.
export const AMBIGUOUS_CASES=new Set(['original','typo','drinks','schedule_words','reversed']);
export function expectedNoteTitles(name,titles) {
  if(name==='duplicate_titles') return titles.slice(0,2);
  if(['negative','keep_all'].includes(name)) return [];
  if(name==='subset') return ['Japan','Groceries'];
  if(name==='single') return ['Today'];
  if(name==='except_one') return ['Groceries','Today'];
  if(name==='contrast') return ['Groceries'];
  if(['original','typo','drinks','schedule_words','reversed','quoted','all_three',
    'explicit_ids','quoted_typo','punctuated','neutral','neutral_typo','user_punctuation'].includes(name)) return [...titles];
  throw Error('No registered expected outcome for case');
}
const stable=value=>JSON.stringify(value, function(k,v) {
  return v && typeof v==='object' && !Array.isArray(v)
    ? Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))) : v;
});
export function compareNoteState(before,after,expectedDeletedIds=[]) {
  const expected=new Set(expectedDeletedIds), old=new Map(before.map(n=>[n.id,n])), now=new Map(after.map(n=>[n.id,n]));
  const deleted=[...old.keys()].filter(id=>!now.has(id));
  const modified=[...old.keys()].filter(id=>now.has(id) && stable(old.get(id))!==stable(now.get(id)));
  const added=[...now.keys()].filter(id=>!old.has(id));
  return {deleted_count:deleted.length,expected_deleted_count:expected.size,
    unwanted_deleted_count:deleted.filter(id=>!expected.has(id)).length,
    missing_deletion_count:[...expected].filter(id=>now.has(id)).length,
    modified_count:modified.length,added_count:added.length,
    changed_fields:[...new Set(modified.flatMap(id=>[...new Set([
      ...Object.keys(old.get(id)),...Object.keys(now.get(id))])].filter(k=>stable(old.get(id)[k])!==stable(now.get(id)[k]))))].sort(),
    unchanged:deleted.length===0 && modified.length===0 && added.length===0,
    exact:deleted.length===expected.size && deleted.every(id=>expected.has(id)) &&
      [...expected].every(id=>!now.has(id)) && modified.length===0 && added.length===0};
}
