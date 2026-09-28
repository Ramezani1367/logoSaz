from __future__ import annotations
import base64, copy, hashlib, importlib.util, io, json, math, mimetypes, os, re, socket, sys, tempfile, threading, urllib.parse, zipfile
from datetime import datetime
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

APP_DIR=Path(__file__).resolve().parent
SAMPLE_DIR=APP_DIR/'samples'
CONFIG_DIR=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'LogoColorStudio'
CONFIG_FILE=CONFIG_DIR/'settings.json'
SVG_NS='http://www.w3.org/2000/svg'; XLINK_NS='http://www.w3.org/1999/xlink'
ET.register_namespace('',SVG_NS); ET.register_namespace('xlink',XLINK_NS)
LOCK=threading.RLock(); ASSETS={}
PREVIEW_CACHE=OrderedDict(); ALPHA_BOUNDS_CACHE=OrderedDict()
DEFAULTS={'library':str(SAMPLE_DIR),'output':str(Path.home()/'Desktop'/'Logo Exports'),'favorites':[],'recent':[],'aliases':{},'sheet_order':[]}
CONFIG=dict(DEFAULTS)

def save_config():
    CONFIG_DIR.mkdir(parents=True,exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(CONFIG,ensure_ascii=False,indent=2),encoding='utf-8')

def load_config():
    global CONFIG
    CONFIG_DIR.mkdir(parents=True,exist_ok=True)
    try:
        x=json.loads(CONFIG_FILE.read_text(encoding='utf-8')); CONFIG={**DEFAULTS,**x} if isinstance(x,dict) else dict(DEFAULTS)
    except (OSError,json.JSONDecodeError): CONFIG=dict(DEFAULTS)
    if not Path(CONFIG['library']).is_dir(): CONFIG['library']=str(SAMPLE_DIR)
    Path(CONFIG['output']).expanduser().mkdir(parents=True,exist_ok=True)
    save_config()

def slug_title(s):
    s=Path(s).stem
    s=re.sub(r'(?i)^logo[\s_-]*','',s)
    s=re.sub(r'(?i)(?:[_\s.-]+(?:fa|en|english|persian|farsi|original|mono|color|white|black|rgb|cmyk|[owb]))+$','',s)
    return re.sub(r'[_-]+',' ',s).strip(' ._-\t') or 'Untitled logo'

def detect_language(s):
    if re.search(r'[\u0600-\u06ff]',s) or re.search(r'(?i)(^|[/_. -])(fa|farsi|persian|فارسی)([/_. -]|$)',s): return 'FA'
    if re.search(r'(?i)(^|[/_. -])(en|eng|english)([/_. -]|$)',s): return 'EN'
    return 'EN'

def source_palette(path):
    """Return distinct explicit SVG paint colors as normalized six-digit HEX values."""
    try: root=ET.fromstring(Path(path).read_bytes())
    except (OSError,ET.ParseError): return []
    try: from PIL import ImageColor
    except ImportError: ImageColor=None
    colors=[]
    def add(value):
        value=re.sub(r'(?i)\s*!important\s*$','',(value or '').strip()).strip()
        if not value or value.casefold() in {'none','transparent','inherit','currentcolor','context-fill','context-stroke'} or 'url(' in value.casefold():return
        match=re.fullmatch(r'#[\da-fA-F]{3}(?:[\da-fA-F]{3})?',value)
        if match:
            try: color='#'+''.join(f'{x:02X}' for x in parse_color(value))
            except ValueError:return
        else:
            m=re.fullmatch(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',value,re.I)
            if m: rgb=tuple(max(0,min(255,int(x))) for x in m.groups())
            elif ImageColor is not None:
                try: rgb=ImageColor.getrgb(value)[:3]
                except (ValueError,TypeError):return
            else:return
            color='#'+''.join(f'{x:02X}' for x in rgb)
        if color not in colors: colors.append(color)
    for node in root.iter():
        for key,value in node.attrib.items():
            prop=key.rsplit('}',1)[-1].casefold()
            if prop in {'fill','stroke','stop-color','flood-color','lighting-color','color'}: add(value)
            elif prop=='style':
                for match in re.finditer(r'(?i)(?:^|;)\s*(?:fill|stroke|stop-color|flood-color|lighting-color|color)\s*:\s*([^;]+)',value): add(match.group(1))
        if node.tag.rsplit('}',1)[-1].casefold()=='style' and node.text:
            for match in re.finditer(r'(?i)(?:fill|stroke|stop-color|flood-color|lighting-color|color)\s*:\s*([^;}]+)',node.text): add(match.group(1))
    return colors[:24]

def scan_library():
    global ASSETS
    root=Path(CONFIG['library']).expanduser()
    if not root.is_dir(): raise ValueError(f'پوشهٔ کتابخانه پیدا نشد: {root}')
    found={}
    for p in sorted(root.rglob('*.svg'),key=lambda x:str(x).casefold()):
        if not p.is_file(): continue
        rel=p.relative_to(root).as_posix(); aid=hashlib.sha1(rel.encode()).hexdigest()[:14]; parts=Path(rel).parts
        if len(parts)>1:
            dirs=[x for x in parts[:-1] if x.casefold() not in {'en','fa','english','persian','farsi','svg'}]
            brand=dirs[0] if dirs else slug_title(parts[-1])
        else: brand=slug_title(parts[-1])
        stat=p.stat()
        found[aid]={'id':aid,'brand':brand,'language':detect_language(rel),'file':p.name,'relative':rel,'path':str(p),'size':stat.st_size,'modified':stat.st_mtime_ns,'version':hashlib.sha1(f'{p.resolve()}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()[:12],'palette':source_palette(p)}
    with LOCK: ASSETS=found
    return found

def norm(s):
    s=(s or '').casefold().strip(); s=re.sub(r'[\u200c\u200f\u202a-\u202e]','',s); return re.sub(r'\s+',' ',s)

def library_data(q='',lang='all',shelf='all'):
    q=norm(q); fav=set(CONFIG.get('favorites',[])); rec=CONFIG.get('recent',[]); recset=set(rec); out=[]
    for a in ASSETS.values():
        if lang in ('EN','FA') and a['language']!=lang: continue
        if shelf=='favorites' and a['id'] not in fav: continue
        if shelf=='recent' and a['id'] not in recset: continue
        aliases=CONFIG.get('aliases',{}).get(a['id'],'')
        if q and q not in norm(' '.join([a['brand'],a['file'],a['relative'],aliases])): continue
        out.append({**a,'favorite':a['id'] in fav,'aliases':aliases})
    if shelf=='recent':
        order={x:i for i,x in enumerate(rec)}; out.sort(key=lambda x:order.get(x['id'],9999))
    else: out.sort(key=lambda x:(x['brand'].casefold(),x['language'],x['file'].casefold()))
    return out

def state_data():
    def available(m):
        try:return importlib.util.find_spec(m) is not None
        except (ImportError,ValueError):return False
    return {'library':CONFIG['library'],'output':CONFIG['output'],'assets':len(ASSETS),'favorites':CONFIG.get('favorites',[]),'recent':CONFIG.get('recent',[]),'renderer':available('resvg_py') and available('PIL'),'illustrator':sys.platform=='win32','sample_library':str(SAMPLE_DIR),'sheet_order':CONFIG.get('sheet_order',[])}

def parse_color(c):
    c=(c or '#000000').strip()
    if re.fullmatch(r'#[\da-fA-F]{3}',c): return tuple(int(x*2,16) for x in c[1:])
    if re.fullmatch(r'#[\da-fA-F]{6}',c): return tuple(int(c[i:i+2],16) for i in (1,3,5))
    m=re.fullmatch(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',c,re.I)
    if m:return tuple(max(0,min(255,int(x))) for x in m.groups())
    raise ValueError('رنگ باید به شکل HEX مثل #365FA0 باشد')

def luminance(rgb):
    vals=[]
    for c in rgb:
        v=c/255; vals.append(v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)
    return .2126*vals[0]+.7152*vals[1]+.0722*vals[2]

def map_color(value,target,mode):
    value=(value or '').strip()
    if not value or value.casefold() in {'none','transparent','inherit','currentcolor','context-fill','context-stroke'} or 'url(' in value.casefold(): return value
    rgb=None
    if re.fullmatch(r'#[\da-fA-F]{3}(?:[\da-fA-F]{3})?',value): rgb=parse_color(value)
    else:
        m=re.fullmatch(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',value,re.I)
        if m:rgb=tuple(map(int,m.groups()))
    if rgb is None:return value
    if mode=='solid': out=target
    else:
        l=luminance(rgb); out=tuple(round(c*(1-l)+255*l) for c in target)
    return '#'+''.join(f'{x:02X}' for x in out)

def embed_images(root,base):
    for node in root.iter():
        for attr in ('href',f'{{{XLINK_NS}}}href'):
            ref=node.get(attr)
            if not ref or ref.startswith(('data:','http:','https:','#')):continue
            try:
                f=(base/urllib.parse.unquote(ref)).resolve()
                if not f.is_file() or f.stat().st_size>20*1024*1024:continue
                mime=mimetypes.guess_type(str(f))[0] or 'application/octet-stream'
                node.set(attr,'data:'+mime+';base64,'+base64.b64encode(f.read_bytes()).decode('ascii'))
            except (OSError,ValueError):pass

def recolor_svg(path,color='#214E89',mode='tonal',embed=True):
    target=parse_color(color)
    if mode not in {'original','tonal','solid'}:raise ValueError('حالت رنگ ناشناخته است')
    root=ET.fromstring(Path(path).read_bytes())
    # Illustrator's guide layer is non-artwork; keep it invisible in every derived SVG/export.
    for node in root.iter():
        names=' '.join(v for k,v in node.attrib.items() if k.rsplit('}',1)[-1] in {'id','data-name','label'}).casefold()
        if '__proguides__' in names:
            node.set('display','none')
            declarations=[x for x in (node.get('style','').split(';')) if x.strip() and x.split(':',1)[0].strip().casefold()!='display']
            declarations.append('display:none!important')
            node.set('style',';'.join(declarations))
    if mode!='original':
        for node in root.iter():
            for attr in ('fill','stroke','stop-color','flood-color','lighting-color'):
                if node.get(attr) is not None: node.set(attr,map_color(node.get(attr),target,mode))
            style=node.get('style')
            if style:
                style=re.sub(r'(?i)(fill|stroke|stop-color|flood-color|lighting-color)\s*:\s*([^;]+)',lambda m:m.group(1)+':'+map_color(m.group(2),target,mode),style)
                node.set('style',style)
        for n in root.iter(f'{{{SVG_NS}}}style'):
            if n.text:n.text=re.sub(r'(?i)(fill|stroke|stop-color|flood-color|lighting-color)\s*:\s*([^;}]+)',lambda m:m.group(1)+':'+map_color(m.group(2),target,mode),n.text)
    if embed:embed_images(root,Path(path).parent)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def trim_svg(svg_bytes,source_path,padding_ratio=.04):
    try: import resvg_py; from PIL import Image
    except ImportError as exc: raise RuntimeError('برای تشخیص حاشیهٔ اثر، resvg-py و Pillow لازم است؛ یا حالت «حفظ اندازهٔ آرت‌بورد SVG» را انتخاب کن.') from exc
    root=ET.fromstring(svg_bytes); vb=root.get('viewBox')
    if not vb:raise RuntimeError('این SVG viewBox ندارد؛ برای حفظ ابعاد، حالت «حفظ اندازهٔ آرت‌بورد SVG» را انتخاب کن.')
    p=[float(x) for x in re.split(r'[ ,]+',vb.strip()) if x]
    if len(p)!=4 or p[2]<=0 or p[3]<=0:raise RuntimeError('viewBox این SVG قابل‌خواندن نیست؛ حالت «حفظ اندازهٔ آرت‌بورد SVG» را انتخاب کن.')
    x0,y0,w,h=p; source=Path(source_path); key=None; bounds=None
    try:
        st=source.stat(); key=(str(source.resolve()),st.st_mtime_ns,st.st_size,vb,padding_ratio)
        with LOCK: bounds=ALPHA_BOUNDS_CACHE.get(key)
    except OSError: pass
    if bounds is None:
        png=resvg_py.svg_to_bytes(svg_string=svg_bytes.decode('utf-8'),width=900,resources_dir=str(source.parent))
        im=Image.open(io.BytesIO(png)).convert('RGBA'); box=im.getchannel('A').getbbox()
        if not box:raise RuntimeError('اثر قابل‌مشاهده‌ای برای برش پیدا نشد.')
        left,top,right,bottom=box
        bounds=(left/im.width,top/im.height,right/im.width,bottom/im.height)
        if key:
            with LOCK:
                ALPHA_BOUNDS_CACHE[key]=bounds; ALPHA_BOUNDS_CACHE.move_to_end(key)
                while len(ALPHA_BOUNDS_CACHE)>300:ALPHA_BOUNDS_CACHE.popitem(last=False)
    left,top,right,bottom=bounds
    nx=x0+left*w; ny=y0+top*h; nw=(right-left)*w; nh=(bottom-top)*h
    px=nw*padding_ratio; py=nh*padding_ratio; nx-=px; ny-=py; nw+=2*px; nh+=2*py
    root.set('viewBox',f'{nx:.3f} {ny:.3f} {nw:.3f} {nh:.3f}')
    root.set('width',f'{nw:.3f}'); root.set('height',f'{nh:.3f}')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def svg_viewbox(root):
    vb=root.get('viewBox')
    if not vb: return None
    parts=[float(x) for x in re.split(r'[ ,]+',vb.strip()) if x]
    if len(parts)!=4 or parts[2]<=0 or parts[3]<=0: return None
    return tuple(parts)

def prepare_artboard(svg_bytes,source_path,canvas='artwork',margin_percent=4):
    """Fit visible logo geometry, preserving aspect ratio, centered on the requested artboard."""
    margin=max(0.0,min(25.0,float(margin_percent or 0)))/100.0
    if canvas=='artwork':
        # Convert an edge margin fraction into trim_svg's padding relative to artwork size.
        padding=margin/(1-2*margin) if margin<.5 else 0
        return trim_svg(svg_bytes,source_path,padding)

    original_root=ET.fromstring(svg_bytes)
    original_box=svg_viewbox(original_root)
    if canvas=='original' and not original_box:
        # Keep legacy SVGs without a viewBox untouched rather than inventing coordinates.
        return svg_bytes
    if not original_box:
        raise RuntimeError('برای ساخت آرت‌بورد سفارشی، SVG باید viewBox معتبر داشته باشد.')

    original_width=original_root.get('width'); original_height=original_root.get('height')
    if canvas=='original':
        x0,y0,target_w,target_h=original_box
        page_viewbox=f'{x0:.6f} {y0:.6f} {target_w:.6f} {target_h:.6f}'
        page_width=original_width or f'{target_w:.6f}'
        page_height=original_height or f'{target_h:.6f}'
    else:
        boards={
            'square1080':(1080.0,1080.0,'1080px','1080px'),
            'a4_landscape':(297.0,210.0,'1122.519685px','793.700787px'),
            'a5_landscape':(210.0,148.0,'793.700787px','559.370079px'),
        }
        if canvas not in boards: raise ValueError('اندازهٔ آرت‌بورد ناشناخته است')
        target_w,target_h,page_width,page_height=boards[canvas]
        x0=y0=0.0; page_viewbox=f'0 0 {target_w:g} {target_h:g}'

    # Get tight visible artwork bounds first, so pre-existing SVG whitespace does not
    # affect fitting or centering on the requested artboard.
    tight_bytes=trim_svg(svg_bytes,source_path,0)
    root=ET.fromstring(tight_bytes); art_box=svg_viewbox(root)
    if not art_box: raise RuntimeError('محدودهٔ لوگو برای چیدمان در آرت‌بورد قابل‌خواندن نیست.')
    art_x,art_y,art_w,art_h=art_box
    available_w=target_w*(1-2*margin); available_h=target_h*(1-2*margin)
    scale=min(available_w/art_w,available_h/art_h)
    tx=x0+(target_w-art_w*scale)/2-art_x*scale
    ty=y0+(target_h-art_h*scale)/2-art_y*scale

    # Move artwork elements into a single transformed group, leaving defs and style
    # declarations at the root so gradients, masks, and CSS selectors still resolve.
    non_artwork={'defs','style','title','desc','metadata','script','namedview'}
    drawable=[]
    for child in list(root):
        local=child.tag.rsplit('}',1)[-1].casefold()
        if local not in non_artwork: drawable.append(child)
    if not drawable: raise RuntimeError('عنصر قابل‌چیدمان در SVG پیدا نشد.')
    namespace=root.tag[1:].split('}',1)[0] if root.tag.startswith('{') else ''
    group_tag=f'{{{namespace}}}g' if namespace else 'g'
    group=ET.Element(group_tag,{'transform':f'matrix({scale:.10f} 0 0 {scale:.10f} {tx:.10f} {ty:.10f})'})
    for child in drawable:
        root.remove(child); group.append(child)
    root.append(group)
    root.set('viewBox',page_viewbox); root.set('width',page_width); root.set('height',page_height)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def safe_name(s):return re.sub(r'[<>:"/\\|?*\x00-\x1f]','-',s).strip(' .')[:120] or 'logo'

def convert_raster(svg_bytes,source_path,fmt,background='#FFFFFF',transparent=True,size=2048):
    fmt=fmt.upper(); width=int(size) if int(size)>0 else None
    if fmt=='PDF':
        if sys.platform=='win32':raise RuntimeError('روی Windows، PDF از طریق Illustrator ساخته می‌شود.')
        try:import cairosvg
        except ImportError as exc:raise RuntimeError('روی این سیستم برای PDF به Illustrator یا CairoSVG نیاز است.') from exc
        return cairosvg.svg2pdf(bytestring=svg_bytes,url=str(source_path))
    try:import resvg_py
    except ImportError as exc:raise RuntimeError('نصب کن: py -3 -m pip install -r requirements.txt') from exc
    png=resvg_py.svg_to_bytes(svg_string=svg_bytes.decode('utf-8'),width=width,resources_dir=str(Path(source_path).parent))
    if fmt=='PNG' and transparent:return png
    try:from PIL import Image
    except ImportError as exc:raise RuntimeError('برای PNG و WebP، Pillow را نصب کن') from exc
    fg=Image.open(io.BytesIO(png)).convert('RGBA')
    if fmt=='WEBP' and transparent:
        stream=io.BytesIO(); fg.save(stream,format='WEBP',quality=95,method=6); return stream.getvalue()
    bg=Image.new('RGBA',fg.size,parse_color(background)+(255,)); bg.alpha_composite(fg); stream=io.BytesIO()
    if fmt=='PNG':bg.convert('RGB').save(stream,format='PNG',optimize=True)
    elif fmt=='WEBP':bg.save(stream,format='WEBP',quality=95,method=6)
    else:raise ValueError(f'فرمت پیکسلی پشتیبانی نمی‌شود: {fmt}')
    return stream.getvalue()

def js_path(p):return str(Path(p).resolve()).replace('\\','/').replace('"','\\"')
def illustrator_export(svg_path,out_path,fmt):
    if sys.platform!='win32':raise RuntimeError('خروجی AI و PDF فقط روی Windows و با Illustrator نصب‌شده فعال است.')
    try:import pythoncom,win32com.client
    except ImportError as exc:raise RuntimeError('pywin32 نصب کن: py -3 -m pip install pywin32') from exc
    opt={'AI':'var opts = new IllustratorSaveOptions();','PDF':'var opts = new PDFSaveOptions();'}[fmt]
    script=f'var inputFile=new File("{js_path(svg_path)}"); var outputFile=new File("{js_path(out_path)}"); var doc=app.open(inputFile); {opt} doc.saveAs(outputFile,opts); doc.close(SaveOptions.DONOTSAVECHANGES);'
    pythoncom.CoInitialize()
    try:
        app=win32com.client.Dispatch('Illustrator.Application'); app.DoJavaScript(script)
    except Exception as exc:raise RuntimeError(f'Illustrator نتوانست فایل {fmt} را بسازد: {exc}') from exc
    finally:pythoncom.CoUninitialize()
    if not Path(out_path).exists():raise RuntimeError('Illustrator اجرا شد، اما فایل خروجی ایجاد نشد.')

def prefix_svg_ids(root,prefix):
    ids=[node.get('id') for node in root.iter() if node.get('id')]
    mapping={old:f'{prefix}{old}' for old in ids}
    def rewrite(text,selectors=False):
        if not text:return text
        for old,new in mapping.items():
            escaped=re.escape(old)
            text=re.sub(r"url\(\s*(['\"]?)#"+escaped+r"\1\s*\)",f'url(#{new})',text,flags=re.I)
            if selectors:
                text=re.sub(r'(?<![\w-])#'+escaped+r'(?=[\s,{.:>+~\[])',f'#{new}',text)
        return text
    for node in root.iter():
        if node.get('id') in mapping:node.set('id',mapping[node.get('id')])
        for key,value in list(node.attrib.items()):
            local=key.rsplit('}',1)[-1].casefold()
            if local in {'href','src'} and value.startswith('#') and value[1:] in mapping:
                node.set(key,'#'+mapping[value[1:]])
            elif local in {'aria-labelledby','aria-describedby','for'}:
                node.set(key,' '.join(mapping.get(x,x) for x in value.split()))
            else:node.set(key,rewrite(value,selectors=(local=='style')))
        if node.tag.rsplit('}',1)[-1].casefold()=='style' and node.text:
            node.text=rewrite(node.text,selectors=True)
    return root

def prefix_svg_classes(root,prefix):
    classes={}
    for node in root.iter():
        for name in node.get('class','').split():classes.setdefault(name,f'{prefix}{name}')
    for node in root.iter():
        if node.get('class'):
            node.set('class',' '.join(classes.get(name,name) for name in node.get('class','').split()))
        if node.tag.rsplit('}',1)[-1].casefold()=='style' and node.text:
            for old,new in classes.items():
                node.text=re.sub(r'(?<![\w-])\.'+re.escape(old)+r'(?![\w-])','.'+new,node.text)
    return root

def scope_svg_styles(root,root_id):
    """Scope ordinary CSS selectors to a nested SVG viewport in a one-page sheet."""
    prefix='#'+root_id
    for node in root.iter():
        if node.tag.rsplit('}',1)[-1].casefold()!='style' or not node.text:continue
        def scope_rule(match):
            selector_text,body=match.group(1),match.group(2)
            stripped=selector_text.strip()
            if not stripped or stripped.startswith('@'):return match.group(0)
            scoped=[]
            for selector in selector_text.split(','):
                selector=selector.strip()
                if not selector:continue
                if selector.startswith('svg'):
                    selector=prefix+selector[3:]
                elif selector.startswith(':root'):
                    selector=prefix+selector[5:]
                else:selector=prefix+' '+selector
                scoped.append(selector)
            return ','.join(scoped)+'{'+body+'}' if scoped else match.group(0)
        node.text=re.sub(r'([^{}]+)\{([^{}]*)\}',scope_rule,node.text)
    return root

def build_logo_sheet_svg(assets,canvas='a4_landscape',margin_percent=4,mode='original',color='#214E89',labels=True,sort_by='name',columns=None,order=None):
    boards={
        'square1080':(1080.0,1080.0,'1080px','1080px'),
        'a4_landscape':(297.0,210.0,'1122.519685px','793.700787px'),
        'a5_landscape':(210.0,148.0,'793.700787px','559.370079px'),
    }
    if canvas not in boards:raise ValueError('اندازهٔ صفحهٔ مجموعه ناشناخته است')
    if not assets:raise ValueError('در کتابخانه لوگویی برای ساخت صفحهٔ مجموعه وجود ندارد.')
    order=order or []
    if sort_by=='language':assets=sorted(assets,key=lambda a:(a.get('language',''),a.get('brand','').casefold(),a.get('file','').casefold()))
    elif sort_by=='custom':
        rank={str(aid):i for i,aid in enumerate(order)}
        assets=sorted(assets,key=lambda a:(rank.get(str(a['id']),len(rank)),a.get('brand','').casefold(),a.get('file','').casefold()))
    else:assets=sorted(assets,key=lambda a:(a.get('brand','').casefold(),a.get('language',''),a.get('file','').casefold()))
    page_w,page_h,width,height=boards[canvas];margin=max(0.0,min(25.0,float(margin_percent or 0)))/100
    count=len(assets)
    try:fixed_columns=int(columns) if columns not in (None,'','auto') else None
    except (TypeError,ValueError):fixed_columns=None
    if fixed_columns is not None:
        cols=max(1,min(count,fixed_columns));rows=math.ceil(count/cols)
        layout=[]
    else:layout=[]
    if fixed_columns is None:
        for candidate_cols in range(1,count+1):
            candidate_rows=math.ceil(count/candidate_cols)
            cell_ratio=(page_w/candidate_cols)/(page_h/candidate_rows)
            unused=(candidate_cols*candidate_rows-count)/count
            score=abs(math.log(cell_ratio))+.12*unused
            layout.append((score,candidate_cols,candidate_rows))
        _,cols,rows=min(layout)
    area_x=page_w*margin;area_y=page_h*margin;area_w=page_w*(1-2*margin);area_h=page_h*(1-2*margin)
    cell_w=area_w/cols;cell_h=area_h/rows
    svg_tag=f'{{{SVG_NS}}}svg'; root=ET.Element(svg_tag,{'width':width,'height':height,'viewBox':f'0 0 {page_w:g} {page_h:g}','version':'1.1'})
    id_counts={}
    for a in assets:id_counts[a['id']]=id_counts.get(a['id'],0)+1
    for index,asset in enumerate(assets):
        source=Path(asset['path']); item_mode=asset.get('_sheet_mode',mode); item_color=asset.get('_sheet_color',color)
        try:
            logo=recolor_svg(source,item_color,item_mode,True)
            tight=trim_svg(logo,source,0)
            logo_root=ET.fromstring(tight);vb=svg_viewbox(logo_root)
            if not vb:raise RuntimeError('viewBox نامعتبر')
        except Exception as exc:
            raise RuntimeError(f"لوگوی «{asset['brand']}» آماده نشد: {exc}") from exc
        prefix_svg_ids(logo_root,f'lcs_{index}_')
        prefix_svg_classes(logo_root,f'lcs_{index}_')
        viewport_id=f'lcs_{index}_viewport'
        scope_svg_styles(logo_root,viewport_id)
        col=index%cols;row=index//cols;cell_x=area_x+col*cell_w;cell_y=area_y+row*cell_h
        pad_x=cell_w*.10;pad_top=cell_h*.08;label_h=cell_h*.17 if labels else 0;pad_bottom=cell_h*.08
        box_x=cell_x+pad_x;box_y=cell_y+pad_top;box_w=max(0.1,cell_w-2*pad_x);box_h=max(0.1,cell_h-pad_top-pad_bottom-label_h)
        nested_attrs={'id':viewport_id,'x':f'{box_x:.6f}','y':f'{box_y:.6f}','width':f'{box_w:.6f}','height':f'{box_h:.6f}', 'viewBox':' '.join(f'{x:.6f}' for x in vb),'preserveAspectRatio':'xMidYMid meet','overflow':'hidden'}
        # Carry root-level paint/inheritance settings into the nested logo viewport.
        for key,value in logo_root.attrib.items():
            local=key.rsplit('}',1)[-1].casefold()
            if local not in {'id','viewbox','width','height','x','y','version','preserveaspectratio','xmlns','transform'}:nested_attrs[key]=value
        nested=ET.SubElement(root,svg_tag,nested_attrs)
        for child in list(logo_root):nested.append(copy.deepcopy(child))
        if labels:
            font_size=min(cell_w*.075,cell_h*.10,20.0 if canvas=='square1080' else 4.2)
            text=ET.SubElement(root,f'{{{SVG_NS}}}text',{'x':f'{cell_x+cell_w/2:.6f}','y':f'{cell_y+cell_h-pad_bottom*.45:.6f}','text-anchor':'middle','font-family':'Arial, sans-serif','font-size':f'{font_size:.4f}','fill':'#35445B','direction':'auto'})
            label=asset.get('_sheet_label') or asset.get('brand') or Path(asset['file']).stem
            if id_counts.get(asset['id'],0)>1 and not asset.get('_sheet_label'):
                label+=(' · رنگ اصلی' if item_mode=='original' else ' · '+str(item_color).upper())
            text.text=str(label)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def export_logo_sheet(payload):
    scope=payload.get('scope','all')
    if scope=='selected':
        assets=[]
        for variant in payload.get('items',[]):
            asset=ASSETS.get(str(variant.get('id','')))
            if not asset:continue
            entry={**asset,'_sheet_mode':variant.get('mode','original'),'_sheet_color':variant.get('color','#214E89')}
            if variant.get('label'):entry['_sheet_label']=str(variant['label'])
            assets.append(entry)
        if not assets:raise ValueError('نسخهٔ انتخاب‌شده‌ای در سبد برای ساخت صفحه نیست.')
    else:assets=library_data('','all','all')
    canvas=payload.get('canvas','a4_landscape');margin=payload.get('margin_percent',4)
    mode=payload.get('mode','original');color=payload.get('color','#214E89');labels=bool(payload.get('labels',True))
    sort_by=payload.get('sort','name');columns=payload.get('columns','auto');order=payload.get('order',CONFIG.get('sheet_order',[]))
    formats=[str(x).upper() for x in payload.get('formats',['PDF'])]
    allowed={'PDF','SVG','PNG','AI'};formats=list(dict.fromkeys(x for x in formats if x in allowed))
    if not formats:raise ValueError('حداقل یک فرمت برای صفحهٔ مجموعه انتخاب کن.')
    page=build_logo_sheet_svg(assets,canvas,margin,mode,color,labels,sort_by,columns,order)
    output=Path(CONFIG['output']).expanduser();output.mkdir(parents=True,exist_ok=True)
    base=f"LogoStudio-sheet-{datetime.now().strftime('%Y%m%d-%H%M%S')}";written=[];errors=[]
    for fmt in formats:
        target=output/f'{base}.{fmt.lower()}'
        try:
            if fmt=='SVG':target.write_bytes(page)
            elif fmt in {'PDF','AI'}:
                if sys.platform=='win32':
                    with tempfile.NamedTemporaryFile(suffix='.svg',prefix='lcs_sheet_',delete=False,dir=output) as temp:
                        temp.write(page);temp_path=Path(temp.name)
                    try:
                        if target.exists():target.unlink()
                        illustrator_export(temp_path,target,fmt)
                    finally:temp_path.unlink(missing_ok=True)
                elif fmt=='PDF':
                    try:import cairosvg
                    except ImportError as exc:raise RuntimeError('برای ساخت PDF روی این سیستم، Windows و Illustrator یا CairoSVG لازم است.') from exc
                    target.write_bytes(cairosvg.svg2pdf(bytestring=page))
                else:raise RuntimeError('خروجی AI صفحهٔ مجموعه به Windows و Illustrator نیاز دارد.')
            elif fmt=='PNG':
                size=1080 if canvas=='square1080' else payload.get('size',2048)
                target.write_bytes(convert_raster(page,Path(assets[0]['path']),fmt,payload.get('background','#FFFFFF'),bool(payload.get('transparent',True)),size))
            written.append(target)
        except Exception as exc:errors.append({'format':fmt,'message':str(exc)})
    return {'files':[str(x) for x in written],'folder':str(output),'success':len(written),'logos':len(assets),'errors':errors}

def export_items(payload):
    written=[]; errors=[]
    output=Path(CONFIG['output']).expanduser(); output.mkdir(parents=True,exist_ok=True)
    background=payload.get('background','#FFFFFF')
    transparent=bool(payload.get('transparent',True))
    size=payload.get('size',2048)
    canvas=payload.get('canvas','artwork')
    margin_percent=payload.get('margin_percent',4)
    for sel in payload.get('items',[]):
        aid=str(sel.get('id','')); asset=ASSETS.get(aid)
        if not asset:
            errors.append({'id':aid,'format':'','message':'فایل در کتابخانه پیدا نشد'})
            continue
        source=Path(asset['path']); folder=output/safe_name(asset['brand']); folder.mkdir(parents=True,exist_ok=True)
        mode=sel.get('mode','tonal'); color=sel.get('color','#214E89')
        mode_tag={'original':'original','tonal':'tone','solid':'solid'}.get(mode,'tone')
        color_tag='original' if mode=='original' else safe_name(color.replace('#',''))
        base=safe_name(f"{asset['brand']}_{asset['language']}_{Path(asset['file']).stem}_{mode_tag}_{color_tag}")
        try:
            svg=recolor_svg(source,color,mode,True)
            item_canvas=sel.get('canvas',canvas); item_margin=sel.get('margin_percent',margin_percent)
            svg=prepare_artboard(svg,source,item_canvas,item_margin)
        except Exception as exc:
            errors.append({'brand':asset['brand'],'format':'','message':str(exc)})
            continue
        for fmt in [str(x).upper() for x in sel.get('formats',[])]:
            if fmt not in {'SVG','PDF','PNG','WEBP','AI'}:
                errors.append({'brand':asset['brand'],'format':fmt,'message':'فرمت ناشناخته'})
                continue
            target=folder/f'{base}.{fmt.lower()}'
            try:
                if fmt=='SVG':
                    target.write_bytes(svg)
                elif fmt=='AI' or (fmt=='PDF' and sys.platform=='win32'):
                    with tempfile.NamedTemporaryFile(suffix='.svg',prefix='lcs_',delete=False,dir=folder) as temp:
                        temp.write(svg); temp_path=Path(temp.name)
                    try:
                        if target.exists():target.unlink()
                        illustrator_export(temp_path,target,fmt)
                    finally:
                        temp_path.unlink(missing_ok=True)
                else:
                    raster_size=1080 if item_canvas=='square1080' else size
                    target.write_bytes(convert_raster(svg,source,fmt,background,transparent,raster_size))
                written.append(target)
            except Exception as exc:
                errors.append({'brand':asset['brand'],'format':fmt,'message':str(exc)})
    zip_path=None
    if payload.get('zip',False):
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
        zip_path=output/f'LogoStudio-export-{stamp}.zip'
        with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as archive:
            for file_path in written:
                archive.write(file_path,file_path.relative_to(output).as_posix())
    return {'files':[str(p) for p in written],'folder':str(output),'zip':str(zip_path) if zip_path else None,'success':len(written),'errors':errors}

def choose_folder(title,initial):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk(); root.withdraw()
        try:root.attributes('-topmost',True)
        except Exception:pass
        out=filedialog.askdirectory(title=title,initialdir=initial if Path(initial).is_dir() else str(Path.home())); root.destroy(); return out
    except Exception:return ''

def open_in_illustrator(aid):
    a=ASSETS.get(aid)
    if not a:raise ValueError('SVG پیدا نشد')
    if sys.platform!='win32':raise RuntimeError('این قابلیت به Illustrator ویندوز نیاز دارد')
    try:import pythoncom,win32com.client
    except ImportError as exc:raise RuntimeError('pywin32 نصب نیست؛ pip install pywin32') from exc
    pythoncom.CoInitialize()
    try:win32com.client.Dispatch('Illustrator.Application').DoJavaScript(f'app.open(new File("{js_path(a["path"])}"));')
    finally:pythoncom.CoUninitialize()

def open_asset_folder(aid):
    asset=ASSETS.get(aid)
    if not asset: raise ValueError('فایل در کتابخانه پیدا نشد')
    if sys.platform!='win32': raise RuntimeError('بازکردن محل فایل در Explorer فقط روی Windows در دسترس است')
    os.startfile(str(Path(asset['path']).resolve().parent))

def send_json(h,data,status=200):
    b=json.dumps(data,ensure_ascii=False).encode(); h.send_response(status); h.send_header('Content-Type','application/json; charset=utf-8'); h.send_header('Content-Length',str(len(b))); h.send_header('Cache-Control','no-store'); h.end_headers(); h.wfile.write(b)

class Handler(BaseHTTPRequestHandler):
    server_version='LogoColorStudio/1.0'
    def log_message(self,fmt,*args):pass
    def do_GET(self):
        u=urllib.parse.urlparse(self.path)
        if u.path in ('/','/index.html'):
            try:b=(APP_DIR/'index.html').read_bytes()
            except OSError:return send_json(self,{'error':'index.html پیدا نشد'},500)
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b); return
        if u.path=='/api/state':return send_json(self,state_data())
        if u.path=='/api/library':
            q=urllib.parse.parse_qs(u.query); return send_json(self,{'items':library_data(q.get('q',[''])[0],q.get('lang',['all'])[0],q.get('shelf',['all'])[0])})
        if u.path=='/api/preview':
            q=urllib.parse.parse_qs(u.query); a=ASSETS.get(q.get('id',[''])[0])
            if not a:return send_json(self,{'error':'فایل پیدا نشد'},404)
            mode=q.get('mode',['original'])[0]; color=q.get('color',['#214E89'])[0]
            cache_key=(a['path'],a.get('size',0),a.get('modified',0),mode,color.upper())
            with LOCK:b=PREVIEW_CACHE.get(cache_key)
            if b is None:
                try:
                    original=recolor_svg(Path(a['path']),color,mode,True)
                    try:b=trim_svg(original,Path(a['path']))
                    except RuntimeError:b=original
                except Exception as e:return send_json(self,{'error':str(e)},400)
                with LOCK:
                    PREVIEW_CACHE[cache_key]=b; PREVIEW_CACHE.move_to_end(cache_key)
                    while len(PREVIEW_CACHE)>400:PREVIEW_CACHE.popitem(last=False)
            self.send_response(200); self.send_header('Content-Type','image/svg+xml; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','private, max-age=60'); self.end_headers(); self.wfile.write(b); return
        return send_json(self,{'error':'Not found'},404)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>20*1024*1024:raise ValueError('درخواست بیش از حد بزرگ است')
        b=self.rfile.read(n); return json.loads(b.decode()) if b else {}
    def do_POST(self):
        try:
            d=self.body(); route=urllib.parse.urlparse(self.path).path
            if route=='/api/library/set':
                raw=str(d.get('path','')).strip().strip('"')
                if not raw:return send_json(self,{'error':'مسیر کتابخانه خالی است'},400)
                folder=str(Path(raw).expanduser())
                if not Path(folder).is_dir():return send_json(self,{'error':'این پوشه پیدا نشد؛ مسیر را بررسی کن.'},400)
                CONFIG['library']=folder; save_config(); scan_library(); return send_json(self,{'ok':True,'state':state_data()})
            if route=='/api/library/browse':
                folder=choose_folder('انتخاب پوشهٔ SVG لوگوها',CONFIG['library'])
                if folder:CONFIG['library']=folder; save_config(); scan_library()
                return send_json(self,{'ok':bool(folder),'state':state_data()})
            if route=='/api/output/set':
                raw=str(d.get('path','')).strip().strip('"')
                if not raw:return send_json(self,{'error':'مسیر خروجی خالی است'},400)
                folder=str(Path(raw).expanduser())
                Path(folder).mkdir(parents=True,exist_ok=True); CONFIG['output']=folder; save_config(); return send_json(self,{'ok':True,'state':state_data()})
            if route=='/api/output/browse':
                folder=choose_folder('انتخاب پوشهٔ خروجی',CONFIG['output'])
                if folder:Path(folder).mkdir(parents=True,exist_ok=True); CONFIG['output']=folder; save_config()
                return send_json(self,{'ok':bool(folder),'state':state_data()})
            if route=='/api/favorite':
                aid=str(d.get('id','')); fav=list(CONFIG.get('favorites',[]))
                if aid in fav:fav.remove(aid)
                elif aid in ASSETS:fav.insert(0,aid)
                CONFIG['favorites']=fav; save_config(); return send_json(self,{'ok':True,'favorite':aid in fav})
            if route=='/api/recent':
                aid=str(d.get('id',''))
                if aid in ASSETS:CONFIG['recent']=[aid]+[x for x in CONFIG.get('recent',[]) if x!=aid][:39]; save_config()
                return send_json(self,{'ok':True})
            if route=='/api/sheet-order':
                order=d.get('order',[])
                if not isinstance(order,list):return send_json(self,{'error':'ترتیب لوگوها معتبر نیست'},400)
                valid=set(ASSETS); cleaned=[]
                for aid in order:
                    aid=str(aid)
                    if aid in valid and aid not in cleaned:cleaned.append(aid)
                CONFIG['sheet_order']=cleaned;save_config();return send_json(self,{'ok':True,'sheet_order':cleaned})
            if route=='/api/alias':
                aid=str(d.get('id',''))
                if aid not in ASSETS:return send_json(self,{'error':'فایل پیدا نشد'},404)
                aliases=dict(CONFIG.get('aliases',{})); aliases[aid]=str(d.get('aliases','')).strip()[:500]; CONFIG['aliases']=aliases; save_config(); return send_json(self,{'ok':True})
            if route=='/api/illustrator/open':open_in_illustrator(str(d.get('id',''))); return send_json(self,{'ok':True})
            if route=='/api/asset/folder':open_asset_folder(str(d.get('id',''))); return send_json(self,{'ok':True})
            if route=='/api/export':return send_json(self,export_items(d))
            if route=='/api/sheet-export':return send_json(self,export_logo_sheet(d))
            if route=='/api/open-output':
                folder=str(Path(CONFIG['output']).expanduser().resolve())
                if sys.platform=='win32':os.startfile(folder)
                else:raise RuntimeError('بازکردن پوشهٔ خروجی از این سیستم پشتیبانی نمی‌شود')
                return send_json(self,{'ok':True})
            return send_json(self,{'error':'مسیر درخواست ناشناخته است'},404)
        except Exception as e:return send_json(self,{'error':str(e)},500)

def show_start_error(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk(); root.withdraw(); messagebox.showerror('Logo Color Studio',str(message),parent=root); root.destroy()
    except Exception:
        pass

def run_app():
    try:
        load_config(); scan_library()
        server=None
        for port in range(8765,8780):
            try:
                server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
                break
            except OSError:
                continue
        if server is None:raise RuntimeError('هیچ پورتی برای اجرای محلی برنامه آزاد نیست.')
        threading.Thread(target=server.serve_forever,daemon=True,name='LogoStudioAPI').start()
        try:
            import webview
        except ImportError as exc:
            server.shutdown(); server.server_close()
            raise RuntimeError('اجزای پنجرهٔ برنامه نصب نیستند؛ نسخهٔ نصب‌شده را دوباره دریافت کن.') from exc
        url=f'http://127.0.0.1:{server.server_port}/'
        width,height,x,y=1440,920,None,None
        try:
            import ctypes
            screen_w=ctypes.windll.user32.GetSystemMetrics(0); screen_h=ctypes.windll.user32.GetSystemMetrics(1)
            width=min(width,max(800,screen_w-48)); height=min(height,max(640,screen_h-80))
            x=max(0,(screen_w-width)//2); y=max(0,(screen_h-height)//2)
        except Exception:pass
        min_size=(min(1020,width),min(680,height))
        webview.create_window('Logo Color Studio',url,width=width,height=height,x=x,y=y,min_size=min_size,resizable=True,text_select=True,zoomable=True,background_color='#F2F5F9')
        try:
            webview.start(debug=False)
        finally:
            server.shutdown(); server.server_close()
    except Exception as exc:
        show_start_error(exc)

if __name__=='__main__':run_app()
