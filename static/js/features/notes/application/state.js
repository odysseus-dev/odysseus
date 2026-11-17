/** The sole Notes state owner. Views have live read-only access; writes use operations. */
export function createNotesState(initial = {}) {
    const values = {
      open: false, notes: [], editingId: null, selectedIds: new Set(),
      activeLabel: null, activeFilter: null, reminderChipNext: 'reminders', searchQuery: '',
      viewMode: initial.viewMode || 'list', showingArchived: false, selectMode: false,
      suppressNextNoteTapId: null, labelsCollapsed: !!initial.labelsCollapsed,
      loading: false, firedDotDismissedAt: initial.firedDotDismissedAt || 0,
    };
    // Preserve captured note identity across asynchronous completions without
    // giving a view a writable reference to the application store.
    const views = new WeakMap(), targets = new WeakMap();
    const unwrap = value => targets.get(value) || value;
    // Copies built from views (spread, filter, map) still hold nested views;
    // store only raw data so the owner never keeps its own read-only views.
    function unwrapDeep(value, seen = new WeakSet()) {
      if (!value || typeof value !== 'object') return value;
      if (targets.has(value)) return targets.get(value);
      if (seen.has(value)) return value;
      seen.add(value);
      if (Array.isArray(value)) value.forEach((item, index) => { value[index] = unwrapDeep(item, seen); });
      else if (Object.getPrototypeOf(value) === Object.prototype) for (const key of Object.keys(value)) value[key] = unwrapDeep(value[key], seen);
      return value;
    }
    function readonly(value) {
      if (!value || typeof value !== 'object') return value;
      if (targets.has(value)) return value;
      if (views.has(value)) return views.get(value);
      const view = new Proxy(value, {
        get(target, key) {
          if (target instanceof Set) {
            if (['add', 'delete', 'clear'].includes(key)) return () => { throw new TypeError('Use Notes selection operations'); };
            if (key === 'valueOf') return () => views.get(target);
            if (key === 'forEach') return (callback, thisArg) => target.forEach(value => callback.call(thisArg, value, value, views.get(target)));
            const member = Reflect.get(target, key, target);
            return typeof member === 'function' ? member.bind(target) : member;
          }
          return readonly(Reflect.get(target, key));
        },
        set() { throw new TypeError('Use Notes state operations'); },
        deleteProperty() { throw new TypeError('Use Notes state operations'); },
        defineProperty() { throw new TypeError('Use Notes state operations'); },
        setPrototypeOf() { throw new TypeError('Use Notes state operations'); },
        preventExtensions() { throw new TypeError('Use Notes state operations'); },
        getOwnPropertyDescriptor(target, key) {
          const descriptor = Reflect.getOwnPropertyDescriptor(target, key);
          if (descriptor && 'value' in descriptor) return { ...descriptor, value: readonly(descriptor.value) };
          return descriptor;
        },
      });
      views.set(value, view); targets.set(view, value);
      return view;
    }
    const state = Object.freeze(Object.fromEntries(Object.keys(values).map(key => [key, () => readonly(values[key])])));
    const view = Object.freeze(Object.defineProperties({}, Object.fromEntries(Object.entries(state).map(([key, get]) => [key, { get, enumerable: true }]))));
    return {
      state: view,
      setOpen: value => { values.open = value; },
      setEditingId: value => { values.editingId = value; },
      setActiveLabel: value => { values.activeLabel = value; },
      setActiveFilter: value => { values.activeFilter = value; },
      setReminderChipNext: value => { values.reminderChipNext = value; },
      setSearchQuery: value => { values.searchQuery = value; },
      setViewMode: value => { values.viewMode = value; },
      setShowingArchived: value => { values.showingArchived = value; },
      setSelectMode: value => { values.selectMode = value; },
      setSuppressNextNoteTapId: value => { values.suppressNextNoteTapId = value; },
      setLabelsCollapsed: value => { values.labelsCollapsed = value; },
      setLoading: value => { values.loading = value; },
      setFiredDotDismissedAt: value => { values.firedDotDismissedAt = value; },
      replaceNotes: notes => { values.notes = notes; },
      removeNoteAt: index => readonly(values.notes.splice(index, 1)[0]),
      insertNoteAt: (index, note) => { values.notes.splice(index, 0, unwrapDeep(note)); },
      prependNote: note => { values.notes.unshift(unwrapDeep(note)); },
      replaceNoteAt: (index, note) => { values.notes[index] = unwrapDeep(note); },
      mergeNote: (note, patch) => { for (const [key, value] of Object.entries(patch)) unwrap(note)[key] = unwrapDeep(value); },
      updateItem: (note, index, patch) => { Object.assign(unwrap(unwrap(note).items[index]), unwrapDeep({ ...patch })); },
      spliceItems: (note, ...args) => unwrap(unwrap(note).items).splice(...args.map(arg => unwrapDeep(arg))),
      selectId: id => { values.selectedIds.add(id); },
      deselectId: id => values.selectedIds.delete(id),
      clearSelection: () => values.selectedIds.clear(),
    };
}
