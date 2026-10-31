"""Render validated artifact metadata; never load a registry or third party script."""
import re

APP_ID = 'd2497e'


def valid(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{6}', value) is not None


def decorate(page):
    source = page['html']
    if 'data-card-artifacts="1"' in source:
        return source
    card = page.get('aid')
    linked = page.get('linked_ids', [])
    if not isinstance(linked, list):
        linked = []
    ids = list(dict.fromkeys(i for i in linked if valid(i) and i != card))[:20]
    def badge(value, cls='aid'):
        return ('<span class="%s" data-aid="%s" title="Artefakt-ID – klicken zum Kopieren" '
                'tabindex="0" role="button">a#%s</span>') % (cls, value, value)
    ui = '<div data-card-artifacts="1" class="artifact-header">' + badge(APP_ID)
    if valid(card):
        ui += badge(card, 'aid card-aid')
    ui += '</div>'
    if ids:
        ui += '<div class="artifact-links" aria-label="Verknüpfte Artefakte">' + ''.join(badge(i, 'aid aid-chip') for i in ids) + '</div>'
    ui += '''<style>
.artifact-header{position:relative;height:28px;margin:0 16px}
.aid{font:11px monospace;opacity:.55;cursor:pointer;text-transform:none;letter-spacing:normal}
.artifact-header>.aid{position:absolute;right:0;top:6px}.artifact-header>.card-aid{right:100px}
.artifact-links{max-width:860px;margin:6px auto;padding:0 16px;display:flex;gap:8px;flex-wrap:wrap}
.aid-chip{border:1px solid #cbd5e1;border-radius:20px;padding:4px 8px}
</style><script>
(function(){function copy(e){var b=e.target.closest('[data-aid]');if(!b)return;
if(e.type==='keydown'&&e.key!=='Enter'&&e.key!==' ')return;e.preventDefault();
var t='a#'+b.dataset.aid;
if(navigator.clipboard){navigator.clipboard.writeText(t).catch(function(){})}}
document.addEventListener('click',copy);document.addEventListener('keydown',copy)})();
</script>'''
    return re.sub(r'(<body\b[^>]*>)', lambda m: m[1] + ui, source, count=1, flags=re.I)
