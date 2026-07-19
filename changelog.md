### 1.0.0

- Added offline English-to-Chinese definitions to NVDA's repeated current-word review command in Chinese,
  including conservative lookup for common spelling and inflection variants, candidate announcements for
  ambiguous words, and clear feedback for possible abbreviations and words absent from the local dictionary.
- Manual selection, clipboard, and last-spoken translation now use matching local word definitions for
  supported English-Chinese requests. Translation-command and text-review lookup can be controlled separately
  from Common Settings.

### 0.9.7

- Improved smart speech filtering to better preserve user content while avoiding auto-translation of NVDA speech metadata.
- Simplified internal code by removing unused abstractions and redundant wrappers.
- Added Vietnamese localization.

### 0.9.5

- Improved ChromeAI model checks for faster translation responses.
- Improved ChromeAI cold-start performance.
- Hardened ChromeAI's managed Chrome handling for better stability and safety.
