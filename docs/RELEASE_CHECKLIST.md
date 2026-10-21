# Release checklist

## 1. Confirm repository metadata

The manifests, READMEs, docs and `i18n/languages.json` already point at
`Setsuna-Kitahara/proxy-bridge`. If you fork the project or move it to another
owner, update them and grep for leftovers first:

```bash
grep -rn "Setsuna-Kitahara" --exclude-dir=.git .
```

## 2. Bump the version

Two places, they must match:

- `custom_components/proxy_bridge/const.py` → `VERSION`
- `bootstrap/custom_components/proxy_bridge_bootstrap/const.py` → `VERSION`

Also update `manifest.json` `"version"` in both packages (the validator compares
them). For a pre-release, keep a `-dev` suffix and `validate.py --release` will
refuse it — that is on purpose.

## 3. Run everything

```bash
python -m pytest -q
ruff check . && ruff format --check .
python scripts/validate.py --release
python scripts/build_dist.py --release
```

`--release` additionally rejects:

- leftover `YOUR_GITHUB_USERNAME` placeholders (forks only),
- versions containing `-dev`,
- languages marked `needs-translation`.

## 4. Publish the release

`scripts/build_dist.py` writes `dist/RELEASE_NOTES.md`, which already contains
the SHA-256 of both archives and the install instructions. Attach the archives
and `SHA256SUMS` to the GitHub release and use the notes file as the body:

```bash
gh release create v1.0.0 \
  dist/proxy_bridge-1.0.0.zip \
  dist/proxy_bridge_bootstrap-1.0.0.zip \
  dist/SHA256SUMS \
  --title "Proxy Bridge 1.0.0" \
  --notes-file dist/RELEASE_NOTES.md
```

The same checksums are written into the "Verifying your download" table of
`README.md` and `README.zh-Hans.md` by the build script - do not edit that block
by hand, rerun the build instead. `scripts/validate.py` fails when the table no
longer matches `dist/SHA256SUMS`.

## 5. Smoke-test on a real Home Assistant

- Install the bootstrap zip by hand → configure → HACS downloads.
- Install the managed copy → import from bootstrap → delete the bootstrap copy.
- Check *Settings → System → Repairs* is clean.
- Check `proxy_bridge.probe` returns `ok: true` for a blocked host.
- Download diagnostics; confirm the password is absent.

## 5. Publish

```bash
git tag v1.0.0
git push origin main --tags
gh release create v1.0.0 dist/*.zip dist/SHA256SUMS \
  --title "Proxy Bridge 1.0.0" --notes-file release-notes.md
```

Release notes must mention: new proxy types, credential support, the two
packages and which one to download when GitHub is unreachable, and any
translation status changes.

## 6. HACS

- The repository must have `hacs.json` (already present).
- Add the repository to HACS as a custom repository for testing, then open a PR
  to `hacs/default` if it should become default.
- The `hacs/action` CI job validates `hacs.json` and the integration layout.

## 7. After the release

- Verify the release assets are downloadable without authentication.
- Update `docs/BOOTSTRAP.md` if asset names changed.
- Announce the translation workflow so contributors can add languages.
