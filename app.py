import os, re, glob, json, sys, secrets, hashlib, base64
from functools import wraps
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from flask import Flask, request, jsonify, session, redirect, render_template, g, Response
from werkzeug.security import generate_password_hash, check_password_hash

from flask.json.provider import DefaultJSONProvider
class Prov(DefaultJSONProvider):
    def default(self, o):
        return o.isoformat() if hasattr(o, "isoformat") else super().default(o)
app = Flask(__name__)
app.json = Prov(app)
BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))
def cfg(nome):
    v = os.environ.get(nome, "").strip()
    if not v: sys.exit(f"Configuração ausente: {nome}. Veja o .env.example e o README.")
    return v
DB = cfg("DATABASE_URL")
LOGIN = os.environ.get("ADMIN_LOGIN", "admin").strip() or "admin"
# Só para testes locais: entra direto como admin. Ignorado se HOST não for 127.0.0.1 ou COOKIE_SECURE=1.
DEV = os.environ.get("DEV_SEM_LOGIN") == "1" and os.environ.get("HOST", "127.0.0.1") == "127.0.0.1" and os.environ.get("COOKIE_SECURE", "0") != "1"
app.secret_key = cfg("SECRET_KEY")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=28800,
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1")
N = ("nome", "Nome")
# slug: (nome, rótulo da data, campos comuns, campos por pessoa, chaves de nome, chave de exibição)
TIPOS = {
 "novo_convertido": ("Novo convertido", "Data da conversão", [], [N, ("contato1", "Contato 1"), ("contato2", "Contato 2"), ("email", "E-mail"), ("cep", "CEP"), ("logradouro", "Logradouro"), ("numero", "Número"), ("complemento", "Complemento"), ("bairro", "Bairro / Distrito"), ("cidade", "Cidade / Localidade"), ("uf", "UF")], ["nome"], "nome"),
 "batismo_aguas": ("Batismo nas águas", "Data do batismo", [("local", "Local")], [N], ["nome"], "nome"),
 "batismo_es": ("Batismo com Espírito Santo", "Data do batismo", [], [N], ["nome"], "nome"),
 "falecimento": ("Falecimento", "Data do falecimento", [], [N, ("motivo", "Motivo")], ["nome"], "nome"),
 "carta_saida": ("Carta de mudança – saída", "Data da carta", [], [N, ("destino", "Igreja de destino")], ["nome"], "nome"),
 "carta_entrada": ("Carta de mudança – entrada", "Data da carta", [], [N, ("origem", "Igreja de origem")], ["nome"], "nome"),
 "desligamento": ("Desligamento", "Data do desligamento", [], [N, ("obs", "Observação")], ["nome"], "nome"),
 "aclamacao": ("Aclamação", "Data da aclamação", [], [N, ("obs", "Observação")], ["nome"], "nome"),
 "cura_divina": ("Cura divina", "Data", [], [N, ("descricao", "Descrição")], ["nome"], "nome"),
 "crianca": ("Criança apresentada", "Data da apresentação", [], [("crianca", "Nome da criança"), ("mae", "Nome da mãe"), ("pai", "Nome do pai")], ["crianca", "mae", "pai"], "crianca"),
 "casamento": ("Casamento", "Data do casamento", [], [("noivo", "Nome do noivo"), ("noiva", "Nome da noiva")], ["noivo", "noiva"], "noivo"),
}

ENDER = {"cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"}

def conn(direct=False):
    url = (os.environ.get("DATABASE_URL_DIRECT") or DB) if direct else DB
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)

def init_db():
    with conn(direct=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(arquivo text PRIMARY KEY)")
        done = {r["arquivo"] for r in c.execute("SELECT arquivo FROM schema_migrations")}
        for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "migrations", "*.sql"))):
            n = os.path.basename(f)
            if n not in done:
                c.execute(open(f, encoding="utf-8").read())
                c.execute("INSERT INTO schema_migrations VALUES(%s)", (n,))
        for slug, t in TIPOS.items():
            c.execute("INSERT INTO tipos_ocorrencia(slug,nome) VALUES(%s,%s) ON CONFLICT(slug) DO UPDATE SET nome=EXCLUDED.nome", (slug, t[0]))
        if not c.execute("SELECT 1 FROM usuarios WHERE login=%s", (LOGIN,)).fetchone():
            pw = os.environ.get("ADMIN_PASSWORD", "").strip() or (secrets.token_urlsafe(16) if DEV else cfg("ADMIN_PASSWORD"))
            if len(pw) < 8: sys.exit("ADMIN_PASSWORD deve ter ao menos 8 caracteres.")
            c.execute("INSERT INTO usuarios(nome,login,senha_hash,perfil) VALUES('Administrador',%s,%s,'admin')", (LOGIN, generate_password_hash(pw)))

def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    return session["csrf"]

def _sv(senha_hash):
    """Marca da senha atual: trocar a senha invalida as sessões abertas daquele usuário."""
    return hashlib.sha256(senha_hash.encode()).hexdigest()[:16]

def _nao_autenticado():
    return (jsonify(erro="Sessão expirada"), 401) if request.path.startswith("/api") else redirect("/login")

@app.before_request
def auth():
    if request.endpoint in ("health", "static"): return
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        tk = request.headers.get("X-CSRF-Token") or request.form.get("csrf") or ""
        if not tk or not secrets.compare_digest(tk, session.get("csrf", "")):
            return jsonify(erro="Token de segurança inválido. Recarregue a página."), 400
    if DEV and "uid" not in session:
        with conn() as c: u = c.execute("SELECT id,nome,senha_hash FROM usuarios WHERE perfil='admin' AND ativo ORDER BY id LIMIT 1").fetchone()
        if u: session.update(uid=u["id"], nome=u["nome"], sv=_sv(u["senha_hash"]))
    if request.endpoint == "login": return
    if "uid" not in session: return _nao_autenticado()
    # Permissões vêm SEMPRE do banco (nunca do cookie): desativar, trocar o perfil ou as congregações
    # de alguém vale já na próxima requisição dele.
    with conn() as c:
        u = c.execute("SELECT id,nome,perfil,ativo,senha_hash FROM usuarios WHERE id=%s", (session["uid"],)).fetchone()
        if u and u["ativo"] and u["perfil"] != "admin":
            congs = [r["congregacao_id"] for r in c.execute("SELECT congregacao_id FROM usuario_congregacoes WHERE usuario_id=%s", (u["id"],))]
    if not u or not u["ativo"] or session.get("sv") != _sv(u["senha_hash"]):
        session.clear(); return _nao_autenticado()
    g.admin = u["perfil"] == "admin"
    g.congs = None if g.admin else congs   # None = todas; lista = somente essas congregações
    session["nome"] = u["nome"]

def so_admin(f):
    """Bloqueia a rota para quem não é administrador (a checagem é feita aqui, no servidor)."""
    @wraps(f)
    def w(*a, **k):
        if not g.admin: return jsonify(erro="Acesso restrito ao administrador."), 403
        return f(*a, **k)
    return w

def cong_ok(cid):
    """True se o usuário logado pode acessar essa congregação."""
    return g.admin or (cid is not None and cid in g.congs)

def filtro_cong(coluna, w, p):
    """Para usuário comum, acrescenta à consulta a restrição às congregações liberadas."""
    if not g.admin: w.append(f"{coluna} = ANY(%s::int[])"); p.append(list(g.congs))

@app.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    if request.method == "POST":
        with conn() as c:
            u = c.execute("SELECT * FROM usuarios WHERE login=%s AND ativo", (request.form["login"],)).fetchone()
        if u and check_password_hash(u["senha_hash"], request.form["senha"]):
            session.clear(); session.permanent = True; session.update(uid=u["id"], nome=u["nome"], sv=_sv(u["senha_hash"])); return redirect("/")
        msg = "Login ou senha inválidos."
    return render_template("login.html", csrf=csrf(), msg=msg)

@app.route("/")
def index():
    return render_template("index.html", usuario=session["nome"], csrf=csrf(), admin=g.admin)

@app.get("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.get("/health")
def health():
    try:
        with conn() as c: c.execute("SELECT 1")
        return jsonify(status="ok")
    except Exception: return jsonify(status="erro"), 503

@app.errorhandler(psycopg.OperationalError)
def _db(e): return jsonify(erro="Banco de dados indisponível."), 503

@app.errorhandler(psycopg.errors.UndefinedTable)
@app.errorhandler(psycopg.errors.UndefinedColumn)
def _desatualizado(e):
    app.logger.exception("Banco desatualizado")
    return jsonify(erro='O banco de dados está desatualizado. Rode "python app.py migrate" (apontando para esse banco) e reinicie o app.'), 500

@app.errorhandler(Exception)
def _err(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): return e
    app.logger.exception("Erro não tratado")
    return jsonify(erro="Erro interno. Tente novamente."), 500

@app.get("/api/meta")
def meta():
    w, p = [], []; filtro_cong("id", w, p); ex = (" AND " + w[0]) if w else ""; wt = (" WHERE " + w[0]) if w else ""
    with conn() as c:
        cg = c.execute("SELECT id,nome FROM congregacoes WHERE ativo" + ex + " ORDER BY nome", p).fetchall()
        todas = c.execute("SELECT id,nome,ativo FROM congregacoes" + wt + " ORDER BY nome", p).fetchall()
    tipos = [dict(slug=s, nome=t[0], data=t[1], comuns=[dict(k=k, l=l) for k, l in t[2]], campos=[dict(k=k, l=l, g=k in ENDER) for k, l in t[3]]) for s, t in TIPOS.items()]
    return jsonify(tipos=tipos, congregacoes=cg, todas=todas, membros=MEMBROS_META, usuario=dict(id=session["uid"], nome=session["nome"], perfil="admin" if g.admin else "usuario"))

@app.get("/api/painel")
def painel():
    w1, p1, w2, p2 = [], [], [], []; filtro_cong("l.congregacao_id", w1, p1); filtro_cong("id", w2, p2)
    with conn() as c:
        r = c.execute("""SELECT
          (SELECT count(*) FROM ocorrencias o JOIN lotes_ocorrencias l ON l.id=o.lote_id WHERE o.cancelado_em IS NULL AND l.situacao<>'Cancelada' AND date_trunc('month',l.data_ocorrencia)=date_trunc('month',current_date)""" + "".join(" AND " + x for x in w1) + """) mes,
          (SELECT count(*) FROM congregacoes WHERE ativo""" + "".join(" AND " + x for x in w2) + ") cong", p1 + p2).fetchone()
    return jsonify(r)

LISTA = """SELECT l.id,l.data_ocorrencia,t.nome tipo,t.slug tipo_slug,c.nome congregacao,l.situacao,u.nome responsavel,
  count(o.id) n, coalesce(array_agg(o.nome ORDER BY o.id) FILTER (WHERE o.id IS NOT NULL),'{}') nomes
  FROM lotes_ocorrencias l JOIN tipos_ocorrencia t ON t.id=l.tipo_ocorrencia_id JOIN congregacoes c ON c.id=l.congregacao_id
  JOIN usuarios u ON u.id=l.usuario_id LEFT JOIN ocorrencias o ON o.lote_id=l.id AND o.cancelado_em IS NULL"""

@app.get("/api/lotes")
def lotes():
    a, w, p = request.args, [], []
    filtro_cong("l.congregacao_id", w, p)
    if a.get("de"): w.append("l.data_ocorrencia>=%s"); p.append(a["de"])
    if a.get("ate"): w.append("l.data_ocorrencia<=%s"); p.append(a["ate"])
    if a.get("tipo"): w.append("t.slug=%s"); p.append(a["tipo"])
    if a.get("sit"): w.append("l.situacao=%s"); p.append(a["sit"])
    if a.get("cong"): w.append("l.congregacao_id=%s"); p.append(int(a["cong"]))
    if a.get("nome"):
        w.append("EXISTS(SELECT 1 FROM ocorrencias x WHERE x.lote_id=l.id AND x.cancelado_em IS NULL AND x.nome_busca LIKE '%%'||f_unaccent(%s)||'%%')"); p.append(a["nome"])
    sql = LISTA + (" WHERE " + " AND ".join(w) if w else "") + " GROUP BY l.id,t.nome,t.slug,c.nome,u.nome ORDER BY l.data_ocorrencia DESC,l.id DESC LIMIT 500"
    with conn() as c:
        return jsonify(c.execute(sql, p).fetchall())

@app.get("/api/registros")
def registros():
    a, w, p = request.args, ["o.cancelado_em IS NULL", "l.situacao<>'Cancelada'"], []
    filtro_cong("l.congregacao_id", w, p)
    if a.get("tipo"): w.append("t.slug=%s"); p.append(a["tipo"])
    if a.get("de"): w.append("l.data_ocorrencia>=%s"); p.append(a["de"])
    if a.get("ate"): w.append("l.data_ocorrencia<=%s"); p.append(a["ate"])
    if a.get("cong"): w.append("l.congregacao_id=%s"); p.append(int(a["cong"]))
    if a.get("nome"): w.append("o.nome_busca LIKE '%%'||f_unaccent(%s)||'%%'"); p.append(a["nome"])
    por = min(max(int(a.get("por") or 0), 0), 200); pag = max(int(a.get("pagina") or 1), 1)
    jn = "FROM ocorrencias o JOIN lotes_ocorrencias l ON l.id=o.lote_id JOIN tipos_ocorrencia t ON t.id=l.tipo_ocorrencia_id JOIN congregacoes c ON c.id=l.congregacao_id WHERE " + " AND ".join(w)
    with conn() as c:
        total = c.execute("SELECT count(*) n " + jn, p).fetchone()["n"]
        lim = f" LIMIT {por} OFFSET {(pag - 1) * por}" if por else " LIMIT 500"
        rows = c.execute("SELECT o.id,o.nome,t.nome tipo,t.slug tipo_slug,l.id lote,l.data_ocorrencia data,c.nome congregacao,l.situacao,l.dados_comuns comuns,o.dados_especificos d " + jn + " ORDER BY l.data_ocorrencia DESC,o.id DESC" + lim, p).fetchall()
    resp = jsonify(rows); resp.headers["X-Total-Count"] = str(total)
    return resp

@app.get("/api/ocorrencias/<int:id>")
def ver_oc(id):
    with conn() as c:
        o = c.execute("""SELECT o.id,t.slug tipo,l.data_ocorrencia data,l.congregacao_id,l.dados_comuns comuns,o.dados_especificos campos FROM ocorrencias o
          JOIN lotes_ocorrencias l ON l.id=o.lote_id JOIN tipos_ocorrencia t ON t.id=l.tipo_ocorrencia_id WHERE o.id=%s AND o.cancelado_em IS NULL""", (id,)).fetchone()
    return (jsonify(o), 200) if o and cong_ok(o["congregacao_id"]) else (jsonify(erro="Registro não encontrado."), 404)

@app.post("/api/ocorrencias")
def criar_oc():
    d = request.get_json() or {}
    d["linhas"] = [d.pop("campos", {})]
    return criar(d)

@app.put("/api/ocorrencias/<int:id>")
def editar_oc(id):
    d = request.get_json() or {}
    with conn() as c:
        o = c.execute("""SELECT o.id,o.lote_id,o.dados_especificos,t.slug tipo,l.congregacao_id,l.data_ocorrencia,l.dados_comuns FROM ocorrencias o
          JOIN lotes_ocorrencias l ON l.id=o.lote_id JOIN tipos_ocorrencia t ON t.id=l.tipo_ocorrencia_id WHERE o.id=%s AND o.cancelado_em IS NULL""", (id,)).fetchone()
        if not o or not cong_ok(o["congregacao_id"]): return jsonify(erro="Registro não encontrado."), 404
        d["linhas"] = [d.pop("campos", {})]
        ok, erros = validar(o["tipo"], d, o["congregacao_id"])
        if erros: return jsonify(erros=erros), 400
        comuns, regs = ok; v = regs[0]; lote = o["lote_id"]
        outros = c.execute("SELECT count(*) n FROM ocorrencias WHERE lote_id=%s AND cancelado_em IS NULL AND id<>%s", (lote, id)).fetchone()["n"]
        if outros:  # registro veio de um agrupamento antigo: separa para não alterar os demais
            lote = c.execute("INSERT INTO lotes_ocorrencias(tipo_ocorrencia_id,congregacao_id,data_ocorrencia,dados_comuns,usuario_id,situacao) SELECT tipo_ocorrencia_id,%s::int,%s::date,%s::jsonb,%s::int,situacao FROM lotes_ocorrencias WHERE id=%s RETURNING id",
                             (d["congregacao_id"], d["data"], Jsonb(comuns), session["uid"], o["lote_id"])).fetchone()["id"]
        else:
            c.execute("UPDATE lotes_ocorrencias SET data_ocorrencia=%s,congregacao_id=%s,dados_comuns=%s,updated_at=now() WHERE id=%s", (d["data"], d["congregacao_id"], Jsonb(comuns), lote))
        t = TIPOS[o["tipo"]]; nome = v[t[5]] if o["tipo"] != "casamento" else f"{v['noivo']} e {v['noiva']}"
        c.execute("UPDATE ocorrencias SET lote_id=%s,nome=%s,nome_busca=f_unaccent(%s),dados_especificos=%s,updated_at=now() WHERE id=%s", (lote, nome, " ".join(v[k] for k in t[4]), Jsonb(v), id))
        c.execute("INSERT INTO historico_ocorrencias(ocorrencia_id,lote_id,usuario_id,acao,dados_anteriores,dados_novos) VALUES(%s,%s,%s,'editada',%s,%s)",
                  (id, lote, session["uid"], Jsonb(dict(data=str(o["data_ocorrencia"]), congregacao_id=o["congregacao_id"], comuns=o["dados_comuns"], campos=o["dados_especificos"])),
                   Jsonb(dict(data=d["data"], congregacao_id=d["congregacao_id"], comuns=comuns, campos=v))))
    return jsonify(ok=True)

@app.delete("/api/ocorrencias/<int:id>")
def excluir_oc(id):
    with conn() as c:
        o = c.execute("SELECT o.lote_id,o.nome,o.dados_especificos,l.congregacao_id FROM ocorrencias o JOIN lotes_ocorrencias l ON l.id=o.lote_id WHERE o.id=%s AND o.cancelado_em IS NULL", (id,)).fetchone()
        if not o or not cong_ok(o["congregacao_id"]): return jsonify(erro="Registro não encontrado."), 404
        c.execute("UPDATE ocorrencias SET cancelado_em=now(),updated_at=now() WHERE id=%s", (id,))
        c.execute("INSERT INTO historico_ocorrencias(ocorrencia_id,lote_id,usuario_id,acao,dados_anteriores) VALUES(%s,%s,%s,'excluída',%s)", (id, o["lote_id"], session["uid"], Jsonb(o["dados_especificos"])))
        if not c.execute("SELECT 1 FROM ocorrencias WHERE lote_id=%s AND cancelado_em IS NULL", (o["lote_id"],)).fetchone():
            c.execute("UPDATE lotes_ocorrencias SET situacao='Cancelada',cancelado_em=now(),updated_at=now() WHERE id=%s", (o["lote_id"],))
    return jsonify(ok=True)

def validar(slug, d, manter=0):
    t = TIPOS.get(slug)
    if not t: return None, [dict(linha=0, msg="Tipo inválido.")]
    erros = []
    if not d.get("data"): erros.append(dict(linha=0, msg="Informe a data."))
    with conn() as c:
        cg = c.execute("SELECT 1 FROM congregacoes WHERE id=%s AND (ativo OR id=%s)", (d.get("congregacao_id") or 0, manter or 0)).fetchone()
    if not cg: erros.append(dict(linha=0, msg="Selecione uma congregação ativa."))
    elif not cong_ok(_int(d.get("congregacao_id"), None)): erros.append(dict(linha=0, msg="Você não tem acesso à congregação selecionada."))
    linhas = d.get("linhas") or []
    if not linhas: erros.append(dict(linha=0, msg="Adicione ao menos uma pessoa."))
    regs = []
    for i, l in enumerate(linhas, 1):
        v = {k: (l.get(k) or "").strip() for k, _ in t[3]}
        falta = [lb for k, lb in t[3] if k in t[4] and not v[k]]
        if falta: erros.append(dict(linha=i, msg="Preencha: " + ", ".join(falta)))
        if v.get("cep"):
            dig = re.sub(r"\D", "", v["cep"])
            if len(dig) == 8: v["cep"] = dig[:5] + "-" + dig[5:]
            else: erros.append(dict(linha=i, msg="CEP inválido (use 8 dígitos)."))
        if v.get("uf"):
            v["uf"] = v["uf"].upper()
            if not re.fullmatch(r"[A-Z]{2}", v["uf"]): erros.append(dict(linha=i, msg="UF inválida (2 letras)."))
        regs.append(v)
    comuns = {k: (d.get("comuns", {}).get(k) or "").strip() for k, _ in t[2]}
    return (comuns, regs), erros

def gravar_regs(c, lote_id, slug, regs, uid, acao):
    t = TIPOS[slug]
    for v in regs:
        nome = v[t[5]] if slug != "casamento" else f"{v['noivo']} e {v['noiva']}"
        busca = " ".join(v[k] for k in t[4])
        o = c.execute("INSERT INTO ocorrencias(lote_id,nome,nome_busca,dados_especificos) VALUES(%s,%s,f_unaccent(%s),%s) RETURNING id",
                      (lote_id, nome, busca, Jsonb(v))).fetchone()
        c.execute("INSERT INTO historico_ocorrencias(ocorrencia_id,lote_id,usuario_id,acao,dados_novos) VALUES(%s,%s,%s,%s,%s)", (o["id"], lote_id, uid, acao, Jsonb(v)))

@app.post("/api/lotes")
def criar(d=None):
    d = d or request.get_json(); slug = d.get("tipo")
    ok, erros = validar(slug, d)
    if erros: return jsonify(erros=erros), 400
    comuns, regs = ok
    with conn() as c:  # transação: commit no fim do bloco, rollback em exceção
        l = c.execute("INSERT INTO lotes_ocorrencias(tipo_ocorrencia_id,congregacao_id,data_ocorrencia,dados_comuns,usuario_id) VALUES((SELECT id FROM tipos_ocorrencia WHERE slug=%s),%s,%s,%s,%s) RETURNING id",
                      (slug, d["congregacao_id"], d["data"], Jsonb(comuns), session["uid"])).fetchone()
        c.execute("INSERT INTO historico_ocorrencias(lote_id,usuario_id,acao,dados_novos) VALUES(%s,%s,'lote criado',%s)", (l["id"], session["uid"], Jsonb(dict(d, linhas=len(regs)))))
        gravar_regs(c, l["id"], slug, regs, session["uid"], "criada")
    return jsonify(id=l["id"]), 201

def carregar(c, id):
    l = c.execute("""SELECT l.*,t.slug tipo,t.nome tipo_nome,cg.nome congregacao,u.nome responsavel FROM lotes_ocorrencias l
      JOIN tipos_ocorrencia t ON t.id=l.tipo_ocorrencia_id JOIN congregacoes cg ON cg.id=l.congregacao_id JOIN usuarios u ON u.id=l.usuario_id WHERE l.id=%s""", (id,)).fetchone()
    if l:
        l["linhas"] = [r["dados_especificos"] for r in c.execute("SELECT dados_especificos FROM ocorrencias WHERE lote_id=%s AND cancelado_em IS NULL ORDER BY id", (id,))]
        l["historico"] = c.execute("SELECT h.acao,h.created_at,u.nome usuario FROM historico_ocorrencias h JOIN usuarios u ON u.id=h.usuario_id WHERE h.lote_id=%s AND h.acao NOT IN ('criada','substituída') ORDER BY h.id", (id,)).fetchall()
    return l

@app.get("/api/lotes/<int:id>")
def ver(id):
    with conn() as c: l = carregar(c, id)
    return (jsonify(l), 200) if l and cong_ok(l["congregacao_id"]) else (jsonify(erro="Não encontrado"), 404)

@app.put("/api/lotes/<int:id>")
def editar(id):
    d = request.get_json()
    with conn() as c:
        l = carregar(c, id)
        if not l or not cong_ok(l["congregacao_id"]): return jsonify(erro="Não encontrado"), 404
        if l["situacao"] == "Cancelada": return jsonify(erros=[dict(linha=0, msg="Lotes cancelados não podem ser editados.")]), 400
        d["tipo"] = l["tipo"]
        ok, erros = validar(l["tipo"], d)
        if erros: return jsonify(erros=erros), 400
        comuns, regs = ok
        c.execute("INSERT INTO historico_ocorrencias(lote_id,usuario_id,acao,dados_anteriores,dados_novos) VALUES(%s,%s,'lote editado',%s,%s)",
                  (id, session["uid"], Jsonb(dict(data=str(l["data_ocorrencia"]), congregacao_id=l["congregacao_id"], comuns=l["dados_comuns"], linhas=l["linhas"])), Jsonb(d)))
        c.execute("UPDATE ocorrencias SET cancelado_em=now() WHERE lote_id=%s AND cancelado_em IS NULL", (id,))
        c.execute("UPDATE lotes_ocorrencias SET data_ocorrencia=%s,congregacao_id=%s,dados_comuns=%s,updated_at=now() WHERE id=%s", (d["data"], d["congregacao_id"], Jsonb(comuns), id))
        gravar_regs(c, id, l["tipo"], regs, session["uid"], "substituída")
    return jsonify(ok=True)

@app.post("/api/lotes/<int:id>/<acao>")
def mudar(id, acao):
    if acao != "cancelar": return jsonify(erro="Ação inválida"), 400
    with conn() as c:
        l = c.execute("SELECT situacao,congregacao_id FROM lotes_ocorrencias WHERE id=%s", (id,)).fetchone()
        if l and not cong_ok(l["congregacao_id"]): return jsonify(erro="Não encontrado"), 404
        if not l or l["situacao"] == "Cancelada":
            return jsonify(erro="Ação não permitida para a situação atual."), 400
        c.execute("UPDATE lotes_ocorrencias SET situacao='Cancelada',updated_at=now(),cancelado_em=now() WHERE id=%s", (id,))
        c.execute("INSERT INTO historico_ocorrencias(lote_id,usuario_id,acao,dados_anteriores,dados_novos) VALUES(%s,%s,%s,%s,%s)",
                  (id, session["uid"], "lote cancelada", Jsonb(dict(situacao=l["situacao"])), Jsonb(dict(situacao="Cancelada"))))
    return jsonify(ok=True)

@app.post("/api/congregacoes")
@app.put("/api/congregacoes/<int:id>")
@so_admin
def congregacao(id=None):
    d = request.get_json(); nome = (d.get("nome") or "").strip()
    if not nome: return jsonify(erro="Informe o nome da congregação."), 400
    try:
        with conn() as c:
            if id: c.execute("UPDATE congregacoes SET nome=%s,codigo=%s,ativo=%s,updated_at=now() WHERE id=%s", (nome, d.get("codigo") or None, bool(d.get("ativo", True)), id))
            else: c.execute("INSERT INTO congregacoes(nome,codigo,ativo) VALUES(%s,%s,%s)", (nome, d.get("codigo") or None, bool(d.get("ativo", True))))
    except psycopg.errors.UniqueViolation:
        return jsonify(erro="Já existe uma congregação com esse nome."), 400
    return jsonify(ok=True)

@app.delete("/api/congregacoes/<int:id>")
@so_admin
def excluir_congregacao(id):
    with conn() as c:
        if c.execute("SELECT 1 FROM lotes_ocorrencias WHERE congregacao_id=%s LIMIT 1", (id,)).fetchone():
            return jsonify(erro="Esta congregação tem ocorrências vinculadas e não pode ser excluída. Use Inativar para preservar o histórico."), 400
        n = c.execute("DELETE FROM congregacoes WHERE id=%s", (id,)).rowcount
    return (jsonify(ok=True), 200) if n else (jsonify(erro="Congregação não encontrada."), 404)

@app.get("/api/congregacoes")
def congregacoes():
    w, p = [], []; filtro_cong("id", w, p)
    with conn() as c: return jsonify(c.execute("SELECT id,nome,codigo,ativo FROM congregacoes" + (" WHERE " + w[0] if w else "") + " ORDER BY nome", p).fetchall())

# ===================== MEMBROS, HISTÓRICO E ANIVERSARIANTES =====================
from datetime import date, datetime
from itertools import groupby

STATUS_MEMBRO = ["Ativo", "Inativo", "Desligado", "Faleceu", "Disciplinado", "Congregado"]
STATUS_MEMBRESIA = ["Membro", "Congregado"]
CARGOS = ["Obreiro", "Diácono", "Presbítero", "Evangelista", "Pastor"]
SEXOS = ["Masculino", "Feminino"]
ESTADOS_CIVIS = ["Solteiro(a)", "Casado(a)", "Divorciado(a)", "Viúvo(a)", "União estável"]
FORMAS_RECEBIMENTO = ["Batismo", "Aclamação", "Carta de mudança", "Reconciliação"]  # só sugestões; o campo é livre
MEMBROS_META = dict(status=STATUS_MEMBRO, membresia=STATUS_MEMBRESIA, cargos=CARGOS, sexos=SEXOS, estados_civis=ESTADOS_CIVIS, formas=FORMAS_RECEBIMENTO)
# Quem não deve aparecer na lista de aniversariantes (não fazem mais parte do rol ativo). Ajuste aqui se desejar outra regra.
SEM_ANIVERSARIO = ("Faleceu", "Desligado")
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
ROT_DATAS = dict(data_nascimento="Data de nascimento", data_conversao="Data de conversão", data_batismo="Data de batismo", data_chegada="Data de chegada", data_recebimento="Data de recebimento", data_saida="Data de saída")
COLS_MEMBRO = ["nome", "data_nascimento", "sexo", "estado_civil", "cpf", "telefone", "email", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
               "congregacao_id", "data_conversao", "data_batismo", "data_recebimento", "forma_recebimento", "status", "status_membresia", "cargo",
               "data_chegada", "data_saida", "forma_saida", "nome_mae", "nome_pai", "observacoes"]

def _int(x, padrao=0):
    try: return int(x)
    except (TypeError, ValueError): return padrao

def parse_data(x):
    """'' / None -> None; data válida -> date; texto inválido -> False."""
    if not x: return None
    try: return date.fromisoformat(str(x)[:10])
    except ValueError: return False

def cpf_valido(d):
    if len(d) != 11 or d == d[0] * 11: return False
    for n in (9, 10):
        if (sum(int(d[i]) * (n + 1 - i) for i in range(n)) * 10 % 11) % 10 != int(d[n]): return False
    return True

def validar_membro(d):
    """Valida e normaliza a ficha. Retorna (valores, lista_de_erros)."""
    erros, v = [], {}
    def s(k):
        x = d.get(k)
        return x.strip() if isinstance(x, str) else ""
    v["nome"] = " ".join(s("nome").split())
    if not v["nome"]: erros.append("Informe o nome completo.")
    elif len(v["nome"]) > 200: erros.append("Nome muito longo (máximo de 200 caracteres).")
    for k, rot in ROT_DATAS.items():
        dt = parse_data(s(k))
        if dt is False or (dt and dt.year < 1900): erros.append(f"{rot} inválida."); dt = None
        elif dt and k in ("data_nascimento", "data_chegada", "data_saida") and dt > date.today(): erros.append(f"A {ROT_DATAS[k][0].lower() + ROT_DATAS[k][1:]} não pode ser futura."); dt = None
        v[k] = dt
    for k, lista, rot in (("sexo", SEXOS, "Sexo"), ("estado_civil", ESTADOS_CIVIS, "Estado civil")):
        v[k] = s(k) or None
        if v[k] and v[k] not in lista: erros.append(f"{rot} inválido."); v[k] = None
    cpf = re.sub(r"\D", "", s("cpf")); v["cpf"] = None
    if cpf and not cpf_valido(cpf): erros.append("CPF inválido.")
    elif cpf: v["cpf"] = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    v["telefone"] = s("telefone") or None
    if v["telefone"] and not 8 <= len(re.sub(r"\D", "", v["telefone"])) <= 13: erros.append("Telefone inválido."); v["telefone"] = None
    v["email"] = s("email").lower() or None
    if v["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v["email"]): erros.append("E-mail inválido."); v["email"] = None
    cep = re.sub(r"\D", "", s("cep")); v["cep"] = None  # mesma regra da aba Ocorrências
    if s("cep") and len(cep) != 8: erros.append("CEP inválido (use 8 dígitos).")
    elif cep: v["cep"] = cep[:5] + "-" + cep[5:]
    v["uf"] = s("uf").upper() or None
    if v["uf"] and not re.fullmatch(r"[A-Z]{2}", v["uf"]): erros.append("UF inválida (2 letras)."); v["uf"] = None
    for k in ("logradouro", "numero", "complemento", "bairro", "cidade", "forma_recebimento", "forma_saida", "nome_mae", "nome_pai"):
        v[k] = " ".join(s(k).split()) or None
        if v[k] and len(v[k]) > 200: erros.append(f"Campo muito longo: {k.replace('_', ' ')}."); v[k] = None
    v["observacoes"] = s("observacoes") or None
    if v["observacoes"] and len(v["observacoes"]) > 2000: erros.append("Campo muito longo: observacoes."); v["observacoes"] = None
    cg = d.get("congregacao_id"); v["congregacao_id"] = None
    if cg not in (None, ""):
        v["congregacao_id"] = _int(cg, None)
        if v["congregacao_id"] is None: erros.append("Congregação inválida.")
    v["status"] = s("status")
    if v["status"] not in STATUS_MEMBRO: erros.append("Selecione o Status."); v["status"] = None
    v["status_membresia"] = s("status_membresia")
    if v["status_membresia"] not in STATUS_MEMBRESIA: erros.append("Selecione o Status da Membresia."); v["status_membresia"] = None
    cargo = s("cargo"); v["cargo"] = None
    if v["status_membresia"] == "Membro":  # congregado não tem cargo
        if cargo and cargo not in CARGOS: erros.append("Cargo eclesiástico inválido.")
        else: v["cargo"] = cargo or None
    che, rec, sai = v["data_chegada"], v["data_recebimento"], v["data_saida"]
    if che and rec and che > rec: erros.append("A data de chegada não pode ser posterior à data de recebimento.")
    if sai and any(r and sai < r for r in (che, rec)): erros.append("A data de saída não pode ser anterior à data de chegada ou de recebimento.")
    if (sai or v["forma_saida"]) and v["status"] not in ("Desligado", "Faleceu"):
        erros.append("Informe a data e a forma de saída apenas quando o Status for Desligado ou Faleceu."); v["data_saida"] = v["forma_saida"] = None
    return v, erros

FOTO_MAX = 2 * 1024 * 1024   # a página reduz a foto para ~100 KB; este é o teto aceito pelo servidor

def validar_foto(texto):
    """Foto enviada como data URL (data:image/jpeg;base64,...). Confere o conteúdo real (não só o nome/tipo
    informado) e o tamanho. Retorna (bytes, tipo, erro)."""
    m = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)", texto.strip()) if isinstance(texto, str) else None
    if not m: return None, None, "Foto inválida. Use uma imagem JPG, PNG ou WEBP."
    try: b = base64.b64decode(m.group(2), validate=False)
    except Exception: return None, None, "Foto inválida. Use uma imagem JPG, PNG ou WEBP."
    if not b: return None, None, "Foto inválida."
    if len(b) > FOTO_MAX: return None, None, "A foto é grande demais (máximo de 2 MB depois de ajustada)."
    real = ("image/jpeg" if b[:3] == b"\xff\xd8\xff" else "image/png" if b[:8] == b"\x89PNG\r\n\x1a\n"
            else "image/webp" if b[:4] == b"RIFF" and b[8:12] == b"WEBP" else None)
    if real != m.group(1): return None, None, "O arquivo enviado não é uma imagem JPG, PNG ou WEBP válida."
    return b, real, None

def validar_historico(lista):
    """Registros NOVOS do histórico: (data, descrição). O secretário nunca vem do cliente."""
    if not isinstance(lista, list): return [], ["Histórico inválido."]
    erros, out = [], []
    for i, h in enumerate(lista, 1):
        if not isinstance(h, dict): erros.append(f"Histórico, registro {i}: dados inválidos."); continue
        dt = parse_data(h["data"].strip() if isinstance(h.get("data"), str) else "")
        desc = h["descricao"].strip() if isinstance(h.get("descricao"), str) else ""
        if not dt or dt.year < 1900: erros.append(f"Histórico, registro {i}: informe uma data válida.")
        if not desc: erros.append(f"Histórico, registro {i}: informe a descrição.")
        elif len(desc) > 5000: erros.append(f"Histórico, registro {i}: descrição muito longa (máximo de 5000 caracteres).")
        if dt and dt.year >= 1900 and desc and len(desc) <= 5000: out.append((dt, desc))
    return out, erros

@app.get("/api/membros")
def membros():
    a, w, p = request.args, ["m.excluido_em IS NULL"], []
    filtro_cong("m.congregacao_id", w, p)
    if a.get("status"): w.append("m.status=%s"); p.append(a["status"])
    if a.get("cong"): w.append("m.congregacao_id=%s"); p.append(_int(a["cong"]))
    if a.get("membresia"): w.append("m.status_membresia=%s"); p.append(a["membresia"])
    if a.get("cargo"): w.append("m.cargo=%s"); p.append(a["cargo"])
    if a.get("nome"): w.append("m.nome_busca LIKE '%%'||f_unaccent(%s)||'%%'"); p.append(a["nome"])
    por = min(max(_int(a.get("por")), 0), 200); pag = max(_int(a.get("pagina"), 1), 1)
    jn = "FROM membros m LEFT JOIN congregacoes c ON c.id=m.congregacao_id WHERE " + " AND ".join(w)
    with conn() as c:
        total = c.execute("SELECT count(*) n " + jn, p).fetchone()["n"]
        lim = f" LIMIT {por} OFFSET {(pag - 1) * por}" if por else " LIMIT 500"
        rows = c.execute("SELECT m.id,m.status,c.nome congregacao,m.nome,m.status_membresia,m.cargo " + jn + " ORDER BY m.nome_busca,m.id" + lim, p).fetchall()
    resp = jsonify(rows); resp.headers["X-Total-Count"] = str(total)
    return resp

@app.get("/api/membros/<int:id>")
def ver_membro(id):
    with conn() as c:
        m = c.execute("SELECT m.*,cg.nome congregacao FROM membros m LEFT JOIN congregacoes cg ON cg.id=m.congregacao_id WHERE m.id=%s AND m.excluido_em IS NULL", (id,)).fetchone()
        if not m or not cong_ok(m["congregacao_id"]): return jsonify(erro="Membro não encontrado."), 404
        m["historico"] = c.execute("SELECT id,data,secretario_nome secretario,descricao FROM historico_membro WHERE membro_id=%s ORDER BY data,id", (id,)).fetchall()
        f = c.execute("SELECT EXTRACT(EPOCH FROM atualizado_em)::bigint v FROM membro_foto WHERE membro_id=%s", (id,)).fetchone()
    m["tem_foto"], m["foto_v"] = bool(f), (f["v"] if f else None)
    m.pop("nome_busca", None)
    return jsonify(m)

@app.get("/api/membros/<int:id>/foto")
def foto_membro(id):
    with conn() as c:
        r = c.execute("SELECT f.imagem,f.tipo,m.congregacao_id,EXTRACT(EPOCH FROM f.atualizado_em)::bigint v FROM membro_foto f JOIN membros m ON m.id=f.membro_id WHERE f.membro_id=%s AND m.excluido_em IS NULL", (id,)).fetchone()
    if not r or not cong_ok(r["congregacao_id"]): return jsonify(erro="Foto não encontrada."), 404
    resp = Response(bytes(r["imagem"]), mimetype=r["tipo"])
    resp.headers["Cache-Control"] = "private, max-age=86400"   # o endereço leva ?v=<data da foto>, então trocar a foto renova
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = "inline"
    resp.add_etag()
    return resp.make_conditional(request)

@app.post("/api/membros")
@app.put("/api/membros/<int:id>")
def salvar_membro(id=None):
    if (request.content_length or 0) > 4 * 1024 * 1024: return jsonify(erros=[dict(msg="Os dados enviados são grandes demais.")]), 413
    d = request.get_json(silent=True) or {}
    v, erros = validar_membro(d)
    novos, e2 = validar_historico(d.get("historico_novo") or [])
    erros += e2
    foto = None
    if d.get("foto"):  # foto nova ou substituta (a página manda a imagem já recortada e reduzida)
        foto = validar_foto(d["foto"])
        if foto[2]: erros.append(foto[2])
    if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
    try:
        with conn() as c:  # uma transação: ficha + novos registros do histórico, ou nada
            atual = None
            if id:
                atual = c.execute("SELECT congregacao_id FROM membros WHERE id=%s AND excluido_em IS NULL FOR UPDATE", (id,)).fetchone()
                if not atual or not cong_ok(atual["congregacao_id"]): return jsonify(erro="Membro não encontrado."), 404
            if not cong_ok(v["congregacao_id"]): return jsonify(erros=[dict(msg="Você não tem acesso à congregação selecionada.")]), 403
            if v["congregacao_id"] and not c.execute("SELECT 1 FROM congregacoes WHERE id=%s AND (ativo OR id=%s)", (v["congregacao_id"], (atual or {}).get("congregacao_id") or 0)).fetchone():
                return jsonify(erros=[dict(msg="Selecione uma congregação ativa.")]), 400
            vals = [v[k] for k in COLS_MEMBRO]
            if id:
                c.execute("UPDATE membros SET " + ",".join(k + "=%s" for k in COLS_MEMBRO) + ",nome_busca=f_unaccent(%s),updated_at=now() WHERE id=%s", vals + [v["nome"], id])
            else:
                id = c.execute("INSERT INTO membros(" + ",".join(COLS_MEMBRO) + ",nome_busca) VALUES(" + ",".join(["%s"] * len(COLS_MEMBRO)) + ",f_unaccent(%s)) RETURNING id", vals + [v["nome"]]).fetchone()["id"]
                atual = False
            if foto and not foto[2]:
                c.execute("INSERT INTO membro_foto(membro_id,imagem,tipo,tamanho) VALUES(%s,%s,%s,%s) ON CONFLICT(membro_id) DO UPDATE SET imagem=EXCLUDED.imagem,tipo=EXCLUDED.tipo,tamanho=EXCLUDED.tamanho,atualizado_em=now()", (id, foto[0], foto[1], len(foto[0])))
            elif d.get("foto_remover") is True:
                c.execute("DELETE FROM membro_foto WHERE membro_id=%s", (id,))
            for dt, desc in novos:  # secretário = usuário logado (nunca informado pelo navegador)
                c.execute("INSERT INTO historico_membro(membro_id,data,secretario_id,secretario_nome,descricao) VALUES(%s,%s,%s,%s,%s)", (id, dt, session["uid"], session["nome"], desc))
    except psycopg.errors.UniqueViolation:
        return jsonify(erros=[dict(msg="Já existe um membro cadastrado com este CPF.")]), 400
    return jsonify(id=id), (201 if atual is False else 200)

@app.delete("/api/membros/<int:id>")
def excluir_membro(id):
    w, p = ["id=%s", "excluido_em IS NULL"], [id]; filtro_cong("congregacao_id", w, p)
    with conn() as c:  # exclusão lógica: o cadastro sai das telas, o histórico permanece no banco
        n = c.execute("UPDATE membros SET excluido_em=now(),updated_at=now() WHERE " + " AND ".join(w), p).rowcount
    return (jsonify(ok=True), 200) if n else (jsonify(erro="Membro não encontrado."), 404)

def consultar_aniversariantes(a):
    """Filtra pelo dia/mês de nascimento (o ano é ignorado). Retorna (linhas, erro)."""
    de, ate = parse_data(a.get("de")), parse_data(a.get("ate"))
    if de is False or ate is False: return None, "Data inválida."
    mes = _int(a.get("mes"))
    if a.get("mes") and not 1 <= mes <= 12: return None, "Mês inválido."
    if de and ate and ate < de: return None, "O período final não pode ser anterior ao período inicial."
    w, p = ["m.excluido_em IS NULL", "m.data_nascimento IS NOT NULL", "m.status <> ALL(%s::text[])"], [list(SEM_ANIVERSARIO)]
    filtro_cong("m.congregacao_id", w, p)
    if a.get("cong"): w.append("m.congregacao_id=%s"); p.append(_int(a["cong"]))
    if a.get("nome"): w.append("m.nome_busca LIKE '%%'||f_unaccent(%s)||'%%'"); p.append(a["nome"])
    with conn() as c:
        rows = c.execute("SELECT m.id,m.nome,m.nome_busca,m.data_nascimento,cg.nome congregacao FROM membros m LEFT JOIN congregacoes cg ON cg.id=m.congregacao_id WHERE " + " AND ".join(w), p).fetchall()
    md = lambda x: x.month * 100 + x.day
    ini, fim = (md(de) if de else None), (md(ate) if ate else None)
    todo_ano = bool(de and ate and (ate - de).days >= 365)
    volta = bool(de and ate and not todo_ano and ini > fim)  # período que atravessa a virada do ano (ex.: 20/12 a 10/01)
    out = []
    for r in rows:
        n = r["data_nascimento"]; x = md(n)
        if mes and n.month != mes: continue
        if not todo_ano:
            if volta:
                if not (x >= ini or x <= fim): continue
            else:
                if ini is not None and x < ini: continue
                if fim is not None and x > fim: continue
        out.append(dict(id=r["id"], congregacao=r["congregacao"], nome=r["nome"], dia=n.day, mes=n.month, _md=x, _k=r["nome_busca"]))
    ref = ini if volta else 0
    out.sort(key=lambda r: (r["_md"] < ref, r["_md"], r["_k"]))  # dia (dentro do mês) e depois ordem alfabética
    for r in out: r.pop("_md"); r.pop("_k")
    return out, None

@app.get("/api/aniversariantes")
def api_aniversariantes():
    rows, erro = consultar_aniversariantes(request.args)
    if erro: return jsonify(erro=erro), 400
    return jsonify(rows=rows)

def texto_consulta(a):
    de, ate, mes = parse_data(a.get("de")), parse_data(a.get("ate")), _int(a.get("mes"))
    partes = []
    if mes: partes.append("Mês: " + MESES[mes - 1])
    if de and ate: partes.append(f"Período: {de:%d/%m} a {ate:%d/%m}")
    elif de: partes.append(f"Período: a partir de {de:%d/%m}")
    elif ate: partes.append(f"Período: até {ate:%d/%m}")
    return " | ".join(partes) or "Todos os meses"

@app.get("/aniversariantes/imprimir")
def imprimir_aniversariantes():
    a = request.args
    rows, erro = consultar_aniversariantes(a)
    if erro: return erro, 400
    cong = "Todas" if g.admin else "Todas as suas congregações"
    if a.get("cong"):
        with conn() as c: x = c.execute("SELECT nome FROM congregacoes WHERE id=%s", (_int(a["cong"]),)).fetchone()
        cong = x["nome"] if x and cong_ok(_int(a["cong"])) else "–"
    grupos = [(MESES[k - 1] if len({r["mes"] for r in rows}) > 1 else None, list(g)) for k, g in groupby(rows, key=lambda r: r["mes"])]
    return render_template("aniversariantes_imprimir.html", grupos=grupos, total=len(rows), consulta=texto_consulta(a), congregacao=cong,
                           nome=(a.get("nome") or "").strip(), emitido=datetime.now().strftime("%d/%m/%Y %H:%M"), usuario=session.get("nome", ""))

# ===================== CONFIGURAÇÃO: FAIXAS ETÁRIAS E RESUMO DOS MEMBROS =====================
FAIXAS = [("crianca", "Crianças"), ("adolescente", "Adolescentes"), ("jovem", "Jovens"), ("adulto", "Adultos"), ("idoso", "Idosos")]
FAIXAS_PADRAO = dict(crianca=11, adolescente=17, jovem=29, adulto=59)  # idoso = acima de adulto (sem limite)
IDADE_MAX_FAIXA = 119

def carregar_limites(c):
    """Idade máxima de cada faixa (exceto a última). Usa o padrão se algum registro estiver ausente."""
    lim = {r["slug"]: r["idade_ate"] for r in c.execute("SELECT slug,idade_ate FROM faixas_etarias").fetchall()}
    return {k: (lim[k] if lim.get(k) is not None else FAIXAS_PADRAO[k]) for k in FAIXAS_PADRAO}

def faixas_descricao(lim):
    """Lista de faixas com intervalo [de, ate]; 'ate' é None na última (sem limite)."""
    out, de = [], 0
    for slug, nome in FAIXAS:
        ate = lim.get(slug)
        out.append(dict(slug=slug, nome=nome, de=de, ate=ate))
        if ate is not None: de = ate + 1
    return out

def validar_limites(d):
    erros, v = [], {}
    d = d if isinstance(d, dict) else {}
    for slug, nome in FAIXAS[:-1]:
        x = d.get(slug)
        x = str(x).strip() if x is not None and not isinstance(x, bool) else ""
        if not x: erros.append(dict(campo=slug, msg=f"Informe a idade limite de {nome}.")); continue
        if not re.fullmatch(r"\d{1,3}", x): erros.append(dict(campo=slug, msg=f"A idade limite de {nome} deve ser um número inteiro.")); continue
        if int(x) > IDADE_MAX_FAIXA: erros.append(dict(campo=slug, msg=f"A idade limite de {nome} deve estar entre 0 e {IDADE_MAX_FAIXA}.")); continue
        v[slug] = int(x)
    if not erros:
        ant = None
        for slug, nome in FAIXAS[:-1]:
            if ant and v[slug] <= v[ant[0]]: erros.append(dict(campo=slug, msg=f"A idade limite de {nome} deve ser maior que a de {ant[1]} ({v[ant[0]]})."))
            ant = (slug, nome)
    return v, erros

@app.get("/api/config/faixas-etarias")
def ver_faixas():
    with conn() as c: lim = carregar_limites(c)
    return jsonify(faixas=faixas_descricao(lim), padrao=FAIXAS_PADRAO)

@app.put("/api/config/faixas-etarias")
@so_admin
def salvar_faixas():
    v, erros = validar_limites(request.get_json(silent=True))
    if erros: return jsonify(erros=erros), 400
    with conn() as c:
        for i, (slug, nome) in enumerate(FAIXAS[:-1], 1):
            c.execute("INSERT INTO faixas_etarias(slug,nome,ordem,idade_ate) VALUES(%s,%s,%s,%s) ON CONFLICT(slug) DO UPDATE SET idade_ate=EXCLUDED.idade_ate,updated_at=now()", (slug, nome, i, v[slug]))
        lim = carregar_limites(c)
    return jsonify(faixas=faixas_descricao(lim), padrao=FAIXAS_PADRAO)

def idade_em(nasc, hoje):
    return hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))

def resumo_membros(rows, lim, hoje):
    """Conta os cadastros ATIVOS por membresia, sexo e faixa etária (idade em anos completos)."""
    faixas = faixas_descricao(lim)
    r = dict(ativos=len(rows), membros=0, congregados=0, mulheres=0, homens=0, sem_sexo=0, sem_nascimento=0)
    cont = {f["slug"]: 0 for f in faixas}
    for x in rows:
        r["membros" if x["status_membresia"] == "Membro" else "congregados"] += 1
        if x["sexo"] == "Feminino": r["mulheres"] += 1
        elif x["sexo"] == "Masculino": r["homens"] += 1
        else: r["sem_sexo"] += 1
        if not x["data_nascimento"]: r["sem_nascimento"] += 1; continue
        idade = idade_em(x["data_nascimento"], hoje)
        for f in faixas:
            if f["ate"] is None or idade <= f["ate"]: cont[f["slug"]] += 1; break
    r["faixas"] = [dict(f, total=cont[f["slug"]]) for f in faixas]
    return r

@app.get("/api/painel/membros")
def painel_membros():
    with conn() as c:
        lim = carregar_limites(c)
        w, p = ["excluido_em IS NULL", "status='Ativo'"], []; filtro_cong("congregacao_id", w, p)
        rows = c.execute("SELECT sexo,status_membresia,data_nascimento FROM membros WHERE " + " AND ".join(w), p).fetchall()
    return jsonify(resumo_membros(rows, lim, date.today()))

# ===================== RELATÓRIO COMPARATIVO (período atual x anterior) =====================
import calendar
from datetime import timedelta

def ultimo_dia(d): return calendar.monthrange(d.year, d.month)[1]
def meses_atras(d, n):
    m = d.year * 12 + d.month - 1 - n
    return date(m // 12, m % 12 + 1, 1)

def periodo_anterior(de, ate):
    """Mesmo período, calculado para trás. Meses fechados (mês, trimestre, ano...) voltam o mesmo nº de meses;
    outros períodos voltam o mesmo nº de dias, terminando na véspera da data inicial."""
    if de.day == 1 and ate.day == ultimo_dia(ate):
        n = (ate.year - de.year) * 12 + ate.month - de.month + 1
        return meses_atras(de, n), de - timedelta(days=1)
    fim = de - timedelta(days=1)
    return fim - (ate - de), fim

def interpretar_descricao(d):
    """(tipo, chave, rótulo): 'ocorrencia' conta registros no período; 'estoque' conta pessoas na data final."""
    if d == "oc:todas": return "ocorrencia", None, "Todas as ocorrências"
    if d.startswith("oc:") and d[3:] in TIPOS: return "ocorrencia", d[3:], TIPOS[d[3:]][0]
    if d == "membros": return "estoque", ("membro", None), "Membros ativos"
    if d == "congregados": return "estoque", ("congregado", None), "Congregados ativos"
    if d == "geral": return "estoque", ("geral", None), "Geral: membros + congregados ativos"
    if d == "cargo:todos": return "estoque", ("membro", CARGO_TODOS), "Cargo eclesiástico: geral, todos os cargos juntos (membros ativos)"
    if d.startswith("cargo:") and d[6:] in CARGOS: return "estoque", ("membro", d[6:]), f"Cargo eclesiástico: {d[6:]} (membros ativos)"
    return None

def variacao(ant, atu):
    dif = atu - ant
    return dict(tipo="subiu" if dif > 0 else "caiu" if dif < 0 else "igual", dif=dif, pct=round(dif * 100 / ant) if ant else None)

def situacao_em(m, d):
    """'membro', 'congregado' ou None na data d (fim do dia), pelas datas da ficha:
    chegada (veio para a igreja) -> recebimento (registrado como membro) -> saída (carta, falecimento...)."""
    if m["data_saida"] and m["data_saida"] <= d: return None
    che, rec = m["data_chegada"], m["data_recebimento"]
    if m["status_membresia"] == "Membro":
        ini = rec or che
        if ini is None or ini <= d: return "membro"
        return "congregado" if che and che <= d else None
    ini = che or rec
    return "congregado" if ini is None or ini <= d else None

def contar_ocorrencias(rows, per_ant, per_atu):
    cont = {}
    for r in rows:
        c = cont.setdefault(r["congregacao_id"], [0, 0])
        if per_ant[0] <= r["data"] <= per_ant[1]: c[0] += r["n"]
        if per_atu[0] <= r["data"] <= per_atu[1]: c[1] += r["n"]
    return cont

CARGO_TODOS = "*"  # marcador: qualquer cargo eclesiástico (Obreiro, Diácono, Presbítero, Evangelista, Pastor)

def contar_estoque(membros, chave, per_ant, per_atu):
    quem, cargo = chave
    cont, sem_data = {}, 0
    for m in membros:
        if (m["data_chegada"] or m["data_recebimento"]) is None and not m["data_saida"]: sem_data += 1
        c = cont.setdefault(m["congregacao_id"], [0, 0])
        for i, d in enumerate((per_ant[1], per_atu[1])):
            sit = situacao_em(m, d)
            if quem == "geral": ok = sit is not None  # membros + congregados
            else: ok = sit == quem and (cargo is None or bool(m["cargo"]) if cargo == CARGO_TODOS else cargo is None or m["cargo"] == cargo)
            if ok: c[i] += 1
    return cont, sem_data

def parse_periodo(a):
    de, ate = parse_data(a.get("de")), parse_data(a.get("ate"))
    if not de or not ate:
        return None, None, ("Data inválida." if de is False or ate is False else "Informe a data inicial e a data final.")
    if ate < de: return None, None, "A data final não pode ser anterior à data inicial."
    return de, ate, None

def relatorio_comparativo(a):
    """Retorna (relatório, erro). Uma linha por congregação, com total e as notas de como o número foi calculado."""
    de, ate, erro = parse_periodo(a)
    if erro: return None, erro
    desc = interpretar_descricao(a.get("descricao") or "")
    if not desc: return None, "Descrição inválida."
    tipo, chave, rotulo = desc
    ant = periodo_anterior(de, ate); atu = (de, ate)
    notas = []
    with conn() as c:
        wc, pc = [], []; filtro_cong("id", wc, pc)
        congs = c.execute("SELECT id,nome,ativo FROM congregacoes" + (" WHERE " + wc[0] if wc else "") + " ORDER BY nome", pc).fetchall()
        if tipo == "ocorrencia":
            w, p = ["o.cancelado_em IS NULL", "l.situacao<>'Cancelada'", "l.data_ocorrencia BETWEEN %s AND %s"], [ant[0], ate]
            if chave: w.append("t.slug=%s"); p.append(chave)
            filtro_cong("l.congregacao_id", w, p)
            rows = c.execute("SELECT l.congregacao_id,l.data_ocorrencia data,count(*) n FROM ocorrencias o JOIN lotes_ocorrencias l ON l.id=o.lote_id "
                             "JOIN tipos_ocorrencia t ON t.id=l.tipo_ocorrencia_id WHERE " + " AND ".join(w) + " GROUP BY l.congregacao_id,l.data_ocorrencia", p).fetchall()
            cont = contar_ocorrencias(rows, ant, atu)
            notas.append("Conta os registros (pessoas) pela data da ocorrência, dentro de cada período. Ocorrências canceladas não entram.")
        else:
            wm, pm = ["excluido_em IS NULL"], []; filtro_cong("congregacao_id", wm, pm)
            membros = c.execute("SELECT congregacao_id,status_membresia,cargo,data_chegada,data_recebimento,data_saida FROM membros "
                                "WHERE " + " AND ".join(wm) + " AND (status='Ativo' OR data_saida IS NOT NULL)", pm).fetchall()
            cont, sem_data = contar_estoque(membros, chave, ant, atu)
            sem_saida = c.execute("SELECT count(*) n FROM membros WHERE " + " AND ".join(wm) + " AND status IN ('Desligado','Faleceu') AND data_saida IS NULL", pm).fetchone()["n"]
            notas.append(f"Posição no fim de cada período ({ant[1]:%d/%m/%Y} e {ate:%d/%m/%Y}), calculada pelas datas da ficha: chegada à igreja, recebimento (registro como membro) e saída (carta, falecimento ou desligamento).")
            if sem_data: notas.append(f"{sem_data} cadastro(s) sem data de chegada nem de recebimento foram considerados presentes em todos os períodos.")
            if sem_saida: notas.append(f"{sem_saida} cadastro(s) Desligado/Faleceu sem data de saída não entram na contagem; informe a data na ficha para que sejam considerados.")
            notas.append("A congregação" + (" e o cargo" if chave[1] else "") + " são os atuais do cadastro (o sistema não guarda o histórico de mudanças).")
    linhas = []
    for cg in congs:
        x = cont.get(cg["id"], [0, 0])
        if cg["ativo"] or x[0] or x[1]: linhas.append(dict(congregacao=cg["nome"], anterior=x[0], atual=x[1], variacao=variacao(*x)))
    x = cont.get(None, [0, 0])
    if x[0] or x[1]: linhas.append(dict(congregacao="Sem congregação", anterior=x[0], atual=x[1], variacao=variacao(*x)))
    ta, tb = sum(l["anterior"] for l in linhas), sum(l["atual"] for l in linhas)
    iso = lambda p: dict(de=p[0].isoformat(), ate=p[1].isoformat())
    return dict(descricao=rotulo, periodo_anterior=iso(ant), periodo_atual=iso(atu), linhas=linhas,
                total=dict(anterior=ta, atual=tb, variacao=variacao(ta, tb)), notas=notas), None

@app.get("/api/relatorios/comparativo")
def api_comparativo():
    r, erro = relatorio_comparativo(request.args)
    if erro: return jsonify(erro=erro), 400
    return jsonify(r)

@app.get("/relatorios/comparativo/imprimir")
def imprimir_comparativo():
    r, erro = relatorio_comparativo(request.args)
    if erro: return erro, 400
    return render_template("relatorio_comparativo_imprimir.html", r=r, emitido=datetime.now().strftime("%d/%m/%Y %H:%M"), usuario=session.get("nome", ""))

# ===================== USUÁRIOS E PERMISSÕES (somente administrador) =====================
PERFIS = ("admin", "usuario")
LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")

class SemAdmin(Exception): pass

def validar_usuario(d, c, novo):
    """Valida os dados enviados pelo administrador. Retorna (valores, erros)."""
    erros, v = [], {}
    v["nome"] = (d.get("nome") or "").strip()
    if not v["nome"]: erros.append("Informe o nome do usuário.")
    elif len(v["nome"]) > 120: erros.append("Nome muito longo (máx. 120 caracteres).")
    if novo:
        v["login"] = (d.get("login") or "").strip()
        if not LOGIN_RE.fullmatch(v["login"]): erros.append("Login inválido: use de 3 a 40 letras, números, ponto, hífen ou sublinhado.")
    v["perfil"] = d.get("perfil")
    if v["perfil"] not in PERFIS: erros.append("Selecione o perfil de acesso."); v["perfil"] = None
    v["ativo"] = d.get("ativo") is not False and d.get("ativo") not in (0, "0", "false")
    senha = d.get("senha") or ""
    if senha or novo:
        if not isinstance(senha, str) or not 8 <= len(senha) <= 128: erros.append("A senha deve ter entre 8 e 128 caracteres.")
        else: v["senha"] = senha
    ids = d.get("congregacoes") or []
    if not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in ids): erros.append("Lista de congregações inválida."); ids = []
    ids = sorted(set(ids))
    if ids:
        ok = {r["id"] for r in c.execute("SELECT id FROM congregacoes WHERE id = ANY(%s::int[])", (ids,))}
        if len(ok) != len(ids): erros.append("Há congregação inexistente na lista.")
    if v["perfil"] == "usuario" and not ids: erros.append("Selecione ao menos uma congregação para o usuário comum.")
    v["congregacoes"] = [] if v["perfil"] == "admin" else ids   # administrador acessa todas
    return v, erros

@app.get("/api/usuarios")
@so_admin
def listar_usuarios():
    with conn() as c:
        return jsonify(c.execute("""SELECT u.id,u.nome,u.login,u.perfil,u.ativo,
            coalesce(array_agg(uc.congregacao_id ORDER BY uc.congregacao_id) FILTER (WHERE uc.congregacao_id IS NOT NULL),'{}') congregacoes
            FROM usuarios u LEFT JOIN usuario_congregacoes uc ON uc.usuario_id=u.id GROUP BY u.id ORDER BY lower(u.nome),u.id""").fetchall())

def gravar_congs(c, uid, ids):
    c.execute("DELETE FROM usuario_congregacoes WHERE usuario_id=%s", (uid,))
    for i in ids: c.execute("INSERT INTO usuario_congregacoes(usuario_id,congregacao_id) VALUES(%s,%s)", (uid, i))

@app.post("/api/usuarios")
@so_admin
def criar_usuario():
    d = request.get_json(silent=True) or {}
    try:
        with conn() as c:
            v, erros = validar_usuario(d, c, True)
            if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
            uid = c.execute("INSERT INTO usuarios(nome,login,senha_hash,perfil,ativo) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                            (v["nome"], v["login"], generate_password_hash(v["senha"]), v["perfil"], v["ativo"])).fetchone()["id"]
            gravar_congs(c, uid, v["congregacoes"])
    except psycopg.errors.UniqueViolation:
        return jsonify(erros=[dict(msg="Já existe um usuário com esse login.")]), 400
    return jsonify(id=uid), 201

@app.put("/api/usuarios/<int:id>")
@so_admin
def editar_usuario(id):
    d = request.get_json(silent=True) or {}
    try:
        with conn() as c:
            c.execute("SELECT id FROM usuarios WHERE perfil='admin' AND ativo FOR UPDATE")  # serializa mudanças de administradores
            if not c.execute("SELECT 1 FROM usuarios WHERE id=%s", (id,)).fetchone(): return jsonify(erro="Usuário não encontrado."), 404
            v, erros = validar_usuario(d, c, False)
            if id == session["uid"] and (v.get("perfil") != "admin" or not v["ativo"]):
                erros.append("Você não pode alterar o seu próprio perfil nem desativar a sua própria conta.")
            if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
            c.execute("UPDATE usuarios SET nome=%s,perfil=%s,ativo=%s,updated_at=now() WHERE id=%s", (v["nome"], v["perfil"], v["ativo"], id))
            if "senha" in v: c.execute("UPDATE usuarios SET senha_hash=%s WHERE id=%s", (generate_password_hash(v["senha"]), id))
            gravar_congs(c, id, v["congregacoes"])
            if not c.execute("SELECT 1 FROM usuarios WHERE perfil='admin' AND ativo LIMIT 1").fetchone(): raise SemAdmin()
    except SemAdmin:
        return jsonify(erros=[dict(msg="O sistema precisa ter ao menos um administrador ativo.")]), 400
    return jsonify(ok=True)

# ---------------------------------------------------------------- AGENDA (semana, eventos, aniversariantes, Datashow)
DIAS_SEMANA = ["SEGUNDA-FEIRA", "TERÇA-FEIRA", "QUARTA-FEIRA", "QUINTA-FEIRA", "SEXTA-FEIRA", "SÁBADO", "DOMINGO"]

def segunda_da_semana(d):
    """Segunda-feira da semana de d (a semana vai de segunda a domingo)."""
    return d - timedelta(days=d.weekday())

def parse_hora(x):
    """'' / None -> None; 'HH:MM' válido -> time; qualquer outra coisa -> False."""
    if x in (None, ""): return None
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)(?::00)?", str(x).strip())
    return datetime.strptime(m.group(1) + ":" + m.group(2), "%H:%M").time() if m else False

def _titulo(d, erros):
    t = re.sub(r"\s+", " ", str(d.get("titulo") or "")).strip()
    if not t: erros.append("Informe a descrição.")
    elif len(t) > 200: erros.append("A descrição pode ter no máximo 200 caracteres.")
    return t

def validar_compromisso(d):
    """Compromisso = data + horário (opcional) + descrição. Devolve (valores, lista_de_erros)."""
    erros, v = [], {}
    v["data"] = parse_data(d.get("data"))
    if not v["data"]: erros.append("Informe uma data válida."); v["data"] = None
    v["hora"] = parse_hora(d.get("hora"))
    if v["hora"] is False: erros.append("Horário inválido. Use o formato HH:MM."); v["hora"] = None
    v["titulo"] = _titulo(d, erros)
    return v, erros

def validar_evento(d):
    """Evento = título + data de início + data final (opcional)."""
    erros, v = [], {}
    v["data_inicio"] = parse_data(d.get("data_inicio"))
    if not v["data_inicio"]: erros.append("Informe a data do evento."); v["data_inicio"] = None
    v["data_fim"] = parse_data(d.get("data_fim"))
    if v["data_fim"] is False: erros.append("Data final inválida."); v["data_fim"] = None
    elif v["data_fim"] and v["data_inicio"] and v["data_fim"] < v["data_inicio"]: erros.append("A data final não pode ser anterior à data inicial.")
    elif v["data_fim"] == v["data_inicio"]: v["data_fim"] = None
    v["titulo"] = _titulo(d, erros)
    v["tem_categoria"] = "categoria_id" in d          # quem não envia (cadastro rápido) não mexe na categoria existente
    v["categoria_id"] = _int(d.get("categoria_id"), 0) or None
    return v, erros

COLS_COMP = "c.id,c.data,to_char(c.hora,'HH24:MI') hora,c.titulo,c.congregacao_id,cg.nome congregacao"
FROM_COMP = "agenda_compromissos c LEFT JOIN congregacoes cg ON cg.id=c.congregacao_id"
SEM_CONG_MSG = "Você não tem acesso à congregação selecionada."

def _cong_param():
    """?cong= da requisição. Devolve (valor, resposta_de_erro). valor: None = todas as congregações liberadas ao usuário;
    0 = compromissos antigos, sem congregação (só administrador); n = a congregação n (se o usuário tiver acesso)."""
    raw = request.args.get("cong")
    if raw in (None, ""): return None, None
    n = _int(raw, None)
    if n is None or n < 0: return None, (jsonify(erro="Congregação inválida."), 400)
    if (n == 0 and not g.admin) or (n and not cong_ok(n)): return None, (jsonify(erro=SEM_CONG_MSG), 403)
    return n, None

def filtro_agenda(coluna, cong, w, p):
    """Restringe a consulta à congregação escolhida; sem escolha, às congregações liberadas ao usuário (admin: todas)."""
    if cong is None: filtro_cong(coluna, w, p)
    elif cong == 0: w.append(f"{coluna} IS NULL")
    else: w.append(f"{coluna} = %s"); p.append(cong)

def compromissos_entre(c, de, ate, cong=None):
    w, p = ["c.data BETWEEN %s AND %s"], [de, ate]; filtro_agenda("c.congregacao_id", cong, w, p)
    return c.execute("SELECT " + COLS_COMP + " FROM " + FROM_COMP + " WHERE " + " AND ".join(w) + " ORDER BY c.data, c.hora NULLS FIRST, lower(c.titulo), c.id", p).fetchall()

def validar_cong_compromisso(c, d, atual=None, tem_atual=False):
    """Congregação do compromisso (body 'congregacao_id'). Devolve (id|None, erro|None).
    Novo: obrigatória, ativa e liberada ao usuário. Edição: sem o campo, mantém a atual; compromisso antigo (sem congregação) só
    volta a ficar sem congregação se já estava assim."""
    if tem_atual and "congregacao_id" not in d: return atual, None
    n = _int(d.get("congregacao_id"), None) or None
    if n is None:
        return (None, None) if tem_atual and atual is None else (None, "Selecione a congregação.")
    if not cong_ok(n): return None, SEM_CONG_MSG
    if not c.execute("SELECT 1 FROM congregacoes WHERE id=%s AND (ativo OR id=%s)", (n, atual or 0)).fetchone(): return None, "Selecione uma congregação ativa."
    return n, None

COLS_EV = "e.id,e.data_inicio,e.data_fim,e.titulo,e.categoria_id,k.nome categoria,k.cor"

def eventos_desde(c, desde, ate=None):
    """Eventos que ainda não terminaram em 'desde' (e começam até 'ate', se informado)."""
    w, p = ["COALESCE(e.data_fim,e.data_inicio) >= %s"], [desde]
    if ate: w.append("e.data_inicio <= %s"); p.append(ate)
    return c.execute("SELECT " + COLS_EV + " FROM agenda_eventos e LEFT JOIN agenda_categorias k ON k.id=e.categoria_id WHERE " + " AND ".join(w) + " ORDER BY e.data_inicio, lower(e.titulo), e.id", p).fetchall()

def aniversariantes_semana(ini, cong=None):
    """Aniversariantes de segunda a domingo (respeita as congregações do usuário e a congregação escolhida). Devolve lista com a data do aniversário na semana."""
    fim = ini + timedelta(days=6)
    a = {"de": ini.isoformat(), "ate": fim.isoformat()}
    if cong: a["cong"] = cong
    rows, erro = consultar_aniversariantes(a)
    out = []
    for r in rows or []:
        for i in range(7):
            d = ini + timedelta(days=i)
            if (d.month, d.day) == (r["mes"], r["dia"]): break
        else:  # 29/02 em ano não bissexto: comemora em 28/02
            d = next((ini + timedelta(days=i) for i in range(7) if (ini + timedelta(days=i)).month == 2 and (ini + timedelta(days=i)).day == 28), ini)
        out.append(dict(id=r["id"], nome=r["nome"], congregacao=r["congregacao"], data=d, dia_semana=DIAS_SEMANA[d.weekday()]))
    out.sort(key=lambda r: (r["data"], r["nome"].lower()))
    return out

def montar_semana(c, ref, cong=None):
    """Agenda de segunda a domingo da semana de ref (da congregação escolhida ou de todas as liberadas), com o(s) evento(s) em destaque:
    'destaques' traz um por congregação; 'destaque' é o primeiro (com uma congregação só, é o dela)."""
    ini = segunda_da_semana(ref); fim = ini + timedelta(days=6)
    por_dia = {}
    for r in compromissos_entre(c, ini, fim, cong): por_dia.setdefault(r["data"], []).append(r)
    dias = [dict(data=ini + timedelta(days=i), nome=DIAS_SEMANA[i], compromissos=por_dia.get(ini + timedelta(days=i), [])) for i in range(7)]
    w, p = ["d.semana=%s"], [ini]; filtro_agenda("c.congregacao_id", cong, w, p)
    dqs = c.execute("SELECT " + COLS_COMP + " FROM agenda_destaques d JOIN agenda_compromissos c ON c.id=d.compromisso_id "
                    "LEFT JOIN congregacoes cg ON cg.id=c.congregacao_id WHERE " + " AND ".join(w) + " ORDER BY cg.nome NULLS FIRST, c.id", p).fetchall()
    nome = c.execute("SELECT id,nome FROM congregacoes WHERE id=%s", (cong,)).fetchone() if cong else None
    multi = len({r["congregacao_id"] for rs in por_dia.values() for r in rs} | {r["congregacao_id"] for r in dqs}) > 1
    return dict(inicio=ini, fim=fim, anterior=ini - timedelta(days=7), proxima=ini + timedelta(days=7), dias=dias,
                destaque=dqs[0] if dqs else None, destaques=dqs, congregacao=nome, multi=multi)

def _ref():
    """Data de referência da requisição (?data=); sem ela, hoje. Devolve (data, erro_response)."""
    if not request.args.get("data"): return date.today(), None
    d = parse_data(request.args.get("data"))
    return (None, (jsonify(erro="Data inválida."), 400)) if not d else (d, None)

MAX_DUPLICACOES = 60

def datas_duplicacao(ev_inicio, ev_fim, datas):
    """Para cada nova data inicial, devolve (inicio, fim) mantendo a duração do evento original. Ignora repetidas e a data original."""
    dur = (ev_fim - ev_inicio).days if ev_fim else 0
    vistos, out = set(), []
    for d in datas:
        if d == ev_inicio or d in vistos: continue
        vistos.add(d); out.append((d, d + timedelta(days=dur) if dur else None))
    return sorted(out)

@app.get("/api/agenda/categorias")
def agenda_categorias():
    with conn() as c:
        return jsonify(c.execute("SELECT id,slug,nome,cor,mostra_em_eventos FROM agenda_categorias ORDER BY ordem").fetchall())

@app.get("/api/agenda/anual")
def agenda_anual():
    """Tudo que o calendário do ano precisa: eventos que tocam o ano, compromissos do ano e categorias."""
    ano = _int(request.args.get("ano"), date.today().year)
    if not 2000 <= ano <= 2100: return jsonify(erro="Ano inválido."), 400
    cong, e = _cong_param()
    if e: return e
    de, ate = date(ano, 1, 1), date(ano, 12, 31)
    with conn() as c:
        ev = eventos_desde(c, de, ate)
        comp = compromissos_entre(c, de, ate, cong)
        cats = c.execute("SELECT id,slug,nome,cor FROM agenda_categorias ORDER BY ordem").fetchall()
    return jsonify(ano=ano, eventos=ev, compromissos=comp, categorias=cats)

@app.post("/api/agenda/eventos/<int:id>/duplicar")
def duplicar_evento(id):
    """Copia o evento para outras datas (cópias independentes; mantém a duração; datas que já têm o mesmo evento são ignoradas)."""
    b = request.get_json(silent=True) or {}
    lista = b.get("datas")
    if not isinstance(lista, list) or not lista: return jsonify(erros=[dict(msg="Escolha pelo menos uma data.")]), 400
    datas = [parse_data(x) for x in lista]
    if not all(datas): return jsonify(erros=[dict(msg="Há uma data inválida na lista.")]), 400
    with conn() as c:
        o = c.execute("SELECT data_inicio,data_fim,titulo,categoria_id FROM agenda_eventos WHERE id=%s", (id,)).fetchone()
        if not o: return jsonify(erro="Evento não encontrado."), 404
        novas = datas_duplicacao(o["data_inicio"], o["data_fim"], datas)
        if not novas: return jsonify(erros=[dict(msg="Nenhuma data nova para criar: escolha outras datas.")]), 400
        if len(novas) > MAX_DUPLICACOES: return jsonify(erros=[dict(msg=f"O máximo por vez é {MAX_DUPLICACOES} datas.")]), 400
        ja = {r["data_inicio"] for r in c.execute("SELECT data_inicio FROM agenda_eventos WHERE data_inicio=ANY(%s) AND lower(btrim(titulo))=lower(btrim(%s))", ([i for i, _ in novas], o["titulo"])).fetchall()}
        out = [dict(data=i, data_fim=f, status="ja_existe" if i in ja else "nova") for i, f in novas]
        sim = b.get("simular") is True
        if not sim:
            for i, f in novas:
                if i in ja: continue
                c.execute("INSERT INTO agenda_eventos(data_inicio,data_fim,titulo,categoria_id,criado_por) VALUES(%s,%s,%s,%s,%s) ON CONFLICT (data_inicio, lower(btrim(titulo))) DO NOTHING", (i, f, o["titulo"], o["categoria_id"], session["uid"]))
    n = sum(1 for x in out if x["status"] == "nova")
    return jsonify(criados=0 if sim else n, ignorados=len(out) - n, datas=out, simulacao=sim)

@app.get("/api/agenda/resumo")
def agenda_resumo():
    """Cartão da agenda no Dashboard. ?data= é o 'hoje' do navegador (evita diferença de fuso com o servidor)."""
    hoje, e = _ref()
    if e: return e
    cong, e = _cong_param()
    if e: return e
    ini = segunda_da_semana(hoje)
    with conn() as c:
        w = montar_semana(c, hoje, cong)
        hoje_l = next(d["compromissos"] for d in w["dias"] if d["data"] == hoje) if ini <= hoje <= ini + timedelta(days=6) else []
        wp, pp = ["c.data > %s"], [hoje]; filtro_agenda("c.congregacao_id", cong, wp, pp)
        prox = c.execute("SELECT " + COLS_COMP + " FROM " + FROM_COMP + " WHERE " + " AND ".join(wp) + " ORDER BY c.data, c.hora NULLS FIRST, lower(c.titulo), c.id LIMIT 5", pp).fetchall()
        ev = eventos_desde(c, hoje, hoje + timedelta(days=90))[:3]
    an = aniversariantes_semana(ini, cong)
    multi = w["multi"] or len({r["congregacao_id"] for r in prox}) > 1
    return jsonify(hoje=hoje, destaque=w["destaque"], destaques=w["destaques"], multi=multi, compromissos_hoje=hoje_l, proximos=prox, eventos=ev,
                   aniversariantes_semana=len(an), aniversariantes_hoje=[a["nome"] for a in an if a["data"] == hoje])

@app.get("/api/agenda/semana")
def agenda_semana():
    ref, e = _ref()
    if e: return e
    cong, e = _cong_param()
    if e: return e
    with conn() as c: return jsonify(montar_semana(c, ref, cong))

@app.get("/api/agenda/aniversariantes")
def agenda_aniversariantes():
    ref, e = _ref()
    if e: return e
    cong, e = _cong_param()
    if e: return e
    ini = segunda_da_semana(ref)
    return jsonify(inicio=ini, fim=ini + timedelta(days=6), rows=aniversariantes_semana(ini, cong))

@app.get("/api/agenda/eventos")
def agenda_eventos():
    ref, e = _ref()
    if e: return e
    with conn() as c: return jsonify(eventos_desde(c, segunda_da_semana(ref)))

@app.get("/api/agenda/datashow")
def agenda_datashow():
    """Tudo que o Datashow precisa em uma chamada: 7 dias, destaque, eventos e aniversariantes."""
    ref, e = _ref()
    if e: return e
    cong, e = _cong_param()
    if e: return e
    with conn() as c:
        w = montar_semana(c, ref, cong)
        w["eventos"] = eventos_desde(c, w["inicio"], w["inicio"] + timedelta(days=90))
    w["aniversariantes"] = aniversariantes_semana(w["inicio"], cong)
    return jsonify(w)

@app.get("/datashow")
def datashow():
    return render_template("datashow.html", csrf=csrf())

@app.post("/api/agenda/compromissos")
@app.put("/api/agenda/compromissos/<int:id>")
def salvar_compromisso(id=None):
    b = request.get_json(silent=True) or {}
    v, erros = validar_compromisso(b)
    if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
    try:
        with conn() as c:
            atual, tem = None, False
            if id:  # só mexe em compromisso de congregação liberada ao usuário (os antigos, sem congregação, só o administrador)
                o = c.execute("SELECT congregacao_id FROM agenda_compromissos WHERE id=%s FOR UPDATE", (id,)).fetchone()
                if not o or not cong_ok(o["congregacao_id"]): return jsonify(erro="Compromisso não encontrado."), 404
                atual, tem = o["congregacao_id"], True
            v["congregacao_id"], erro = validar_cong_compromisso(c, b, atual, tem)
            if erro: return jsonify(erros=[dict(msg=erro)]), 403 if erro == SEM_CONG_MSG else 400
            if id:
                c.execute("UPDATE agenda_compromissos SET data=%s,hora=%s,titulo=%s,congregacao_id=%s,atualizado_em=now() WHERE id=%s", (v["data"], v["hora"], v["titulo"], v["congregacao_id"], id))
                if v["congregacao_id"] != atual:  # mudou de congregação: o destaque da semana não acompanha
                    c.execute("DELETE FROM agenda_destaques WHERE compromisso_id=%s", (id,))
            else:
                id = c.execute("INSERT INTO agenda_compromissos(data,hora,titulo,congregacao_id,criado_por) VALUES(%s,%s,%s,%s,%s) RETURNING id", (v["data"], v["hora"], v["titulo"], v["congregacao_id"], session["uid"])).fetchone()["id"]
                return jsonify(id=id), 201
    except psycopg.errors.UniqueViolation:
        return jsonify(erros=[dict(msg="Já existe um compromisso com esta data, horário e descrição.")]), 409
    return jsonify(id=id)

@app.delete("/api/agenda/compromissos/<int:id>")
def excluir_compromisso(id):
    with conn() as c:  # o destaque que apontava para ele some junto (ON DELETE CASCADE)
        o = c.execute("SELECT congregacao_id FROM agenda_compromissos WHERE id=%s", (id,)).fetchone()
        if not o or not cong_ok(o["congregacao_id"]): return jsonify(erro="Compromisso não encontrado."), 404
        c.execute("DELETE FROM agenda_compromissos WHERE id=%s", (id,))
    return jsonify(ok=True)

MAX_REPETICOES = 200

def datas_repeticao(origem, dias, semanas):
    """Datas (sem a da origem) em que o compromisso será repetido: para cada uma das 'semanas' semanas, a partir da
    semana da origem, os dias escolhidos (0 = segunda ... 6 = domingo)."""
    ini = segunda_da_semana(origem)
    return [d for w in range(semanas) for i in sorted(set(dias)) for d in [ini + timedelta(days=7 * w + i)] if d != origem]

def validar_repeticao(d):
    erros = []
    dias = d.get("dias")
    if not isinstance(dias, list) or not dias or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6 for x in dias): erros.append("Escolha pelo menos um dia da semana."); dias = []
    semanas = _int(d.get("semanas"), 1)
    if not 1 <= semanas <= 52: erros.append("O número de semanas deve ser de 1 a 52."); semanas = 1
    return sorted(set(dias)), semanas, erros

@app.post("/api/agenda/compromissos/<int:id>/repetir")
def repetir_compromisso(id):
    """Copia o compromisso para outros dias (cópias independentes: editar ou excluir uma não afeta as outras).
    Com simular=true só mostra o que seria criado. Datas que já têm o mesmo compromisso (mesmo horário e descrição) são ignoradas."""
    b = request.get_json(silent=True) or {}
    dias, semanas, erros = validar_repeticao(b)
    if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
    with conn() as c:
        o = c.execute("SELECT data,hora,titulo,congregacao_id FROM agenda_compromissos WHERE id=%s", (id,)).fetchone()
        if not o or not cong_ok(o["congregacao_id"]): return jsonify(erro="Compromisso não encontrado."), 404
        datas = datas_repeticao(o["data"], dias, semanas)
        if not datas: return jsonify(erros=[dict(msg="Nenhuma data nova para criar: escolha outros dias.")]), 400
        if len(datas) > MAX_REPETICOES: return jsonify(erros=[dict(msg=f"Isso criaria {len(datas)} compromissos; o máximo por vez é {MAX_REPETICOES}.")]), 400
        ja = {r["data"] for r in c.execute("SELECT data FROM agenda_compromissos WHERE data=ANY(%s) AND COALESCE(hora,'00:00'::time)=COALESCE(%s,'00:00'::time) AND lower(btrim(titulo))=lower(btrim(%s)) AND congregacao_id IS NOT DISTINCT FROM %s", (datas, o["hora"], o["titulo"], o["congregacao_id"])).fetchall()}
        out = [dict(data=d, nome=DIAS_SEMANA[d.weekday()], status="ja_existe" if d in ja else "nova") for d in datas]
        novas = [x["data"] for x in out if x["status"] == "nova"]
        if b.get("simular") is not True:
            for d in novas:
                c.execute("INSERT INTO agenda_compromissos(data,hora,titulo,congregacao_id,criado_por) VALUES(%s,%s,%s,%s,%s) "
                          "ON CONFLICT (COALESCE(congregacao_id,0), data, COALESCE(hora,'00:00'::time), lower(btrim(titulo))) DO NOTHING", (d, o["hora"], o["titulo"], o["congregacao_id"], session["uid"]))
    return jsonify(criados=0 if b.get("simular") is True else len(novas), ignorados=len(out) - len(novas), datas=out, simulacao=b.get("simular") is True)

@app.post("/api/agenda/eventos")
@app.put("/api/agenda/eventos/<int:id>")
def salvar_evento(id=None):
    v, erros = validar_evento(request.get_json(silent=True) or {})
    if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
    try:
        with conn() as c:
            if v["categoria_id"] and not c.execute("SELECT 1 FROM agenda_categorias WHERE id=%s", (v["categoria_id"],)).fetchone():
                return jsonify(erros=[dict(msg="Categoria inválida.")]), 400
            if id:
                if not c.execute("UPDATE agenda_eventos SET data_inicio=%s,data_fim=%s,titulo=%s,categoria_id=CASE WHEN %s THEN %s ELSE categoria_id END WHERE id=%s",
                                 (v["data_inicio"], v["data_fim"], v["titulo"], v["tem_categoria"], v["categoria_id"], id)).rowcount:
                    return jsonify(erro="Evento não encontrado."), 404
            else:
                id = c.execute("INSERT INTO agenda_eventos(data_inicio,data_fim,titulo,categoria_id,criado_por) VALUES(%s,%s,%s,%s,%s) RETURNING id", (v["data_inicio"], v["data_fim"], v["titulo"], v["categoria_id"], session["uid"])).fetchone()["id"]
                return jsonify(id=id), 201
    except psycopg.errors.UniqueViolation:
        return jsonify(erros=[dict(msg="Já existe um evento com esta data e descrição.")]), 409
    return jsonify(id=id)

@app.delete("/api/agenda/eventos/<int:id>")
def excluir_evento(id):
    with conn() as c:
        if not c.execute("DELETE FROM agenda_eventos WHERE id=%s", (id,)).rowcount: return jsonify(erro="Evento não encontrado."), 404
    return jsonify(ok=True)

@app.put("/api/agenda/destaque")
def definir_destaque():
    d = request.get_json(silent=True) or {}
    sem = parse_data(d.get("semana"))
    if not sem: return jsonify(erro="Semana inválida."), 400
    sem = segunda_da_semana(sem); cid = _int(d.get("compromisso_id"), 0)
    with conn() as c:
        if not cid:  # sem compromisso = remove o destaque da semana DESSA congregação (informada em congregacao_id; 0/vazio = compromissos antigos, só administrador)
            cg = _int(d.get("congregacao_id"), 0) or None
            if "congregacao_id" not in d: return jsonify(erro="Informe a congregação."), 400
            if not cong_ok(cg): return jsonify(erro=SEM_CONG_MSG), 403
            c.execute("DELETE FROM agenda_destaques WHERE semana=%s AND congregacao_id IS NOT DISTINCT FROM %s", (sem, cg)); return jsonify(ok=True)
        r = c.execute("SELECT data,congregacao_id FROM agenda_compromissos WHERE id=%s", (cid,)).fetchone()
        if not r or not cong_ok(r["congregacao_id"]): return jsonify(erro="Compromisso não encontrado."), 404
        if segunda_da_semana(r["data"]) != sem: return jsonify(erro="O evento em destaque precisa ser um compromisso desta semana."), 400
        c.execute("INSERT INTO agenda_destaques(semana,compromisso_id,congregacao_id,definido_por) VALUES(%s,%s,%s,%s) "
                  "ON CONFLICT(semana, COALESCE(congregacao_id,0)) DO UPDATE SET compromisso_id=EXCLUDED.compromisso_id,definido_por=EXCLUDED.definido_por,definido_em=now()", (sem, cid, r["congregacao_id"], session["uid"]))
    return jsonify(ok=True)

def trocar_senha():
    from getpass import getpass
    pw = getpass("Nova senha do admin (mín. 8 caracteres): ")
    if len(pw) < 8 or pw != getpass("Repita a senha: "): sys.exit("Senha curta demais ou diferente. Nada foi alterado.")
    with conn() as c:
        n = c.execute("UPDATE usuarios SET senha_hash=%s WHERE login=%s", (generate_password_hash(pw), LOGIN)).rowcount
    print("Senha do admin alterada." if n else "Usuário admin não encontrado. Rode: python app.py migrate")

def pendentes():
    with conn() as c:
        ok = c.execute("SELECT to_regclass('schema_migrations') t").fetchone()["t"]
        feitas = {r["arquivo"] for r in c.execute("SELECT arquivo FROM schema_migrations")} if ok else set()
        ext = {r["name"] for r in c.execute("SELECT name FROM pg_available_extensions WHERE name IN ('unaccent','pg_trgm')")}
    todas = [os.path.basename(f) for f in sorted(glob.glob(os.path.join(BASE, "migrations", "*.sql")))]
    return [m for m in todas if m not in feitas], ext

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    try:
        if cmd == "migrate":
            init_db(); print("Migrations aplicadas.")
        elif cmd == "senha":
            trocar_senha()
        else:
            pend, ext = pendentes()
            falta = {"unaccent", "pg_trgm"} - ext
            if falta: sys.exit("Extensões ausentes no servidor PostgreSQL: " + ", ".join(sorted(falta)))
            if pend: sys.exit("Migrations pendentes: " + ", ".join(pend) + "\nExecute: python app.py migrate")
            if cmd == "check": print("Conexão, extensões e migrations OK.")
            else:
                if DEV: print("ATENÇÃO: DEV_SEM_LOGIN=1 — sistema aberto SEM senha. Use só para teste local.")
                app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
    except psycopg.OperationalError as e:
        sys.exit("Não foi possível conectar ao PostgreSQL. Verifique o serviço e a DATABASE_URL no .env.\nDetalhe: " + str(e).splitlines()[0])
