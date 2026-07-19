# Word dictionary data

`dictionary.json` and `inflections.json` are tracked source files. SCons
compiles them into `addon/globalPlugins/polyglot/common/resources/dictionary.pickle`.
The JSON files are not included in the add-on package.

## Update the dictionary

Run these commands from the project root. Keep the ECDICT checkout at
`tools/resource/ECDICT`; that directory is ignored by Git.

Clone it once:

```powershell
git clone --depth 1 https://github.com/skywind3000/ECDICT tools/resource/ECDICT
```

Update an existing checkout when needed:

```powershell
git -C tools/resource/ECDICT pull --ff-only
```

Prepare entries for review:

```powershell
python tools/updateWordDictionary.py prepare
```

This writes missing lowercase headwords to the ignored
`tools/resource/candidates.json`. Review each entry, edit `definition` if
necessary, and set `approved` to `true` for entries to keep.

Apply the review:

```powershell
python tools/updateWordDictionary.py apply
```

`apply` validates the approved entries, updates `dictionary.json`, and
regenerates `inflections.json` from ECDICT's `exchange` and `lemma.en.txt`.
The inflection generator is called by `apply`; normally do not run it directly.
It does not build or install the add-on. Review the Git diff, then run:

```powershell
scons
```

## Sources

The current 122,370 headwords are assembled from:

- clipboardEnhancement: 114,835 entries from `Dict.json` (commit `5f3ed93`)
- ECDICT Basic: 7,496 reviewed entries from `ecdict.csv` (commit `bc015ed`)
- ECDICT-ultimate: 39 individually reviewed entries from release `1.0.0`

Routine updates use ECDICT Basic. ECDICT-ultimate is historical provenance,
not a required input. The add-on resource notice contains the source and
license attributions.
