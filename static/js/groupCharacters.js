// static/js/groupCharacters.js
/**
 * Build the list of selectable characters/personas for the group-chat picker.
 *
 * Three sources, merged and de-duplicated by id:
 *   1. built-in characters from PROMPT_TEMPLATES (flagged isCharacter)
 *   2. the single "custom" preset slot (if it has a character_name)
 *   3. every user-saved template — these ARE personas
 *
 * The group picker used to (a) read the templates response as `data.templates`
 * though the endpoint returns a bare array, (b) gate user templates on
 * `t.isCharacter` though the save-as-template flow never sets that flag, and
 * (c) read the prompt from `t.prompt` though templates store `system_prompt`.
 * The net effect was that only the just-edited "custom" persona showed up and
 * none of the user's saved personas could be added to a group (#1656). Taking
 * the already-parsed user-template array and treating each entry as a persona
 * fixes all three.
 */
export function mergeGroupCharacters(promptTemplates, customPreset, userTemplates) {
  const chars = (promptTemplates || [])
    .filter((t) => t && t.isCharacter)
    .map((t) => ({ id: t.id, name: t.name, prompt: t.prompt || '' }));

  if (customPreset && customPreset.character_name) {
    chars.push({
      id: 'custom',
      name: customPreset.character_name,
      prompt: customPreset.system_prompt || customPreset.prompt || '',
    });
  }

  (userTemplates || []).forEach((t) => {
    if (t && t.id != null && !chars.find((c) => c.id === t.id)) {
      chars.push({ id: t.id, name: t.name, prompt: t.system_prompt || t.prompt || '' });
    }
  });

  return chars;
}
