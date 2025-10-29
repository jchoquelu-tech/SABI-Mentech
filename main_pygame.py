# main_pygame.py — Pygame + Chat tipo WhatsApp + Intro de video (8s) + Lógica híbrida
# - Tipografía robusta (evita "cuadritos" de caracteres).
# - Chat mejor presentado (párrafos/viñetas) + avatar Sabi circular.
# - Video de intro: assets/intro.(mp4|webm|mov|avi); fallback: animación con mascota.
# - Panel de chat minimizable.

import pygame
import sys
import os
import json
import sqlite3
import time
import uuid
import re
import unicodedata
import random
import string
import io
from typing import Optional, Tuple


# ====== Motores / Lógica común ======
import api_motor_gemini as sabi  # NLU, chat, ítems, sugerencias

try:
    from logica_bkt import obtener_nueva_probabilidad
except ImportError:
    print("ADVERTENCIA: No se encontró 'logica_bkt.py'. Usando BKT de reserva.")
    def obtener_nueva_probabilidad(user_id, concepto_id, eventos_bkt):
        if not eventos_bkt: return 0.25
        ultimo_acierto = eventos_bkt[-1][0]
        return 0.5 + (0.2 if ultimo_acierto else -0.2)

try:
    import setup_database
except ImportError:
    print("ADVERTENCIA: No se encontró 'setup_database.py'.")
    class SetupDatabaseDummy:
        def create_database(self):
            print("Ejecutando setup_database (dummy)...")
    setup_database = SetupDatabaseDummy()

# ====== Constantes de Archivos y DB ======
DB_NAME = 'usuarios.db'
NODOS_FILE = 'grafo_conocimiento_NODOS.json'
ARISTAS_FILE = 'grafo_conocimiento_ARISTAS.json'
ITEMS_FILE = 'banco_items.json'
ASSETS_DIR = "assets"

# ====== Constantes UI ======
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
FPS = 60

# Paleta
COLOR_BG       = (247, 248, 252)
COLOR_SURFACE  = (255, 255, 255)
COLOR_TEXT     = (16, 24, 40)
COLOR_MUTED    = (100, 116, 139)
COLOR_PRIMARY  = (108, 99, 255)
COLOR_ACCENT   = (0, 184, 148)
COLOR_DANGER   = (231, 76, 60)
COLOR_GREEN_BG = (220, 252, 231)
COLOR_RED_BG   = (254, 226, 226)
COLOR_AMBER_BG = (254, 243, 199)
COLOR_ASSIST   = (31, 42, 58)   # burbuja asistente
COLOR_USER     = (7, 94, 84)    # burbuja usuario
COLOR_PANEL    = (240, 242, 247)
COLOR_HEADER   = (230, 232, 240)
COLOR_BORDER   = (220, 225, 235)

# ====== Modo Online/Offline ======
from dotenv import load_dotenv
load_dotenv()
ONLINE_MODE = (sabi.text_model is not None and sabi.json_model is not None)
print("Modo:", "Online" if ONLINE_MODE else "Offline")

# =====================
#  BASE DE DATOS / IO
# =====================

def ensure_schema():
    try:
        setup_database.create_database()
        print("Base de datos verificada.")
    except Exception as e:
        print(f"Error al verificar schema: {e} — usando fallback local.")
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        script = getattr(setup_database, "SCHEMA", None)
        if script:
            cur.executescript(script)
        con.commit(); con.close()

def db():
    return sqlite3.connect(DB_NAME)

def load_nodos():
    with open(NODOS_FILE,'r',encoding='utf-8') as f:
        data = json.load(f)
    return {n['id']: n for n in data}

def load_aristas():
    with open(ARISTAS_FILE,'r',encoding='utf-8') as f:
        return json.load(f)

def load_items():
    try:
        with open(ITEMS_FILE,'r',encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_items(x):
    with open(ITEMS_FILE,'w',encoding='utf-8') as f:
        json.dump(x,f,indent=2,ensure_ascii=False)

# =====================
#  UTILIDADES TEXTO
# =====================

def _norm_txt(s: str) -> str:
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def match_tema(nodos: dict, mundo: str, grado: str, tema_texto: str):
    if not tema_texto: return None
    t = _norm_txt(tema_texto)
    if not t: return None
    candidatos = [(cid, n) for cid, n in nodos.items()
                  if (not mundo or n["materia"] == mundo) and (not grado or n["año"] == grado)]
    scored = []
    for cid, n in candidatos:
        nombre = _norm_txt(n["concepto"])
        if t in nombre:
            scored.append((2.0, cid))
        else:
            hits = sum(1 for w in t.split() if w in nombre)
            if hits > 0:
                scored.append((1.0 + 0.1 * hits, cid))
    if not scored: return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

# Limpieza del texto del modelo para el chat
def clean_text_for_chat(s: str) -> str:
    s = s or ""
    # Elimina emojis y caracteres no ASCII imprimibles
    s = s.encode('ascii', errors='ignore').decode('ascii')
    # Quita caracteres de control y espaciales invisibles
    replacements = {
        "“": "\"", "”": "\"", "’": "'", "‘": "'",
        "–": "-", "—": "-", "•": "·", "●": "·", "▪": "·", "◦": "·",
        "\u00a0": " ", "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": ""
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"(?<=[\.!?])\s+(?=[A-ZÁÉÍÓÚÑ0-9])", "\n", s)
    s = re.sub(r"^\s*-\s*", "· ", s, flags=re.M)
    # Elimina cualquier caracter fuera del rango estándar español
    allowed = string.ascii_letters + string.digits + string.punctuation + " áéíóúÁÉÍÓÚñÑ¿?¡!.,:;()[]{}<>\"'\\/+-*=_%\n"
    s = "".join(c for c in s if c in allowed)
    return s.strip()

# =====================
#  PRIOR / PERFIL
# =====================

def _grado_to_num(grado: str) -> int:
    if not grado: return 3
    g = grado.strip().lower()
    if g.startswith("1"): return 1
    if g.startswith("2"): return 2
    if g.startswith("3"): return 3
    if g.startswith("4"): return 4
    if g.startswith("5"): return 5
    for n in "12345":
        if n in g: return int(n)
    return 3

def compute_depths(nodos: dict, aristas: list) -> dict:
    parents = {cid:set() for cid in nodos}
    for e in aristas:
        parents.setdefault(e['a'], set()).add(e['de'])
        parents.setdefault(e['de'], set())
    depth = {cid: None for cid in nodos}
    roots = [cid for cid,ps in parents.items() if len(ps)==0]
    for r in roots: depth[r] = 0
    changed = True
    while changed:
        changed = False
        for cid in nodos:
            if depth[cid] is None and all(depth[p] is not None for p in parents[cid]):
                depth[cid] = (max(depth[p] for p in parents[cid]) + 1) if parents[cid] else 0
                changed = True
    for cid,v in depth.items():
        if v is None: depth[cid] = 2
    return depth

def prior_inicial_concepto(concepto_id: str, nodos: dict, aristas: list, grado_usuario: str, depths_cache: dict=None) -> float:
    if depths_cache is None: depths_cache = compute_depths(nodos, aristas)
    d = depths_cache.get(concepto_id, 2)
    base = 0.60 if d==0 else (0.50 if d==1 else (0.40 if d==2 else 0.30))
    g_user = _grado_to_num(grado_usuario)
    g_conc = _grado_to_num(nodos[concepto_id]['año'])
    delta = g_user - g_conc
    if delta > 0:  base += 0.05 * min(delta, 3)
    elif delta < 0: base -= 0.10 * min(-delta, 2)
    return max(0.05, min(0.85, base))

def get_or_create_user(user_id, nombre=None):
    con=db(); cur=con.cursor()
    cur.execute("SELECT id_usuario FROM usuarios WHERE id_usuario=?",(user_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO usuarios(id_usuario,nombre,fecha_registro) VALUES(?,?,?)",
                    (user_id, nombre or user_id, int(time.time())))
        con.commit()
    con.close()

def get_user_profile(user_id, nodos, aristas, grado_usuario):
    con=db(); cur=con.cursor()
    cur.execute("SELECT concepto_id, prob_maestria FROM dominio_usuario WHERE id_usuario=?", (user_id,))
    rows = cur.fetchall()
    con.close()
    return dict(rows)

def update_user_prob(user_id, concepto_id, p):
    con=db(); cur=con.cursor()
    cur.execute("""
        UPDATE dominio_usuario
        SET prob_maestria = ?
        WHERE id_usuario = ? AND concepto_id = ?
    """, (p, user_id, concepto_id))
    con.commit(); con.close()

def get_user_history(user_id, concepto_id):
    con=db(); cur=con.cursor()
    cur.execute("""SELECT correcta, tiempo_ms, pistas_usadas, timestamp
                   FROM historial_respuestas
                   WHERE id_usuario=? AND concepto_id=?
                   ORDER BY timestamp DESC LIMIT 10""", (user_id, concepto_id))
    rows = cur.fetchall()
    con.close()
    return [(int(c), int(t_ms or 0), int(p or 0), int(ts)) for c,t_ms,p,ts in rows]

def start_session(user_id, objetivo, mundo, grado, tema):
    sid=str(uuid.uuid4())
    con=db(); cur=con.cursor()
    cur.execute("""INSERT INTO sesiones(sesion_id,id_usuario,objetivo,mundo,grado,tema,fecha_inicio)
                   VALUES(?,?,?,?,?,?,?)""",
                (sid,user_id,objetivo,mundo,grado,tema,int(time.time())))
    con.commit(); con.close()
    return sid

def log_respuesta(sid, user_id, objetivo, mundo, grado, tema, concepto_id,
                  item, correcta, opcion, t_inicio_ms, pistas):
    con=db(); cur=con.cursor()
    cur.execute("""INSERT INTO historial_respuestas
      (sesion_id,id_usuario,concepto_id,item_id,correcta,opcion_elegida,dificultad_item,pistas_usadas,timestamp,objetivo,mundo,grado,tema,tiempo_ms)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (sid,user_id,concepto_id,item['item_id'], int(correcta), opcion, item.get('dificultad','media'),
       int(pistas), int(time.time()), objetivo, mundo, grado, tema, int(pygame.time.get_ticks() - t_inicio_ms)))
    # Asegura fila en dominio_usuario
    cur.execute("""
        INSERT INTO dominio_usuario (id_usuario, concepto_id, prob_maestria, intentos)
        VALUES (?, ?, 0.0, 0)
        ON CONFLICT(id_usuario, concepto_id) DO NOTHING;
    """, (user_id, concepto_id))
    cur.execute("UPDATE dominio_usuario SET intentos=COALESCE(intentos,0)+1 WHERE id_usuario=? AND concepto_id=?",
                (user_id, concepto_id))
    con.commit(); con.close()

# =====================
#  RECOMENDADOR / ÍTEMS
# =====================

def filtrar_ids(nodos, mundo=None, grado=None):
    return {cid for cid,n in nodos.items() if (not mundo or n['materia']==mundo) and (not grado or n['año']==grado)}

def prereqs_map(aristas):
    mp={}
    for e in aristas:
        mp.setdefault(e['a'], set()).add(e['de'])
        mp.setdefault(e['de'], set())
    return mp

def get_prereqs(concepto_id, aristas):
    return [e['de'] for e in aristas if e['a']==concepto_id]

def get_successors(concepto_id, aristas):
    return [e['a'] for e in aristas if e['de']==concepto_id]

def recomendar_ruta(perfil, ids, aristas, k=5, thr=0.6):
    pre=prereqs_map(aristas)
    candidatos=[]
    for cid in ids:
        pres=pre.get(cid,set())
        if all(perfil.get(p,0.0)>=thr for p in pres):
            candidatos.append((cid, perfil.get(cid,0.0)))
    candidatos.sort(key=lambda x:x[1])
    return [cid for cid,_ in candidatos[:k]]

def apply_heuristic_propagation(user_id, concepto_fallado_id, aristas, perfil):
    DECAY=0.15
    pres=[e['de'] for e in aristas if e['a']==concepto_fallado_id]
    for p in pres:
        new=max(0.01, perfil.get(p,0.0)-DECAY)
        update_user_prob(user_id,p,new)
        perfil[p] = new

def mastery_summary(user_id, mundo, nodos, grado=None, only_attempted=False, fallback=0.0):
    con=db(); cur=con.cursor()
    cur.execute("SELECT concepto_id, prob_maestria, intentos FROM dominio_usuario WHERE id_usuario=?", (user_id,))
    rows = cur.fetchall()
    con.close()
    m = {c:(p,i) for c,p,i in rows}
    ids = [cid for cid,n in nodos.items() if n['materia']==mundo and (grado is None or n['año']==grado)]
    if not ids: return 0.0, []
    if only_attempted:
        ids = [cid for cid in ids if m.get(cid,(fallback,0))[1] > 0]
        if not ids: return 0.0, []
    prom = sum(m.get(cid,(fallback,0))[0] for cid in ids) / len(ids)
    debiles = sorted([(cid, m.get(cid,(fallback,0))[0]) for cid in ids], key=lambda x:x[1])[:8]
    return prom, debiles

def rule_suggestions(concepto_id, perfil, aristas, nodos, thr_weak=0.45, thr_ready=0.65, maxn=4):
    pre = prereqs_map(aristas)
    pres = list(pre.get(concepto_id, set()))
    weak_pr = [p for p in pres if perfil.get(p,0.0) < thr_weak]
    weak_pr_sorted = sorted(weak_pr, key=lambda cid: perfil.get(cid,0.0))[:maxn]
    succ = get_successors(concepto_id, aristas)
    advance = []
    for s in succ:
        s_pres = pre.get(s, set())
        if s_pres and all(perfil.get(x,0.0) >= thr_ready for x in s_pres):
            advance.append(s)
    advance_sorted = sorted(advance, key=lambda cid: perfil.get(cid,0.0), reverse=True)[:maxn]
    return weak_pr_sorted, advance_sorted

def build_estado_estudiante(user_id, ctx, concepto_id, nodos, aristas, perfil):
    hist = get_user_history(user_id, concepto_id)
    ultimos3 = hist[:3]
    aciertos_ultimos_3 = sum(1 for c,_,_,_ in ultimos3 if c==1)
    tiempo_prom_ms = int(sum(t for _,t,_,_ in ultimos3)/len(ultimos3)) if ultimos3 else 60000
    pistas_ult = sum(p for _,_,p,_ in ultimos3)
    mastery_actual = perfil.get(concepto_id, 0.0)
    pres = get_prereqs(concepto_id, aristas)
    succ = get_successors(concepto_id, aristas)
    por_mundo = {}
    for m in {n['materia'] for n in nodos.values()}:
        ids = [cid for cid,n in nodos.items() if n['materia']==m]
        if ids:
            por_mundo[m] = sum(perfil.get(cid,0.0) for cid in ids) / len(ids)
    return {
      "usuario": {"id": user_id, "nombre": None},
      "contexto": {"objetivo": ctx["objetivo"], "mundo": ctx["mundo"], "grado": ctx["grado"]},
      "concepto_actual": {"id": concepto_id, "nombre": nodos[concepto_id]['concepto']},
      "grafo_concepto_actual": {
        "prerrequisitos": [{"id":p, "nombre": nodos[p]['concepto']} for p in pres],
        "sucesores": [{"id":s, "nombre": nodos[s]['concepto']} for s in succ]
      },
      "desempeno": {
        "aciertos_ultimos_3": aciertos_ultimos_3,
        "tiempo_prom_ms": tiempo_prom_ms,
        "pistas_usadas_ultimas": pistas_ult,
        "hints_total": pistas_ult,
        "confianza_autoreportada": "media"
      },
      "mastery": {"concepto_actual": mastery_actual, "por_mundo": por_mundo},
      "intereses": {"me_gusta": [], "tiempo_por_mundo_ms": {}, "preferencia_dificultad": "media"}
    }

# =====================
#  ÍTEMS / SUGERENCIAS (HÍBRIDO)
# =====================

def get_question_hybrid(concepto_id, nodos, dificultad="media", evitar_ids=None):
    banco = load_items()
    evitar_ids = set(evitar_ids or [])
    cand = [it for it in banco if it['concepto_id'] == concepto_id and it.get('item_id') not in evitar_ids]
    if cand: return random.choice(cand)
    n = nodos[concepto_id]
    if ONLINE_MODE:
        it = sabi.generar_item_con_explicacion(concepto_id, n['concepto'], n['año'], n['materia'], dificultad=dificultad)
        if it:
            banco.append(it); save_items(banco)
            return it
    return sabi._fallback_item(concepto_id, n['concepto'], n['año'], n['materia'], dificultad)

def get_adaptive_suggestion_hybrid(user_id, ctx, concepto_id, nodos, aristas, perfil):
    if ONLINE_MODE:
        try:
            estado = build_estado_estudiante(user_id, ctx, concepto_id, nodos, aristas, perfil)
            sug = sabi.sugerir_siguiente_concepto(estado)
            if sug and isinstance(sug, dict):
                siguiente_id = sug.get("siguiente_concepto", {}).get("id")
                if siguiente_id and siguiente_id in nodos:
                    return {
                        "decision": sug.get("decision"),
                        "siguiente_id": siguiente_id,
                        "razon": sug.get("siguiente_concepto", {}).get("razon", "Sugerencia de Sabi IA"),
                        "alternativas": sug.get("alternativas", [])
                    }
        except Exception as e:
            print("Error sugerencia online:", e)
    weak_ids, adv_ids = rule_suggestions(concepto_id, perfil, aristas, nodos)
    if adv_ids:
        return {"decision":"avanzar","siguiente_id":adv_ids[0],"razon":"Listo para avanzar (local)",
                "alternativas":[{"id":cid,"nombre":nodos[cid]['concepto'],"tipo":"avance"} for cid in adv_ids]}
    return {"decision":"reintentar","siguiente_id":concepto_id,"razon":"Reintentar para reforzar (local)",
            "alternativas":[{"id":cid,"nombre":nodos[cid]['concepto'],"tipo":"prerrequisito"} for cid in weak_ids]}

# =====================
#  TIPOGRAFÍA / RENDER
# =====================

def load_font_chain(size: int, bold=False) -> pygame.font.Font:
    """Carga la primera tipografía disponible de la cadena preferida (evita 'cuadritos')."""
    candidates = ["Segoe UI", "Arial", "Noto Sans", "DejaVu Sans", "Calibri", "Tahoma", "Verdana"]
    for name in candidates:
        try:
            path = pygame.font.match_font(name, bold=bold)
            if path:
                return pygame.font.Font(path, size)
        except Exception:
            pass
    # Fallback
    return pygame.font.SysFont(None, size, bold=bold)

def draw_text(surface, text, x, y, font, color=COLOR_TEXT, align="left", max_width=None):
    words = (text or "").split(' ')
    lines, cur = [], ""
    for w in words:
        if max_width:
            t = (cur + " " + w).strip()
            if font.size(t)[0] <= max_width:
                cur = t
            else:
                lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    py = y
    for line in lines:
        s = font.render(line, True, color)
        r = s.get_rect()
        if align == "left":   r.topleft  = (x, py)
        if align == "center": r.midtop   = (x, py)
        if align == "right":  r.topright = (x, py)
        surface.blit(s, r)
        py += font.get_linesize()
    return py

def _pct_to_color_bg(p):
    if p < 0.4: return COLOR_RED_BG
    if p < 0.7: return COLOR_AMBER_BG
    return COLOR_GREEN_BG

# Botones genéricos
class Button:
    def __init__(self, rect, text, callback_id,
                 color=COLOR_SURFACE, hover_color=COLOR_PRIMARY,
                 text_color=COLOR_TEXT, text_hover_color=(255,255,255)):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.callback_id = callback_id
        self.color = color
        self.hover_color = hover_color
        self.text_color = text_color
        self.text_hover_color = text_hover_color
        self.hovered = False
        self.clicked = False
    def handle_event(self, event):
        self.hovered = False
        self.clicked = False
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos): self.clicked = True
        return self.clicked
    def draw(self, surface, font):
        bg = self.hover_color if self.hovered else self.color
        fg = self.text_hover_color if self.hovered else self.text_color
        pygame.draw.rect(surface, bg, self.rect, border_radius=12)
        pygame.draw.rect(surface, COLOR_MUTED, self.rect, width=1, border_radius=12)
        draw_text(surface, self.text, self.rect.centerx, self.rect.centery - font.get_linesize()//2, font, fg, "center")

def _cb_is(cb, name: str) -> bool:
    if isinstance(cb, str): return cb == name
    if isinstance(cb, (tuple, list)) and cb: return cb[0] == name
    return False

# =====================
#  AVATAR / MASCOTA
# =====================

def circular_crop(surface: pygame.Surface, diameter: int) -> pygame.Surface:
    """Devuelve una superficie recortada en círculo con alpha."""
    surf = pygame.transform.smoothscale(surface, (diameter, diameter)).convert_alpha()
    mask = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255,255,255,255), (diameter//2, diameter//2), diameter//2)
    surf.blit(mask, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
    return surf

def load_svg_as_surface(path_svg: str, wh=(36,36)) -> Optional[pygame.Surface]:
    if not os.path.exists(path_svg):
        return None
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(url=path_svg, output_width=wh[0], output_height=wh[1])
        return pygame.image.load(io.BytesIO(png_bytes)).convert_alpha()
    except Exception:
        # Fallback: PNG con mismo nombre
        alt = os.path.splitext(path_svg)[0] + ".png"
        if os.path.exists(alt):
            try:
                return pygame.image.load(alt).convert_alpha()
            except Exception:
                return None
    return None

# =====================
#  Burbujas de chat
# =====================

def render_bubble(surface, text, x, y_bottom, font, max_width, align_left=True, color_bg=(255,255,255), text_color=(255,255,255), avatar=None):
    """Dibuja una burbuja anclada a y_bottom (de abajo hacia arriba). Devuelve nueva y_bottom."""
    text = clean_text_for_chat(text)
    # Partir por párrafos
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    wrapped = []
    line_h = font.get_linesize()
    inner_w = int(max_width * 0.72)
    for p in paras:
        words = p.split(" ")
        cur = ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.size(t)[0] <= inner_w:
                cur = t
            else:
                if cur: wrapped.append(cur)
                cur = w
        if cur: wrapped.append(cur)
        wrapped.append("")  # separador
    if wrapped and wrapped[-1] == "": wrapped.pop()

    pad_x, pad_y = 14, 10
    bw = min(int(max_width * 0.78), max(180, min(inner_w + pad_x*2, int(max_width*0.92))))
    text_w = bw - pad_x*2
    h = len(wrapped) * line_h + pad_y*2

    bx = x if align_left else x - bw
    by = y_bottom - h
    bubble = pygame.Rect(bx, by, bw, h)

    # Avatar (solo asistente)
    if align_left and avatar:
        ax = bx - 44
        ay = y_bottom - 36
        surface.blit(avatar, (ax, ay))

    pygame.draw.rect(surface, color_bg, bubble, border_radius=12)
    pygame.draw.rect(surface, (0,0,0), bubble, width=1, border_radius=12)

    # Cola
    if align_left:
        tail = [(bx+10, by+h-14), (bx-6, by+h-6), (bx+10, by+h-2)]
    else:
        tail = [(bx+bw-10, by+h-14), (bx+bw+6, by+h-6), (bx+bw-10, by+h-2)]
    pygame.draw.polygon(surface, color_bg, tail)

    ty = by + pad_y
    for line in wrapped:
        if line == "":
            ty += int(line_h * 0.6)
            continue
        draw_text(surface, line, bx + pad_x, ty, font, text_color, "left", max_width=text_w)
        ty += line_h

    return by - 8

# =====================
#  CHAT LOGIC
# =====================

def process_chat_message(msg: str, g, nodos, aristas, MUNDOS, GRADOS):
    text = (msg or "").strip()
    if not text: return
    g["chat_log"].append(("user", text))

    if not g["user_id"]:
        g["chat_log"].append(("assistant", "Primero ingresa tu **ID de estudiante** arriba y presiona Enter."))
        return

    cmd = sabi.interpretar_comando(text)

    if not g["ctx"]:
        intent = sabi.interpretar_intencion_usuario(text, set(MUNDOS), set(GRADOS))
        if intent.get("objetivo") and intent.get("mundo"):
            if not intent.get("grado"):
                intent["grado"] = "5to de secundaria" if intent["objetivo"]=="pre_u" and "5to de secundaria" in GRADOS else GRADOS[0]
            tema_detectado = None
            m_tema = re.search(r"tema\s*[:\- ]\s*([a-z0-9 áéíóúñ]+)", text, re.I)
            if m_tema:
                tema_detectado = m_tema.group(1).strip()
            else:
                m_lo = _norm_txt(text)
                for clave in ["polinomio","polinomios","fraccion","fracciones","ecuacion","ecuaciones","logaritmo","logaritmos",
                              "angulo","ángulo","angulos","triangulo","triángulo","circunferencia","matrices","determinantes",
                              "funcion","funciones","derivada","integral","media","mediana","moda"]:
                    if clave in m_lo: tema_detectado = clave; break
            g["tema"] = tema_detectado
            if tema_detectado:
                cid_tema = match_tema(nodos, intent["mundo"], intent["grado"], tema_detectado)
                if cid_tema: g["override_next"] = cid_tema

            g["ctx"] = {"objetivo":intent["objetivo"], "mundo":intent["mundo"], "grado":intent["grado"]}
            g["sid"] = start_session(g["user_id"], intent["objetivo"], intent["mundo"], intent["grado"], g["tema"])
            g["chat_log"].append(("assistant", f"¡Listo! Trabajaremos **{intent['mundo']}** ({intent['grado']}) con objetivo **{intent['objetivo']}**."))
            g["perfil"] = get_user_profile(g["user_id"], nodos, aristas, g["ctx"]["grado"])
            g["game_state"] = "MAP"
            return
        else:
            g["chat_log"].append(("assistant","Para empezar, dime tu **objetivo** (repasar/explorar/pre_u), el **mundo** y el **año**."))
            return

    # Con contexto: chat pedagógico + comandos
    respuesta = sabi.sabi_chat(text, g["ctx"])
    if respuesta:
        g["chat_log"].append(("assistant", clean_text_for_chat(respuesta)))

    if cmd["cmd"] == "set_dificultad":
        g["prefs"]["dificultad"] = cmd["nivel"]
        g["chat_log"].append(("assistant", f"Dificultad ajustada a **{cmd['nivel']}**."))
    elif cmd["cmd"] == "quiz_len":
        g["prefs"]["quiz_len"] = max(3, min(30, cmd["n"]))
        g["chat_log"].append(("assistant", f"Haré quizzes de **{g['prefs']['quiz_len']}** preguntas."))
    elif cmd["cmd"] == "pausar" and g["sid"]:
        con=db(); cur=con.cursor()
        cur.execute("UPDATE sesiones SET estado='pausada', fecha_fin=? WHERE sesion_id=?", (int(time.time()), g["sid"]))
        con.commit(); con.close()
        g["chat_log"].append(("assistant", "Sesión pausada. Cuando quieras, escribe *retomar*."))
    elif cmd["cmd"] == "retomar":
        con=db(); cur=con.cursor()
        cur.execute("""SELECT sesion_id, objetivo, mundo, grado, tema
                       FROM sesiones WHERE id_usuario=?
                       ORDER BY fecha_inicio DESC LIMIT 1""",(g["user_id"],))
        r=cur.fetchone(); con.close()
        if r:
            g["sid"]=r[0]; g["ctx"]={"objetivo":r[1],"mundo":r[2],"grado":r[3]}; g["tema"]=r[4]
            g["chat_log"].append(("assistant","Sesión retomada. Continuemos."))
            g["game_state"] = "MAP"
    elif cmd["cmd"] == "resumen":
        prom, debiles = mastery_summary(g["user_id"], g["ctx"]["mundo"], nodos, grado=g["ctx"]["grado"], only_attempted=True, fallback=0.0)
        texto = f"Promedio en **{g['ctx']['mundo']} · {g['ctx']['grado']}** (temas practicados): {prom*100:.0f}%\n\n**Pendientes clave:**\n" + \
                "\n".join(f"- {nodos[cid]['concepto']} — {p*100:.0f}%" for cid,p in debiles[:5])
        g["chat_log"].append(("assistant", clean_text_for_chat(texto)))
    elif cmd["cmd"] == "cambiar_tema":
        g["ctx"] = None; g["current_item"] = None; g["usados_items"] = []
        g["quiz_count"] = 0; g["aciertos"] = 0
        g["chat_log"].append(("assistant","Ok, dime el nuevo objetivo/mundo/año."))
        g["game_state"] = "MENU"
    elif cmd["cmd"] == "decision":
        base = g.get("current_cid")
        perfil_tmp = get_user_profile(g["user_id"], nodos, aristas, g["ctx"]["grado"])
        weak_ids, adv_ids = rule_suggestions(base, perfil_tmp, aristas, nodos) if base else ([],[])
        tema_cid = None
        if cmd.get("tema_text"):
            tema_cid = match_tema(nodos, g["ctx"]["mundo"], g["ctx"]["grado"], cmd["tema_text"])
        accion = cmd.get("accion")
        if accion == "reintentar" and base:
            target = base
        elif accion == "repasar":
            target = tema_cid or (weak_ids[0] if weak_ids else None)
        elif accion == "avanzar":
            target = tema_cid or (adv_ids[0] if adv_ids else None)
        else:
            target = None
        if target:
            g["current_cid"] = target
            g["current_item"] = None
            g["quiz_count"] = 0
            g["aciertos"] = 0
            g["usados_items"] = []
            g["game_state"] = "PRACTICE"
            g["chat_log"].append(("assistant", f"Vamos con **{nodos[target]['concepto']}**."))
        else:
            g["chat_log"].append(("assistant","No identifiqué un tema adecuado. Prueba *repasar [tema]* o *avanzar [tema]*."))

# =====================
#  PANEL DE CHAT
# =====================

def render_chat_panel(screen, g, FONT_REG, FONT_SMALL):
    ui = {}
    if g["chat_minimized"]:
        open_btn = pygame.Rect(12, 12, 44, 44)
        pygame.draw.rect(screen, COLOR_PRIMARY, open_btn, border_radius=22)
        draw_text(screen, "💬", open_btn.centerx, open_btn.centery-12, FONT_REG, (255,255,255), "center")
        ui["open"] = open_btn
        g["last_chat_ui"] = ui
        return ui

    CHAT_W = 380
    panel  = pygame.Rect(0, 0, CHAT_W, SCREEN_HEIGHT)
    pygame.draw.rect(screen, COLOR_PANEL, panel)

    header = pygame.Rect(0, 0, CHAT_W, 54)
    pygame.draw.rect(screen, COLOR_HEADER, header)
    # Avatar + título
    if g["mascota"]:
        screen.blit(g["mascota"], (10, 9))
    draw_text(screen, "Sabi · Conversación", 60, 14, FONT_REG, COLOR_TEXT, "left")
    # Minimizar
    min_btn = pygame.Rect(CHAT_W-44, 8, 36, 36)
    pygame.draw.rect(screen, COLOR_MUTED, min_btn, border_radius=8)
    draw_text(screen, "—", min_btn.centerx, min_btn.centery-12, FONT_REG, (255,255,255), "center")

    # Inbox
    inbox = pygame.Rect(8, 60, CHAT_W-16, SCREEN_HEIGHT-60-68)
    pygame.draw.rect(screen, COLOR_SURFACE, inbox, border_radius=10)
    pygame.draw.rect(screen, COLOR_BORDER,  inbox, width=1, border_radius=10)

    # Mensajes (bottom-up)
    y = inbox.bottom - 10
    max_bubble_w = inbox.width - 20
    msgs = g["chat_log"][-200:]
    avatar = g["mascota_small"]

    for role, content in reversed(msgs):
        is_user = (role == "user")
        color_bg = COLOR_USER if is_user else COLOR_ASSIST
        text_col = (255,255,255)
        if is_user:
            y = render_bubble(screen, content, inbox.right - 12, y, FONT_REG, max_bubble_w, align_left=False,
                              color_bg=color_bg, text_color=text_col, avatar=None)
        else:
            y = render_bubble(screen, content, inbox.x + 12 + 42, y, FONT_REG, max_bubble_w - 42, align_left=True,
                              color_bg=color_bg, text_color=text_col, avatar=avatar)
        if y < inbox.y + 8: break

    # Input
    input_rect = pygame.Rect(8, SCREEN_HEIGHT-60, CHAT_W-16-56, 52)
    pygame.draw.rect(screen, COLOR_SURFACE, input_rect, border_radius=10)
    pygame.draw.rect(screen, COLOR_BORDER,  input_rect, width=1, border_radius=10)
    text_display = g["chat_input"][-180:] if g["chat_input"] else ""
    draw_text(screen, text_display or "Escribe aquí…", input_rect.x+10, input_rect.y+14, FONT_REG,
              COLOR_TEXT if text_display else COLOR_MUTED, "left")
    send_btn = pygame.Rect(input_rect.right+8, input_rect.y, 48, 52)
    pygame.draw.rect(screen, COLOR_PRIMARY, send_btn, border_radius=10)
    draw_text(screen, "➤", send_btn.centerx, send_btn.centery-12, FONT_REG, (255,255,255), "center")

    ui.update({"panel": panel, "min": min_btn, "inbox": inbox, "input": input_rect, "send": send_btn})
    g["last_chat_ui"] = ui
    return ui

# =====================
#  INTRO: VIDEO / FALLBACK
# =====================

def play_intro_video_or_logo(screen, mascot_surface: pygame.Surface):
    """
    Reproduce un video corto (máx 8 s) desde assets/intro.*.
    Si no hay OpenCV o el archivo no existe, muestra un fallback animado con la mascota.
    Se puede saltar con ESC o SPACE.
    """
    pygame.display.set_caption("Sabi · Cargando…")
    clock = pygame.time.Clock()

    # Buscar un archivo de video en assets
    candidates = ["intro.mp4", "intro.webm", "intro.mov", "intro.avi", "intro.mkv"]
    video_path = None
    for name in candidates:
        p = os.path.join(ASSETS_DIR, name)
        if os.path.exists(p):
            video_path = p
            break

    max_seconds = 8.0
    start_ticks = pygame.time.get_ticks()

    # Intentar con OpenCV (si existe)
    played = False
    if video_path:
        try:
            import cv2, numpy as np
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                while True:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT: return
                        if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_SPACE):
                            cap.release(); played = True; break
                    if played: break

                    ret, frame = cap.read()
                    if not ret: break

                    # Convertir y escalar manteniendo aspecto
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    fh, fw, _ = frame.shape
                    scale = min(SCREEN_WIDTH / fw, SCREEN_HEIGHT / fh)
                    nw, nh = int(fw*scale), int(fh*scale)
                    frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)

                    surface = pygame.surfarray.make_surface(frame.swapaxes(0,1))
                    screen.fill((0,0,0))
                    screen.blit(surface, ((SCREEN_WIDTH-nw)//2, (SCREEN_HEIGHT-nh)//2))
                    pygame.display.flip()
                    clock.tick(60)

                    if (pygame.time.get_ticks() - start_ticks) / 1000.0 >= max_seconds:
                        break
                cap.release()
                played = True
        except Exception as e:
            print("Intro: OpenCV no disponible o error:", e)

    if not played:
        # Fallback: animación simple con la mascota y fade-in
        logo = circular_crop(mascot_surface, 160)
        title_font = load_font_chain(28, bold=True)
        small_font = load_font_chain(16, bold=False)
        t0 = pygame.time.get_ticks()
        while (pygame.time.get_ticks() - t0) / 1000.0 < max_seconds:
            for event in pygame.event.get():
                if event.type == pygame.QUIT: return
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_SPACE):
                    break
            screen.fill((0,0,0))
            # Fade
            elapsed = (pygame.time.get_ticks() - t0) / 1000.0
            alpha = min(1.0, elapsed / 1.2)
            # Fondo suave
            rect = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            rect.set_alpha(int(alpha*255))
            rect.fill((8,12,20))
            screen.blit(rect, (0,0))
            # Logo
            screen.blit(logo, (SCREEN_WIDTH//2 - 80, SCREEN_HEIGHT//2 - 140))
            draw_text(screen, "Sabi — Tutor Inteligente", SCREEN_WIDTH//2, SCREEN_HEIGHT//2 + 10, title_font, (230,235,245), "center")
            draw_text(screen, "Cargando entorno de aprendizaje…", SCREEN_WIDTH//2, SCREEN_HEIGHT//2 + 50, small_font, (200,205,215), "center")
            pygame.display.flip()
            clock.tick(60)

    # Pequeño fade a la app
    fade = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    for a in range(0, 255, 25):
        fade.set_alpha(255 - a)
        fade.fill((0,0,0))
        screen.blit(fade, (0,0))
        pygame.display.flip()
        clock.tick(60)

# =====================
#  MAIN LOOP
# =====================

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(f"Sabi - Tutor Inteligente (Modo: {'Online' if ONLINE_MODE else 'Offline'})")

    # Tipografías (cadena de fallbacks)
    FONT_BOLD  = load_font_chain(24, bold=True)
    FONT_REG   = load_font_chain(16, bold=False)
    FONT_SMALL = load_font_chain(12, bold=False)

    # Datos
    ensure_schema()
    nodos   = load_nodos()
    aristas = load_aristas()
    MUNDOS  = sorted({n['materia'] for n in nodos.values()})
    GRADOS  = sorted({n['año'] for n in nodos.values()})

    # Mascota
    mascot_big = load_svg_as_surface(os.path.join(ASSETS_DIR, "sabi.png"), (36,36))
    if mascot_big is None:
        alt = os.path.join(ASSETS_DIR, "sabi.png")
        if os.path.exists(alt):
            mascot_big = pygame.image.load(alt).convert_alpha()
        else:
            mascot_big = pygame.Surface((36,36), pygame.SRCALPHA)
            pygame.draw.circle(mascot_big, COLOR_PRIMARY, (18,18), 18)
            pygame.draw.circle(mascot_big, (255,255,255), (18,18), 16, 2)
    mascot_big   = circular_crop(mascot_big, 36)
    mascot_small = circular_crop(pygame.transform.smoothscale(mascot_big, (32,32)), 32)

    # ===== Intro (video / fallback) =====
    play_intro_video_or_logo(screen, mascot_big)

    clock = pygame.time.Clock()

    # Estado global
    g = {
        "game_state": "LOGIN",
        "user_id": "",
        "user_id_active": False,

        "ctx": None, "sid": None, "perfil": {},
        "current_cid": None, "current_item": None,
        "selected_option": None,

        "item_feedback": None, "item_feedback_timer": 0, "t0_item": 0,
        "quiz_count": 0, "aciertos": 0,
        "prefs": {"dificultad":"media","quiz_len":3},
        "usados_items": [],

        # Chat
        "chat_log": [("assistant", clean_text_for_chat("¡Hola! Soy Sabi. Pídeme Explorar/Repasar/Pre_U + mundo + año, o escribe un tema (ej.: Quiero repasar funciones)."))],
        "chat_minimized": False, "chat_input":"", "chat_active": False,
        "last_chat_ui": {},
        "mascota": mascot_big, "mascota_small": mascot_small,

        "tema": None, "override_next": None,
        "post_quiz_sug": None
    }

    ui_elements = []
    login_input_rect = pygame.Rect(SCREEN_WIDTH // 2 - 150, SCREEN_HEIGHT // 2 - 20, 300, 40)

    running = True
    while running:
        screen.fill(COLOR_BG)

        # 1) Chat
        chat_ui = render_chat_panel(screen, g, FONT_REG, FONT_SMALL)

        # 2) Layout principal
        X0 = 64 if g["chat_minimized"] else 380 + 16
        MAIN_W = SCREEN_WIDTH - X0 - 8
        CENTER_X = X0 + MAIN_W // 2

        ui_elements = []
        clicked_callback = None

        # ===== LOGIN =====
        if g["game_state"] == "LOGIN":
            draw_text(screen, "Bienvenido a Sabi", CENTER_X, SCREEN_HEIGHT//2 - 110, FONT_BOLD, COLOR_TEXT, "center")
            draw_text(screen, "Ingresa tu ID de estudiante:", CENTER_X, SCREEN_HEIGHT//2 - 60, FONT_REG, COLOR_MUTED, "center")
            login_input_rect = pygame.Rect(CENTER_X - 150, SCREEN_HEIGHT//2 - 20, 300, 40)
            pygame.draw.rect(screen, COLOR_SURFACE, login_input_rect, border_radius=10)
            border_col = COLOR_PRIMARY if g["user_id_active"] else COLOR_MUTED
            pygame.draw.rect(screen, border_col, login_input_rect, width=2, border_radius=10)
            draw_text(screen, g["user_id"] or "", login_input_rect.x+10, login_input_rect.y+10, FONT_REG, COLOR_TEXT, "left")

        # ===== MENU =====
        if g["game_state"] == "MENU":
            if not g["ctx"]: g["ctx"] = {}
            y_pos = 90
            draw_text(screen, "Define tu Misión", CENTER_X, y_pos, FONT_BOLD, COLOR_TEXT, "center")
            y_pos += 80

            draw_text(screen, "1. Elige un Objetivo", X0 + 40, y_pos, FONT_REG, COLOR_MUTED, "left")
            y_pos += 30
            for i, obj in enumerate(["explorar","repasar","pre_u"]):
                btn = Button((X0 + 40 + i*160, y_pos, 150, 40), obj.title(), ("set_objetivo", obj))
                if g["ctx"].get("objetivo")==obj: btn.color = COLOR_ACCENT
                ui_elements.append(btn)
            y_pos += 80

            draw_text(screen, "2. Elige un Mundo", X0 + 40, y_pos, FONT_REG, COLOR_MUTED, "left")
            y_pos += 30
            for i, mundo in enumerate(sorted({n['materia'] for n in nodos.values()})):
                btn = Button((X0 + 40 + i*160, y_pos, 150, 40), mundo.title(), ("set_mundo", mundo))
                if g["ctx"].get("mundo")==mundo: btn.color = COLOR_ACCENT
                ui_elements.append(btn)
            y_pos += 80

            draw_text(screen, "3. Elige tu Grado", X0 + 40, y_pos, FONT_REG, COLOR_MUTED, "left")
            y_pos += 30
            for i, grado in enumerate(sorted({n['año'] for n in nodos.values()})):
                btn = Button((X0 + 40 + i*160, y_pos, 150, 40), grado.title(), ("set_grado", grado))
                if g["ctx"].get("grado")==grado: btn.color = COLOR_ACCENT
                ui_elements.append(btn)

            if g["ctx"].get("objetivo") and g["ctx"].get("mundo") and g["ctx"].get("grado"):
                btn_start = Button((CENTER_X - 100, SCREEN_HEIGHT - 100, 200, 50), "¡Comenzar Aventura!", ("start_session",),
                                   COLOR_PRIMARY, COLOR_ACCENT, text_hover_color=(0,0,0))
                ui_elements.append(btn_start)

        # ===== MAP =====
        if g["game_state"] == "MAP":
            draw_text(screen, f"Mundo: {g['ctx']['mundo']} ({g['ctx']['grado']})", CENTER_X, 50, FONT_BOLD, COLOR_TEXT, "center")
            map_ids = filtrar_ids(nodos, g["ctx"]["mundo"], g["ctx"]["grado"])
            perfil = g["perfil"]
            recomendados = set(recomendar_ruta(perfil, map_ids, aristas))
            y_pos = 120; x_pos = X0 + 20
            for i, cid in enumerate(sorted(map_ids)):
                nodo = nodos[cid]; p = perfil.get(cid, 0.0)
                rect = (x_pos, y_pos, 400, 50)
                btn = Button(rect, f"{nodo['concepto']} ({p*100:.0f}%)", ("select_nodo", cid), color=_pct_to_color_bg(p))
                ui_elements.append(btn)
                if cid in recomendados: pygame.draw.rect(screen, COLOR_PRIMARY, (x_pos - 10, y_pos, 5, 50))
                y_pos += 60
                if y_pos > SCREEN_HEIGHT - 100: y_pos = 120; x_pos += 420

        # ===== PRACTICE =====
        if g["game_state"] == "PRACTICE":
            if not g["current_item"]:
                cid = g["current_cid"]
                g["current_item"] = get_question_hybrid(cid, nodos, dificultad=g["prefs"]["dificultad"], evitar_ids=g["usados_items"])
                g["t0_item"] = pygame.time.get_ticks()
                g["selected_option"] = None
                g["item_feedback"] = None
                g["item_feedback_timer"] = 0
                if not g["current_item"]:
                    g["chat_log"].append(("assistant", f"No hay preguntas locales para {nodos[cid]['concepto']}."))
                    g["game_state"] = "MAP"

            item = g["current_item"]
            if item:
                y_pos = 80
                y_pos = draw_text(screen, f"Practicando: {nodos[g['current_cid']]['concepto']}", CENTER_X, y_pos, FONT_REG, COLOR_MUTED, "center")
                y_pos += 10
                y_pos = draw_text(screen, f"Pregunta {g['quiz_count'] + 1}/{g['prefs']['quiz_len']}", CENTER_X, y_pos, FONT_BOLD, COLOR_TEXT, "center")
                y_pos += 30

                q_rect = pygame.Rect(CENTER_X - 400, y_pos, 800, 150)
                pygame.draw.rect(screen, COLOR_SURFACE, q_rect, border_radius=12)
                pygame.draw.rect(screen, COLOR_MUTED, q_rect, width=1, border_radius=12)
                draw_text(screen, item["pregunta"], q_rect.x + 20, q_rect.y + 20, FONT_REG, COLOR_TEXT, "left", max_width=q_rect.width - 40)
                y_pos += 170

                for i, opcion in enumerate(item["opciones"]):
                    btn = Button((CENTER_X - 400, y_pos + i*60, 800, 50), opcion, ("select_opcion", opcion))
                    if g["selected_option"] == opcion: btn.color = COLOR_AMBER_BG
                    ui_elements.append(btn)

                btn_resp = Button((CENTER_X - 100, SCREEN_HEIGHT - 100, 200, 50), "Responder", ("submit_answer",),
                                  COLOR_PRIMARY, text_hover_color=(0,0,0))
                if not g["selected_option"]: btn_resp.color = COLOR_MUTED
                ui_elements.append(btn_resp)

        # ===== POST_QUIZ =====
        if g["game_state"] == "POST_QUIZ":
            y_pos = 100
            draw_text(screen, "¡Quiz Completado!", CENTER_X, y_pos, FONT_BOLD, COLOR_TEXT, "center")
            y_pos += 50
            draw_text(screen, f"Resultado: {g['aciertos']} / {g['quiz_count']} correctas", CENTER_X, y_pos, FONT_REG, COLOR_TEXT, "center")
            y_pos += 80
            sug = g["post_quiz_sug"]
            if sug:
                draw_text(screen, f"Sugerencia de Sabi: {sug['razon']}", CENTER_X, y_pos, FONT_REG, COLOR_MUTED, "center", max_width=600)
                y_pos += 60
                btn_retry = Button((CENTER_X - 200, y_pos, 400, 50), f"Reintentar: {nodos[g['current_cid']]['concepto']}",
                                   ("select_nodo", g["current_cid"]))
                ui_elements.append(btn_retry); y_pos += 60
                for alt in sug.get("alternativas", [])[:3]:
                    nombre = alt.get('nombre', nodos[alt['id']]['concepto'])
                    tipo = alt.get('tipo', 'repasar')
                    btn_alt = Button((CENTER_X - 200, y_pos, 400, 50), f"{tipo.title()}: {nombre}", ("select_nodo", alt['id']),
                                     color=COLOR_SURFACE, hover_color=COLOR_AMBER_BG)
                    ui_elements.append(btn_alt); y_pos += 60
            btn_map = Button((CENTER_X - 200, y_pos, 400, 50), "Volver al Mapa", ("go_map",),
                             color=COLOR_MUTED, text_color=(255,255,255))
            ui_elements.append(btn_map)

        # 3) Eventos
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.MOUSEBUTTONDOWN:
                pos = event.pos
                # Chat open/close
                if g["chat_minimized"]:
                    if chat_ui.get("open") and chat_ui["open"].collidepoint(pos):
                        g["chat_minimized"] = False
                else:
                    if chat_ui.get("min") and chat_ui["min"].collidepoint(pos):
                        g["chat_minimized"] = True
                    if chat_ui.get("input") and chat_ui["input"].collidepoint(pos):
                        g["chat_active"] = True
                    elif chat_ui.get("panel") and chat_ui["panel"].collidepoint(pos):
                        pass
                    else:
                        g["chat_active"] = False
                    # enviar
                    if chat_ui.get("send") and chat_ui["send"].collidepoint(pos):
                        if g["chat_input"].strip():
                            process_chat_message(g["chat_input"], g, nodos, aristas, MUNDOS, GRADOS)
                            g["chat_input"] = ""

                # login focus
                if g["game_state"] == "LOGIN":
                    g["user_id_active"] = login_input_rect.collidepoint(pos)

            if event.type == pygame.KEYDOWN:
                if g["chat_active"] and not g["chat_minimized"]:
                    if event.key == pygame.K_RETURN:
                        if g["chat_input"].strip():
                            process_chat_message(g["chat_input"], g, nodos, aristas, MUNDOS, GRADOS)
                            g["chat_input"] = ""
                    elif event.key == pygame.K_BACKSPACE:
                        g["chat_input"] = g["chat_input"][:-1]
                    else:
                        if event.unicode and (32 <= ord(event.unicode) <= 126 or event.unicode in "áéíóúñÁÉÍÓÚÑ¿?¡!.,:;()[]{}<>\"'\\/+-*=_% "):
                            g["chat_input"] += event.unicode

                if g["game_state"] == "LOGIN" and g["user_id_active"]:
                    if event.key == pygame.K_RETURN:
                        if g["user_id"]:
                            get_or_create_user(g["user_id"])
                            g["game_state"] = "MENU"
                    elif event.key == pygame.K_BACKSPACE:
                        g["user_id"] = g["user_id"][:-1]
                    else:
                        if event.unicode and len(event.unicode) == 1:
                            g["user_id"] += event.unicode

            # Botones del área principal
            for elem in ui_elements:
                if elem.handle_event(event):
                    clicked_callback = elem.callback_id

        # 4) Transiciones por callbacks
        if g["game_state"] == "MENU":
            if clicked_callback and _cb_is(clicked_callback, "set_objetivo"):
                g["ctx"]["objetivo"] = clicked_callback[1]
            if clicked_callback and _cb_is(clicked_callback, "set_mundo"):
                g["ctx"]["mundo"] = clicked_callback[1]
            if clicked_callback and _cb_is(clicked_callback, "set_grado"):
                g["ctx"]["grado"] = clicked_callback[1]
            if clicked_callback and _cb_is(clicked_callback, "start_session"):
                g["sid"] = start_session(g["user_id"], g["ctx"]["objetivo"], g["ctx"]["mundo"], g["ctx"]["grado"], None)
                g["perfil"] = get_user_profile(g["user_id"], nodos, aristas, g["ctx"]["grado"])
                g["game_state"] = "MAP"

        elif g["game_state"] == "MAP":
            if clicked_callback and _cb_is(clicked_callback, "select_nodo"):
                g["current_cid"] = clicked_callback[1]
                g["game_state"] = "PRACTICE"
                g["current_item"] = None
                g["quiz_count"] = 0
                g["aciertos"] = 0
                g["usados_items"] = []
                g["item_feedback"] = None

        elif g["game_state"] == "PRACTICE":
            if clicked_callback:
                if _cb_is(clicked_callback, "select_opcion"):
                    g["selected_option"] = clicked_callback[1]
                elif _cb_is(clicked_callback, "submit_answer") and g["selected_option"] and not g["item_feedback"]:
                    item = g["current_item"]; op = g["selected_option"]
                    ok = (op == item["respuesta_correcta"])
                    if ok:
                        g["aciertos"] += 1
                        g["item_feedback"] = ("¡Correcto!", COLOR_ACCENT)
                    else:
                        g["item_feedback"] = (f"Incorrecto. La respuesta era: {item['respuesta_correcta']}", COLOR_DANGER)
                        apply_heuristic_propagation(g["user_id"], g["current_cid"], aristas, g["perfil"])
                    g["item_feedback_timer"] = FPS * 2
                    log_respuesta(g["sid"], g["user_id"], g["ctx"]["objetivo"], g["ctx"]["mundo"], g["ctx"]["grado"], g.get("tema"),
                                  g["current_cid"], item, ok, op, g["t0_item"], 0)
                    hist = get_user_history(g["user_id"], g["current_cid"])
                    eventos_bkt = [(c, ts) for c, _, _, ts in hist][::-1]
                    try:
                        nueva = obtener_nueva_probabilidad(g["user_id"], g["current_cid"], eventos_bkt)
                        if nueva is None:
                            prev = g["perfil"].get(g["current_cid"], 0.0)
                            nueva = prev + (0.1 if ok else -0.1)
                    except Exception as e:
                        print("Error BKT:", e)
                        prev = g["perfil"].get(g["current_cid"], 0.0)
                        nueva = prev + (0.1 if ok else -0.1)
                    nueva = max(0.01, min(0.99, float(nueva)))
                    update_user_prob(g["user_id"], g["current_cid"], nueva)
                    g["perfil"][g["current_cid"]] = nueva
                    g["usados_items"].append(item['item_id'])
                    g["quiz_count"] += 1

            if g["item_feedback_timer"] > 0:
                g["item_feedback_timer"] -= 1
                draw_text(screen, g["item_feedback"][0], CENTER_X, SCREEN_HEIGHT - 150, FONT_REG, g["item_feedback"][1], "center")
                ui_elements = [btn for btn in ui_elements if not _cb_is(btn.callback_id, "select_opcion")]
                if g["item_feedback_timer"] == 0:
                    if g["quiz_count"] >= g["prefs"]["quiz_len"]:
                        g["game_state"] = "POST_QUIZ"
                        g["post_quiz_sug"] = get_adaptive_suggestion_hybrid(
                            g["user_id"], g["ctx"], g["current_cid"], nodos, aristas, g["perfil"]
                        )
                    else:
                        g["current_item"] = None; g["selected_option"] = None; g["item_feedback"] = None

        elif g["game_state"] == "POST_QUIZ":
            if clicked_callback:
                if _cb_is(clicked_callback, "select_nodo"):
                    g["current_cid"] = clicked_callback[1]
                    g["game_state"] = "PRACTICE"
                    g["current_item"] = None
                    g["quiz_count"] = 0
                    g["aciertos"] = 0
                    g["usados_items"] = []
                    g["item_feedback"] = None
                elif _cb_is(clicked_callback, "go_map"):
                    g["game_state"] = "MAP"

        # 5) Dibujar botones del área principal
        for elem in ui_elements:
            elem.draw(screen, FONT_REG)

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
