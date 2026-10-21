# Translating Proxy Bridge

Proxy Bridge uses the standard Home Assistant translation layout, so anything
you already know about HA translations applies.

## Where the strings live

```
custom_components/proxy_bridge/strings.json          ← source of truth (English)
custom_components/proxy_bridge/translations/en.json  ← generated copy of strings.json
custom_components/proxy_bridge/translations/<lang>.json
bootstrap/custom_components/proxy_bridge_bootstrap/strings.json
bootstrap/custom_components/proxy_bridge_bootstrap/translations/<lang>.json
i18n/languages.json                                  ← language registry
```

Both integrations must have the same language set. The bootstrap copy has a
few strings fewer (no YAML import, no repair issue, no service) plus a short
"Bootstrap copy" note at the top of the step descriptions.

## The language registry

`i18n/languages.json` describes each language:

```json
{
  "fr": {
    "name": "French",
    "native_name": "Français",
    "status": "complete",
    "maintainers": ["@someone"]
  }
}
```

`status` is one of:

| value | meaning | `validate.py --release` |
| --- | --- | --- |
| `source` | English, the source language | allowed |
| `complete` | reviewed, every key present | allowed |
| `needs-translation` | work in progress or stale | **refused** |

`check` treats a language with missing keys or placeholder mismatches as
failing; a `needs-translation` file may be incomplete, but then it must be
marked as such in the registry.

## Human workflow

```bash
python scripts/i18n.py scaffold fr     # creates both translations/fr.json files
# ... translate the "value" strings, keep the "key" strings ...
python scripts/i18n.py check           # key + placeholder parity
python scripts/validate.py             # full repository validation
```

Then edit `i18n/languages.json`: set `status` to `complete` (or leave
`needs-translation` for a partial first pass) and add your handle to
`maintainers`.

Commit, push, open a PR titled `Add French translation`. CI runs the same two
commands.

## What to keep unchanged

- **Keys** — `config.step.connection.title` must stay `config.step.connection.title`.
- **Placeholders** — `{name}`, `{count}`, `{error}` must appear exactly as in
  English, including braces and spelling. `check` fails otherwise.
- **Markdown** — `**bold**`, backticks and list items should be preserved.
  Home Assistant renders translations as Markdown.
- **Selector option values** — `http`, `https`, `socks5`, `socks5h` are
  protocol names; translate only the surrounding label if needed, never the
  value.
- **Technical nouns** — "GitHub", "HACS", "Home Assistant", "SOCKS5",
  "TLS", "Basic auth", "CONNECT" are usually left as-is.

## Style rules

- Use the same wording Home Assistant itself uses for UI concepts in your
  language ("Settings", "Devices & services", "Add integration", "Repair").
  Home Assistant's own translations are a good reference.
- Describe what the user does, not the implementation: "Domains to route",
  not "regex host list".
- Keep strings short — wizard labels are narrow.
- Use the language's own punctuation (French « », Japanese 。, etc.).
- Write UTF-8. `scripts/i18n.py build` normalises the JSON formatting.

## For an AI agent

Everything you need is in the repository; do not guess at the schema.

1. Read `custom_components/proxy_bridge/strings.json`. It is the English
   source. Read `bootstrap/custom_components/proxy_bridge_bootstrap/strings.json`
   for the bootstrap-specific strings.
2. Run `python scripts/i18n.py scaffold <code>` where `<code>` is a BCP-47
   language tag (`de`, `fr`, `pt-BR`, `zh-Hant`). This creates
   `translations/<code>.json` in both integrations with English values.
3. Translate **values only**. Do not add, remove or rename keys. Keep every
   `{placeholder}` verbatim and keep Markdown markers.
4. Run `python scripts/i18n.py check`. Fix anything it reports; do not edit the
   checker.
5. Add the language to `i18n/languages.json` with `native_name`,
   `status: "needs-translation"` while partial, and your identifier in
   `maintainers`.
6. Run `python -m pytest -q` and `python scripts/validate.py`.
7. Open a PR containing only: the new translation files, the registry entry,
   and a one-line note in `docs/TRANSLATING.md` if the language needs a
   particular convention.

If you are asked to *review* a translation, check placeholders first
(`python scripts/i18n.py check`), then read the rendered wizard text in your
head: a good translation lets a non-technical user finish the two steps without
reading English.

## Partial translations

Missing keys fall back to English, so a partial file is usable. Mark it
`needs-translation` and keep the file in the PR; someone else can finish it.
A partial file must never claim `complete`.
