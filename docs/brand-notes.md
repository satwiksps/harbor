# Assets and licenses

The documentation and original assets under `docs/` use the [MIT license](LICENSE). This license does not apply to the application source. Third-party packages and assets retain their own licenses.

## Fonts and icons

| Asset | Used in | Source and license |
| --- | --- | --- |
| Switzer | Console interface | [Fontshare](https://www.fontshare.com/fonts/switzer), ITF Free Font License |
| JetBrains Mono | Console technical text | `@fontsource/jetbrains-mono`, SIL Open Font License |
| Lucide | Console icons | `lucide-react`, ISC license |
| System fonts | Documentation | Fonts already available on the reader's device |

The console's `npm run fonts` command downloads Switzer directly from its publisher. Development and production builds run this step automatically. The font binary is excluded from Git; the publisher's license is retained in `dashboard/public/fonts/Switzer-LICENSE.txt`. A fresh build needs access to `api.fontshare.com` and `cdn.fontshare.com`. The running console serves its fonts locally.

JetBrains Mono and Lucide are installed through npm. Their license files are included in their packages.

## Original artwork

The Harbor mark and the storefront's tote, bottle and notebook illustrations are original SVG assets. The product illustrations live in `docker/wordpress/assets/` and do not depend on an external image service.
