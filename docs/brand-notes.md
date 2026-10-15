# Visual design and asset provenance

Harbor uses a quiet sidebar, generous whitespace, compact operational tables, restrained color, and a chat workspace with live connection context. The application mark and layout are original to this project.

## Design tokens

| Token | Value | Use |
| --- | --- | --- |
| Canvas | `#F3F2ED` | Warm background |
| Foreground | `#121517` | Main text |
| Accent | `#136B69` | Primary actions and selected controls |
| Border | `#D6D3C8` | Panels and dividers |

The dashboard uses Switzer for interface text and JetBrains Mono for technical details. The documentation website uses system fonts and the project's SVG mark, with the same restrained palette.

## Fonts

- Switzer is from [Fontshare](https://www.fontshare.com/fonts/switzer). The publisher's ITF Free Font License allows self-hosting for the application's own use but restricts redistribution of font software in public repositories. The binary is therefore ignored by Git. `npm run fonts` downloads it directly from Fontshare for each installation, and `predev` / `prebuild` run that step automatically. The unmodified publisher license is at `dashboard/public/fonts/Switzer-LICENSE.txt`. A standard system sans-serif fallback is always configured. Font downloads require access to `api.fontshare.com` and `cdn.fontshare.com` during a fresh build, never at application runtime.
- JetBrains Mono is installed from `@fontsource/jetbrains-mono` and bundled locally. It uses the SIL Open Font License, included in the npm package and copied into the dashboard's public font directory.
- The application mark is an original inline SVG. Interface icons are from Lucide, under its ISC license. No third-party logo is reused.
- The three seeded storefront product illustrations are original SVG artwork in `docker/wordpress/assets/`: a canvas tote, a steel bottle, and a notebook. They use no stock photography, external image service, or third-party product branding.

## UI behavior

All counts, connection states, members, keys, and activity come from the API. Empty states remain empty until a real action succeeds. Connection status refreshes every 30 seconds while the page is visible. API secrets are held only in memory and shown once after creation. Native modal dialogs trap keyboard focus; labels, visible focus, reduced-motion preferences, responsive layouts, and text descriptions accompany controls and statuses.

Conversation text is rendered as plain text, with no raw HTML execution. Tool arguments and results are expandable, and proposed write actions require explicit review and confirmation. Role rules remain enforced by the backend; UI controls provide guidance, not authorization.
