# Baymax Chat – VS Code Extension

Chat with your company RAG system (Baymax) without leaving VS Code.

## Requirements

1. Start the Baymax API server first:
   ```bash
   # from the baymax-rag-system root
   .\venv\Scripts\uvicorn api:app --host 127.0.0.1 --port 8888 --reload
   ```

2. Open this extension folder in VS Code and run **Tasks: Run Build Task** (`Ctrl+Shift+B`) to compile, then press **F5** to launch the Extension Development Host.

## Usage

- Click the **Baymax** icon in the **Activity Bar** (left sidebar) to open the chat panel.
- Type your question and press **Enter** to send (Shift+Enter for a newline).
- A green dot in the header means the API is reachable; red means it's offline.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `baymax.apiUrl` | `http://127.0.0.1:8888` | URL of the Baymax FastAPI server |
| `baymax.showSources` | `true` | Show source document references |

## Commands

| Command | Description |
|---------|-------------|
| `Baymax: Open Chat` | Focus the chat panel |
| `Baymax: Clear Conversation History` | Wipe current session |
| `Baymax: Check API Health` | Show model + graph status |

## Building a .vsix package

```bash
npm install -g @vscode/vsce
cd vscode-extension
npm install
npm run compile
vsce package
```
