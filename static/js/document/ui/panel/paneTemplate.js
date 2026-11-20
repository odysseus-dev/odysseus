// ui/panel/paneTemplate.js

export function mountPaneTemplate({ pane }) {
pane.innerHTML = `
      <input type="hidden" id="doc-title-input" value="" />
      <div class="doc-mobile-grabber" id="doc-mobile-grabber" aria-hidden="true"></div>
      <div class="doc-editor-header" id="doc-editor-actions">
        <button id="doc-undo-btn" class="doc-action-icon-btn" title="Undo (Ctrl+Z)" aria-label="Undo" aria-disabled="true" disabled style="gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg><span style="font-size:11px;">Undo</span></button>
        <button id="doc-redo-btn" class="doc-action-icon-btn" title="Redo (Ctrl+Shift+Z)" aria-label="Redo" aria-disabled="true" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.13-9.36L23 10"/></svg></button>
        <button id="doc-header-preview-btn" class="doc-action-icon-btn" title="Run / Preview" style="display:none;opacity:0.85;gap:4px;"></button>
        <span id="doc-stream-indicator" class="doc-stream-indicator" style="display:none"><span class="doc-stream-dot"></span> editing</span>
        <span id="doc-version-badge" class="doc-version-badge" title="Version history" style="display:none">v1</span>
        <span style="flex:1"></span>
        <button id="doc-export-pdf-btn" class="doc-action-icon-btn" title="Export PDF" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 18 15 15"/></svg> <span style="font-size:11px;">Export PDF</span></button>
        <button id="doc-pdf-view-btn" class="doc-action-icon-btn" title="Toggle PDF view" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> <span style="font-size:11px;">PDF</span></button>
        <select id="doc-language-select" class="doc-language-select">
          <option value="python">python</option>
          <option value="javascript">javascript</option>
          <option value="typescript">typescript</option>
          <option value="html">html</option>
          <option value="css">css</option>
          <option value="richtext">Rich Text</option>
          <option value="markdown">markdown</option>
          <option value="docx">Word / DOCX</option>
          <option value="json">json</option>
          <option value="yaml">yaml</option>
          <option value="bash">bash</option>
          <option value="sql">sql</option>
          <option value="rust">rust</option>
          <option value="go">go</option>
          <option value="java">java</option>
          <option value="c">c</option>
          <option value="cpp">c++</option>
          <option value="csharp">c#</option>
          <option value="xml">xml</option>
          <option value="svg">svg</option>
          <option value="toml">toml</option>
          <option value="ini">ini</option>
          <option value="ruby">ruby</option>
          <option value="php">php</option>
          <option value="csv">csv</option>
          <option value="email">email</option>
          <option value="pdf">pdf</option>
        </select>
        <!-- Close + Copy/Export moved to the bottom action footer (#doc-actions-footer)
             so regular docs match the email footer layout. -->
      </div>
      <div class="doc-tab-bar" id="doc-tab-bar"></div>
      <div id="doc-email-header" class="doc-email-header" style="display:none">
        <button type="button" id="doc-email-collapse-btn" class="doc-email-collapse-btn" title="Hide email fields" aria-expanded="true">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 15 12 9 18 15"/></svg>
          <span id="doc-email-collapse-summary" class="doc-email-collapse-summary">No recipient · No subject</span>
        </button>
        <div id="doc-email-fields" class="doc-email-fields">
          <div class="email-field" style="position:relative">
            <span class="email-field-prefix">To</span>
            <input type="text" id="doc-email-to" placeholder="recipient@example.com" autocomplete="off" />
            <div id="doc-email-to-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" id="doc-email-show-cc" class="email-cc-toggle" title="Show Cc/Bcc">Cc</button>
          </div>
          <div class="email-field" id="doc-email-cc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Cc</span>
            <input type="text" id="doc-email-cc" placeholder="cc@example.com, example2" autocomplete="off" />
            <div id="doc-email-cc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" id="doc-email-bcc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Bcc</span>
            <input type="text" id="doc-email-bcc" placeholder="bcc@example.com" autocomplete="off" />
            <div id="doc-email-bcc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" style="position:relative"><span class="email-field-prefix">Subject</span><input type="text" id="doc-email-subject" placeholder="" /></div>
          <div id="doc-email-attachments" class="email-attachments" style="display:none"></div>
          <div id="doc-email-compose-atts" class="email-compose-atts" style="display:none"></div>
        </div>
        <input type="hidden" id="doc-email-in-reply-to" />
        <input type="hidden" id="doc-email-references" />
        <input type="hidden" id="doc-email-source-uid" />
        <input type="hidden" id="doc-email-source-folder" />
        <input type="file" id="doc-email-file-input" multiple style="display:none" />
      </div>
      <input type="file" id="doc-md-image-input" accept="image/*" multiple style="display:none" />
      <div class="doc-md-toolbar" id="doc-md-toolbar" style="display:none">
        <div class="md-toolbar-leading-controls">
          <span class="md-view-toggle" id="doc-md-view-toggle" style="display:none" role="group" aria-label="Editor mode">
            <button type="button" class="md-view-opt" data-mdview="visual" title="Visual editor"><span class="md-view-label">Visual</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4Z"/></svg></button>
            <button type="button" class="md-view-opt" data-mdview="edit" title="Markdown source"><span class="md-view-label">Markdown</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></button>
          </span>
          <select id="doc-source-insert" class="doc-source-insert" aria-label="Insert Markdown template">
            <option value="">Insert Markdown…</option>
            <option value="reference">Reference link</option>
            <option value="footnote">Footnote</option>
            <option value="math">Inline math</option>
            <option value="equation">Display math</option>
            <option value="mermaid">Mermaid diagram</option>
            <option value="details">Collapsible section</option>
          </select>
          <span class="md-view-toggle" id="doc-render-view-toggle" style="display:none" role="group" aria-label="Code or run">
            <button type="button" class="md-view-opt" data-renderview="code" title="Edit code"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></button>
            <button type="button" class="md-view-opt" data-renderview="run" title="Run / Preview"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>
          </span>
        </div>
        <button type="button" class="md-scroll-arrow md-scroll-left" id="md-scroll-left" title="Scroll left" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
        <div class="md-toolbar-items" id="md-toolbar-items">
          <button id="doc-email-ai-reply-btn" class="doc-action-icon-btn md-toolbar-email-only" type="button" title="Draft a reply with AI (fast + optional context)" style="display:none;align-items:center;gap:4px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="color:var(--accent, var(--red));flex-shrink:0;position:relative;top:-1px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg><span style="font-size:11px;">Reply</span></button>
          <span id="md-toolbar-sep-after-ai-reply" class="md-toolbar-sep md-toolbar-manual-sep md-toolbar-email-only" aria-hidden="true"></span>
          <button type="button" id="doc-ai-writing-btn" class="doc-ai-writing-btn md-toolbar-edit-only" title="Writing tools" aria-label="Writing tools" aria-haspopup="menu" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 1.8 14.45 9.55 22.2 12l-7.75 2.45L12 22.2l-2.45-7.75L1.8 12l7.75-2.45L12 1.8Z"/></svg><svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button id="doc-fontsize-btn" class="doc-action-icon-btn" title="Editor display size" aria-label="Editor display size" style="position:relative;width:28px;height:26px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7;"><path d="M4 7V4h16v3"/><path d="M12 4v16"/><path d="M8 20h8"/></svg><span class="doc-fontsize-levels"><i data-sz="s">S</i><i data-sz="m">M</i><i data-sz="l">L</i></span></button>
          <button id="doc-diff-toggle-btn" class="doc-action-icon-btn" title="Compare changes" style="opacity:0.7;display:none;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 12H2l5-5 5 5H9"/><path d="M19 12h3l-5 5-5-5h3"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-toolbar-edit-only" data-md="bold" title="Bold (Ctrl+B)" aria-label="Bold (Ctrl+B)"><b>B</b></button>
          <button type="button" class="md-toolbar-edit-only" data-md="italic" title="Italic (Ctrl+I)" aria-label="Italic (Ctrl+I)"><i>I</i></button>
          <button type="button" class="md-toolbar-edit-only" data-md="strike" title="Strikethrough (Ctrl+Shift+X)" aria-label="Strikethrough (Ctrl+Shift+X)"><s>S</s></button>
          <button type="button" class="md-toolbar-edit-only md-toolbar-rich-only" data-md="underline" title="Underline (Ctrl+U)" aria-label="Underline (Ctrl+U)" style="display:none"><u>U</u></button>
          <button type="button" class="md-toolbar-rich-only" data-md="superscript" title="Superscript" aria-label="Superscript" style="display:none"><span class="rich-script-icon">x<sup>2</sup></span></button>
          <button type="button" class="md-toolbar-rich-only" data-md="subscript" title="Subscript" aria-label="Subscript" style="display:none"><span class="rich-script-icon">x<sub>2</sub></span></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" class="md-dd-toggle md-toolbar-edit-only" data-dd="heading" title="Heading (Ctrl+Alt+1-6)" aria-label="Heading (Ctrl+Alt+1-6)"><b>H</b><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only md-toolbar-email-hide" data-dd="alert" title="GitHub alert type" aria-label="GitHub alert type" style="display:none"><span aria-hidden="true">!</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="font" title="Font family" aria-label="Font family" style="display:none"><span>Aa</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span id="md-toolbar-sep-after-heading" class="md-toolbar-sep md-toolbar-manual-sep md-toolbar-edit-only" aria-hidden="true"></span>
          <button type="button" class="md-dd-toggle md-toolbar-edit-only" data-dd="list" title="Bulleted list" aria-label="Bulleted list"><svg class="rich-list-bullet-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="4" cy="6" r="1.5"/><circle cx="4" cy="12" r="1.5"/><circle cx="4" cy="18" r="1.5"/><path d="M9 5h12v2H9zM9 11h12v2H9zM9 17h12v2H9z"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" id="md-toolbar-attach-btn" class="md-toolbar-attach-btn md-toolbar-edit-only" title="Attach file"><svg class="md-attach-paperclip-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><svg class="md-attach-image-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/><line x1="18" y1="4" x2="18" y2="10"/><line x1="15" y1="7" x2="21" y2="7"/></svg></button>
          <button type="button" id="md-toolbar-inline-image-btn" class="md-toolbar-inline-image-btn md-toolbar-edit-only" title="Insert image" aria-label="Insert image" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/><line x1="18" y1="4" x2="18" y2="10"/><line x1="15" y1="7" x2="21" y2="7"/></svg></button>
          <button type="button" class="md-toolbar-edit-only" data-md="link" title="Link (Ctrl+K)" aria-label="Link (Ctrl+K)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="image" title="Image options" aria-label="Image options" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-email-hide md-toolbar-edit-only" data-dd="code" title="Code" aria-label="Code"><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-rich-only" style="display:none"></span>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="textsize" title="Font size" aria-label="Font size" style="display:none"><span class="rich-font-size-icon">16</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-align-control" data-dd="align" title="Text alignment (Ctrl+Shift+L/E/J)" aria-label="Text alignment (Ctrl+Shift+L/E/J)" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="10" x2="16" y2="10"/><line x1="4" y1="14" x2="20" y2="14"/><line x1="4" y1="18" x2="14" y2="18"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="spacing" title="Line spacing" aria-label="Line spacing" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 12h16M4 17h16"/><path d="m1 5 2-2 2 2M3 3v16m-2-2 2 2 2-2"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="outdent" title="Decrease indent (Ctrl+[)" aria-label="Decrease indent (Ctrl+[)" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="12" x2="21" y2="12"/><line x1="11" y1="18" x2="21" y2="18"/><polyline points="7 8 3 12 7 16"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="indent" title="Increase indent (Ctrl+])" aria-label="Increase indent (Ctrl+])" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="12" x2="21" y2="12"/><line x1="11" y1="18" x2="21" y2="18"/><polyline points="3 8 7 12 3 16"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-inline-format" data-dd="color" title="Text color" aria-label="Text color" style="display:none"><span class="rich-color-letter">A</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-inline-format" data-dd="highlight" title="Highlight color" aria-label="Highlight color" style="display:none"><svg class="rich-highlight-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 11-6 6v3h3l6-6"/><path d="m22 12-7-7-8.5 8.5 7 7Z"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span id="md-toolbar-sep-after-highlight" class="md-toolbar-sep md-toolbar-manual-sep md-toolbar-edit-only" aria-hidden="true"></span>
          <button type="button" class="md-toolbar-rich-only" data-md="quote" title="Block quote" aria-label="Block quote" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M7 17H3l3-10h5zm10 0h-4l3-10h5z"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-rich-only" data-dd="table" title="Table" aria-label="Table" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-inline-format" data-dd="separator" title="Insert separator" aria-label="Insert separator" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="12" x2="21" y2="12"/></svg><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-toolbar-rich-only md-toolbar-email-hide" data-md="pagebreak" title="Page break" aria-label="Page break" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18M3 19h18"/><path d="M12 8v7m0 0-3-3m3 3 3-3"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="unlink" title="Remove link" aria-label="Remove link" disabled style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 15l-2 2a4 4 0 0 1-6-6l3-3a4 4 0 0 1 5-1"/><path d="M15 9l2-2a4 4 0 0 1 6 6l-3 3a4 4 0 0 1-5 1"/><line x1="8" y1="2" x2="16" y2="22"/></svg></button>
          <button type="button" class="md-toolbar-rich-only" data-md="removeformat" title="Clear formatting" aria-label="Clear formatting" style="display:none"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M12 5v14M8 19h8"/><path d="m4 4 16 16"/></svg></button>
          <span class="md-toolbar-sep md-toolbar-edit-only"></span>
          <button type="button" id="doc-find-toolbar-btn" class="md-toolbar-edit-only" title="Find (Ctrl+F)" aria-label="Find"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg></button>
          <button type="button" id="doc-outline-toolbar-btn" title="Document outline" aria-label="Document outline" aria-haspopup="true" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="3" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="3" cy="18" r="1" fill="currentColor" stroke="none"/></svg></button>
          <span id="md-toolbar-emoji-slot" class="md-toolbar-edit-only"></span>
          <span class="md-toolbar-sep md-toolbar-pdf-only" style="display:none"></span>
          <button type="button" id="doc-pdf-add-sign-btn" class="md-toolbar-pdf-only" title="Add signature (then click on PDF)" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3l6 6-9 9-3-3z"/><path d="M9 15l-3 1 1-3"/><path d="M4 18l3-3"/><path d="M3 20l3-3"/><path d="M5 22l3-3"/></svg><span class="doc-pdf-sign-label">sign</span></button>
          <span class="md-toolbar-sep md-toolbar-pdf-only" style="display:none"></span>
          <button type="button" id="doc-pdf-add-text-btn" class="md-toolbar-pdf-only" title="Add text box (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg></button>
          <button type="button" id="doc-pdf-add-check-btn" class="md-toolbar-pdf-only" title="Add checkmark (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></button>
          <button type="button" id="doc-pdf-refresh-btn" class="md-toolbar-pdf-only" title="Reload PDF view" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
        </div>
        <button type="button" class="md-scroll-arrow md-scroll-right" id="md-scroll-right" title="Scroll right" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
        <div class="md-toolbar-overflow-wrapper" id="md-toolbar-overflow-wrapper" style="display:none">
          <button class="md-toolbar-overflow-toggle" id="md-toolbar-overflow-toggle" title="More formatting"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
          <div class="md-toolbar-overflow-menu" id="md-toolbar-overflow-menu"></div>
        </div>
      </div>
      <div id="doc-find-bar" class="doc-find-bar" style="display:none">
        <div class="doc-find-row">
          <input id="doc-find-input" class="doc-find-input" type="text" placeholder="Find..." autocomplete="off" />
          <span id="doc-find-count" class="doc-find-count" aria-live="polite"></span>
          <button type="button" id="doc-find-prev" class="doc-find-nav" title="Previous match" aria-label="Previous match">&uarr;</button>
          <button type="button" id="doc-find-next" class="doc-find-nav" title="Next match" aria-label="Next match">&darr;</button>
          <button type="button" id="doc-find-replace-toggle" class="doc-find-nav" title="Show replace" aria-label="Show replace" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m17 3 4 4-4 4"/><path d="M3 7h18"/><path d="m7 21-4-4 4-4"/><path d="M21 17H3"/></svg></button>
          <button type="button" id="doc-find-close" class="doc-find-close" title="Close" aria-label="Close find">&times;</button>
        </div>
        <div id="doc-replace-row" class="doc-replace-row" hidden>
          <input id="doc-replace-input" class="doc-find-input" type="text" placeholder="Replace with..." autocomplete="off" />
          <button type="button" id="doc-replace-current" class="doc-find-action">Replace</button>
          <button type="button" id="doc-replace-all" class="doc-find-action">Replace all</button>
        </div>
      </div>
      <div id="doc-editor-surface" role="tabpanel" class="doc-editor-surface">
      <div id="doc-visual-compat-banner" class="doc-visual-compat-banner" style="display:none" role="status"></div>
      <div id="doc-editor-wrap" class="doc-editor-wrap">
        <div id="doc-line-numbers" class="doc-line-numbers">1</div>
        <pre id="doc-editor-highlight" class="doc-editor-highlight"><code id="doc-editor-code"></code></pre>
        <textarea id="doc-editor-textarea" class="doc-editor-textarea" placeholder="Document content..." spellcheck="false"></textarea>
      </div>
      <!-- WYSIWYG email body. In email mode this replaces the source editor:
           B/I/S act on the live text (execCommand), and on send its HTML becomes
           the email's HTML part. Its plain text is mirrored into the textarea so
           the existing send/draft/change-detection paths keep working. -->
      <div id="doc-email-richbody" class="doc-email-richbody" role="textbox" aria-multiline="true" contenteditable="true" spellcheck="false" style="display:none" data-no-swipe-dismiss></div>
      <div id="doc-rich-empty-import" class="doc-rich-empty-import" style="display:none" aria-live="polite">
        <button type="button" class="doc-preview-hover-edit doc-rich-empty-import-btn" title="Import a document" aria-label="Import a document">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M12 12v6"></path><path d="m9 15 3 3 3-3"></path></svg>
          <span>Import document</span>
        </button>
      </div>
      <div id="doc-email-actions" class="doc-email-actions" style="display:none">
        <button id="doc-email-discard-btn" class="email-discard-btn" title="Close email" style="display:inline-flex;align-items:center;gap:5px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span>Close</span></button>
        <span style="flex:1"></span>
        <div class="email-send-split">
          <button type="button" id="doc-email-send-btn" class="email-send-btn email-send-main" title="Send email (Ctrl+Enter)" onpointerdown="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)" onmousedown="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)" onclick="window.odysseusEmailSendIntent&&window.odysseusEmailSendIntent(event)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>Send</button>
          <button type="button" id="doc-email-send-caret" class="email-send-btn email-send-caret" title="More send options" aria-haspopup="true" aria-expanded="false" onpointerdown="window.odysseusEmailCaretIntent&&window.odysseusEmailCaretIntent(event)" onmousedown="window.odysseusEmailCaretIntent&&window.odysseusEmailCaretIntent(event)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>
          <div id="doc-email-more-menu" class="email-more-menu" style="display:none">
            <div class="dropdown-item-compact" id="doc-email-draft-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></span>Save Draft</div>
            <div class="dropdown-item-compact" id="doc-email-schedule-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>Schedule Send...</div>
            <div class="dropdown-item-compact" id="doc-email-unread-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg></span>Mark Unread</div>
          </div>
        </div>
      </div>
      <div id="doc-md-preview" class="doc-md-preview" style="display:none"></div>
      <div id="doc-docx-preview" class="doc-docx-preview" style="display:none"></div>
      <div id="doc-csv-preview" class="doc-csv-preview" style="display:none"></div>
      <iframe id="doc-html-preview" class="doc-html-preview" sandbox="allow-scripts allow-modals" style="display:none"></iframe>
      <div id="doc-pdf-view" style="display:none;width:100%;flex:1;min-height:0;overflow:auto;background:#525659;padding:20px 0;position:relative;">
        <div id="doc-pdf-save-pill" style="display:none;position:absolute;top:8px;right:14px;padding:4px 10px;border-radius:12px;font-size:11px;z-index:5;pointer-events:none;background:transparent;color:transparent;"></div>
      </div>
      </div>
      <!-- Action footer sits AFTER all the content/preview panes so it stays
           pinned to the bottom no matter which pane (editor / md-preview /
           csv / html / pdf) is the one growing to fill. -->
      <div id="doc-actions-footer" class="doc-email-actions">
        <span id="doc-stats" class="doc-stats-wrap">
          <button type="button" id="doc-stats-btn" class="doc-stats-btn" aria-haspopup="true" aria-expanded="false" title="0 words in document">
            <span id="doc-stats-count">0</span><span id="doc-stats-unit" class="doc-stats-unit"> words</span>
          </button>
          <div id="doc-stats-popover" class="doc-stats-popover" hidden>
            <div id="doc-stats-scope" class="doc-stats-scope">Document</div>
            <div class="doc-stats-row"><span>Words</span><strong id="doc-stats-words">0</strong></div>
            <div class="doc-stats-row"><span>Characters</span><strong id="doc-stats-characters">0</strong></div>
            <div class="doc-stats-row"><span>Without spaces</span><strong id="doc-stats-characters-no-spaces">0</strong></div>
            <div class="doc-stats-row"><span>Lines</span><strong id="doc-stats-lines">0</strong></div>
            <div class="doc-stats-row"><span>Reading time</span><strong id="doc-stats-reading">0 min</strong></div>
          </div>
        </span>
        <span class="email-send-split" id="doc-copy-export-split">
          <button type="button" id="doc-footer-copy-btn" class="email-send-btn email-send-main doc-save-button" title="All changes saved (Ctrl+S)" data-mode="save" data-save-state="saved" aria-label="Saved" aria-live="polite">
            <svg class="doc-save-state-icon doc-save-state-dirty" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 3h12l4 4v14H4z"/><path d="M8 3v6h8V3"/><path d="M8 21v-6h8v6"/><path d="m16 3 6 6m0-6-6 6" stroke="var(--fg)" stroke-width="3"/></svg>
            <svg class="doc-save-state-icon doc-save-state-saving" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.56"/></svg>
            <svg class="doc-save-state-icon doc-save-state-saved" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
            <svg class="doc-save-state-icon doc-save-state-error" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><line x1="12" y1="7" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            <span class="doc-save-button-label">Saved</span>
          </button>
          <button type="button" id="doc-footer-export-btn" class="email-send-btn email-send-caret" title="Export as…" aria-label="Export options"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 15 12 9 18 15"/></svg></button>
        </span>
      </div>
      <div id="doc-version-panel" class="doc-version-panel hidden">
        <div class="doc-version-header">
          <span>Version History</span>
          <button id="doc-version-close" class="doc-action-icon-btn" title="Close" aria-label="Close version history"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div id="doc-version-list" class="doc-version-list"></div>
      </div>
      <div id="doc-mobile-footer" class="doc-mobile-footer">
        <button id="doc-mobile-close" class="doc-mobile-footer-btn" type="button">Unlink</button>
        <span style="flex:1"></span>
        <button id="doc-mobile-copy" class="doc-mobile-footer-btn" type="button">Copy</button>
      </div>
    `;
}
