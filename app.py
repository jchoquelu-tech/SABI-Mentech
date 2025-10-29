# app.py — v1.1
import streamlit as st
import json, sqlite3, time, uuid, os, re, unicodedata, random
import api_motor_gemini as sabi
from logica_bkt import obtener_nueva_probabilidad

DB_NAME = 'usuarios.db'
NODOS_FILE = 'grafo_conocimiento_NODOS.json'
ARISTAS_FILE = 'grafo_conocimiento_ARISTAS.json'
ITEMS_FILE = 'banco_items.json'
ASSETS_DIR = "assets"

# --------- Config inicial ----------
START_AT_ZERO = True  # Nuevos conceptos empiezan en 0% (no 25%)

# Estados de flujo de práctica/quiz
QUIZ_ACTIVE = "ACTIVE"
QUIZ_WAIT_DECISION = "WAIT_DECISION"

# ----------------------- BD: Crear/Migrar -----------------------
SCHEMA_BASE = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
  id_usuario TEXT PRIMARY KEY,
  nombre TEXT,
  apodo TEXT,
  preferencias_json TEXT,
  fecha_registro INTEGER
);

CREATE TABLE IF NOT EXISTS dominio_usuario (
  id_usuario     TEXT NOT NULL,
  concepto_id    TEXT NOT NULL,
  prob_maestria  REAL DEFAULT 0.0,        -- arrancar en 0.0
  intentos       INTEGER DEFAULT 0,
  PRIMARY KEY (id_usuario, concepto_id)
);

CREATE TABLE IF NOT EXISTS sesiones (
  sesion_id     TEXT PRIMARY KEY,
  id_usuario    TEXT NOT NULL,
  objetivo      TEXT NOT NULL,
  mundo         TEXT,
  grado         TEXT,
  tema          TEXT,                     -- alineado con setup_database
  estado        TEXT DEFAULT 'activa',
  fecha_inicio  INTEGER NOT NULL,
  fecha_fin     INTEGER
);

CREATE TABLE IF NOT EXISTS historial_respuestas (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sesion_id       TEXT,
  id_usuario      TEXT NOT NULL,
  concepto_id     TEXT NOT NULL,
  item_id         TEXT NOT NULL,
  correcta        INTEGER NOT NULL CHECK(correcta IN (0,1)),
  opcion_elegida  TEXT,
  dificultad_item TEXT,
  pistas_usadas   INTEGER DEFAULT 0,
  timestamp       INTEGER NOT NULL,
  objetivo        TEXT,
  mundo           TEXT,
  grado           TEXT,                   -- alineado con setup_database
  tema            TEXT,                   -- alineado con setup_database
  tiempo_ms       INTEGER,
  me_gusto        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_hist_user_concept ON historial_respuestas(id_usuario, concepto_id);
"""

def ensure_schema():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.executescript(SCHEMA_BASE)

    # Migraciones suaves (añadir columnas si no existen)
    try:
        # historial_respuestas
        cur.execute("PRAGMA table_info(historial_respuestas)")
        cols = {row[1] for row in cur.fetchall()}
        if "dificultad_item" not in cols:
            cur.execute("ALTER TABLE historial_respuestas ADD COLUMN dificultad_item TEXT")
        if "pistas_usadas" not in cols:
            cur.execute("ALTER TABLE historial_respuestas ADD COLUMN pistas_usadas INTEGER DEFAULT 0")
        if "grado" not in cols:
            cur.execute("ALTER TABLE historial_respuestas ADD COLUMN grado TEXT")
        if "tema" not in cols:
            cur.execute("ALTER TABLE historial_respuestas ADD COLUMN tema TEXT")

        # sesiones
        cur.execute("PRAGMA table_info(sesiones)")
        cols_s = {row[1] for row in cur.fetchall()}
        if "tema" not in cols_s:
            cur.execute("ALTER TABLE sesiones ADD COLUMN tema TEXT")
    except Exception as e:
        print("⚠️ Migración suave falló:", e)

    con.commit(); con.close()

def db(): 
    return sqlite3.connect(DB_NAME)

# ----------------------- Carga de datos -----------------------
@st.cache_data
def load_nodos():
    with open(NODOS_FILE,'r',encoding='utf-8') as f:
        data = json.load(f)
    return {n['id']: n for n in data}

@st.cache_data
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

# ----------------------- Utils: texto/tema -----------------------
def _norm_txt(s: str) -> str:
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def match_tema(nodos: dict, mundo: str, grado: str, tema_texto: str):
    """Elige el concepto más parecido al 'tema' dentro de mundo/grado."""
    if not tema_texto: 
        return None
    t = _norm_txt(tema_texto)
    if not t: 
        return None
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
    if not scored: 
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

# ----------------------- Priors dinámicos -----------------------
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

@st.cache_data
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
    if depths_cache is None:
        depths_cache = compute_depths(nodos, aristas)
    d = depths_cache.get(concepto_id, 2)
    base = 0.60 if d==0 else (0.50 if d==1 else (0.40 if d==2 else 0.30))
    g_user = _grado_to_num(grado_usuario)
    g_conc = _grado_to_num(nodos[concepto_id]['año'])
    delta = g_user - g_conc
    if delta > 0:  base += 0.05 * min(delta, 3)
    elif delta < 0: base -= 0.10 * min(-delta, 2)
    return max(0.05, min(0.85, base))

# ----------------------- Perfil / Sesión -----------------------
def get_or_create_user(user_id, nombre=None):
    con=db(); cur=con.cursor()
    cur.execute("SELECT id_usuario FROM usuarios WHERE id_usuario=?",(user_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO usuarios(id_usuario,nombre,fecha_registro) VALUES(?,?,?)", (user_id, nombre or user_id, int(time.time())))
        con.commit()
    con.close()

def get_user_profile(user_id, nodos, aristas, grado_usuario):
    con=db(); cur=con.cursor()
    cur.execute("SELECT concepto_id,prob_maestria,intentos FROM dominio_usuario WHERE id_usuario=?", (user_id,))
    rows = cur.fetchall()
    perfil   = {c:p for c,p,_ in rows}
    intentos = {c:i for c,_,i in rows}
    depths = compute_depths(nodos, aristas)

    for cid in nodos:
        if cid not in perfil:
            prior = 0.0 if START_AT_ZERO else prior_inicial_concepto(cid, nodos, aristas, grado_usuario, depths)
            try:
                cur.execute("INSERT INTO dominio_usuario (id_usuario, concepto_id, prob_maestria, intentos) VALUES (?,?,?,0)", (user_id, cid, prior))
                perfil[cid] = prior
            except sqlite3.IntegrityError:
                pass
        else:
            # Normaliza 0.25 heredado si nunca intentó y quieres arrancar en 0.0
            if intentos.get(cid,0)==0 and abs(perfil[cid]-0.25) < 1e-6:
                prior = 0.0 if START_AT_ZERO else prior_inicial_concepto(cid, nodos, aristas, grado_usuario, depths)
                cur.execute("UPDATE dominio_usuario SET prob_maestria=? WHERE id_usuario=? AND concepto_id=?", (prior, user_id, cid))
                perfil[cid] = prior
    con.commit(); con.close()
    return perfil

def update_user_prob(user_id, concepto_id, p):
    con=db(); cur=con.cursor()
    cur.execute("UPDATE dominio_usuario SET prob_maestria=? WHERE id_usuario=? AND concepto_id=?", (p,user_id,concepto_id))
    con.commit(); con.close()

def get_user_history(user_id, concepto_id):
    con=db(); cur=con.cursor()
    cur.execute("""SELECT correcta, tiempo_ms, pistas_usadas, timestamp
                   FROM historial_respuestas
                   WHERE id_usuario=? AND concepto_id=?
                   ORDER BY id DESC LIMIT 10""", (user_id, concepto_id))
    rows = cur.fetchall()
    con.close()
    return [(int(c), int(t_ms or 0), int(p or 0), int(ts)) for c,t_ms,p,ts in rows]

def start_session(user_id, objetivo, mundo, grado, tema):
    sid=str(uuid.uuid4())
    con=db(); cur=con.cursor()
    cur.execute("INSERT INTO sesiones(sesion_id,id_usuario,objetivo,mundo,grado,tema,fecha_inicio) VALUES(?,?,?,?,?,?,?)",
                (sid,user_id,objetivo,mundo,grado,tema,int(time.time())))
    con.commit(); con.close()
    return sid

def log_respuesta(sid, user_id, objetivo, mundo, grado, tema, concepto_id, item, correcta, opcion, t_inicio_ms, pistas):
    con=db(); cur=con.cursor()
    cur.execute("""INSERT INTO historial_respuestas
      (sesion_id,id_usuario,concepto_id,item_id,correcta,opcion_elegida,dificultad_item,pistas_usadas,timestamp,objetivo,mundo,grado,tema,tiempo_ms)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (sid,user_id,concepto_id,item['item_id'], int(correcta), opcion, item.get('dificultad','media'),
       int(pistas), int(time.time()), objetivo, mundo, grado, tema, int(time.time()*1000 - t_inicio_ms)))
    cur.execute("UPDATE dominio_usuario SET intentos=COALESCE(intentos,0)+1 WHERE id_usuario=? AND concepto_id=?", (user_id, concepto_id))
    con.commit(); con.close()

# ----------------------- Recomendador / Ítems -----------------------
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

def get_question(concepto_id, nodos, explicacion=False, dificultad="media", evitar_ids=None):
    """Recupera/genera ítem evitando repetir item_ids ya usados."""
    evitar_ids = set(evitar_ids or [])
    banco = load_items()
    cand = [it for it in banco if it['concepto_id'] == concepto_id and it.get('item_id') not in evitar_ids]
    if cand:
        return random.choice(cand)
    n = nodos[concepto_id]
    if explicacion:
        it = sabi.generar_item_con_explicacion(concepto_id, n['concepto'], n['año'], n['materia'], dificultad=dificultad)
    else:
        it = sabi.generar_item_para_concepto(concepto_id, n['concepto'], n['año'], n['materia'])
    if it:
        banco.append(it); save_items(banco)
    return it

def apply_heuristic_propagation(user_id, concepto_fallado_id, aristas, perfil):
    DECAY=0.15
    pres=[e['de'] for e in aristas if e['a']==concepto_fallado_id]
    for p in pres:
        new=max(0.01, perfil.get(p,0.0)-DECAY)
        update_user_prob(user_id,p,new)

def mastery_summary(user_id, mundo, nodos, grado=None, only_attempted=False, fallback=0.0):
    con=db(); cur=con.cursor()
    cur.execute("SELECT concepto_id, prob_maestria, intentos FROM dominio_usuario WHERE id_usuario=?", (user_id,))
    rows = cur.fetchall()
    con.close()

    m = {c:(p,i) for c,p,i in rows}  # concepto_id -> (prob, intentos)

    # Filtrar conceptos por mundo y (opcional) grado
    ids = [cid for cid,n in nodos.items() if n['materia']==mundo and (grado is None or n['año']==grado)]
    if not ids: return 0.0, []

    if only_attempted:
        ids = [cid for cid in ids if m.get(cid,(fallback,0))[1] > 0]
        if not ids:
            return 0.0, []

    prom = sum(m.get(cid,(fallback,0))[0] for cid in ids) / len(ids)
    debiles = sorted([(cid, m.get(cid,(fallback,0))[0]) for cid in ids], key=lambda x:x[1])[:8]
    return prom, debiles

def rule_suggestions(concepto_id, perfil, aristas, nodos, thr_weak=0.45, thr_ready=0.65, maxn=4):
    """Devuelve (weak_prereqs_ids, advance_ready_ids) para el concepto."""
    pre = prereqs_map(aristas)
    # Prerrequisitos débiles del concepto actual
    pres = list(pre.get(concepto_id, set()))
    weak_pr = [p for p in pres if perfil.get(p,0.0) < thr_weak]
    weak_pr_sorted = sorted(weak_pr, key=lambda cid: perfil.get(cid,0.0))[:maxn]

    # Sucesores para los que cumple prereqs (temas futuros listos)
    succ = get_successors(concepto_id, aristas)
    advance = []
    for s in succ:
        s_pres = pre.get(s, set())
        if s_pres and all(perfil.get(x,0.0) >= thr_ready for x in s_pres):
            advance.append(s)
    # Orden: más dominio primero
    advance_sorted = sorted(advance, key=lambda cid: perfil.get(cid,0.0), reverse=True)[:maxn]
    return weak_pr_sorted, advance_sorted

# ----------------------- Métricas recientes (para sugerencia) -----------------------
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
            por_mundo[m] = sum(perfil.get(cid,0.0) for cid in ids)/len(ids)

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
      "mastery": {
        "concepto_actual": mastery_actual,
        "por_mundo": por_mundo
      },
      "intereses": {
        "me_gusta": [],
        "tiempo_por_mundo_ms": {},
        "preferencia_dificultad": "media"
      }
    }

# ----------------------- UI & Estilos -----------------------
st.set_page_config(layout="wide", page_title="Sabi · Tutor Adaptativo", page_icon="🧠")
ensure_schema()
nodos=load_nodos(); aristas=load_aristas()
MUNDOS=sorted({n['materia'] for n in nodos.values()})
GRADOS=sorted({n['año'] for n in nodos.values()})

st.markdown("""
<style>
.app-grid{display:grid; grid-template-columns: 1.1fr 1fr; gap:16px;}
@media (max-width: 1100px){ .app-grid{grid-template-columns: 1fr;} }
.chat-card{border:1px solid rgba(255,255,255,.12); border-radius:12px; padding:.75rem; background:rgba(255,255,255,.03);}
.chat-window{height:68vh; overflow-y:auto; padding-right:.5rem;}
.msg-row{display:flex; align-items:flex-end; margin:.35rem 0;}
.msg-row .avatar{width:32px; height:32px; border-radius:50%; object-fit:cover;}
.msg-row.left .bubble{background:#1f2a3a; border:1px solid rgba(255,255,255,.1);}
.msg-row.right{justify-content:flex-end;}
.msg-row.right .bubble{background:#075E54; border:1px solid rgba(0,0,0,.2);}
.bubble{max-width:72%; padding:.5rem .75rem; border-radius:14px; line-height:1.35;}
.name{font-size:.75rem; opacity:.8; margin:.15rem 0 .1rem;}
.card{border:1px solid rgba(255,255,255,.12); border-radius:12px; padding:1rem; background:rgba(255,255,255,.03);}
.callout{border-left:4px solid #6c63ff; background:rgba(108,99,255,.08); padding:.7rem .9rem; border-radius:8px; margin-top:.5rem;}
.callout.pista{border-left-color:#00b894; background:rgba(0,184,148,.08);}
.callout.exp{border-left-color:#0984e3; background:rgba(9,132,227,.08);}
.chips .chip{display:inline-block; padding:.15rem .5rem; margin:.1rem .25rem; border-radius:999px; border:1px solid rgba(255,255,255,.15); font-size:.85rem; opacity:.9;}
.sugg { border-left:4px solid #f39c12; background: rgba(243,156,18,.08); padding:.7rem .9rem; border-radius:8px; }
.badge{display:inline-block;border:1px solid rgba(255,255,255,.2); border-radius:999px; padding:.15rem .5rem; margin:.1rem .25rem; font-size:.8rem;}
</style>
""", unsafe_allow_html=True)

st.title("Sabi — Tutor Adaptativo")

# --------- Estado global ---------
user_id = st.text_input("ID de estudiante", value="estudiante_01")
if user_id: get_or_create_user(user_id)

if "chat" not in st.session_state: st.session_state.chat=[]
if "ctx" not in st.session_state: st.session_state.ctx=None
if "sid" not in st.session_state: st.session_state.sid=None
if "prefs" not in st.session_state: st.session_state.prefs={"dificultad":"media","quiz_len":4}
if "hints" not in st.session_state: st.session_state.hints=0
if "aciertos" not in st.session_state: st.session_state.aciertos=0
if "override_next" not in st.session_state: st.session_state.override_next=None
if "quiz_count" not in st.session_state: st.session_state.quiz_count = 0
if "usados_items" not in st.session_state: st.session_state.usados_items = []
if "tema" not in st.session_state: st.session_state.tema = None
if "current_item" not in st.session_state: st.session_state.current_item = None
if "t0" not in st.session_state: st.session_state.t0 = 0
if "quiz_state" not in st.session_state: st.session_state.quiz_state = QUIZ_ACTIVE
if "decision_ctx" not in st.session_state: st.session_state.decision_ctx = None  # {'concepto_id', 'weak', 'advance', 'sug'}

# Helpers
def _cargar_siguiente_item(concepto_id, nodos, ctx):
    st.session_state.current_item = get_question(
        concepto_id, nodos,
        explicacion=(ctx["objetivo"] == "pre_u"),
        dificultad=st.session_state.prefs["dificultad"],
        evitar_ids=st.session_state.usados_items
    )
    st.session_state.t0 = int(time.time() * 1000)
    st.session_state["radio_opcion"] = None  # limpiar selección del radio

def _set_next_target(cid_target, motivo_txt):
    """Fija siguiente concepto y reactiva el quiz."""
    if not cid_target: return
    st.session_state.override_next = cid_target
    st.session_state.tema = nodos[cid_target]['concepto']
    st.session_state.current_item = None
    st.session_state.usados_items = []
    st.session_state.quiz_count = 0
    st.session_state.aciertos = 0
    st.session_state.quiz_state = QUIZ_ACTIVE
    st.session_state.decision_ctx = None
    st.session_state.chat.append(("assistant", motivo_txt))
    st.rerun()

def _aplicar_decision(desicion_cmd, ctx, perfil, aristas, nodos):
    """Procesa decisión del usuario (reintentar, repasar [tema], avanzar [tema]) vía chat."""
    if st.session_state.quiz_state != QUIZ_WAIT_DECISION:
        st.session_state.chat.append(("assistant","Por ahora no hay un quiz esperando decisión. Continúa practicando o cambia de tema."))
        return
    dc = st.session_state.decision_ctx or {}
    base_cid = dc.get("concepto_id")
    weak = dc.get("weak") or []
    advance = dc.get("advance") or []
    sug = dc.get("sug") or {}

    accion = desicion_cmd.get("accion")
    tema_text = desicion_cmd.get("tema_text")

    # Intentar mapear tema_text a un concepto del contexto actual
    tema_cid = None
    if tema_text:
        tema_cid = match_tema(nodos, ctx["mundo"], ctx["grado"], tema_text)

    if accion == "reintentar":
        target = base_cid
        _set_next_target(target, f"👌 Reintentemos **{nodos[target]['concepto']}** con una variante.")
        return

    if accion == "repasar":
        target = tema_cid or (weak[0] if weak else None)
        if not target:
            st.session_state.chat.append(("assistant","No identifiqué un prerrequisito débil. Puedes escribir: _repasar [tema]_ o _avanzar_."))
            return
        _set_next_target(target, f"🔁 Repasaremos **{nodos[target]['concepto']}** (prerrequisito).")
        return

    if accion == "avanzar":
        # Preferir sugerencia del LLM si válida; luego reglas
        sc = (sug.get("siguiente_concepto") or {}) if isinstance(sug, dict) else {}
        sugg_id = sc.get("id") if sc else None
        cand = tema_cid or (sugg_id if sugg_id in nodos else None) or (advance[0] if advance else None)
        if not cand:
            st.session_state.chat.append(("assistant","No veo un tema listo para avanzar. Puedes _reintentar_ o _repasar [tema]_."))
            return
        _set_next_target(cand, f"➡️ Avanzamos a **{nodos[cand]['concepto']}**.")
        return

# Layout
st.markdown("<div class='app-grid'>", unsafe_allow_html=True)

# ------------------ Panel Izquierdo: Chat ------------------
with st.container():
    st.markdown("<div class='chat-card'>", unsafe_allow_html=True)
    st.subheader("💬 Conversación")
    st.markdown("<div class='chat-window'>", unsafe_allow_html=True)

    for role,content in st.session_state.chat:
        side = "left" if role=="assistant" else "right"
        name = "Sabi" if role=="assistant" else "Tú"
        st.markdown(
            f"""
<div class='msg-row {side}'>
  <div>
    <div class='name'>{name}</div>
    <div class='bubble'>{content}</div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)  # chat-window

    msg = st.chat_input("Escribe tu mensaje… (Ej: 'Explorar Álgebra 4to, tema polinomios')")
    if msg and user_id:
        st.session_state.chat.append(("user", msg))
        cmd = sabi.interpretar_comando(msg)

        if cmd["cmd"]=="set_nombre":
            con=db(); cur=con.cursor()
            cur.execute("UPDATE usuarios SET nombre=? WHERE id_usuario=?", (cmd["nombre"], user_id))
            con.commit(); con.close()
            st.session_state.chat.append(("assistant", f"¡Encantado, {cmd['nombre']}! 😊"))
            st.rerun()

        if not st.session_state.ctx:
            intent = sabi.interpretar_intencion_usuario(msg, set(MUNDOS), set(GRADOS))
            if intent.get("objetivo") and intent.get("mundo"):
                if not intent.get("grado"):
                    intent["grado"]="5to de secundaria" if intent["objetivo"]=="pre_u" and "5to de secundaria" in GRADOS else GRADOS[0]
                # Detectar "tema" desde el mensaje
                tema_detectado = None
                m_tema = re.search(r"tema\s*[:\- ]\s*([a-z0-9 áéíóúñ]+)", msg, re.I)
                if m_tema:
                    tema_detectado = m_tema.group(1).strip()
                else:
                    m_lo = _norm_txt(msg)
                    for clave in ["polinomio","polinomios","fraccion","fracciones","ecuacion","ecuaciones","logaritmo","logaritmos",
                                  "ángulo","angulo","angulos","circunferencia","triangulo","triángulo","matrices","determinantes"]:
                        if clave in m_lo:
                            tema_detectado = clave
                            break
                st.session_state.tema = tema_detectado

                # Fijar concepto inicial por tema si se puede
                nodos_local = load_nodos()
                if tema_detectado:
                    cid_tema = match_tema(nodos_local, intent["mundo"], intent["grado"], tema_detectado)
                    if cid_tema:
                        st.session_state.override_next = cid_tema

                st.session_state.ctx={"objetivo":intent["objetivo"],"mundo":intent["mundo"],"grado":intent["grado"]}
                st.session_state.sid=start_session(user_id, intent["objetivo"], intent["mundo"], intent["grado"], st.session_state.tema)
                st.session_state.chat.append(("assistant", f"¡Listo! Trabajaremos **{intent['mundo']}** ({intent['grado']}) con objetivo **{intent['objetivo']}**."))
                st.rerun()
            else:
                st.session_state.chat.append(("assistant","Para empezar, dime tu **objetivo** (repasar/explorar/pre_u), el **mundo** y el **año**."))
                st.rerun()
        else:
            # Chat pedagógico por defecto
            respuesta = sabi.sabi_chat(msg, st.session_state.ctx)
            st.session_state.chat.append(("assistant", respuesta))

            # Comandos de ajustes
            if cmd["cmd"]=="set_dificultad":
                st.session_state.prefs["dificultad"]=cmd["nivel"]
                st.session_state.chat.append(("assistant", f"Dificultad ajustada a **{cmd['nivel']}**."))
            elif cmd["cmd"]=="quiz_len":
                st.session_state.prefs["quiz_len"]=max(3, min(30, cmd["n"]))
                st.session_state.chat.append(("assistant", f"Haré quizzes de **{st.session_state.prefs['quiz_len']}** preguntas."))
            elif cmd["cmd"]=="pausar" and st.session_state.sid:
                con=db(); cur=con.cursor()
                cur.execute("UPDATE sesiones SET estado='pausada', fecha_fin=? WHERE sesion_id=?", (int(time.time()), st.session_state.sid))
                con.commit(); con.close()
                st.session_state.chat.append(("assistant", "Sesión pausada. Cuando quieras di *retomar*."))
            elif cmd["cmd"]=="retomar":
                con=db(); cur=con.cursor()
                cur.execute("SELECT sesion_id, objetivo, mundo, grado, tema FROM sesiones WHERE id_usuario=? ORDER BY fecha_inicio DESC LIMIT 1",(user_id,))
                r=cur.fetchone(); con.close()
                if r:
                    st.session_state.sid=r[0]; st.session_state.ctx={"objetivo":r[1],"mundo":r[2],"grado":r[3]}
                    st.session_state.tema = r[4]
                    st.session_state.chat.append(("assistant","Sesión retomada. Continuemos."))
            elif cmd["cmd"]=="resumen":
                prom, debiles = mastery_summary(user_id, st.session_state.ctx["mundo"], nodos, grado=st.session_state.ctx["grado"], only_attempted=True, fallback=0.0)
                texto = f"Promedio en **{st.session_state.ctx['mundo']} · {st.session_state.ctx['grado']}** (temas practicados): {prom*100:.0f}%\n\n**Pendientes clave:**\n" + \
                        "\n".join(f"- {nodos[cid]['concepto']} — {p*100:.0f}%" for cid,p in debiles[:5])
                st.session_state.chat.append(("assistant", texto))
            elif cmd["cmd"]=="cambiar_tema":
                st.session_state.ctx=None
                st.session_state.current_item=None
                st.session_state.usados_items=[]
                st.session_state.quiz_count=0
                st.session_state.aciertos=0
                st.session_state.quiz_state = QUIZ_ACTIVE
                st.session_state.decision_ctx = None
                st.session_state.chat.append(("assistant","Ok, dime el nuevo objetivo/mundo/año."))
            elif cmd["cmd"]=="decision":
                # Decisión via chat (reintentar/repasar/avanzar)
                perfil_tmp = get_user_profile(user_id, nodos, aristas, st.session_state.ctx["grado"])
                _aplicar_decision(cmd, st.session_state.ctx, perfil_tmp, aristas, nodos)

            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)  # chat-card

# ------------------ Panel Derecho: Práctica + Sugerencia ------------------
with st.container():
    st.subheader("🎯 Práctica y progreso")
    if st.session_state.ctx and st.session_state.sid:
        ctx = st.session_state.ctx
        perfil = get_user_profile(user_id, nodos, aristas, ctx["grado"])
        ids = filtrar_ids(nodos, ctx["mundo"], ctx["grado"])

        # Chips de contexto (incluye tema)
        st.markdown("<div class='chips'>"
                    f"<span class='chip'>🎯 {ctx['objetivo']}</span>"
                    f"<span class='chip'>🌍 {ctx['mundo']}</span>"
                    f"<span class='chip'>🏫 {ctx['grado']}</span>"
                    f"<span class='chip'>📚 tema: {st.session_state.tema or '—'}</span>"
                    f"<span class='chip'>⚙️ dif: {st.session_state.prefs['dificultad']}</span>"
                    "</div>", unsafe_allow_html=True)

        # Selector visual de tema (ordenado por nombre de concepto)
        label_pairs = sorted([(nodos[cid]['concepto'], cid) for cid in ids], key=lambda x: x[0])
        labels = [lp[0] for lp in label_pairs]
        label_to_cid = {label: cid for label, cid in label_pairs}

        # índice por coincidencia con tema (si lo hay)
        default_idx = 0
        if st.session_state.tema:
            cid_match = match_tema(nodos, ctx["mundo"], ctx["grado"], st.session_state.tema)
            if cid_match:
                name_match = nodos[cid_match]['concepto']
                if name_match in labels:
                    default_idx = labels.index(name_match)

        if labels:
            sel_label = st.selectbox("📚 Elige un tema (filtrado por materia y año)", labels, index=default_idx)
            sel_cid = label_to_cid[sel_label]
        else:
            sel_label = None
            sel_cid = None

        # Si cambian manualmente el tema, forzar siguiente concepto a ese
        if sel_label and st.session_state.tema != sel_label:
            st.session_state.tema = sel_label
            st.session_state.override_next = sel_cid
            st.session_state.current_item = None  # para que cargue nueva pregunta
            # Si estaba esperando decisión, salimos del gating
            st.session_state.quiz_state = QUIZ_ACTIVE
            st.session_state.decision_ctx = None

        # Elegir concepto objetivo (tema > override > recomendación)
        recomendados = recomendar_ruta(perfil, ids, aristas, k=5)
        if st.session_state.override_next:
            concepto_id = st.session_state.override_next
            st.session_state.override_next = None
        elif st.session_state.tema:
            concepto_id = match_tema(nodos, ctx["mundo"], ctx["grado"], st.session_state.tema) or (recomendados[0] if recomendados else None)
        else:
            concepto_id = recomendados[0] if recomendados else (next(iter(ids)) if ids else None)

        if concepto_id:
            nodo = nodos[concepto_id]
            st.markdown(f"<div class='card'><strong>{nodo['materia']} · {nodo['concepto']} ({nodo['año']})</strong></div>", unsafe_allow_html=True)

            c1, c2 = st.columns(2)
            if c1.button("💡 Micro‑lección"):
                ml = sabi.generar_micro_leccion(nodo['concepto'], nodo['año'], nodo['materia'])
                if ml:
                    pasos = ' → '.join(ml.get('pasos',[])) if isinstance(ml.get('pasos',[]), list) else ml.get('pasos','')
                    st.markdown(f"<div class='callout exp'><b>Definición:</b> {ml.get('definicion','')}<br><b>Pasos:</b> {pasos}<br><b>Ejemplo:</b> {ml.get('ejemplo','')}</div>", unsafe_allow_html=True)
            if c2.button("🔁 Cambiar objetivo"):
                st.session_state.ctx=None
                st.session_state.current_item=None
                st.session_state.usados_items=[]
                st.session_state.quiz_count=0
                st.session_state.aciertos=0
                st.session_state.quiz_state = QUIZ_ACTIVE
                st.session_state.decision_ctx = None
                st.session_state.chat.append(("assistant","Ok, dime el nuevo objetivo/mundo/año."))
                st.rerun()

            # GATING: si estamos esperando decisión, no mostraremos otra pregunta.
            if st.session_state.quiz_state == QUIZ_WAIT_DECISION:
                dc = st.session_state.decision_ctx or {}
                weak_ids = dc.get("weak") or []
                adv_ids = dc.get("advance") or []
                st.markdown("<div class='callout'><b>⏸️ Quiz finalizado.</b> Escribe en el chat: <b>reintentar</b>, <b>repasar [tema]</b> o <b>avanzar [tema]</b>.</div>", unsafe_allow_html=True)

                if weak_ids:
                    st.markdown("**Prerrequisitos débiles (para repasar):**")
                    for cid in weak_ids[:5]:
                        st.markdown(f"- 🔁 {nodos[cid]['concepto']} — {perfil.get(cid,0.0)*100:.0f}%")
                if adv_ids:
                    st.markdown("**Temas futuros listos (para avanzar):**")
                    for cid in adv_ids[:5]:
                        st.markdown(f"- ➡️ {nodos[cid]['concepto']} — {perfil.get(cid,0.0)*100:.0f}%")

            else:
                # ——— Gestión del Ítem actual ———
                if st.session_state.current_item is None:
                    with st.spinner("Generando/buscando pregunta..."):
                        _cargar_siguiente_item(concepto_id, nodos, ctx)

                item = st.session_state.current_item
                if item:
                    st.markdown(f"<div class='card'><strong>{item['pregunta']}</strong></div>", unsafe_allow_html=True)

                    # Botones de ayuda
                    colA, colB, colC = st.columns(3)
                    if colA.button("Pista 1"):
                        st.session_state.hints += 1
                        pista = sabi.sabi_chat("Dame una PISTA 1 muy concreta para esta pregunta.", {"modo":"pista1","concepto":nodo['concepto']})
                        st.markdown(f"<div class='callout pista'>{pista}</div>", unsafe_allow_html=True)
                    if colB.button("Pista 2"):
                        st.session_state.hints += 1
                        pista2 = sabi.sabi_chat("Dame una PISTA 2 un poco más directa (sin dar la respuesta).", {"modo":"pista2","concepto":nodo['concepto']})
                        st.markdown(f"<div class='callout pista'>{pista2}</div>", unsafe_allow_html=True)
                    if colC.button("Explicación breve"):
                        expl = sabi.sabi_chat(f"Explícame en 3 pasos cómo resolver: {item['pregunta']}", {"modo":"explicacion","concepto":nodo['concepto']})
                        st.markdown(f"<div class='callout exp'>{expl}</div>", unsafe_allow_html=True)

                    # Form para responder de forma transaccional
                    with st.form("quiz_form", clear_on_submit=False):
                        op = st.radio("Elige una opción:", item['opciones'], index=None, key="radio_opcion")
                        submit = st.form_submit_button("Responder")

                    if submit:
                        if op is None:
                            st.warning("Selecciona una respuesta.")
                        else:
                            ok = (op == item['respuesta_correcta'])
                            if ok:
                                st.success("¡Correcto! 👏")
                                st.session_state.aciertos += 1
                            else:
                                st.error(f"Incorrecto. Respuesta: **{item['respuesta_correcta']}**")
                                st.info("No pasa nada; volver a fundamentos a veces acelera el avance. 💪")
                                apply_heuristic_propagation(user_id, concepto_id, aristas, perfil)

                            # Guardar en BD y actualizar BKT
                            log_respuesta(
                                st.session_state.sid, user_id, ctx["objetivo"], ctx["mundo"], ctx["grado"], st.session_state.tema,
                                concepto_id, item, ok, op, st.session_state.t0, st.session_state.hints
                            )
                            hist = get_user_history(user_id, concepto_id)

                            # Robusto ante None/errores en BKT
                            try:
                                eventos_bkt = [(c, ts) for c, _, _, ts in hist][::-1]
                                bkt_val = obtener_nueva_probabilidad(user_id, concepto_id, eventos_bkt)
                                if bkt_val is None:
                                    print("⚠️ BKT devolvió None. Usando incremento simple.")
                                    prev = perfil.get(concepto_id, 0.0)
                                    bkt_val = prev + (0.05 if ok else -0.05)
                            except Exception as e:
                                print("❌ Error al calcular BKT:", repr(e), "Hist:", hist)
                                prev = perfil.get(concepto_id, 0.0)
                                bkt_val = prev + (0.05 if ok else -0.05)

                            nueva = max(0.01, min(0.99, float(bkt_val)))
                            update_user_prob(user_id, concepto_id, nueva)
                            st.session_state.hints = 0

                            # Marcar item usado y avanzar contador del quiz
                            st.session_state.usados_items.append(item['item_id'])
                            st.session_state.quiz_count += 1

                            # Breve feedback al chat
                            breve = "✅ Bien" if ok else "❌ A reforzar"
                            st.session_state.chat.append(("assistant", f"{breve} — {nodo['concepto']} ({nodo['materia']}). Dominio estimado: {nueva*100:.0f}%"))

                            # ¿terminó el quiz?
                            if st.session_state.quiz_count >= st.session_state.prefs['quiz_len']:
                                total = st.session_state.prefs['quiz_len']
                                ac = st.session_state.aciertos
                                nivel = "Excelente" if ac >= 3 else ("En camino" if ac == 2 else "Necesita refuerzo")

                                # Sugerencia adaptativa (LLM + reglas)
                                estado = build_estado_estudiante(user_id, ctx, concepto_id, nodos, aristas, perfil)
                                sug = sabi.sugerir_siguiente_concepto(estado)  # puede ser None si no hay modelo
                                weak_ids, adv_ids = rule_suggestions(concepto_id, perfil, aristas, nodos)

                                msg_sug = ""
                                if sug and isinstance(sug, dict):
                                    sc = (sug.get("siguiente_concepto") or {})
                                    if sc:
                                        msg_sug = f"\n\n**Siguiente sugerido:** {sc.get('nombre','(mantener)')} · dif: {sc.get('dificultad_sugerida','media')}. _{sc.get('razon','')}_"

                                # Resumen + instrucción de gating (como en tu captura)
                                st.session_state.chat.append(("assistant",
                                    f"**Resumen del quiz ({total}):** {ac}/{total} correctas — {nivel}.{msg_sug}\n\n"
                                    "¿Quieres **reintentar** el mismo tema, **repasar fundamentos** o **avanzar**?"
                                ))

                                # Guardar contexto de decisión y pasar a espera
                                st.session_state.decision_ctx = {
                                    "concepto_id": concepto_id,
                                    "weak": weak_ids,
                                    "advance": adv_ids,
                                    "sug": sug
                                }
                                st.session_state.quiz_state = QUIZ_WAIT_DECISION

                                # reset de quiz contadores (no cambiamos de tema hasta decisión)
                                st.session_state.quiz_count = 0
                                st.session_state.aciertos = 0
                                st.session_state.usados_items = []
                            else:
                                # Continuar mismo concepto con nuevo ítem
                                st.session_state.override_next = concepto_id

                            # Forzar siguiente ítem (o quedar en espera)
                            st.session_state.current_item = None
                            st.rerun()

            # Progreso por mundo (solo practicados y por grado)
            st.markdown("---")
            prom, deb = mastery_summary(user_id, ctx["mundo"], nodos, grado=ctx["grado"], only_attempted=True, fallback=0.0)
            st.subheader("📈 Progreso")
            st.write(f"Promedio (solo temas practicados) en **{ctx['mundo']} · {ctx['grado']}**: {prom*100:.0f}%")
            for cid,p in deb[:5]:
                try:
                    st.progress(p)  # 0..1
                except Exception:
                    st.progress(int(p*100))  # fallback 0..100
                st.caption(f"{nodos[cid]['concepto']}: {p*100:.0f}%")
        else:
            st.info("No hay conceptos disponibles para ese filtro.")
    else:
        st.info("Inicia la conversación en el panel izquierdo para configurar tu objetivo y empezar.")

st.markdown("</div>", unsafe_allow_html=True)  # fin grid
