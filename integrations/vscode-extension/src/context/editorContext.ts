import * as vscode from "vscode";

export interface EditorSelectionContext {
  filePath: string;
  languageId: string;
  selection: string;
}

export interface CurrentFileContext {
  filePath: string;
  languageId: string;
  content: string;
}

const SELECTION_LIMIT = 8000;
const FILE_CONTEXT_LIMIT = 16000;

export function getSelectionContext(): EditorSelectionContext | null {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) {
    return null;
  }

  return {
    filePath: editor.document.uri.fsPath,
    languageId: editor.document.languageId,
    selection: editor.document.getText(editor.selection).slice(0, SELECTION_LIMIT),
  };
}

export function getCurrentFileContext(): CurrentFileContext | null {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return null;
  }

  return {
    filePath: editor.document.uri.fsPath,
    languageId: editor.document.languageId,
    content: editor.document.getText().slice(0, FILE_CONTEXT_LIMIT),
  };
}

export function getCurrentFileLabel(): string | null {
  const file = getCurrentFileContext();
  return file ? `${file.filePath} (${file.languageId})` : null;
}

export async function insertCodeAtCursor(code: string): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    throw new Error("Open an editor before inserting code.");
  }

  await editor.edit((editBuilder) => {
    if (editor.selection && !editor.selection.isEmpty) {
      editBuilder.replace(editor.selection, code);
      return;
    }
    editBuilder.insert(editor.selection.active, code);
  });
}

export async function previewCodeDiff(code: string, label: string): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    throw new Error("Open an editor before previewing a diff.");
  }

  const original = editor.document.getText();
  const selection = editor.selection;
  const nextContent =
    selection && !selection.isEmpty
      ? original.slice(0, editor.document.offsetAt(selection.start)) +
        code +
        original.slice(editor.document.offsetAt(selection.end))
      : original.slice(0, editor.document.offsetAt(selection.active)) +
        code +
        original.slice(editor.document.offsetAt(selection.active));

  const modified = await vscode.workspace.openTextDocument({
    content: nextContent,
    language: editor.document.languageId,
  });
  const title = `${label || "Odysseus Suggestion"} ↔ ${editor.document.fileName.split("/").pop() || "Current File"}`;
  await vscode.commands.executeCommand("vscode.diff", editor.document.uri, modified.uri, title, {
    preview: true,
  });
}
