"""Genera index.html (página pública) a partir del tablero fuente.

Uso: python3 scripts/build.py RUTA/consumo-mk-house.html
La página lee sus datos de data.json en el mismo directorio.
"""
import sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text()
root = pathlib.Path(__file__).resolve().parent.parent
shim = ('<script>window.claude={use:async n=>{if(n!=="db")return null;let D={};'
        'try{D=await fetch("data.json?t="+Date.now(),{cache:"no-store"}).then(r=>r.json())}catch(e){}'
        'return{collection:c=>({onSnapshot:f=>{setTimeout(()=>f({docs:Object.entries(D[c]||{})'
        '.map(([id,d])=>({id,exists:true,data:()=>d}))}),0);return()=>{}}})}}};</script>')
head = '''<!doctype html><html lang="es-MX"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Consumo MK"><meta name="robots" content="noindex,nofollow">
<link rel="apple-touch-icon" href="icon.png"><link rel="icon" href="icon.png">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,-apple-system,sans-serif}[hidden]{display:none!important}img{max-width:100%}</style>
</head><body>'''
(root / "index.html").write_text(head + shim + src + "</body></html>")
print("index.html", len(head + shim + src))
