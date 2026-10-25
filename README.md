# SCP Field Reader

A phone-first reader for the [SCP Wiki](https://scp-wiki.wikidot.com/): themes, adjustable type, bookmarks, reading progress, search, and browsing by series, object class, tag and collection.

Open the site in Safari on iPhone and choose **Share → Add to Home Screen** to install it.

## Content and licence

Article text is from the SCP Wiki and is licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Each article credits its author and links back to the original page. The archive was built from the [scp-data](https://github.com/scp-data/scp-api) dataset; images are loaded directly from the SCP Wiki.

## Layout

- `index.html` – the whole app
- `sw.js`, `manifest.webmanifest`, icons – install and offline support
- `data/index.gz` – article list and metadata
- `data/c/*.gz` – article text in compressed chunks, loaded on demand
