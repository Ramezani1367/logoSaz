from __future__ import annotations
import base64, csv, hashlib, importlib.util, io, json, mimetypes, os, re, socket, sys, tempfile, threading, urllib.parse, webbrowser, zipfile
from datetime import datetime
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
DEFAULTS={'library':str(SAMPLE_DIR),'output':str(Path.home()/'Desktop'/'Logo Exports'),'favorites':[],'recent':[],'aliases':{}}
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
        found[aid]={'id':aid,'brand':brand,'language':detect_language(rel),'file':p.name,'relative':rel,'path':str(p),'size':p.stat().st_size}
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
    return {'library':CONFIG['library'],'output':CONFIG['output'],'assets':len(ASSETS),'favorites':CONFIG.get('favorites',[]),'recent':CONFIG.get('recent',[]),'renderer':available('resvg_py') and available('PIL'),'illustrator':sys.platform=='win32','sample_library':str(SAMPLE_DIR)}

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
    except ImportError as exc: raise RuntimeError('برای تشخیص حاشیهٔ اثر، resvg-py و Pillow لازم است؛ یا «حفظ صفحهٔ اصلی» را انتخاب کن.') from exc
    root=ET.fromstring(svg_bytes); vb=root.get('viewBox')
    if not vb:raise RuntimeError('این SVG viewBox ندارد؛ برای حفظ ابعاد، «حفظ صفحهٔ اصلی» را انتخاب کن.')
    p=[float(x) for x in re.split(r'[ ,]+',vb.strip()) if x]
    if len(p)!=4 or p[2]<=0 or p[3]<=0:raise RuntimeError('viewBox این SVG قابل‌خواندن نیست؛ «حفظ صفحهٔ اصلی» را انتخاب کن.')
    x0,y0,w,h=p
    png=resvg_py.svg_to_bytes(svg_string=svg_bytes.decode('utf-8'),width=1800,resources_dir=str(Path(source_path).parent))
    im=Image.open(io.BytesIO(png)).convert('RGBA'); box=im.getchannel('A').getbbox()
    if not box:raise RuntimeError('اثر قابل‌مشاهده‌ای برای برش پیدا نشد.')
    left,top,right,bottom=box; sx=w/im.width; sy=h/im.height
    px=(right-left)*sx*padding_ratio; py=(bottom-top)*sy*padding_ratio
    nx=x0+left*sx-px; ny=y0+top*sy-py; nw=(right-left)*sx+2*px; nh=(bottom-top)*sy+2*py
    root.set('viewBox',f'{nx:.3f} {ny:.3f} {nw:.3f} {nh:.3f}')
    root.set('width',f'{nw:.3f}'); root.set('height',f'{nh:.3f}')
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
    except ImportError as exc:raise RuntimeError('برای JPG و WebP، Pillow را نصب کن') from exc
    fg=Image.open(io.BytesIO(png)).convert('RGBA')
    if fmt=='WEBP' and transparent:
        stream=io.BytesIO(); fg.save(stream,format='WEBP',quality=95,method=6); return stream.getvalue()
    bg=Image.new('RGBA',fg.size,parse_color(background)+(255,)); bg.alpha_composite(fg); stream=io.BytesIO()
    if fmt=='JPG':bg.convert('RGB').save(stream,format='JPEG',quality=95,optimize=True,subsampling=0)
    elif fmt=='PNG':bg.convert('RGB').save(stream,format='PNG',optimize=True)
    elif fmt=='WEBP':bg.save(stream,format='WEBP',quality=95,method=6)
    else:raise ValueError(f'فرمت پیکسلی پشتیبانی نمی‌شود: {fmt}')
    return stream.getvalue()

def js_path(p):return str(Path(p).resolve()).replace('\\','/').replace('"','\\"')
def illustrator_export(svg_path,out_path,fmt):
    if sys.platform!='win32':raise RuntimeError('خروجی AI و EPS فقط روی Windows و با Illustrator نصب‌شده فعال است.')
    try:import pythoncom,win32com.client
    except ImportError as exc:raise RuntimeError('pywin32 نصب کن: py -3 -m pip install pywin32') from exc
    opt={'AI':'var opts = new IllustratorSaveOptions();','EPS':'var opts = new EPSSaveOptions();','PDF':'var opts = new PDFSaveOptions();'}[fmt]
    script=f'var inputFile=new File("{js_path(svg_path)}"); var outputFile=new File("{js_path(out_path)}"); var doc=app.open(inputFile); {opt} doc.saveAs(outputFile,opts); doc.close(SaveOptions.DONOTSAVECHANGES);'
    pythoncom.CoInitialize()
    try:
        app=win32com.client.Dispatch('Illustrator.Application'); app.DoJavaScript(script)
    except Exception as exc:raise RuntimeError(f'Illustrator نتوانست فایل {fmt} را بسازد: {exc}') from exc
    finally:pythoncom.CoUninitialize()
    if not Path(out_path).exists():raise RuntimeError('Illustrator اجرا شد، اما فایل خروجی ایجاد نشد.')

def export_items(payload):
    rows=[]; written=[]; output=Path(CONFIG['output']).expanduser(); output.mkdir(parents=True,exist_ok=True)
    background=payload.get('background','#FFFFFF'); transparent=bool(payload.get('transparent',True)); size=payload.get('size',2048); canvas=payload.get('canvas','artwork')
    for sel in payload.get('items',[]):
        aid=str(sel.get('id','')); a=ASSETS.get(aid)
        if not a:rows.append([aid,'','','','error','فایل در کتابخانه پیدا نشد']); continue
        path=Path(a['path']); folder=output/safe_name(a['brand']); folder.mkdir(parents=True,exist_ok=True)
        mode=sel.get('mode','tonal'); color=sel.get('color','#214E89'); mode_tag={'original':'original','tonal':'tone','solid':'solid'}.get(mode,'tone')
        ctag='original' if mode=='original' else safe_name(color.replace('#',''))
        base=safe_name(f"{a['brand']}_{a['language']}_{Path(a['file']).stem}_{mode_tag}_{ctag}")
        try:
            svg=recolor_svg(path,color,mode,True)
            if canvas=='artwork':svg=trim_svg(svg,path)
        except Exception as exc:rows.append([a['brand'],a['language'],a['relative'],'','error',str(exc)]);continue
        for fmt in [str(x).upper() for x in sel.get('formats',[])]:
            if fmt not in {'SVG','PDF','PNG','JPG','WEBP','AI','EPS'}:rows.append([a['brand'],a['language'],a['relative'],fmt,'error','فرمت ناشناخته']);continue
            target=folder/f'{base}.{fmt.lower()}'
            try:
                if fmt=='SVG':target.write_bytes(svg)
                elif fmt in {'AI','EPS'} or (fmt=='PDF' and sys.platform=='win32'):
                    with tempfile.NamedTemporaryFile(suffix='.svg',prefix='lcs_',delete=False,dir=folder) as t:t.write(svg); tmp=Path(t.name)
                    try:
                        if target.exists():target.unlink()
                        illustrator_export(tmp,target,fmt)
                    finally:tmp.unlink(missing_ok=True)
                else:target.write_bytes(convert_raster(svg,path,fmt,background,transparent,size))
                written.append(target); rows.append([a['brand'],a['language'],a['relative'],str(target.relative_to(output)),'ok',''])
            except Exception as exc:rows.append([a['brand'],a['language'],a['relative'],fmt,'error',str(exc)])
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S'); manifest=output/f'LogoStudio-manifest-{stamp}.csv'
    with manifest.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(['brand','language','source_svg','output','status','message']); w.writerows(rows)
    zpath=None
    if payload.get('zip',True):
        zpath=output/f'LogoStudio-export-{stamp}.zip'
        with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED) as z:
            for f in written:z.write(f,f.relative_to(output).as_posix())
            z.write(manifest,manifest.name)
    return {'files':[str(x) for x in written],'manifest':str(manifest),'zip':str(zpath) if zpath else None,'success':sum(r[4]=='ok' for r in rows),'errors':[r for r in rows if r[4]=='error']}

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

def send_json(h,data,status=200):
    b=json.dumps(data,ensure_ascii=False).encode(); h.send_response(status); h.send_header('Content-Type','application/json; charset=utf-8'); h.send_header('Content-Length',str(len(b))); h.send_header('Cache-Control','no-store'); h.end_headers(); h.wfile.write(b)

class Handler(BaseHTTPRequestHandler):
    server_version='LogoColorStudio/1.0'
    def log_message(self,fmt,*args):print('[Logo Studio] '+fmt%args)
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
            try:
                original=recolor_svg(Path(a['path']),q.get('color',['#214E89'])[0],q.get('mode',['tonal'])[0],True)
                try:b=trim_svg(original,Path(a['path']))
                except RuntimeError:b=original
            except Exception as e:return send_json(self,{'error':str(e)},400)
            self.send_response(200); self.send_header('Content-Type','image/svg+xml; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b); return
        if u.path=='/api/download':
            q=urllib.parse.parse_qs(u.query); name=Path(q.get('name',[''])[0]).name; root=Path(CONFIG['output']).expanduser().resolve(); target=(root/name).resolve()
            if target.parent!=root or not target.is_file():return send_json(self,{'error':'فایل خروجی پیدا نشد'},404)
            b=target.read_bytes(); self.send_response(200); self.send_header('Content-Type','application/zip' if target.suffix.lower()=='.zip' else 'application/octet-stream'); self.send_header('Content-Disposition',f'attachment; filename="{target.name}"'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if u.path=='/api/backup':
            b=json.dumps(CONFIG,ensure_ascii=False,indent=2).encode(); self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename="logo-color-studio-settings.json"'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
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
            if route=='/api/alias':
                aid=str(d.get('id',''))
                if aid not in ASSETS:return send_json(self,{'error':'فایل پیدا نشد'},404)
                aliases=dict(CONFIG.get('aliases',{})); aliases[aid]=str(d.get('aliases','')).strip()[:500]; CONFIG['aliases']=aliases; save_config(); return send_json(self,{'ok':True})
            if route=='/api/illustrator/open':open_in_illustrator(str(d.get('id',''))); return send_json(self,{'ok':True})
            if route=='/api/export':return send_json(self,export_items(d))
            if route=='/api/backup/import':
                inc=d.get('settings')
                if not isinstance(inc,dict):return send_json(self,{'error':'فایل تنظیمات معتبر نیست'},400)
                for key in ('library','output','favorites','recent','aliases'):
                    if key in inc:CONFIG[key]=inc[key]
                if not Path(CONFIG['library']).is_dir():return send_json(self,{'error':'مسیر کتابخانه در این رایانه پیدا نشد؛ اول کتابخانه را انتخاب کن.'},400)
                Path(CONFIG['output']).expanduser().mkdir(parents=True,exist_ok=True); save_config(); scan_library(); return send_json(self,{'ok':True,'state':state_data()})
            return send_json(self,{'error':'مسیر درخواست ناشناخته است'},404)
        except Exception as e:return send_json(self,{'error':str(e)},500)

def run_server():
    load_config(); scan_library(); server=None
    for port in range(8765,8780):
        try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler); break
        except OSError:continue
    if server is None:raise RuntimeError('هیچ پورتی بین 8765 تا 8779 آزاد نیست')
    url=f'http://127.0.0.1:{server.server_port}/'; print('Logo Color Studio is running at '+url); print('برای بستن برنامه، پنجرهٔ ترمینال را با Ctrl+C ببندید.')
    try:webbrowser.open(url)
    except Exception:pass
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nدر حال بستن برنامه...')
    finally:server.server_close()

if __name__=='__main__':run_server()
