/**
 * extension.ts — Entry point for the Baymax Chat VS Code extension.
 */

import * as vscode from "vscode";
import { ChatPanelProvider } from "./chatPanel";

let statusBarItem: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {
  console.log("[Baymax] Extension activated");

  // ── Status bar ──────────────────────────────────────────────────────────────
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBarItem.text = "$(comment-discussion) Baymax";
  statusBarItem.tooltip = "Open Baymax Chat";
  statusBarItem.command = "baymax.openChat";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // ── Webview provider ─────────────────────────────────────────────────────────
  const provider = new ChatPanelProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("baymax.chatView", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  // ── Commands ─────────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("baymax.openChat", () => {
      vscode.commands.executeCommand("baymax.chatView.focus");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("baymax.clearHistory", () => {
      provider.clearHistory();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("baymax.checkHealth", async () => {
      const status = await provider.checkHealth();
      if (status) {
        vscode.window.showInformationMessage(
          `Baymax API model: ${status.chat_model}  graph: ${status.use_graph}`
        );
      }
    })
  );
}

export function deactivate() {
  statusBarItem?.dispose();
}
