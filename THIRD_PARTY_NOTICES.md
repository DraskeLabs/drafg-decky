# Third-party notices

This plugin's own code is under the BSD 3-Clause License (see `LICENSE`).
The built file `dist/index.js` also contains parts of the following projects,
under their own licenses:

| project | license | where |
|---|---|---|
| [@decky/api](https://github.com/SteamDeckHomebrew/loader-api) 1.1.3 by SteamDeckHomebrew Team | LGPL-2.1 (text in `LICENSES/LGPL-2.1.txt`) | the loader connection code at the top of `dist/index.js`, unchanged except that the build leaves out the parts this plugin does not use and writes in the plugin's name |
| [react-icons](https://github.com/react-icons/react-icons) 5.7.0 | MIT, Copyright 2018 kamijin_fanta (text in `LICENSES/react-icons-MIT.txt`) | the icon component |
| [Font Awesome Free](https://fontawesome.com) by Fonticons, Inc. | [CC BY 4.0](https://fontawesome.com/license/free) (icons) | the "film" icon's outline |

@decky/api's source, at the version bundled, is in its npm package
(<https://www.npmjs.com/package/@decky/api/v/1.1.3>, which includes `src/`) and
at <https://github.com/SteamDeckHomebrew/loader-api>. You may replace it with a
modified version: this plugin's source and build steps are at
<https://github.com/DraskeLabs/drafg-decky> (`pnpm install && pnpm run build`).

`@decky/ui` is not included: Decky Loader provides it at runtime.

The files `decky.pyi`, `rollup.config.js`, `tsconfig.json` and `.gitignore`
come from the
[Decky plugin template](https://github.com/SteamDeckHomebrew/decky-plugin-template)
(BSD 3-Clause, Steam Deck Homebrew), as noted in `LICENSE`.
