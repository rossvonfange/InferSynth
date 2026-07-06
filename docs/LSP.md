# InferSynth language server (WP6)

`infersynth lsp` runs a stdio LSP server over `infersynth.lint` (UX.md
"Editor (LSP)" surface). It is a thin adapter: no lint/synthesis logic
lives in `infersynth/lsp/` — `infersynth/lsp/core.py` re-shapes results
from `infersynth.lint` (and `infersynth.lint.vocab.Vocabulary`) into
LSP-ish plain-Python values, and `infersynth/lsp/server.py` is the only
module that talks to `pygls`/`lsprotocol`.

Install the optional dependency first:

```
pip install infersynth[lsp]
```

Run it:

```
infersynth lsp --catalog catalog
```

Without `--catalog` the server still starts and publishes EARS-grammar
diagnostics, but catalog-vocabulary completions, hovers, and disambiguation
code actions are unavailable (no vocabulary to draw from).

## What's implemented

- **Diagnostics** on `textDocument/didOpen` and `textDocument/didChange`,
  lint run on the in-memory buffer (`infersynth.lint.lint_text` — no saved
  file required). No debounce (v0, per BUILD_PLAN).
- **Completions** (`textDocument/completion`): one item per catalog idiom
  keyword phrase and one per idiom param name, generated from the installed
  catalog. Item `detail` is the owning cell's `name@version`; `documentation`
  is the cell description plus a param range table.
- **Hover** (`textDocument/hover`): when the word under the cursor matches an
  idiom keyword token or param name, shows the cell's description, full
  param table (name/type/range/default), and depth level.
- **Code actions** (`textDocument/codeAction`): for `frd.ambiguous`
  diagnostics, one "Specify `<cell>`" action per candidate cell, inserting a
  short disambiguating parameter assignment (or the cell's own keyword) at
  the diagnostic's range end. For `frd.no-primitive`, one "Insert
  catalog-gap stub" action that appends the diagnostic's catalog-gap stub
  text as a comment block under the requirement.

Not implemented (out of scope for v0, per BUILD_PLAN): diagnostics
debouncing, `workspace/didChangeConfiguration`, go-to-definition, and any
editor/e2e integration testing — tests invoke handlers directly (see
`tests/test_lsp_server.py`).

## VS Code (generic LSP client)

VS Code has no bundled "run any stdio LSP server" extension; the
lightest-weight option is the generic
[`vscode-glsp`](https://open-vsx.org/) style client or a tiny custom
extension. If you already use an extension that supports arbitrary LSP
servers via settings (e.g. some "Generic LSP Client" extensions on the
marketplace), point it at:

```jsonc
{
  "genericLanguageClient.servers": [
    {
      "languageId": "markdown",
      "command": "infersynth",
      "args": ["lsp", "--catalog", "${workspaceFolder}/catalog"],
      "filePattern": "**/*.frd.md"
    }
  ]
}
```

Adjust the `filePattern`/`languageId` keys to whatever your chosen client
extension expects — the important part is the command line:
`infersynth lsp --catalog <catalog-dir>`, stdio transport.

## Neovim (`nvim-lspconfig`)

`infersynth` isn't a built-in `lspconfig` server, so register it as a
custom one:

```lua
local lspconfig = require('lspconfig')
local configs = require('lspconfig.configs')

if not configs.infersynth then
  configs.infersynth = {
    default_config = {
      cmd = { 'infersynth', 'lsp', '--catalog', vim.fn.getcwd() .. '/catalog' },
      filetypes = { 'markdown' },
      root_dir = lspconfig.util.root_pattern('catalog', '.git'),
      settings = {},
    },
  }
end

lspconfig.infersynth.setup({})
```

Attach it only to your FRD buffers (e.g. via an autocommand matching
`*.frd.md`) if you don't want it competing with another markdown server.
