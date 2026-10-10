"""Noise pattern filtering for WordPress/batcache/cache generated content.

Strips auto-generated, constantly-changing metadata so diffs only
show meaningful content changes.
"""

import hashlib
import json
import re
import urllib.parse

# Bump this whenever _PAGE_NOISE_PATTERNS / _DIFF_NOISE_LINE_PATTERNS change
# in a way that invalidates previously-stored content hashes. The daemon then
# performs one quiet re-baseline cycle instead of emitting spurious diffs.
PATTERN_VERSION = 16  # v16: strip WP.com/Jetpack platform wave (dns-prefetch hints, combined CSS bundle, jetpack-comments noscript)

_PAGE_NOISE_PATTERNS = [
    (re.compile(r'<!--[^>]*?(?:generated|batcached|expires).*?-->', re.DOTALL), ''),
    (re.compile(r'var WP_Statistics_Tracker_Object\s*=\s*\{.*?\}\s*;', re.DOTALL), ''),
    (re.compile(r'<strong>\d+</strong>\s*Live Connection'), '<strong>0</strong> Live Connection'),
    (re.compile(r'\d+\s+Inbound\s+Attempts?'), 'N Inbound Attempts'),
    (re.compile(r'<strong>\d+</strong>\s*Dreamers?\s+online'), '<strong>0</strong> Dreamers online'),
    (re.compile(r'\d+\s+Dreamers?\s+online'), 'N Dreamers online'),
    (re.compile(r'[?&]m=\d+'), ''),
    (re.compile(r'e-\d{6}\.js'), 'e-000000.js'),
    (re.compile(r'nonce=[a-f0-9]+'), 'nonce=REMOVED'),
    (re.compile(r'"nonce"\s*:\s*"[a-f0-9]+"'), '"nonce":"REMOVED"'),
    (re.compile(r'(name|id)="_wpnonce"\s+value="[a-f0-9]+"'), r'\1="_wpnonce" value="REMOVED"'),
    (re.compile(r'\s+id="_wpnonce"\s+name="_wpnonce"\s+value="[a-f0-9]+"'), ' id="_wpnonce" name="_wpnonce" value="REMOVED"'),
    (re.compile(r'name="_wpnonce"\s+value="[a-f0-9]+"'), 'name="_wpnonce" value="REMOVED"'),
    (re.compile(r'name="_wp_http_referer"\s+value="[^"]*"'), 'name="_wp_http_referer" value="REMOVED"'),
    (re.compile(r'generated in \d+\.\d+ seconds'), ''),
    (re.compile(r'\d+ bytes batcached for \d+ seconds'), ''),
    (re.compile(r'served from batcache in \d+\.\d+ seconds'), ''),
    (re.compile(r'expires in \d+ seconds'), ''),
    (re.compile(r'generated \d+ seconds? ago'), ''),
    (re.compile(r'"j"\s*:\s*"\d+:\d+\.\d+-[a-z]\.\d+"'), '"j": "0:0.0-a.0"'),
    (re.compile(r'_stq\.push\([^)]*\)'), ''),
    (re.compile(r'\(new Image\(\)\).src = .+'), ''),
    (re.compile(r'/\*# sourceURL=.+\.(?:min\.)?css\s*\*/'), ''),
    (re.compile(r'\.wp-block-\w+\{[^}]+\}'), ''),
    (re.compile(r':is\([^)]*\.wp-block[^)]*\)[^{}]*\{[^}]*\}'), ''),
    (re.compile(r'\.wp-container-\w+\{[^}]+\}'), ''),
    (re.compile(r':root\s+:where\([^)]+\)'), ''),
    (re.compile(r'"signature":"[a-f0-9]+"'), '"signature":"REMOVED"'),
    (re.compile(r'"_wpnonce":"[a-f0-9]+"'), '"_wpnonce":"REMOVED"'),
    (re.compile(r'wp-custom-css-[a-f0-9]+'), 'wp-custom-css-XXXXXXXXX'),
    (re.compile(r'<button\b[^>]*type=[\x22\x27]submit[\x22\x27][^>]*>[\s\S]*?</button>', re.IGNORECASE), ''),
    (re.compile(r'"is_logged_in"\s*:\s*"\w*"'), '"is_logged_in":""'),
    (re.compile(r'"ajaxurl"\s*:\s*"[^"]*"'), '"ajaxurl":""'),
    (re.compile(r'"lang"\s*:\s*"[^"]*"'), '"lang":""'),
    (re.compile(r'"display_exif"\s*:\s*"[^"]*"'), '"display_exif":""'),
    (re.compile(r'"display_comments"\s*:\s*"[^"]*"'), '"display_comments":""'),
    (re.compile(r'"single_image_gallery"\s*:\s*"[^"]*"'), '"single_image_gallery":""'),
    (re.compile(r'"jetpack_subscriptions_widget"[^}]*\}'), ''),
    (re.compile(r'img#wpstats\{display:none\}'), ''),
    (re.compile(r'img:is\([^)]*\)\{contain-intrinsic-size:\d+px \d+px\}'), ''),
    (re.compile(r'<script[^>]*type=[\x22\x27]importmap[\x22\x27][^>]*>[\s\S]*?</script>', re.IGNORECASE), ''),
    (re.compile(r'<style id="wp-block-library-inline-css">.*?</style>', re.DOTALL), ''),
    (re.compile(r'<input\s[^>]*name\s*=\s*["\']jetpack_contact_form_jwt["\'][^>]*>', re.IGNORECASE), ''),
    (re.compile(r'<input\s[^>]*name\s*=\s*["\']ak_js["\'][^>]*>', re.IGNORECASE), ''),
    (re.compile(r'<script[^>]*>\s*window\.TOWER_TOKEN\s*=\s*"[^"]*".*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+id=[\'"]jetpack-search-theme-token-sampler[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+id=[\'"]jetpack-stats-js-before[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+id=[\'"]jetpack-mu-wpcom-settings-js-before[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+type=[\'"]application/ld\+json[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+type=[\'"]speculationrules[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+id=[\'"]wp-script-module-data-[^\'"]*[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+id=[\'"]wp-emoji-settings[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\s+type=["\']module["\'][^>]*>\s*</script>', re.IGNORECASE), ''),
    (re.compile(r'<script\s+type=["\']module["\'][^>]*>(?:(?!</script>).)*?script#wp-emoji-settings.*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\b[^>]*>(?:(?!</script>).)*(?:_wpemojiSettings|wpEmojiSettingsSupports|wp-emoji-loader).*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\b[^>]*id=["\']@?[^"\']*-js-module["\'][^>]*>.*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<script\b[^>]*id=["\']jp-forms-blocks-js["\'][^>]*>.*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<link\b[^>]*rel=["\']modulepreload["\'][^>]*/?>', re.IGNORECASE), ''),
    (re.compile(r'<link\b[^>]*id=["\']grunion\.css-css["\'][^>]*/?>', re.IGNORECASE), ''),
    (re.compile(r'<link\b[^>]*id=["\']jetpack-forms-layout-css["\'][^>]*/?>', re.IGNORECASE), ''),
    (re.compile(r'(like-post-wrapper-\d+-\d+-)[a-f0-9]+'), r'\1XXXXXXXXX'),
    (re.compile(r'obj_id=\d+-\d+-[a-f0-9]+'), 'obj_id=XXXXXXXXX'),
    (re.compile(r'(id="wp-duotone-[^"]+-)\d+'), r'\g<1>0'),
    # Hidden wp-duotone SVG filter blocks: their presence/count varies
    # between cache variants (Jetpack renders 2-4 blocks depending on
    # variant) -- strip the whole block so variants compare equal.
    (re.compile(r'<svg\b[^>]*>.*?<filter\s+id="wp-duotone-[^"]+".*?</svg>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<style\s+class="wpcode-css-snippet">/\*[\s=]*DATA\s+LOSS[\s=]*\*/.*?</style>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<meta\s+id=["\']bilmur["\'][^>]*>', re.IGNORECASE), ''),
    (re.compile(r'<script[^>]*src=[\'"][^\'"]*bilmur\.min\.js[^\'"]*[\'"][^>]*></script>', re.IGNORECASE), ''),
    (re.compile(r'<script\s+id=[\'"]wp-statistics-tracker-js-extra[\'"].*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'(?:\?|&(?:amp;|#0?38;)?)ver=[^"\'&\s>]+'), ''),
    (re.compile(r'\?\?-eJ[^"\'&\s>]+'), '??-CACHEKEY'),
    (re.compile(r'all-css-[a-f0-9]+'), 'all-css-HASH'),
    (re.compile(r'<!-- This site is optimized with the Yoast SEO plugin v\d+\.\d+ - https://yoast\.com/\S+ -->'), ''),
    (re.compile(r'<!--\s*Analytics by WP Statistics[^>]*?-->', re.IGNORECASE), ''),
    # Cache-buster query args: our own fetcher appends ?_cb=<random> when
    # bypassing Batcache; WordPress propagates the arg into rendered links
    # (pagination etc.), so poisoned cache variants differ from clean ones
    # only by this param. Normalize it away on both sides.
    (re.compile(r'[?&]_cb=\d+'), ''),
    (re.compile(r'<div\b[^>]*data-test\s*=\s*[\'"]contact-form[\'"][^>]*>.*?</form>\s*</div>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<div\b[^>]*class=["\'][^"\']*wp-block-jetpack-contact-form[^"\']*["\'][^>]*>.*?</div>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<style\s+id=["\']core-block-supports-inline-css["\']>.*?</style>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<style\s+id=["\']jetpack-stats-inline-css["\']>.*?</style>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'wp-elements-[a-f0-9]{6,}'), 'wp-elements-HASH'),
    (re.compile(r'wp-elements-\d+'), 'wp-elements-HASH'),
    (re.compile(r'is-layout-[a-f0-9]{8}\b'), 'is-layout-HASH'),
    (re.compile(r'wp-container-core-[a-z-]+-is-layout-[a-f0-9]{8}'), 'wp-container-LAYOUT'),
    (re.compile(r'<script\s+id=["\']wp-i18n-js-after["\']>.*?</script>', re.DOTALL | re.IGNORECASE), ''),
    (re.compile(r'<style\s+id=["\']wp-block-button-inline-css["\']>.*?</style>', re.DOTALL | re.IGNORECASE), ''),
    # General inline-CSS family: wp-block-columns/group/heading/...-inline-css
    # blocks vary in presence/count between cache variants -- strip them all.
    (re.compile(r'<style\s+id=["\']wp-block-[a-z-]+-inline-css["\']>.*?</style>', re.DOTALL | re.IGNORECASE), ''),
    # Jetpack carousel loading overlay: style="display: none;" present on some
    # cache variants, absent on others -- normalize the attribute away.
    (re.compile(r'(<div\s+id=["\']jp-carousel-loading-overlay["\'])\s+style="[^"]*"', re.IGNORECASE), r'\1'),
    (re.compile(r'<ul\b[^>]*\bclass="[^"]*\binline-posts-loop\b[^"]*"[^>]*>.*?</ul>', re.DOTALL), ''),
    (re.compile(r'"login_url"\s*:\s*"[^"]*"'), '"login_url":""'),
    # Jetpack Map block: data-api-key is empty on one cache variant, a
    # Mapbox public token on another -- normalize the value away so
    # variants compare equal. Map location text still diffs normally.
    (re.compile(r'data-api-key\s*=\s*"[^"]*"'), 'data-api-key=""'),
    # WP attachment EXIF output (data-image-meta="{aperture:...,camera:...}")
    # is machine-generated metadata stripped/added by WP/Jetpack updates --
    # never authored content. REMOVE the whole attribute (with its leading
    # whitespace) so pre/post-update renders hash identically. Covers both
    # raw HTML (data-image-meta="...") and JSON-escaped (data-image-meta=\"...\")
    # forms.
    (re.compile(r'\s+data-image-meta=\\?"?\{[^}]*\}\\?"?'), ''),
    # Jetpack tiled-gallery comment-count attribute, added by the same
    # plugin-update wave -- machine-generated markup, never authored content.
    (re.compile(r'\s+data-comments-count=\\?"?\d+\\?"?'), ''),
    # WordPress font-library block (wp-fonts-local): machine-generated
    # @font-face declarations whose contents vary between render variants
    # (theme-local weight files vs CDN). Pure infrastructure -- strip the
    # whole block (class or id, both quote styles) so variants compare equal.
    (re.compile(r'<style\s+(?:class|id)\s*=\s*["\']wp-fonts-local["\'][^>]*>.*?</style>', re.DOTALL | re.IGNORECASE), ''),
    # Jetpack block asset preloads (swiper.js etc.) injected into rendered
    # pages by plugin updates -- infrastructure, never content.
    (re.compile(r'<link\b[^>]*href=["\']?[^"\'>]*/jetpack/_inc/blocks/[^"\'>]*["\']?[^>]*/?>', re.IGNORECASE), ''),
    # WordPress.com/Jetpack platform template wave (2026-10): the platform
    # dropped two dns-prefetch hints, merged the perenne theme CSS into a
    # combined /_static/?? bundle together with the jetpack-comments CSS,
    # and injected a noscript lazy-comments style. All four are platform
    # infrastructure, never authored content -- normalize them away so a
    # WP.com release does not flag every comment-enabled page.
    (re.compile(r"<link\b[^>]*rel=['\"]dns-prefetch['\"][^>]*>\s*", re.IGNORECASE), ''),
    (re.compile(r"<noscript>\s*<style>\s*\.jetpack-comments\s*\{\s*visibility\s*:\s*visible\s*!important\s*\}\s*</style>\s*</noscript>\s*", re.IGNORECASE), ''),
    (re.compile(r"(<link\b[^>]*\bhref=['\"])(?:https?://project-skyscraper\.com)?/_static/\?\?[^'\"]*(['\"])", re.IGNORECASE), r"\1_static/??-CACHEKEY\2"),
    (re.compile(r"(<link\b[^>]*\bhref=['\"])(?:https?://project-skyscraper\.com)?/wp-content/themes/perenne/style\.css[^'\"]*(['\"])", re.IGNORECASE), r"\1_static/??-CACHEKEY\2"),
]

_DIFF_NOISE_LINE_PATTERNS = [
    re.compile(r'^[ +-]\s*generated in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\s*\d+ bytes batcached for \d+ seconds$'),
    re.compile(r'^[ +-]\s*generated \d+ seconds? ago$'),
    re.compile(r'^[ +-]\s*served from batcache in \d+\.\d+ seconds$'),
    re.compile(r'^[ +-]\s*expires in \d+ seconds$'),
    re.compile(r'^[ +-]\s*<!--$'),
    re.compile(r'^[ +-]\s*-->$'),
    re.compile(r'^[ +-]\s*//# sourceURL=.+$'),
    re.compile(r'^[ +-]\s*<button\s+type.*$'),
    re.compile(r'^[ +-]\s*"nonce"\s*:.*$'),
    re.compile(r'^[ +-]\s*"is_logged_in"\s*:.*$'),
    re.compile(r'^[ +-]\s*"ajaxurl"\s*:.*$'),
    re.compile(r'^[ +-]\s*"lang"\s*:.*$'),
    re.compile(r'^[ +-]\s*"display_exif"\s*:.*$'),
    re.compile(r'^[ +-]\s*"display_comments"\s*:.*$'),
    re.compile(r'^[ +-]\s*"single_image_gallery"\s*:.*$'),
    re.compile(r'^[ +-]\s*_stq\s*='),
    re.compile(r'^[ +-]\s*_stq\.'),
    re.compile(r'^[ +-]\s*\(new Image\(\)\)\.src ='),
    re.compile(r'^[ +-]\s*"j"\s*:\s*"\d+:\d+'),
    re.compile(r'^[ +-]\s*"hp":'),
    re.compile(r'^[ +-]\s*"ac":'),
    re.compile(r'^[ +-]\s*"amp":'),
    re.compile(r'^[ +-]\s*/?\*?# sourceURL=.+$'),
    re.compile(r'^[ +-]\s*\.wp-block-\w+'),
    re.compile(r'^[ +-]\s*\.wp-container-\w+'),
    re.compile(r'^[ +-]\s*:root\s+:where\('),
    re.compile(r'^[ +-]\s*:where\(\.wp-block-'),
    re.compile(r'^[ +-]\s*:is\('),
    re.compile(r'^[ +-]\s*}[\]\)]*;\s*$'),
    re.compile(r'^[ +-]\s*var Jetpack_Block_Assets_Base_Url'),
    re.compile(r'^[ +-]\s*\{?"baseUrl":'),
    re.compile(r'^[ +-]\s*"concatemoji":'),
    re.compile(r'^[ +-]\s*:root\{--wp--preset--'),
    re.compile(r'^\.\.\.\s*\(truncated\)$'),
    re.compile(r'^[ +-]\s*img#wpstats'),
    re.compile(r'^[ +-]\s*img:is\(.*$'),
    re.compile(r'^[ +-]\s*"@wordpress/interactivity".*$'),
    re.compile(r'^[ +-]\s*"imports".*$'),
    re.compile(r'^[ +-]\s*@wordpress/interactivity.*ver=.*$'),
    re.compile(r'^[ +-]\s*_stq\s*=\s*window[.]?_stq.*$'),
    re.compile(r'^[ +-]\s*_stq\s*=.*\[\]'),
    re.compile(r'^[ .]+\.\.\s*\(\d+ more lines?\)$'),
    re.compile(r'^[ .]+\.\.\s*\(truncated\)$'),
    re.compile(r'^[ +-]\s*.+&#8211;\s*project-skyscraper'),
    re.compile(r'^[ +-]\s*.+\u2013\s*project-skyscraper'),
    re.compile(r'^[ +-]\s*:root\{--wp-block-synced-color'),
    re.compile(r'^[ +-]\s*:root\{--wp-admin-theme-color'),
    re.compile(r'^[ +-].*jetpack_contact_form_jwt'),
    re.compile(r'^[ +-].*name\s*=\s*["\']ak_js["\']'),
    re.compile(r'^[ +-].*TOWER_TOKEN'),
    re.compile(r'^[ +-].*jetpack-search-theme-token-sampler'),
    re.compile(r'^[ +-].*jetpack-stats-js-before'),
    re.compile(r'^[ +-].*jetpack-mu-wpcom-settings-js-before'),
    re.compile(r'^[ +-].*wp-emoji-settings'),
    re.compile(r'^[ +-].*wp-script-module-data-'),
    re.compile(r'^[ +-].*application/ld\+json'),
    re.compile(r'^[ +-].*type=[\'"]speculationrules[\'"]'),
    re.compile(r'^[ +-].*like-post-wrapper-\d+-\d+-[a-f0-9]+'),
    re.compile(r'^[ +-].*wp-duotone-[a-f0-9]+-[a-f0-9]+-\d+'),
    re.compile(r'^[ +-].*obj_id=\d+-\d+-[a-f0-9]+'),
    re.compile(r'^[ +-].*wpcode-css-snippet'),
    re.compile(r'^[ +-].*DATA\s+LOSS'),
    re.compile(r'^[ +-].*bilmur'),
    re.compile(r'^[ +-].*wp-statistics-tracker-js-extra'),
    re.compile(r'^[ +-].*(?:\?|&(?:amp;|#0?38;)?)ver=[^"\'>&\s]+'),
    re.compile(r'^[ +-].*Yoast SEO plugin v\d+\.\d+'),
    re.compile(r'^[ +-]\s*\d+\s+Inbound\s+Attempts?'),
    re.compile(r'^[ +-]\s*\d+\s+Live\s+Connection'),
    re.compile(r'^[ +-]\s*\d+\s+Live\s+Visitor'),
    re.compile(r'^[ +-]\s*\d+\s+Dreamers?\s+online'),
    re.compile(r'^[ +-]\s*<strong>\d+</strong>\s+Dreamers?\s+online'),
    re.compile(r'^[ +-].*auto-generated'),
    re.compile(r'^[ +-].*grunion\.css-css'),
    re.compile(r'^[ +-].*jetpack-forms-layout-css'),
    re.compile(r'^[ +-].*jp-forms-view-js-module'),
    re.compile(r'^[ +-].*jp-forms-blocks-js'),
    re.compile(r'^[ +-].*rel=["\']modulepreload["\']'),
    re.compile(r'^[ +-].*-js-module["\'][^>]*>'),
    re.compile(r'^[ +-].*_wpemojiSettings'),
    re.compile(r'^[ +-].*wpEmojiSettingsSupports'),
    re.compile(r'^[ +-].*JSON\.parse\(t\.text\)'),
    re.compile(r'^[ +-].*Uint32Array'),
    re.compile(r'^[ +-].*HTMLScriptElement'),
    re.compile(r'^[ +-].*document\.querySelector'),
    re.compile(r'^[ +-].*Element missing'),
    re.compile(r'^[ +-].*sessionStorage\.setItem\([os],'),
    re.compile(r'^[ +-].*\[\s*["\']flag["\']\s*,'),
    re.compile(r'^[ +-]\s*(?:display|color|padding|margin|font|border|background|width|height|text-align|flex|grid|align-|justify|position)\s*:'),
    re.compile(r'^[ +-].*data-test\s*=\s*[\'"]contact-form[\'"]'),
    re.compile(r'^[ +-].*jetpack-contact-form-container'),
    re.compile(r'^[ +-].*contact-form-submission'),
    re.compile(r'^[ +-].*contact-form-success-'),
    re.compile(r'^[ +-].*jp-form-[a-f0-9]+'),
    re.compile(r'^[ +-].*jetpack-contact-form__form'),
    re.compile(r'^[ +-].*wp-block-jetpack-contact-form'),
    re.compile(r'^[ +-].*Submit a form'),
    re.compile(r'^[ +-].*wp-elements-[a-f0-9]+'),
    re.compile(r'^[ +-].*is-layout-[a-f0-9]{8}\b'),
    re.compile(r'^[ +-].*core-block-supports-inline-css'),
    re.compile(r'^[ +-].*jetpack-stats-inline-css'),
    re.compile(r'^[ +-]\s*<style[^>]*>\s*$'),
    re.compile(r'^[ +-]\s*<style[^>]*></style>\s*$'),
    re.compile(r'^[ +-]\s*</style>\s*$'),
    re.compile(r'^[ +-]\s*$'),
    re.compile(r'^[ +-].*wp-i18n-js-after'),
    re.compile(r'^[ +-].*wp\.i18n\.setLocaleData'),
    re.compile(r'^[ +-].*gutenberg/build/scripts'),
    re.compile(r'^[ +-].*wp-includes/js/dist/vendor'),
    re.compile(r'^[ +-].*wp-polyfill\.min\.js'),
    re.compile(r'^[ +-].*wp-hooks-js'),
    re.compile(r'^[ +-].*wp-i18n-js'),
    re.compile(r'^[ +-].*contact-form-\d+'),
    re.compile(r'^[ +-].*data-wp-interactive'),
    re.compile(r'^[ +-].*data-wp-context=.*formId'),
    re.compile(r'^[ +-].*"formId"'),
    re.compile(r'^[ +-].*formHash'),
    re.compile(r'^[ +-].*jetpack_forms_contact-form'),
    re.compile(r'^[ +-].*jetpack/form'),
    re.compile(r'^[ +-].*go-back-message'),
    re.compile(r'^[ +-].*Thank you!!'),
    re.compile(r'^[ +-].*data-wp-'),
    re.compile(r'^[ +-]\s*>$'),
    re.compile(r'^[ +-].*method=["\']post["\']'),
    re.compile(r'^[ +-].*novalidate'),
    re.compile(r'^[ +-].*</form>'),
    re.compile(r'^[ +-].*</div></div><form'),
    re.compile(r'^[ +-].*wp-block-post-content'),
    re.compile(r'^[ +-].*=> e === [a-z]\[t\]'),
    re.compile(r'^[ +-].*all-css-[a-f0-9]+'),
    re.compile(r'^[ +-].*_static/\?ver='),
    re.compile(r'^[ +-].*Analytics by WP Statistics'),
    re.compile(r'^[ +-].*wp-elements-\d+'),
    re.compile(r'^[ +-].*wp-elements-HASH'),
    re.compile(r'^[ +-].*wp-block-button-inline-css'),
    re.compile(r'^[ +-]\{background-color: var\(--wp--preset--color--secondary\)'),
    re.compile(r'^[ +-]\)\)\{color: var\(--wp--preset--color--base\)'),
    re.compile(r'^[ +-].*post_password'),
    re.compile(r'^[ +-].*pwbox-\d+'),
    re.compile(r'^[ +-].*data-api-key'),
    re.compile(r'^[ +-].*wp-duotone'),
    re.compile(r'^[ +-].*wp-block-[a-z-]+-inline-css'),
    re.compile(r'^[ +-].*jp-carousel-loading-overlay'),
    # WP/Jetpack plugin-update churn: EXIF attachment metadata removed/
    # added in one line, and jetpack block asset preloads (swiper.js).
    re.compile(r'^[ +-].*data-image-meta'),
    re.compile(r'^[ +-].*data-comments-count'),
    re.compile(r'^[ +-].*@font-face'),
    re.compile(r'^[ +-].*wp-fonts-local'),
    re.compile(r'^[ +-].*/jetpack/_inc/blocks/'),
    re.compile(r'^[ +-].*swiper\.js'),
]

_JSON_NOISE_KEYS = frozenset({
    "_links", "_embedded", "guid", "meta", "code",
    "modified", "modified_gmt", "date_gmt",
    "id", "author", "status", "type", "slug", "template", "featured_media",
    "comment_status", "ping_status", "menu_order", "parent", "order",
    "generated_slug", "_private", "link", "class_list", "categories",
    "tags", "sticky", "format", "password",
    "acf", "yoast_head", "yoast_head_json",
})


def strip_page_noise(html: str) -> str:
    for pattern, replacement in _PAGE_NOISE_PATTERNS:
        html = pattern.sub(replacement, html)
    html = re.sub(r'\n[ \t]*\n+', '\n', html)
    return html


def _normalize_for_compare(text: str) -> str:
    lines = text.splitlines()
    lines = [l.rstrip() for l in lines]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def is_noise_only_page_change(old_text: str, new_text: str) -> bool:
    old_stripped = strip_page_noise(old_text)
    new_stripped = strip_page_noise(new_text)
    old_norm = _normalize_for_compare(old_stripped)
    new_norm = _normalize_for_compare(new_stripped)
    return hashlib.md5(old_norm.encode("utf-8")).hexdigest() == \
           hashlib.md5(new_norm.encode("utf-8")).hexdigest()


def strip_json_noise(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text

    def _walk(v):
        if isinstance(v, dict):
            return {k: _walk(v) for k, v in v.items() if k not in _JSON_NOISE_KEYS}
        if isinstance(v, list):
            return [_walk(i) for i in v]
        return v

    cleaned = _walk(data)
    return json.dumps(cleaned, indent=2, sort_keys=False, ensure_ascii=False)


def is_noise_diff_line(line: str) -> bool:
    return any(r.match(line) for r in _DIFF_NOISE_LINE_PATTERNS)


def is_noise_route_index(url: str) -> bool:
    """WordPress REST API route index (/wp-json/) -- changes with every
    Jetpack/plugin route registration and carries no narrative content.
    Treat the whole resource as noise-equivalent so endpoint churn never
    produces notifications (content collections under /wp-json/wp/v2/* are
    unaffected -- only the bare route index matches)."""
    parsed = urllib.parse.urlparse(url)
    return parsed.path.rstrip("/") == "/wp-json"


def diff_has_real_changes(diff_text: str) -> bool:
    changed_lines = []
    for line in diff_text.splitlines():
        if line.startswith(('--- ', '+++ ', '@@', '#', 'diff --git')):
            continue
        if line.startswith(('-', '+')):
            changed_lines.append(line)

    if not changed_lines:
        return False

    for line in changed_lines:
        if is_noise_diff_line(line):
            continue
        if line.startswith('-') and '+' in line:
            parts = line[1:].split('+', 1)
            if len(parts) == 2 and parts[0].rstrip() == parts[1].rstrip():
                continue
        prefix = line[0]
        other = '-' if prefix == '+' else '+'
        stripped = line[1:].rstrip()
        paired = any(
            l[0] == other and l[1:].rstrip() == stripped
            for l in changed_lines
        )
        if not paired:
            return True
    return False
