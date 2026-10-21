## What does this change?

<!-- One or two sentences. -->

## Checklist

- [ ] `python -m pytest -q` passes
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `python scripts/validate.py` passes
- [ ] If `proxy_core.py` changed: both copies are byte-identical (`cp`)
- [ ] If a string changed: `python scripts/i18n.py build` was run and every
      language still passes `python scripts/i18n.py check`
- [ ] If a language was added: it is registered in `i18n/languages.json`
- [ ] Docs updated when behaviour changed
