const ALLOWED_CONTAINERS = ['#chat-history', '#doc-editor-pane'];
function isAllowed(sel) {
    const node = sel.anchorNode;
    return ALLOWED_CONTAINERS.some(container => {
        document.querySelector(selector)?.contains(node)
    });
}

document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    const text = sel.toString().trim()

    if (!text || !isAllowed(sel)) {
        // TODO: hide pop-up
        return;
    }
   // TODO: show pop-up 
   console.log('Selected text:', text); // testing purposes
});