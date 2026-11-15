# Appendix A: machine-generated function-to-module assignment

Generated from `static/js/document.js` at 087e6c88 by `fullcheck.cjs` (graph, layer legality via the real `allowsDependency`, cycles), `waves.cjs` (wave simulation) and `appendix2.cjs`. All 436 top-level declarations and the 9 executable top-level statements (`stmt#<line>`) are assigned exactly once; `openPanel` is split into its 120 top-level statements (shown as `openPanel#<line>` blocks with their true line counts, grouped into 12 contiguous ranges) so the cycle check covers it. Level is the longest dependency chain below the module after the twelve command edges, the six persistence hooks and the image observer are removed. "Exports referenced elsewhere" is the minimum public surface. `(command)` marks one of the twelve view commands, exported by the implementing module and reached through `documentCommands`.

Checks recorded when this file was generated: module graph acyclic (re-run after adding the statement nodes); 15 top-level `let` bindings are written outside their owner and each has a named operation (plan 5.5); zero layer violations (one reported edge, `showSelectionBadge -> clearAll`, is a false positive from a local variable named `clearAll`); every dependency of a module points at the same or an earlier wave; `domain/*` assignments contain no platform identifier except `atob` in `_decodeBase64EmailWrapper` (not on the checker's forbidden list).

## application/documentState  (level 0, wave 1, ~13 lines)
- Declarations: API_BASE, _closingDocs, _deletedDocIds, _documentNavigationGeneration, _inFlightDeletes, _lastSessionId, _minimizedDocId, activeDocId, docs, isOpen, isPanelOpen
- Exports referenced elsewhere: API_BASE, _closingDocs, _deletedDocIds, _documentNavigationGeneration, _inFlightDeletes, _lastSessionId, _minimizedDocId, activeDocId, docs, isOpen, isPanelOpen
- Imports from: none

## domain/documentKinds  (level 0, wave 1, ~90 lines)
- Declarations: AUTO_DETECT_DELAY, AUTO_DETECT_MIN_CHARS, AUTO_DETECT_MIN_RELEVANCE, AUTO_DETECT_SAMPLE_SIZE, HLJS_TO_DROPDOWN, _DOC_SAVE_STATE, _buildDiffSummary, _esc, _escHtml, _hasViewToggle, _isDocxLang, _isMarkdownImageFile, _isRenderLang, _isRichTextLang, _looksLikeMarkdown, _markdownImageAlt
- Exports referenced elsewhere: AUTO_DETECT_DELAY, AUTO_DETECT_MIN_CHARS, AUTO_DETECT_MIN_RELEVANCE, AUTO_DETECT_SAMPLE_SIZE, HLJS_TO_DROPDOWN, _DOC_SAVE_STATE, _buildDiffSummary, _esc, _escHtml, _hasViewToggle, _isDocxLang, _isMarkdownImageFile, _isRenderLang, _isRichTextLang, _looksLikeMarkdown, _markdownImageAlt
- Imports from: none

## domain/emailMessage  (level 0, wave 1, ~105 lines)
- Declarations: _ATTACH_RE, _accountCanSend, _buildEmailContent, _buildEmailContentFromFields, _decodeBase64EmailWrapper, _emailQuoteMarkerMatch, _looksLikeWrappedEmailContent, _parseEmailHeader, _unfoldEmailHeaderLines
- Exports referenced elsewhere: _ATTACH_RE, _accountCanSend, _buildEmailContent, _buildEmailContentFromFields, _decodeBase64EmailWrapper, _emailQuoteMarkerMatch, _looksLikeWrappedEmailContent, _parseEmailHeader
- Imports from: none

## domain/richTextRules  (level 0, wave 1, ~77 lines)
- Declarations: _RICH_BLOCK_INPUT_RULES, _RICH_COLOR_PALETTE, _RICH_FONT_SIZE_PX, _RICH_SLASH_COMMANDS, _isRichInlineCodeMarker, _normalizeRichLinkUrl
- Exports referenced elsewhere: _RICH_BLOCK_INPUT_RULES, _RICH_COLOR_PALETTE, _RICH_FONT_SIZE_PX, _RICH_SLASH_COMMANDS, _isRichInlineCodeMarker, _normalizeRichLinkUrl
- Imports from: none

## ui/header/overflow  (level 0, wave 3, ~131 lines)
- Declarations: _DOC_MAX_VISIBLE, _DOC_RECENTS_KEY, _getDocRecent, _trackDocAction, initActionOverflow
- Exports referenced elsewhere: none
- Imports from: none

## ui/panel/paneTemplate  (level 0, wave 7, ~242 lines)
- Declarations: none
- `openPanel` blocks: openPanel#6286
- Exports referenced elsewhere: none
- Imports from: none

## application/documentSession  (level 1, wave 2, ~44 lines)
- Declarations: _docActiveKey, _docMinimizedKey, _docOpenKey, _forgetActiveDoc, _markDocVisibleState, addDocToTabs
- Exports referenced elsewhere: _docActiveKey, _docMinimizedKey, _docOpenKey, _forgetActiveDoc, _markDocVisibleState, addDocToTabs
- Imports from: application/documentState

## application/persistence  (level 1, wave 2, ~189 lines)
- Declarations: _autoSaveDebounce, _cancelAllAutoSaves, _cancelAutoSave, _emailRichbodySaveDebounce, _flushAutoSave, _lastAutoSaveErrorAt, _markDocumentDirty, _pendingAutoSaves, _saveClosingDraft, _scheduleAutoSave, _setDocumentSaveState, saveDocument
- Exports referenced elsewhere: _autoSaveDebounce, _cancelAutoSave, _emailRichbodySaveDebounce, _flushAutoSave, _markDocumentDirty, _pendingAutoSaves, _saveClosingDraft, _scheduleAutoSave, _setDocumentSaveState, saveDocument
- Imports from: application/documentState, domain/documentKinds

## ui/documentActions  (level 1, wave 3, ~150 lines)
- Declarations: autoTitleFromContent, copyDocument, runDocument, updateLanguage, updateTitle
- Exports referenced elsewhere: autoTitleFromContent, copyDocument, runDocument, updateLanguage, updateTitle
- Imports from: application/documentState, domain/documentKinds

## ui/email/localDraft  (level 1, wave 3, ~117 lines)
- Declarations: _EMAIL_LOCAL_DRAFT_PREFIX, _clearCurrentEmailLocalDraft, _clearEmailLocalDraft, _emailFieldsWithLocalDraft, _emailLocalDraftDebounce, _emailLocalDraftKey, _loadEmailLocalDraft, _persistEmailLocalDraftNow, _persistEmailLocalDraftSoon, _sanitizeOutgoingEmailBody
- Exports referenced elsewhere: _clearCurrentEmailLocalDraft, _clearEmailLocalDraft, _emailFieldsWithLocalDraft, _emailLocalDraftDebounce, _persistEmailLocalDraftNow, _persistEmailLocalDraftSoon, _sanitizeOutgoingEmailBody
- Imports from: application/documentState, domain/emailMessage

## ui/email/recipients  (level 1, wave 3, ~163 lines)
- Declarations: _commitRecipient, _searchContacts, _showContactSearchStatus, _splitRecipientsAndFragment, _wireRecipientAutocomplete
- Exports referenced elsewhere: _wireRecipientAutocomplete
- Imports from: application/documentState, domain/documentKinds

## ui/status/documentStatus  (level 1, wave 3, ~313 lines)
- Declarations: _activeDocLanguage, _closeDocumentOutline, _docHistoryStateFrame, _docOutlineClose, _docOutlineMenu, _docStatsFrame, _documentOutlineEntries, _documentOutlineSupported, _documentStatsSource, _flashOutlineTarget, _jumpToDocumentOutlineEntry, _normalizeRichStatsText, _openDocumentOutline, _refreshDocumentOutline, _renderDocumentOutlineMenu, _renderDocumentSaveState, _renderDocumentStats, _scheduleDocumentHistoryControls, _scheduleDocumentStats, _setDocumentHistoryControlState, _syncDocumentHistoryControlsNow, _syncEditorPlaceholder
- Exports referenced elsewhere: _activeDocLanguage, _closeDocumentOutline, _documentOutlineEntries, _documentOutlineSupported, _openDocumentOutline, _refreshDocumentOutline, _renderDocumentSaveState, _renderDocumentStats, _scheduleDocumentHistoryControls, _scheduleDocumentStats, _setDocumentHistoryControlState, _syncDocumentHistoryControlsNow, _syncEditorPlaceholder
- Imports from: application/documentState, domain/documentKinds

## ui/editor/sourceEditor  (level 2, wave 3, ~372 lines)
- Declarations: _autoDetectDebounce, _autoTitleDebounce, _ensureLineNumberResizeObserver, _handleMarkdownPreviewClickHint, _hlDebounce, _lineHeightPx, _lineNumberContentEl, _lineNumberMeasureEl, _lineNumberObservedTextarea, _lineNumberResizeObserver, _lineNumberResizeRaf, _lineNumberStyleSignature, _mdPreviewClickTimes, _mdPreviewHintLastAt, _measureLineNumberHeights, _postProcessMarkdown, _refreshMarkdownPreviewIfVisible, _renderLineNumberRows, _replaceRange, _scheduleLineNumberRerender, _syncLineNumberMeasureStyle, _textareaTextWidth, attemptAutoDetect, populateEditor, stmt#10945, syncGutterScroll, syncHighlighting, updateLineNumbers
- Exports referenced elsewhere: _autoDetectDebounce, _autoTitleDebounce, _handleMarkdownPreviewClickHint, _hlDebounce, _lineNumberContentEl, _lineNumberObservedTextarea, _lineNumberResizeObserver, _lineNumberResizeRaf, _refreshMarkdownPreviewIfVisible, _replaceRange, attemptAutoDetect, syncGutterScroll, syncHighlighting, updateLineNumbers
- Imports from: application/documentState, domain/documentKinds, ui/documentActions

## ui/richEditor/richBodySync  (level 2, wave 4, ~104 lines)
- Declarations: _emailHtmlToPlainText, _emailPlainTextToHtml, _emailRichbodyActive, _richTextContentToHtml, _sanitizedRichTextHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport
- Exports referenced elsewhere: _emailHtmlToPlainText, _emailPlainTextToHtml, _emailRichbodyActive, _richTextContentToHtml, _sanitizedRichTextHtml, _scheduleEmailRichbodySave, _syncEmailRichbody, _syncRichEmptyImport
- Imports from: application/documentState, application/persistence, domain/documentKinds, domain/emailMessage, domain/richTextRules, ui/email/localDraft

## ui/email/emailBody  (level 3, wave 5, ~299 lines)
- Declarations: _captureEmailBodyFocusState, _emailBodyFragmentToHtml, _emailBodyToHtml, _emailQuoteStartIndex, _emailQuoteStartOffset, _emailReplyOwnText, _emailStreamAnimFrame, _emailStreamRenderedBody, _emailStreamTargetBody, _focusEmailBodyEnd, _renderStreamingEmailBody, _restoreEmailBodyFocusState, _setEmailBodyText, _splitEmailReplyQuote, _streamEmailBodyText, _stripEmailReplyQuoteText, focusEmailReplyBody
- Exports referenced elsewhere: _captureEmailBodyFocusState, _emailBodyToHtml, _emailReplyOwnText, _emailStreamAnimFrame, _emailStreamRenderedBody, _emailStreamTargetBody, _focusEmailBodyEnd, _renderStreamingEmailBody, _restoreEmailBodyFocusState, _setEmailBodyText, _splitEmailReplyQuote
- Imports from: domain/emailMessage, ui/editor/sourceEditor, ui/email/localDraft, ui/richEditor/richBodySync

## ui/history/versionHistory  (level 3, wave 3, ~190 lines)
- Declarations: _closeVersionPanel, _historyOwnsView, _versionClickOutside, _versionHistory, _versionHistoryGeneration, loadVersionHistory, previewVersion, restoreVersion, toggleVersionHistory
- Exports referenced elsewhere: _closeVersionPanel, _historyOwnsView, _versionHistory, toggleVersionHistory
- Imports from: application/documentState, application/persistence, domain/documentKinds, ui/editor/sourceEditor

## ui/panel/paneLayout  (level 3, wave 3, ~265 lines)
- Declarations: _dismissDocKb, _wireSwipeDismiss, initDividerDrag, toggleFullscreen
- `openPanel` blocks: openPanel#6265, openPanel#6684, openPanel#6692, openPanel#6693
- Exports referenced elsewhere: _dismissDocKb, _wireSwipeDismiss, initDividerDrag, toggleFullscreen
- Imports from: ui/editor/sourceEditor

## ui/richEditor/richOperations  (level 3, wave 4, ~467 lines)
- Declarations: _activeRichImage, _applyRichLineSpacing, _cleanRichTextPasteHtml, _focusRichTextOffset, _handleRichChecklistEnter, _handleRichHeadingEnter, _insertSmartRichPasteLink, _normalizeRichChecklists, _normalizeRichInlineCode, _replaceRichChecklist, _restoreRichSelectionTextOffsets, _richChecklistItems, _richClosestSpacingBlock, _richInlineCodeTypingArmed, _richSelectedSpacingBlockIndexes, _richSelectionChecklistItem, _richSelectionElement, _richSelectionInlineCode, _richSelectionListItem, _richSelectionTextOffsets, _richSpacingBlockSelector, _richSpacingBlocks, _richTextOffsetWithin, _richTextPointAtOffset, _selectedRichImage, _setRichChecklistItemChecked, _smartRichPasteUrl, _toggleRichChecklist, _toggleRichInlineCode
- Exports referenced elsewhere: _activeRichImage, _applyRichLineSpacing, _cleanRichTextPasteHtml, _handleRichChecklistEnter, _handleRichHeadingEnter, _insertSmartRichPasteLink, _normalizeRichChecklists, _normalizeRichInlineCode, _richInlineCodeTypingArmed, _richSelectedSpacingBlockIndexes, _richSelectionChecklistItem, _richSelectionInlineCode, _richSpacingBlocks, _selectedRichImage, _setRichChecklistItemChecked, _toggleRichChecklist, _toggleRichInlineCode
- Imports from: domain/richTextRules, ui/richEditor/richBodySync, ui/status/documentStatus

## ui/documentFlows  (level 4, wave 4, ~580 lines)
- Declarations: _autoCreateFromInput, _autoCreateSession, _autoCreating, _closeWithoutDeleting, _creatingDoc, _detachDocFromSession, _ensureDocPaneMounted, _moveDocToSession, _rememberActiveDoc, _visibleDocIdsForCurrentSession, closeTab, createDocument, deleteActiveDocument, ensureDocPanel, getChatDocumentId, injectFreshDoc, loadDocument, loadSessionDocs, moveActiveDocumentToCurrentChat, moveActiveDocumentToNewChat
- Exports referenced elsewhere: _autoCreateFromInput, _autoCreateSession, _closeWithoutDeleting, _creatingDoc, _detachDocFromSession, _ensureDocPaneMounted, _rememberActiveDoc, _visibleDocIdsForCurrentSession, closeTab, createDocument, deleteActiveDocument, getChatDocumentId, injectFreshDoc, loadDocument, moveActiveDocumentToCurrentChat, moveActiveDocumentToNewChat
- Imports from: application/documentSession, application/documentState, application/persistence, domain/documentKinds, domain/emailMessage, ui/documentActions, ui/editor/sourceEditor, ui/email/localDraft, ui/history/versionHistory, ui/richEditor/richBodySync, ui/status/documentStatus

## ui/email/aiReply  (level 4, wave 5, ~277 lines)
- Declarations: _AI_REPLY_CONTEXT_STORE_PREFIX, _aiReply, _clearDocAiReplyContext, _closeDocAiReplyChoice, _docAiReplyChoiceMenu, _docAiReplyContextKey, _emailAiReplyGeneration, _loadDocAiReplyContext, _saveDocAiReplyContext, _showDocAiReplyChoice, generateEmailReply
- Exports referenced elsewhere: _showDocAiReplyChoice, generateEmailReply
- Imports from: application/documentState, ui/email/emailBody, ui/host, ui/richEditor/richBodySync

## ui/richEditor/imageOps  (level 4, wave 4, ~174 lines)
- Declarations: _clearRichImageSelection, _deleteRichImage, _insertRichTextImages, _normalizeRichTextImages, _replaceRichImage, _replaceRichImageFigure, _richImageInsertRange, _selectRichImage
- Exports referenced elsewhere: _clearRichImageSelection, _deleteRichImage, _insertRichTextImages, _normalizeRichTextImages, _replaceRichImage, _replaceRichImageFigure, _richImageInsertRange, _selectRichImage
- Imports from: domain/documentKinds, ui/richEditor/richBodySync, ui/richEditor/richOperations

## ui/editor/markdownImages  (level 5, wave 4, ~80 lines)
- Declarations: _handleMarkdownImageUpload, _insertMarkdownImages, _scheduleMarkdownImageAutosave, _uploadMarkdownImages
- Exports referenced elsewhere: _handleMarkdownImageUpload, _uploadMarkdownImages
- Imports from: application/documentState, application/persistence, domain/documentKinds, ui/documentActions, ui/editor/sourceEditor, ui/richEditor/imageOps, ui/richEditor/richBodySync, ui/status/documentStatus

## ui/email/sendWorkflow  (level 5, wave 5, ~529 lines)
- Declarations: _bodyMentionsAttachment, _clearMissingAttachmentWarnings, _confirmMissingAttachment, _discardEmail, _emailAccountsCache, _emailAccountsCacheAt, _emailSendInFlight, _getEmailAccountsCached, _markUnreadAndClose, _resolveComposeSendAccountId, _saveDraft, _saveEmailDraftForRecovery, _scheduleSend, _sendEmail
- Exports referenced elsewhere: _discardEmail, _markUnreadAndClose, _saveDraft, _scheduleSend, _sendEmail
- Imports from: application/documentState, application/persistence, domain/emailMessage, ui/documentFlows, ui/email/emailBody, ui/email/localDraft, ui/host, ui/richEditor/richBodySync

## ui/richEditor/links  (level 5, wave 4, ~342 lines)
- Declarations: _applyRichImageAction, _editRichImageAlt, _editRichImageCaption, _promptImageAlt, _promptImageCaption, _promptLink, _removeRichLink, _restoreRichLinkRange, _richLinkAtRange, _wysiwygInsertLink
- Exports referenced elsewhere: _applyRichImageAction, _editRichImageAlt, _editRichImageCaption, _removeRichLink, _restoreRichLinkRange, _richLinkAtRange, _wysiwygInsertLink
- Imports from: domain/richTextRules, ui/richEditor/imageOps, ui/richEditor/richBodySync, ui/richEditor/richOperations

## ui/selection/selectionHighlights  (level 5, wave 4, ~553 lines)
- Declarations: _ensureSelResizeObserver, _isCodeDoc, _measurePos, _richRangeFromOffsets, _richRootText, _richSelectionHighlightName, _scheduleSelRerender, _selResizeObserver, _selResizeScheduled, _selections, _validateSelections, clearSelection, clearSelectionAt, getSelectionContext, renderAllSelectionHighlights, restoreSelectionReference, showSelectionBadge, stmt#11153, syncSelectionOverlay, updateRichSelectionState, updateSelectionState
- Exports referenced elsewhere: _measurePos, _scheduleSelRerender, _selResizeObserver, _selections, clearSelection, syncSelectionOverlay, updateRichSelectionState, updateSelectionState
- Imports from: application/documentState, domain/documentKinds, index, ui/documentFlows, ui/richEditor/richBodySync, ui/status/documentStatus

## ui/richEditor/formatting  (level 6, wave 4, ~647 lines)
- Declarations: _applyHeadingToggle, _applyLinePrefixToggle, _applyOrderedList, _applyRichDefaultTextColor, _applyRichFontFamily, _applyRichHighlight, _applyRichPixelFontSize, _applyWrapToggle, _clearRichSelectionFormatting, _currentBlockTag, _insertRichPageBreak, _lastMdFormat, _normalizedRichMenuColor, _richContrastTextColor, _richMenuColorHex, _richSelectionHasFormatting, _saveFormatSelection, _savedFormatRichRange, _savedFormatTextareaSelection, applyMdFormat
- Exports referenced elsewhere: _currentBlockTag, _normalizedRichMenuColor, _richMenuColorHex, _richSelectionHasFormatting, _saveFormatSelection, applyMdFormat
- Imports from: domain/richTextRules, ui/editor/sourceEditor, ui/richEditor/links, ui/richEditor/richBodySync, ui/richEditor/richOperations, ui/status/documentStatus

## ui/richEditor/formatMenus  (level 7, wave 4, ~526 lines)
- Declarations: _mdDdActivationSerial, _mdDdLastActivation, _richColorMenuItems, _richDropdownCurrentActions, _showMdDropdown, nextMdDropdownActivationToken
- Exports referenced elsewhere: _mdDdActivationSerial, _richDropdownCurrentActions, _showMdDropdown, nextMdDropdownActivationToken
- Imports from: application/documentState, domain/richTextRules, ui/richEditor/formatting, ui/richEditor/links, ui/richEditor/richBodySync, ui/richEditor/richOperations

## ui/richEditor/slashMenu  (level 7, wave 4, ~238 lines)
- Declarations: _applyRichBlockInputRule, _chooseRichSlashCommand, _hideRichSlashMenu, _renderRichSlashMenu, _richSlashActiveIndex, _richSlashClose, _richSlashCommands, _richSlashContext, _richSlashMenu, _richSlashOwner, _syncRichSlashActiveOption, _syncRichSlashMenu
- Exports referenced elsewhere: _applyRichBlockInputRule, _chooseRichSlashCommand, _hideRichSlashMenu, _renderRichSlashMenu, _richSlashActiveIndex, _richSlashContext, _richSlashMenu, _syncRichSlashMenu
- Imports from: application/documentState, domain/richTextRules, ui/richEditor/formatting, ui/richEditor/richBodySync, ui/richEditor/richOperations

## ui/richEditor/imageMenus  (level 8, wave 4, ~138 lines)
- Declarations: _ensureRichImageToolbar, _hideRichImageToolbar, _insertRichGalleryImage, _richImageToolbar, _showRichImageSourceMenu
- Exports referenced elsewhere: _showRichImageSourceMenu
- Imports from: application/documentState, domain/documentKinds, ui/richEditor/formatMenus, ui/richEditor/imageOps, ui/richEditor/richBodySync, ui/richEditor/richOperations

## ui/email/attachments  (level 9, wave 5, ~390 lines)
- Declarations: _afterOdysseusAttachmentsAdded, _attachMenuEscape, _attachMenuOutsideClick, _attachOdysseusItem, _attachSelectedOdysseusItems, _closeOdysseusAttachMenu, _handleAttachUpload, _loadOdysseusAttachItems, _odysseusAttachLabel, _odysseusAttachMenu, _positionOdysseusAttachMenu, _renderComposeAttachments, _selectedOdysseusAttachRows, _showComposeAttachMenu, _stageForwardedSourceAttachments, _stageOdysseusAttachment, _stageOdysseusZip, _syncOdysseusAttachSelection, _uploadComposeFiles
- Exports referenced elsewhere: _handleAttachUpload, _renderComposeAttachments, _showComposeAttachMenu, _stageForwardedSourceAttachments, _uploadComposeFiles
- Imports from: application/documentState, application/persistence, domain/documentKinds, ui/richEditor/imageMenus, ui/status/documentStatus

## ui/richEditor/selectionToolbar  (level 9, wave 4, ~214 lines)
- Declarations: _ensureRichSelectionToolbar, _followRichSelectionToolbarLayout, _hideRichSelectionToolbar, _renderRichSelectionToolbar, _restoreRichSelectionToolbarRange, _richSelectionToolbar, _richSelectionToolbarButton, _richSelectionToolbarFollowFrame, _richSelectionToolbarFrame, _richSelectionToolbarRange, _scheduleRichSelectionToolbar
- Exports referenced elsewhere: _followRichSelectionToolbarLayout, _hideRichSelectionToolbar, _richSelectionToolbar, _scheduleRichSelectionToolbar
- Imports from: ui/richEditor/formatting, ui/richEditor/imageMenus, ui/richEditor/imageOps, ui/richEditor/links

## ui/email/signedReply  (level 10, wave 5, ~97 lines)
- Declarations: _sendSignedReply
- Exports referenced elsewhere: _sendSignedReply
- Imports from: application/documentSession, application/documentState, ui/documentFlows, ui/email/attachments

## ui/richEditor/richBodyWiring  (level 10, wave 4, ~389 lines)
- Declarations: _showRichTextEditor, _wireEmailRichbody
- Exports referenced elsewhere: _showRichTextEditor, _wireEmailRichbody
- Imports from: application/documentState, domain/documentKinds, domain/richTextRules, ui/documentActions, ui/editor/markdownImages, ui/richEditor/formatMenus, ui/richEditor/formatting, ui/richEditor/imageOps, ui/richEditor/links, ui/richEditor/richBodySync, ui/richEditor/richOperations, ui/richEditor/selectionToolbar, ui/richEditor/slashMenu, ui/selection/selectionHighlights, ui/status/documentStatus

## ui/toolbar/toolbar  (level 10, wave 4, ~446 lines)
- Declarations: _AI_WRITING_ACTIONS, _DOCUMENT_TOOLBAR_GROUPS, _aiWritingButtonMarkup, _aiWritingSelectionText, _closeAiWritingMenu, _orderDocumentToolbar, _setAiWritingLoading, _showAiWritingMenu, _syncDocumentToolbarSeparators, initMdToolbar
- Exports referenced elsewhere: _syncDocumentToolbarSeparators, initMdToolbar
- Imports from: application/documentState, application/persistence, ui/panel/paneLayout, ui/richEditor/formatMenus, ui/richEditor/formatting, ui/richEditor/richBodySync, ui/richEditor/selectionToolbar

## ui/email/composerFields  (level 11, wave 5, ~328 lines)
- Declarations: _emailHeaderManualExpandUntil, _hideEmailFields, _maybeAutoCollapseEmailHeader, _setEmailHeaderCollapsed, _setEmailHeaderInputValue, _shouldAutoCollapseEmailHeader, _showEmailFields, _syncEmailHeaderSummary, _syncStreamingEmailFields
- Exports referenced elsewhere: _hideEmailFields, _maybeAutoCollapseEmailHeader, _setEmailHeaderCollapsed, _showEmailFields, _syncEmailHeaderSummary, _syncStreamingEmailFields
- Imports from: application/documentState, domain/documentKinds, domain/emailMessage, ui/documentFlows, ui/editor/sourceEditor, ui/email/attachments, ui/email/emailBody, ui/email/localDraft, ui/richEditor/richBodyWiring, ui/richEditor/selectionToolbar, ui/richEditor/slashMenu

## ui/header/headerActions  (level 11, wave 7, ~466 lines)
- Declarations: _syncHeaderActions, _syncHeaderBarVisibility
- `openPanel` blocks: openPanel#6801, openPanel#6802, openPanel#6805, openPanel#6806, openPanel#6807, openPanel#6808, openPanel#6813, openPanel#6817, openPanel#6820, openPanel#6821, openPanel#6823, openPanel#6824, openPanel#7376, openPanel#7394, openPanel#7403, openPanel#7408, openPanel#7443, openPanel#7444, openPanel#7445, openPanel#7446, openPanel#7447, openPanel#7448, openPanel#7449, openPanel#7469, openPanel#7471, openPanel#7479, openPanel#7480, openPanel#7504, openPanel#7505, openPanel#7526, openPanel#7527
- Exports referenced elsewhere: _syncHeaderActions (command)
- Imports from: application/documentState, application/persistence, domain/documentKinds, ui/diffViewer, ui/documentActions, ui/documentFlows, ui/editor/sourceEditor, ui/email/signedReply, ui/exportDocx, ui/header/overflow, ui/history/versionHistory, ui/panel/paneLayout, ui/pdfTools, ui/previewModes, ui/richEditor/richBodySync, ui/selection/selectionHighlights, ui/status/documentStatus, ui/toolbar/toolbar

## ui/tabs/tabBar  (level 11, wave 6, ~507 lines)
- Declarations: _closeDocTabMenu, _docTabMenu, _isEditingTabTitle, _syncDocIndicator, initTabDragReorder, renderTabs, showDocTabMenu, showEmptyState, startTitleEdit, stmt#206, updateArrowVisibility
- Exports referenced elsewhere: _closeDocTabMenu, _docTabMenu, _syncDocIndicator (command), renderTabs (command), showEmptyState (command)
- Imports from: application/documentState, application/persistence, domain/documentKinds, ui/documentActions, ui/documentFlows, ui/editor/sourceEditor, ui/email/signedReply, ui/history/versionHistory, ui/panel/paneLayout, ui/status/documentStatus

## ui/aiUpdates/liveStream  (level 12, wave 6, ~259 lines)
- Declarations: _animateDocEdit, _editAnimFrame, _streamDocId, _streamHlDebounce, _syncStreamDocChrome, streamDocDelta, streamDocFinalize, streamDocOpen
- Exports referenced elsewhere: streamDocFinalize
- Imports from: application/documentState, application/persistence, domain/emailMessage, ui/editor/sourceEditor, ui/email/composerFields, ui/email/emailBody, ui/history/versionHistory, ui/status/documentStatus

## ui/email/composerWiring  (level 12, wave 7, ~252 lines)
- Declarations: none
- `openPanel` blocks: openPanel#7095, openPanel#7096, openPanel#7104, openPanel#7121, openPanel#7122, openPanel#7130, openPanel#7131, openPanel#7138, openPanel#7160, openPanel#7161, openPanel#7171, openPanel#7187, openPanel#7205, openPanel#7240, openPanel#7244, openPanel#7245, openPanel#7249, openPanel#7254, openPanel#7260, openPanel#7261, openPanel#7277, openPanel#7286, openPanel#7287, openPanel#7293, openPanel#7294, openPanel#7304, openPanel#7316, openPanel#7319, openPanel#7328, openPanel#7333, openPanel#7334, openPanel#7337, openPanel#7349, openPanel#7371, openPanel#7372, openPanel#7373
- Exports referenced elsewhere: none
- Imports from: application/documentState, application/persistence, domain/documentKinds, ui/editor/markdownImages, ui/email/aiReply, ui/email/attachments, ui/email/composerFields, ui/email/emailBody, ui/email/localDraft, ui/email/recipients, ui/email/sendWorkflow, ui/richEditor/imageMenus, ui/richEditor/richBodySync, ui/status/documentStatus

## ui/email/emailDocuments  (level 12, wave 5, ~168 lines)
- Declarations: ensureEmailDraftEnvelope, findEmailDocId, getActiveEmailComposerContext, openEmailDraft, replaceEmailReplyBody
- Exports referenced elsewhere: none
- Imports from: application/documentState, application/persistence, domain/emailMessage, ui/documentFlows, ui/email/composerFields, ui/email/emailBody, ui/richEditor/richBodySync

## ui/header/languagePicker  (level 12, wave 7, ~257 lines)
- Declarations: none
- `openPanel` blocks: openPanel#6826, openPanel#6834, openPanel#6853, openPanel#6854, openPanel#7009
- Exports referenced elsewhere: none
- Imports from: application/documentState, application/persistence, domain/documentKinds, domain/emailMessage, ui/documentActions, ui/editor/sourceEditor, ui/email/composerFields, ui/richEditor/richBodySync, ui/richEditor/richBodyWiring, ui/status/documentStatus

## ui/moduleWiring  (level 12, wave 7, ~105 lines)
- Declarations: _disposePdfTools, _wireDiffViewer, _wireExportDocx, _wireFindReplace, _wirePdfTools, _wirePreviewModes, _wireSuggestions, stmt#241, stmt#260
- Exports referenced elsewhere: _disposePdfTools, _wireDiffViewer, _wireFindReplace, _wirePdfTools, _wireSuggestions
- Imports from: application/documentCommands, application/documentSession, application/documentState, application/persistence, domain/documentKinds, ui/diffViewer, ui/documentFlows, ui/editor/sourceEditor, ui/email/composerFields, ui/exportDocx, ui/findReplace, ui/panel/paneLayout, ui/pdfTools, ui/previewModes, ui/richEditor/richBodySync, ui/richEditor/richBodyWiring, ui/selection/selectionHighlights, ui/suggestions

## ui/panel/documentPresenter  (level 12, wave 7, ~236 lines)
- Declarations: saveCurrentToMap, switchToDoc
- Exports referenced elsewhere: saveCurrentToMap (command), switchToDoc (command)
- Imports from: application/documentCommands, application/documentState, application/persistence, domain/documentKinds, domain/emailMessage, ui/diffViewer, ui/documentFlows, ui/editor/sourceEditor, ui/email/composerFields, ui/email/localDraft, ui/history/versionHistory, ui/pdfTools, ui/previewModes, ui/richEditor/richBodySync, ui/richEditor/richBodyWiring, ui/richEditor/selectionToolbar, ui/richEditor/slashMenu, ui/selection/selectionHighlights, ui/status/documentStatus, ui/suggestions

## ui/aiUpdates/documentUpdate  (level 13, wave 6, ~387 lines)
- Declarations: _animationCancel, animateDocChange, handleDocUpdate, lineDiff
- Exports referenced elsewhere: none
- Imports from: application/documentState, domain/documentKinds, domain/emailMessage, ui/aiUpdates/liveStream, ui/documentActions, ui/editor/sourceEditor, ui/email/composerFields, ui/email/emailBody, ui/email/localDraft, ui/history/versionHistory, ui/richEditor/richBodyWiring, ui/status/documentStatus

## ui/editor/sourceEditorBinding  (level 13, wave 7, ~221 lines)
- Declarations: none
- `openPanel` blocks: openPanel#7562, openPanel#7563, openPanel#7564
- Exports referenced elsewhere: none
- Imports from: application/documentCommands, application/documentState, application/persistence, domain/documentKinds, ui/diffViewer, ui/documentActions, ui/documentFlows, ui/editor/markdownImages, ui/editor/sourceEditor, ui/email/attachments, ui/email/localDraft, ui/findReplace, ui/moduleWiring, ui/richEditor/formatting, ui/richTables, ui/selection/selectionHighlights, ui/status/documentStatus

## ui/panel/panelLifecycle  (level 13, wave 7, ~484 lines)
- Declarations: _ensureDocChipRegistered, _hasMountedPanel, _hideLoadingOverlay, _loadingSpinner, _minimizeNotesForDocumentOpen, _showLoadingOverlay, closePanel, newDocument, openPanel, prepareDocumentOpen, stmt#11782, swapSide
- `openPanel` blocks: openPanel#6178, openPanel#6179, openPanel#6183, openPanel#6184, openPanel#6185, openPanel#6189, openPanel#6193, openPanel#6194, openPanel#6196, openPanel#6199, openPanel#6200, openPanel#6202, openPanel#6205, openPanel#6206, openPanel#6207, openPanel#6208, openPanel#6212, openPanel#6213, openPanel#6214, openPanel#6223, openPanel#6225, openPanel#6226, openPanel#6232, openPanel#6233, openPanel#6234, openPanel#6235, openPanel#6236, openPanel#6237, openPanel#6255, openPanel#6537, openPanel#6579, openPanel#6623, openPanel#6627, openPanel#6628, openPanel#6629, openPanel#6642, openPanel#6643, openPanel#7559, openPanel#7784, openPanel#7787
- Exports referenced elsewhere: _ensureDocChipRegistered (command), _hideLoadingOverlay (command), _minimizeNotesForDocumentOpen (command), _showLoadingOverlay (command), closePanel (command), openPanel (command)
- Imports from: application/documentSession, application/documentState, application/persistence, ui/diffViewer, ui/documentFlows, ui/editor/sourceEditor, ui/email/localDraft, ui/history/versionHistory, ui/moduleWiring, ui/panel/documentPresenter, ui/panel/paneLayout, ui/pdfTools, ui/richEditor/richBodySync, ui/richEditor/selectionToolbar, ui/richEditor/slashMenu, ui/selection/selectionHighlights, ui/status/documentStatus, ui/suggestions, ui/tabs/tabBar, ui/toolbar/toolbar

## index (composition root, ~300-450 lines when authored)
- Declarations assigned from today's file: init, _maybeOpenDocFromHash, clearAll, getCurrentDocId, documentModule, stmt#13543, stmt#13545, stmt#13546

## Lane C mount and lifecycle interfaces

These interfaces wrap the assigned `openPanel` statement ranges and keep their
execution at the original mount positions. They are the only new named exports
needed to call the extracted children. The two disposal operations keep the
line-number, selection, and PDF lifecycle state writable only by their owner.

| Module | Additional exports |
| --- | --- |
| `ui/panel/paneTemplate` | `mountPaneTemplate` |
| `ui/panel/paneLayout` | `bindPaneLayoutPointerSupport`, `bindPaneLayoutControls` |
| `ui/header/headerActions` | `bindHeaderActionsPrimary`, `bindHeaderActionsSecondary` |
| `ui/header/languagePicker` | `bindLanguagePicker` |
| `ui/email/composerWiring` | `bindComposerWiring` |
| `ui/editor/sourceEditorBinding` | `bindSourceEditor` |
| `ui/panel/panelLifecycle` | `installEscapeLayering` |
| `ui/panel/panelLifecycle` | `newDocument`, `prepareDocumentOpen`, `swapSide` |
| `ui/moduleWiring` | `activateModuleWiring`, `disposePdfTools`, `_wireExportDocx`, `_wirePreviewModes` |
| `ui/editor/sourceEditor` | `disposeSourceEditor`, `scheduleSourceEditorWork` |
| `ui/selection/selectionHighlights` | `disposeSelectionHighlights` |
| `ui/header/overflow` | `_syncOverflow` |
