"""Reading source structure, for any language a grammar exists for.

Nothing here resolves a call to a target. tree-sitter answers what is
defined, where it sits, what the signature and doc line are, where the call
sites are, and whether each one sits inside a conditional or a loop. Those
answers are the same whoever is doing the resolving, so this package is
shared by every tier: `structural` guesses from names alone, `scip` reads an
index, `lsp` asks a language server, and all three start here.
"""
