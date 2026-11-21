import os, re, glob, json, sys, secrets, hashlib, base64, unicodedata, time
from functools import wraps
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from flask import Flask, request, jsonify, session, redirect, render_template, g, Response, has_request_context
from werkzeug.middleware.proxy_fix import ProxyFix
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
# Atrás do Render (um proxy): TRUST_PROXY=1 faz o sistema enxergar o IP real do visitante. Sem proxy, deixe desligado
# (ligar sem proxy deixaria o visitante forjar o próprio IP).
if os.environ.get("TRUST_PROXY") == "1": app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
INATIVIDADE = 15 * 60          # segundos sem uso até a sessão ser bloqueada (pede a senha de novo)
LOGIN_FALHAS_MAX = 5           # 5 senhas incorretas seguidas = conta bloqueada até o administrador liberar
IP_FALHAS_MAX, IP_JANELA = 5, "15 minutes"   # 5 falhas por origem (IP) em 15 min: recusa novas tentativas dessa origem (sem contá-las contra as contas)
MSG_LOGIN = "Não foi possível entrar. Confira o login e a senha ou procure a Secretaria."   # mesma resposta para qualquer falha
DUMMY_HASH = generate_password_hash(secrets.token_hex(16))   # equaliza o tempo de resposta quando o login não existe
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
            if erro_senha(pw, LOGIN): sys.exit("ADMIN_PASSWORD inválida: " + erro_senha(pw, LOGIN))
            c.execute("INSERT INTO usuarios(nome,login,senha_hash,perfil) VALUES('Administrador',%s,%s,'admin')", (LOGIN, generate_password_hash(pw)))

def erro_senha(s, login=None):
    """Regra de senha de todas as contas: 8 a 128 caracteres, com letras e números, e diferente do login."""
    if not isinstance(s, str) or not 8 <= len(s) <= 128: return "A senha deve ter entre 8 e 128 caracteres."
    if not (re.search(r"[A-Za-z]", s) and re.search(r"\d", s)): return "A senha deve ter letras e números."
    if login and s.lower() == str(login).lower(): return "A senha não pode ser igual ao login."
    return None

def _ip():
    return (request.remote_addr or "")[:45] if has_request_context() else ""

_AUTO = object()
SEGREDOS = ("senha", "password", "token", "hash", "secret", "csrf")
def _limpa(x):
    """Remove qualquer campo de segredo antes de gravar na auditoria (a auditoria nunca guarda senha, hash ou token)."""
    if isinstance(x, dict): return {k: ("[omitido]" if any(t in str(k).lower() for t in SEGREDOS) else _limpa(v)) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [_limpa(i) for i in x]
    return x.isoformat() if hasattr(x, "isoformat") else x

def audit(c, operacao, entidade=None, registro_id=None, antes=None, depois=None, motivo=None, resultado="sucesso",
          usuario_id=_AUTO, usuario_nome=_AUTO, perfil=_AUTO):
    """Registro permanente de auditoria. Com `c` (a mesma conexão da operação) grava na MESMA transação: ou a operação e o
    registro valem juntos, ou nenhum dos dois. Sem `c`, abre a própria conexão."""
    rq = has_request_context()
    if usuario_id is _AUTO: usuario_id = session.get("uid") if rq else None
    if usuario_nome is _AUTO: usuario_nome = session.get("nome") if rq else None
    if perfil is _AUTO: perfil = getattr(g, "perfil", None) if rq else None
    args = (usuario_id, (usuario_nome or None), perfil, _ip() or None, operacao, entidade, None if registro_id is None else str(registro_id),
            None if antes is None else Jsonb(_limpa(antes)), None if depois is None else Jsonb(_limpa(depois)), motivo, resultado)
    sql = "INSERT INTO auditoria(usuario_id,usuario_nome,perfil,ip,operacao,entidade,registro_id,antes,depois,motivo,resultado) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    if c is not None: c.execute(sql, args)
    else:
        with conn() as c2: c2.execute(sql, args)

def _ip_limitado(c, ip):
    return c.execute("SELECT count(*) n FROM login_falhas_ip WHERE ip=%s AND created_at > now() - %s::interval", (ip, IP_JANELA)).fetchone()["n"] >= IP_FALHAS_MAX

def _falha_ip(c, ip):
    c.execute("INSERT INTO login_falhas_ip(ip) VALUES(%s)", (ip,))

def _falha_senha_conta(c, u, origem):
    """Conta uma senha incorreta. Na 5ª seguida a conta é BLOQUEADA, sem prazo: só o administrador libera.
    Retorna (tentativas, bloqueou_agora). Uma conta já bloqueada não é contada de novo."""
    r = c.execute("""UPDATE usuarios SET falhas_login=falhas_login+1,
        bloqueado=CASE WHEN falhas_login+1>=%(m)s THEN true ELSE bloqueado END,
        bloqueado_em=CASE WHEN falhas_login+1>=%(m)s THEN now() ELSE bloqueado_em END,
        bloqueio_motivo=CASE WHEN falhas_login+1>=%(m)s THEN %(mot)s ELSE bloqueio_motivo END,
        total_bloqueios=total_bloqueios+CASE WHEN falhas_login+1>=%(m)s THEN 1 ELSE 0 END
        WHERE id=%(id)s AND NOT bloqueado RETURNING falhas_login,bloqueado""",
        dict(m=LOGIN_FALHAS_MAX, mot=f"{LOGIN_FALHAS_MAX} tentativas de senha incorreta seguidas", id=u["id"])).fetchone()
    if not r: return LOGIN_FALHAS_MAX, False
    audit(c, "login_falha", "usuarios", u["login"], depois=dict(tentativa=r["falhas_login"], de=LOGIN_FALHAS_MAX, origem=origem),
          resultado="falha", usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
    if r["bloqueado"]:
        audit(c, "conta_bloqueada", "usuarios", u["login"], depois=dict(tentativas=r["falhas_login"]), motivo="Quinta senha incorreta seguida",
              usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
    return r["falhas_login"], bool(r["bloqueado"])

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
    if request.endpoint in ("login", "membro_entrada", "membro_sair"): return
    # A sessão do portal (senha única, "pm") e a do secretário/usuário ("uid") convivem no mesmo navegador sem se derrubar:
    # as telas do portal seguem a sessão do portal; o resto do sistema segue a do usuário. Por isso, atualizar a página
    # (F5) deixa cada aba onde estava.
    if "pm" in session and request.endpoint in PORTAL_ROTAS_CODIGO and _pm_manda(): return _auth_codigo()
    if "uid" not in session and "pm" in session: return _auth_codigo()   # membro que entrou pela senha única do portal
    if "uid" not in session: return _nao_autenticado()
    # Permissões vêm SEMPRE do banco (nunca do cookie): desativar, trocar o perfil ou as congregações
    # de alguém vale já na próxima requisição dele.
    congs, membro_ok = [], False
    with conn() as c:
        u = c.execute("SELECT id,nome,perfil,ativo,senha_hash,membro_id,trocar_senha,bloqueado FROM usuarios WHERE id=%s", (session["uid"],)).fetchone()
        if u and u["ativo"] and u["perfil"] == "membro":
            # conta de membro: o acesso precisa estar ATIVO e o cadastro vinculado não pode ter sido excluído (revogar vale na hora)
            membro_ok = bool(c.execute("SELECT 1 FROM membro_acesso a JOIN membros m ON m.id=a.membro_id WHERE a.usuario_id=%s AND a.membro_id=%s AND a.situacao='ativo' AND m.excluido_em IS NULL",
                                       (u["id"], u["membro_id"])).fetchone())
        elif u and u["ativo"] and u["perfil"] != "admin":
            congs = [r["congregacao_id"] for r in c.execute("SELECT congregacao_id FROM usuario_congregacoes WHERE usuario_id=%s", (u["id"],))]
    if not u or not u["ativo"] or u["bloqueado"] or session.get("sv") != _sv(u["senha_hash"]) or (u["perfil"] == "membro" and not membro_ok):
        session.clear(); return _nao_autenticado()
    if u["perfil"] == "membro" and not _portal_ativo():   # chave geral desligada: vale também para contas individuais já criadas
        session.clear(); return _portal_indisponivel()
    g.admin = u["perfil"] == "admin"
    g.congs = None if g.admin else congs   # None = todas; lista = somente essas congregações
    g.membro_id = u["membro_id"] if u["perfil"] == "membro" else None   # vem do BANCO, nunca do navegador
    g.trocar_senha = bool(u["trocar_senha"])
    g.perfil = u["perfil"]
    session["nome"] = u["nome"]
    # Inatividade: 15 minutos sem uso bloqueiam a sessão; só a senha reabre. O servidor decide (o navegador só avisa).
    agora = time.time()
    if session.get("ult") is None: session["ult"] = agora
    if not session.get("travada") and agora - session["ult"] > INATIVIDADE:
        session["travada"] = True
        audit(None, "sessao_travada", "usuarios", u["id"], motivo="15 minutos sem uso")
    if session.get("travada"):
        if request.endpoint not in ("destravar", "logout"):
            if request.path.startswith("/api"): return jsonify(erro="Sessão bloqueada por inatividade. Informe a senha para continuar.", travada=True), 423
            return redirect("/destravar")
    elif request.headers.get("X-Atividade") != "automatica": session["ult"] = agora
    if g.membro_id:
        # Conta de membro: lista fechada (deny-by-default). Qualquer outra rota do sistema é negada no servidor.
        ep = request.endpoint
        if ep not in PORTAL_ROTAS: return _negado_membro()
        if g.trocar_senha and ep not in PORTAL_ROTAS_TROCA: return _negado_membro(trocar=True)

def _pm_manda():
    """Há sessão do portal (senha única). Nas telas do portal ela vale mesmo com um secretário/administrador logado no mesmo
    navegador; só cede lugar a uma conta individual de membro (perfil 'membro'), que tem o seu próprio fluxo."""
    if "uid" not in session: return True
    with conn() as c: u = c.execute("SELECT perfil FROM usuarios WHERE id=%s", (session["uid"],)).fetchone()
    return not u or u["perfil"] != "membro"

def _abrir_sessao_portal(pm):
    """Abre a sessão do portal SEM derrubar a do secretário aberta no mesmo navegador. Só uma conta individual de membro
    (que tem outro fluxo) sai da sessão ao entrar pela senha única."""
    uid = session.get("uid")
    if uid:
        with conn() as c: u = c.execute("SELECT perfil FROM usuarios WHERE id=%s", (uid,)).fetchone()
        if not u or u["perfil"] == "membro":
            for k in ("uid", "sv", "nome"): session.pop(k, None)
    session["pm"] = pm; session["pm_ult"] = time.time(); csrf()

PORTAL_INDISPONIVEL = "O acesso está temporariamente indisponível, fale com o secretário!"

def _portal_ativo():
    with conn() as c: return bool(c.execute("SELECT ativo FROM portal_config WHERE id=1").fetchone()["ativo"])

def _portal_indisponivel():
    if request.path.startswith("/api"): return jsonify(erro=PORTAL_INDISPONIVEL, portal_indisponivel=True), 401
    return redirect("/membro")

# Rotas que quem entrou pela SENHA ÚNICA pode usar (as mesmas telas do portal, sem troca de senha). Todo o resto é negado.
PORTAL_ROTAS_CODIGO = {"portal", "portal_ver", "portal_salvar", "portal_foto", "membro_sair"}

def _auth_codigo():
    """Sessão de membro aberta com a senha única. Tudo é conferido no BANCO a cada requisição: se o portal for desativado
    ou a senha trocada pelo administrador, a sessão cai na hora. O membro vem da sessão assinada, nunca do navegador."""
    pm = session.get("pm") or {}
    agora = time.time()
    if agora - session.get("pm_ult", agora) > INATIVIDADE:   # 15 min sem uso: a sessão por senha única termina
        session.pop("pm", None); session.pop("pm_ult", None)
        return (jsonify(erro="Sua sessão terminou por inatividade. Entre novamente com a senha do portal.", sessao_expirada=True), 401) if request.path.startswith("/api") else redirect("/membro")
    if request.headers.get("X-Atividade") != "automatica": session["pm_ult"] = agora
    with conn() as c:
        cf = c.execute("SELECT ativo,versao FROM portal_config WHERE id=1").fetchone()
        ok = bool(cf["ativo"]) and cf["versao"] == pm.get("v") and (pm.get("m") is None or bool(
            c.execute("SELECT 1 FROM membros WHERE id=%s AND excluido_em IS NULL", (pm.get("m"),)).fetchone()))
    if not ok:
        off = not cf["ativo"]; session.pop("pm", None)   # só a sessão do portal cai; a do secretário (se houver) continua
        if off: return _portal_indisponivel()
        return (jsonify(erro="Sua sessão terminou. Entre novamente com a senha do portal.", sessao_expirada=True), 401) if request.path.startswith("/api") else redirect("/membro")
    g.perfil = "membro (senha única)"
    g.admin, g.congs, g.membro_id, g.trocar_senha, g.codigo, g.novo = False, [], pm.get("m"), False, True, pm.get("m") is None   # novo = ainda vai criar o próprio cadastro
    if request.endpoint not in PORTAL_ROTAS_CODIGO: return _negado_membro()

# Únicas rotas que uma conta de membro pode usar. Todo o resto do sistema responde 403/redireciona.
PORTAL_ROTAS = {"portal", "portal_ver", "portal_salvar", "portal_foto", "portal_senha", "logout", "destravar"}
PORTAL_ROTAS_TROCA = {"portal", "portal_senha", "logout", "destravar"}   # enquanto a senha inicial não for trocada

def _negado_membro(trocar=False):
    if request.path.startswith("/api"):
        return jsonify(erro="Troque a senha inicial para continuar." if trocar else "Acesso restrito.", trocar_senha=trocar), 403
    return redirect("/portal")

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; connect-src 'self' https://viacep.com.br; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

@app.after_request
def _cabecalhos(resp):
    # Cabeçalhos de segurança em todas as respostas. 'unsafe-inline' é necessário hoje porque as telas usam script e estilo
    # dentro do próprio HTML; fica como melhoria futura mover isso para arquivos (aí a CSP pode ficar estrita).
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if app.config["SESSION_COOKIE_SECURE"]: resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Dados pessoais e credenciais nunca ficam guardados no cache do navegador (a foto define o próprio cache privado).
    if request.endpoint != "static" and "Cache-Control" not in resp.headers: resp.headers["Cache-Control"] = "no-store"
    return resp

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

def membro_visivel(cong_id, status_membresia):
    """Cadastro do portal (sem congregação e sem status da membresia) fica visível a todo usuário, até a secretaria completá-lo."""
    return cong_ok(cong_id) or (cong_id is None and status_membresia is None)

def filtro_cong(coluna, w, p):
    """Para usuário comum, acrescenta à consulta a restrição às congregações liberadas."""
    if not g.admin: w.append(f"{coluna} = ANY(%s::int[])"); p.append(list(g.congs))

@app.route("/login", methods=["GET", "POST"])
def login():
    msg, status = "", 200
    if request.method == "POST":
        login_ = (request.form.get("login") or "").strip()[:60]; senha = request.form.get("senha") or ""
        ip = _ip(); ident = login_ if LOGIN_RE.fullmatch(login_) else "(formato inválido)"   # nunca grava texto livre (poderia ser uma senha digitada no lugar errado)
        ok = desligado = membro = False; u = None
        with conn() as c:
            if _ip_limitado(c, ip):   # origem com tentativas demais: recusa SEM contar contra a conta (evita bloquear terceiros em massa)
                audit(c, "login_limitado_ip", "usuarios", ident, resultado="negado", usuario_id=None, usuario_nome=None, perfil="anônimo")
                msg, status = MSG_LOGIN, 429
            else:
                u = c.execute("SELECT * FROM usuarios WHERE login=%s", (login_,)).fetchone()
                senha_ok = check_password_hash(u["senha_hash"] if u else DUMMY_HASH, senha) and bool(u)   # sempre confere um hash: mesmo tempo com ou sem login
                if not u:
                    _falha_ip(c, ip); audit(c, "login_falha", "usuarios", ident, resultado="falha", usuario_id=None, usuario_nome=None, perfil="anônimo")
                elif not u["ativo"]:
                    _falha_ip(c, ip); audit(c, "login_conta_desativada", "usuarios", u["login"], resultado="negado", usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
                elif u["bloqueado"]:   # bloqueada: nem a senha certa entra; só o administrador libera
                    _falha_ip(c, ip); audit(c, "login_conta_bloqueada", "usuarios", u["login"], resultado="negado", usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
                elif not senha_ok:
                    _falha_ip(c, ip); _falha_senha_conta(c, u, "login")
                else:
                    membro = u["perfil"] == "membro"; ok = True
                    if membro:
                        desligado = not bool(c.execute("SELECT ativo FROM portal_config WHERE id=1").fetchone()["ativo"])
                        if desligado: ok = False
                        else: ok = bool(c.execute("SELECT 1 FROM membro_acesso a JOIN membros m ON m.id=a.membro_id WHERE a.usuario_id=%s AND a.situacao='ativo' AND m.excluido_em IS NULL", (u["id"],)).fetchone())
                    if ok:
                        c.execute("UPDATE usuarios SET falhas_login=0 WHERE id=%s", (u["id"],))
                        if membro: c.execute("UPDATE membro_acesso SET ultimo_acesso_em=now() WHERE usuario_id=%s", (u["id"],))
                        audit(c, "login_sucesso", "usuarios", u["login"], usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
                    else:
                        audit(c, "login_negado", "usuarios", u["login"], motivo="Portal desligado" if desligado else "Acesso do membro não está ativo", resultado="negado",
                              usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
                if not ok and not msg: msg = PORTAL_INDISPONIVEL if desligado else MSG_LOGIN
        if ok:
            pm = None if membro else session.get("pm")   # entrar como secretário não derruba o portal aberto em outra aba
            pmu = session.get("pm_ult")
            session.clear(); session.permanent = True; session.update(uid=u["id"], nome=u["nome"], sv=_sv(u["senha_hash"]), ult=time.time())
            if pm: session["pm"] = pm; session["pm_ult"] = pmu or time.time()
            return redirect("/portal" if membro else "/")
        if not msg: msg = MSG_LOGIN
    return render_template("login.html", csrf=csrf(), msg=msg), status

@app.route("/destravar", methods=["GET", "POST"])
def destravar():
    """Sessão bloqueada por inatividade: só a senha da própria conta reabre. Errar a senha aqui conta na regra das 5 tentativas."""
    msg = ""
    if request.method == "POST":
        senha = request.form.get("senha") or ""; ip = _ip()
        with conn() as c:
            u = c.execute("SELECT id,nome,login,perfil,senha_hash,ativo,bloqueado FROM usuarios WHERE id=%s", (session["uid"],)).fetchone()
            if not u or not u["ativo"] or u["bloqueado"]:
                session.clear(); return redirect("/login")
            if _ip_limitado(c, ip): msg = MSG_LOGIN
            elif check_password_hash(u["senha_hash"], senha):
                c.execute("UPDATE usuarios SET falhas_login=0 WHERE id=%s", (u["id"],))
                session["travada"] = False; session["ult"] = time.time()
                audit(c, "sessao_destravada", "usuarios", u["login"])
                return redirect("/portal" if u["perfil"] == "membro" else "/")
            else:
                _falha_ip(c, ip); n, bloq = _falha_senha_conta(c, u, "destravar")
                if bloq: session.clear(); return redirect("/login")
                msg = "Senha incorreta."
    elif not session.get("travada"):
        session["travada"] = True; audit(None, "sessao_travada", "usuarios", session["uid"], motivo="Bloqueio manual/inatividade no navegador")
    return render_template("login.html", csrf=csrf(), msg=msg, travada=True, nome=session.get("nome", ""), acao="/destravar"), (429 if msg == MSG_LOGIN else 200)

@app.route("/")
def index():
    return render_template("index.html", usuario=session["nome"], csrf=csrf(), admin=g.admin)

@app.get("/logout")
def logout():
    if session.get("uid"): audit(None, "logout", "usuarios", session["uid"])
    pm = session.get("pm"); pmu = session.get("pm_ult"); session.clear()   # sair do sistema não derruba o portal do membro aberto em outra aba
    if pm: session["pm"] = pm; session["pm_ult"] = pmu or time.time()
    return redirect("/login")

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
            audit(c, "congregacao_alterada" if id else "congregacao_criada", "congregacoes", id, depois=dict(nome=nome, codigo=d.get("codigo") or None, ativo=bool(d.get("ativo", True))))
    except psycopg.errors.UniqueViolation:
        return jsonify(erro="Já existe uma congregação com esse nome."), 400
    return jsonify(ok=True)

@app.delete("/api/congregacoes/<int:id>")
@so_admin
def excluir_congregacao(id):
    # Preservação documental: congregação não é apagada de forma definitiva (o banco também impede). Use Inativar.
    audit(None, "exclusao_congregacao_negada", "congregacoes", id, resultado="negado", motivo="Preservação documental: use Inativar")
    return jsonify(erro="Congregações não são apagadas definitivamente, para preservar os registros. Use Inativar."), 409

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
COLS_MEMBRO = ["nome", "data_nascimento", "sexo", "estado_civil", "cpf", "sem_cpf", "telefone", "email", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
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
    campos = v["_campos"] = {}   # mensagem -> campo do formulário (a página usa para destacar o campo certo)
    def s(k):
        x = d.get(k)
        return x.strip() if isinstance(x, str) else ""
    def erro(k, msg):
        erros.append(msg); campos[msg] = k
    def obr(k, rot): erro(k, f"{rot}: campo obrigatório.")
    v["nome"] = " ".join(s("nome").split())
    if not v["nome"]: obr("nome", "Nome completo")
    elif len(v["nome"]) > 200: erros.append("Nome muito longo (máximo de 200 caracteres).")
    for k, rot in ROT_DATAS.items():
        dt = parse_data(s(k))
        if dt is False or (dt and dt.year < 1900): erros.append(f"{rot} inválida."); dt = None
        elif dt and k in ("data_nascimento", "data_chegada", "data_saida") and dt > date.today(): erros.append(f"A {ROT_DATAS[k][0].lower() + ROT_DATAS[k][1:]} não pode ser futura."); dt = None
        v[k] = dt
    if not s("data_nascimento"): obr("data_nascimento", "Data de nascimento")
    for k, lista, rot in (("sexo", SEXOS, "Sexo"), ("estado_civil", ESTADOS_CIVIS, "Estado civil")):
        v[k] = s(k) or None
        if not v[k]: obr(k, rot)
        elif v[k] not in lista: erros.append(f"{rot} inválido."); v[k] = None
    cpf = re.sub(r"\D", "", s("cpf")); v["cpf"] = None
    v["sem_cpf"] = d.get("sem_cpf") is True   # "Não possui CPF": o CPF deixa de ser obrigatório (e não pode vir preenchido)
    if v["sem_cpf"]:
        if s("cpf"): erro("cpf", "Desmarque “Não possui CPF” ou apague o CPF informado.")
    elif not cpf: obr("cpf", "CPF")
    elif not cpf_valido(cpf): erros.append("CPF inválido.")
    else: v["cpf"] = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    v["telefone"] = s("telefone") or None
    if v["telefone"] and not 8 <= len(re.sub(r"\D", "", v["telefone"])) <= 13: erros.append("Telefone inválido."); v["telefone"] = None
    v["email"] = s("email").lower() or None
    if v["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v["email"]): erros.append("E-mail inválido."); v["email"] = None
    cep = re.sub(r"\D", "", s("cep")); v["cep"] = None  # mesma regra da aba Ocorrências
    if not s("cep"): obr("cep", "CEP")
    elif len(cep) != 8: erros.append("CEP inválido (use 8 dígitos).")
    else: v["cep"] = cep[:5] + "-" + cep[5:]
    v["uf"] = s("uf").upper() or None
    if not v["uf"]: obr("uf", "Estado (UF)")
    elif not re.fullmatch(r"[A-Z]{2}", v["uf"]): erros.append("UF inválida (2 letras)."); v["uf"] = None
    for k in ("logradouro", "numero", "complemento", "bairro", "cidade", "forma_recebimento", "forma_saida", "nome_mae", "nome_pai"):
        v[k] = " ".join(s(k).split()) or None
        if v[k] and len(v[k]) > 200: erros.append(f"Campo muito longo: {k.replace('_', ' ')}."); v[k] = None
    for k, rot in (("logradouro", "Rua / Logradouro"), ("complemento", "Complemento"), ("bairro", "Bairro"), ("cidade", "Cidade")):
        if not v[k] and not any(m.startswith(f"Campo muito longo: {k}") for m in erros): obr(k, rot)
    # Número: obrigatório, salvo "Sem número (S/N)" (gravado como 'S/N' no próprio campo, sem coluna nova)
    sn = d.get("sem_numero") is True or (v["numero"] or "").upper().replace(" ", "") in ("S/N", "SN", "S.N.")
    if sn:
        if v["numero"] and v["numero"].upper().replace(" ", "") not in ("S/N", "SN", "S.N."): erro("numero", "Desmarque “Sem número (S/N)” ou apague o número informado.")
        else: v["numero"] = "S/N"
    elif not v["numero"] and not any(m.startswith("Campo muito longo: numero") for m in erros): obr("numero", "Número")
    v["observacoes"] = s("observacoes") or None
    if v["observacoes"] and len(v["observacoes"]) > 2000: erros.append("Campo muito longo: observacoes."); v["observacoes"] = None
    cg = d.get("congregacao_id"); v["congregacao_id"] = None
    if cg in (None, ""): obr("congregacao_id", "Congregação")
    else:
        v["congregacao_id"] = _int(cg, None)
        if v["congregacao_id"] is None: erros.append("Congregação inválida.")
    v["status"] = s("status")
    if v["status"] not in STATUS_MEMBRO: erros.append("Selecione o Status."); v["status"] = None
    v["status_membresia"] = s("status_membresia")
    if v["status_membresia"] not in STATUS_MEMBRESIA: erro("status_membresia", "Selecione o Status da Membresia."); v["status_membresia"] = None
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
    if not g.admin:   # usuário comum: suas congregações + cadastros novos do portal ainda sem congregação/status
        w.append("(m.congregacao_id = ANY(%s::int[]) OR (m.congregacao_id IS NULL AND m.status_membresia IS NULL))"); p.append(list(g.congs))
    if a.get("status"): w.append("m.status=%s"); p.append(a["status"])
    if a.get("cong"): w.append("m.congregacao_id=%s"); p.append(_int(a["cong"]))
    if a.get("membresia") == "sem": w.append("m.status_membresia IS NULL")   # cadastros do portal aguardando a secretaria definir
    elif a.get("membresia"): w.append("m.status_membresia=%s"); p.append(a["membresia"])
    if a.get("cargo"): w.append("m.cargo=%s"); p.append(a["cargo"])
    if a.get("nome"):
        q = a["nome"].strip(); dig = re.sub(r"\D", "", q)
        if len(dig) >= 3 and re.fullmatch(r"[\d.\-\s]+", q):   # só números/pontuação: procura pelo CPF (com ou sem máscara)
            w.append("regexp_replace(coalesce(m.cpf,''), '[^0-9]', '', 'g') LIKE '%%'||%s||'%%'"); p.append(dig)
        else: w.append("m.nome_busca LIKE '%%'||f_unaccent(%s)||'%%'"); p.append(q)
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
        if not m or not membro_visivel(m["congregacao_id"], m["status_membresia"]): return jsonify(erro="Membro não encontrado."), 404
        m["historico"] = c.execute("SELECT id,data,secretario_nome secretario,descricao FROM historico_membro WHERE membro_id=%s ORDER BY data,id", (id,)).fetchall()
        audit(c, "membro_consultado", "membros", id)   # consulta a dados pessoais/religiosos (sensíveis)
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
    if erros: return jsonify(erros=[dict(msg=m, campo=v["_campos"].get(m)) for m in erros]), 400
    try:
        with conn() as c:  # uma transação: ficha + novos registros do histórico, ou nada
            atual = None
            if id:
                atual = c.execute("SELECT * FROM membros WHERE id=%s AND excluido_em IS NULL FOR UPDATE", (id,)).fetchone()
                if not atual or not membro_visivel(atual["congregacao_id"], atual["status_membresia"]): return jsonify(erro="Membro não encontrado."), 404
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
            ant = atual or {}   # valores anteriores x posteriores (CPF sempre mascarado) na auditoria
            dif = {k: dict(antes=_ver_valor(k, ant.get(k)), depois=_ver_valor(k, v[k])) for k in COLS_MEMBRO if _norm(ant.get(k)) != _norm(v[k])}
            if foto and not foto[2]: dif["foto"] = dict(antes=None, depois="Nova foto enviada")
            elif d.get("foto_remover") is True: dif["foto"] = dict(antes="Com foto", depois="Foto removida (versão anterior preservada)")
            if novos: dif["historico_novo"] = dict(antes=None, depois=f"{len(novos)} registro(s) acrescentado(s)")
            audit(c, "membro_alterado" if atual else "membro_criado", "membros", id, depois=dif)
    except psycopg.errors.UniqueViolation:
        return jsonify(erros=[dict(msg="Já existe um membro cadastrado com este CPF.")]), 400
    return jsonify(id=id), (201 if atual is False else 200)

@app.delete("/api/membros/<int:id>")
def excluir_membro(id):
    w, p = ["id=%s", "excluido_em IS NULL"], [id]; filtro_cong("congregacao_id", w, p)
    with conn() as c:  # exclusão lógica: o cadastro sai das telas, o histórico permanece no banco
        n = c.execute("UPDATE membros SET excluido_em=now(),updated_at=now() WHERE " + " AND ".join(w), p).rowcount
        if n: audit(c, "membro_excluido_logico", "membros", id, motivo="Exclusão lógica: cadastro, histórico e documentos permanecem preservados")
    return (jsonify(ok=True), 200) if n else (jsonify(erro="Membro não encontrado."), 404)

@app.get("/api/membros/<int:id>/foto/versoes")
@so_admin
def foto_versoes(id):
    with conn() as c:
        return jsonify(c.execute("SELECT id,tipo,tamanho,foto_atualizada_em,arquivada_em,operacao FROM membro_foto_versoes WHERE membro_id=%s ORDER BY id DESC", (id,)).fetchall())

@app.get("/api/membros/<int:id>/foto/versoes/<int:vid>")
@so_admin
def foto_versao(id, vid):
    with conn() as c:
        r = c.execute("SELECT imagem,tipo FROM membro_foto_versoes WHERE id=%s AND membro_id=%s", (vid, id)).fetchone()
        if not r: return jsonify(erro="Versão não encontrada."), 404
        audit(c, "foto_versao_consultada", "membros", id, depois=dict(versao=vid))
    resp = Response(bytes(r["imagem"]), mimetype=r["tipo"]); resp.headers["Content-Disposition"] = "inline"
    return resp

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
    audit(None, "relatorio_impresso", "aniversariantes", None, depois=a.to_dict())
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
    audit(None, "relatorio_impresso", "comparativo", None, depois=request.args.to_dict())
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
        er = erro_senha(senha, v.get("login"))
        if er: erros.append(er)
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
        return jsonify(c.execute("""SELECT u.id,u.nome,u.login,u.perfil,u.ativo,u.bloqueado,u.falhas_login,u.bloqueado_em,u.desbloqueado_em,u.desbloqueado_por_nome,u.total_bloqueios,
            coalesce(array_agg(uc.congregacao_id ORDER BY uc.congregacao_id) FILTER (WHERE uc.congregacao_id IS NOT NULL),'{}') congregacoes
            FROM usuarios u LEFT JOIN usuario_congregacoes uc ON uc.usuario_id=u.id WHERE u.perfil <> 'membro' GROUP BY u.id ORDER BY lower(u.nome),u.id""").fetchall())

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
            audit(c, "usuario_criado", "usuarios", uid, depois=dict(nome=v["nome"], login=v["login"], perfil=v["perfil"], ativo=v["ativo"], congregacoes=v["congregacoes"]))
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
            if not c.execute("SELECT 1 FROM usuarios WHERE id=%s AND perfil <> 'membro'", (id,)).fetchone(): return jsonify(erro="Usuário não encontrado."), 404
            v, erros = validar_usuario(d, c, False)
            if id == session["uid"] and (v.get("perfil") != "admin" or not v["ativo"]):
                erros.append("Você não pode alterar o seu próprio perfil nem desativar a sua própria conta.")
            if erros: return jsonify(erros=[dict(msg=m) for m in erros]), 400
            ant = c.execute("SELECT nome,perfil,ativo FROM usuarios WHERE id=%s", (id,)).fetchone()
            ant["congregacoes"] = [r["congregacao_id"] for r in c.execute("SELECT congregacao_id FROM usuario_congregacoes WHERE usuario_id=%s ORDER BY 1", (id,))]
            c.execute("UPDATE usuarios SET nome=%s,perfil=%s,ativo=%s,updated_at=now() WHERE id=%s", (v["nome"], v["perfil"], v["ativo"], id))
            if "senha" in v: c.execute("UPDATE usuarios SET senha_hash=%s WHERE id=%s", (generate_password_hash(v["senha"]), id))   # trocar a senha NÃO desbloqueia a conta
            gravar_congs(c, id, v["congregacoes"])
            audit(c, "usuario_alterado", "usuarios", id, antes=ant, depois=dict(nome=v["nome"], perfil=v["perfil"], ativo=v["ativo"], congregacoes=v["congregacoes"], senha_alterada="senha" in v))
            if not c.execute("SELECT 1 FROM usuarios WHERE perfil='admin' AND ativo LIMIT 1").fetchone(): raise SemAdmin()
    except SemAdmin:
        return jsonify(erros=[dict(msg="O sistema precisa ter ao menos um administrador ativo.")]), 400
    return jsonify(ok=True)

@app.get("/api/usuarios/bloqueados")
@so_admin
def contas_bloqueadas():
    """Todas as contas bloqueadas (inclusive de membros) para o administrador desbloquear. Não é publicada para ninguém mais."""
    with conn() as c:
        return jsonify(c.execute("""SELECT id,nome,login,perfil,falhas_login,bloqueado_em,bloqueio_motivo,total_bloqueios,desbloqueado_em,desbloqueado_por_nome
                                    FROM usuarios WHERE bloqueado ORDER BY bloqueado_em DESC,id""").fetchall())

@app.post("/api/usuarios/<int:id>/desbloquear")
@so_admin
def desbloquear_usuario(id):
    """DESBLOQUEAR ACESSO: somente administrador ativo (conferido no servidor). Zera as tentativas; o histórico fica na auditoria."""
    d = request.get_json(silent=True) or {}
    motivo = (d.get("motivo") or "").strip()[:300] if isinstance(d.get("motivo"), str) else ""
    if id == session["uid"]: return jsonify(erro="Você não pode desbloquear a própria conta."), 403
    with conn() as c:
        r = c.execute("""UPDATE usuarios SET bloqueado=false,falhas_login=0,bloqueado_ate=NULL,desbloqueado_em=now(),desbloqueado_por=%s,desbloqueado_por_nome=%s,updated_at=now()
                         WHERE id=%s AND bloqueado RETURNING login,nome,perfil,bloqueado_em,total_bloqueios""", (session["uid"], session["nome"], id)).fetchone()
        if not r: return jsonify(erro="Esta conta não está bloqueada."), 409
        audit(c, "desbloqueio_administrativo", "usuarios", id, antes=dict(bloqueado=True, bloqueado_em=r["bloqueado_em"]), depois=dict(bloqueado=False, login=r["login"], perfil=r["perfil"]), motivo=motivo or None)
    return jsonify(ok=True, login=r["login"])

@app.get("/api/auditoria")
@so_admin
def api_auditoria():
    a = request.args; w, p = [], []
    if a.get("operacao"): w.append("operacao ILIKE %s"); p.append("%" + a["operacao"].strip() + "%")
    if a.get("usuario"): w.append("(usuario_nome ILIKE %s OR registro_id ILIKE %s)"); p += ["%" + a["usuario"].strip() + "%"] * 2
    if a.get("resultado") in ("sucesso", "falha", "negado"): w.append("resultado=%s"); p.append(a["resultado"])
    de, ate = parse_data(a.get("de")), parse_data(a.get("ate"))
    if de: w.append("(created_at AT TIME ZONE 'America/Sao_Paulo')::date >= %s"); p.append(de)
    if ate: w.append("(created_at AT TIME ZONE 'America/Sao_Paulo')::date <= %s"); p.append(ate)
    por = min(max(_int(a.get("por"), 50), 1), 200); pag = max(_int(a.get("pagina"), 1), 1)
    cond = (" WHERE " + " AND ".join(w)) if w else ""
    with conn() as c:
        total = c.execute("SELECT count(*) n FROM auditoria" + cond, p).fetchone()["n"]
        rows = c.execute("SELECT id,created_at,usuario_nome,perfil,ip,operacao,entidade,registro_id,antes,depois,motivo,resultado FROM auditoria" + cond +
                         f" ORDER BY created_at DESC,id DESC LIMIT {por} OFFSET {(pag - 1) * por}", p).fetchall()
    resp = jsonify(rows); resp.headers["X-Total-Count"] = str(total)
    return resp

# ---------------------------------------------------------------- PORTAL DO MEMBRO
# O membro (conta de perfil "membro") só enxerga e altera o PRÓPRIO cadastro. O vínculo conta -> cadastro vem do banco
# (usuarios.membro_id, gravado só pelo servidor ao liberar o acesso); nenhum id de membro é aceito do navegador.
# A liberação, a revogação e a nova senha temporária são EXCLUSIVAS do administrador (@so_admin).
SENHA_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"   # sem 0/O/1/l/I (evita erro ao digitar)

def gerar_senha(n=12):
    """Senha inicial aleatória (secrets, 12 caracteres) com maiúscula, minúscula e número."""
    while True:
        s = "".join(secrets.choice(SENHA_ALFABETO) for _ in range(n))
        if any(x.islower() for x in s) and any(x.isupper() for x in s) and any(x.isdigit() for x in s): return s

def gerar_login(c, nome):
    """Login legível e único: primeiro.ultimo + 3 dígitos (ex.: maria.souza482)."""
    ascii_ = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    partes = re.sub(r"[^a-z0-9 ]", "", ascii_).split()
    base = (partes[0] + "." + partes[-1] if len(partes) > 1 else partes[0] if partes else "membro")[:30]
    for _ in range(30):
        cand = f"{base}{secrets.randbelow(900) + 100}"
        if not c.execute("SELECT 1 FROM usuarios WHERE login=%s", (cand,)).fetchone(): return cand
    return "membro" + secrets.token_hex(4)

def so_membro(f):
    @wraps(f)
    def w(*a, **k):
        if not g.membro_id and not getattr(g, "novo", False):
            return (jsonify(erro="Recurso exclusivo do portal do membro."), 403) if request.path.startswith("/api") else redirect("/")
        return f(*a, **k)
    return w

def _log_acesso(c, membro_id, acao):
    c.execute("INSERT INTO membro_acesso_log(membro_id,acao,por_id,por_nome) VALUES(%s,%s,%s,%s)", (membro_id, acao, session["uid"], session["nome"]))
    audit(c, "membro_acesso_" + acao, "membros", membro_id)

# ----- administrador: liberar / revogar / nova senha temporária
@app.get("/api/membros/<int:id>/acesso")
@so_admin
def ver_acesso(id):
    with conn() as c:
        if not c.execute("SELECT 1 FROM membros WHERE id=%s AND excluido_em IS NULL", (id,)).fetchone(): return jsonify(erro="Membro não encontrado."), 404
        a = c.execute("""SELECT a.situacao,a.liberado_em,a.liberado_por_nome,a.revogado_em,a.revogado_por_nome,a.ultimo_acesso_em,u.login,u.trocar_senha
                         FROM membro_acesso a JOIN usuarios u ON u.id=a.usuario_id WHERE a.membro_id=%s""", (id,)).fetchone()
        log = c.execute("SELECT acao,por_nome,created_at FROM membro_acesso_log WHERE membro_id=%s ORDER BY id DESC LIMIT 20", (id,)).fetchall()
        alt = c.execute("SELECT usuario_nome,campos,created_at FROM membro_alteracoes WHERE membro_id=%s ORDER BY id DESC LIMIT 20", (id,)).fetchall()
    return jsonify(situacao=a["situacao"] if a else "nao_liberado", acesso=a, log=log, alteracoes=alt)

def _emitir_credenciais(id, modo):
    """modo 'liberar': cria (ou reativa) a conta do membro · modo 'nova_senha': nova senha temporária de um acesso ativo.
    A senha só existe em memória: vai para o banco como HASH e para a resposta desta requisição (nunca para log)."""
    senha = gerar_senha(); h = generate_password_hash(senha)
    for _ in range(5):
        try:
            with conn() as c:
                m = c.execute("SELECT id,nome FROM membros WHERE id=%s AND excluido_em IS NULL FOR UPDATE", (id,)).fetchone()
                if not m: return jsonify(erro="Membro não encontrado."), 404
                a = c.execute("SELECT usuario_id,situacao FROM membro_acesso WHERE membro_id=%s FOR UPDATE", (id,)).fetchone()
                if modo == "nova_senha" and not (a and a["situacao"] == "ativo"):
                    return jsonify(erro="O acesso precisa estar ativo para gerar uma nova senha temporária."), 409
                if modo == "liberar" and a and a["situacao"] == "ativo":
                    return jsonify(erro="O acesso deste membro já está ativo. Para entregar outra senha, use “Gerar nova senha temporária”."), 409
                if a:   # conta já vinculada: mantém o mesmo login
                    uid = a["usuario_id"]
                    c.execute("UPDATE usuarios SET senha_hash=%s,ativo=true,trocar_senha=true,nome=%s,updated_at=now() WHERE id=%s", (h, m["nome"], uid))   # não mexe no bloqueio: só o botão Desbloquear acesso libera
                    login = c.execute("SELECT login FROM usuarios WHERE id=%s", (uid,)).fetchone()["login"]
                    if modo == "liberar":
                        c.execute("UPDATE membro_acesso SET situacao='ativo',liberado_em=now(),liberado_por=%s,liberado_por_nome=%s,senha_gerada_em=now(),"
                                  "revogado_em=NULL,revogado_por=NULL,revogado_por_nome=NULL WHERE membro_id=%s", (session["uid"], session["nome"], id))
                    else:
                        c.execute("UPDATE membro_acesso SET senha_gerada_em=now() WHERE membro_id=%s", (id,))
                else:   # primeira liberação: cria a conta vinculada ao cadastro
                    login = gerar_login(c, m["nome"])
                    uid = c.execute("INSERT INTO usuarios(nome,login,senha_hash,perfil,ativo,membro_id,trocar_senha) VALUES(%s,%s,%s,'membro',true,%s,true) RETURNING id",
                                    (m["nome"], login, h, id)).fetchone()["id"]
                    c.execute("INSERT INTO membro_acesso(membro_id,usuario_id,situacao,liberado_por,liberado_por_nome) VALUES(%s,%s,'ativo',%s,%s)", (id, uid, session["uid"], session["nome"]))
                _log_acesso(c, id, "liberou" if modo == "liberar" else "nova_senha")
            resp = jsonify(login=login, senha=senha, nome=m["nome"])
            resp.headers["Cache-Control"] = "no-store"
            return resp
        except psycopg.errors.UniqueViolation:
            continue   # login sorteado já existe (raríssimo): sorteia outro
    return jsonify(erro="Não foi possível gerar o login. Tente novamente."), 500

@app.post("/api/membros/<int:id>/acesso/liberar")
@so_admin
def liberar_acesso(id): return _emitir_credenciais(id, "liberar")

@app.post("/api/membros/<int:id>/acesso/nova-senha")
@so_admin
def nova_senha_acesso(id): return _emitir_credenciais(id, "nova_senha")

@app.post("/api/membros/<int:id>/acesso/revogar")
@so_admin
def revogar_acesso(id):
    with conn() as c:
        a = c.execute("SELECT usuario_id,situacao FROM membro_acesso WHERE membro_id=%s FOR UPDATE", (id,)).fetchone()
        if not a or a["situacao"] != "ativo": return jsonify(erro="Este membro não tem acesso ativo."), 409
        c.execute("UPDATE membro_acesso SET situacao='revogado',revogado_em=now(),revogado_por=%s,revogado_por_nome=%s WHERE membro_id=%s", (session["uid"], session["nome"], id))
        # conta desativada + senha trocada por uma aleatória que ninguém conhece: novos logins e sessões abertas caem na hora
        c.execute("UPDATE usuarios SET ativo=false,senha_hash=%s,updated_at=now() WHERE id=%s", (generate_password_hash(secrets.token_urlsafe(32)), a["usuario_id"]))
        _log_acesso(c, id, "revogou")   # o cadastro e todo o histórico do membro permanecem intactos
    return jsonify(ok=True)

# ----- membro: tela, leitura, atualização, foto e senha
# Campos que o MEMBRO pode alterar. Tudo que não está aqui (status da membresia, cargo, congregação, datas oficiais...) é ignorado.
PORTAL_EDITAVEIS = ["nome", "data_nascimento", "sexo", "estado_civil", "cpf", "sem_cpf", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf", "congregacao_id", "data_conversao", "data_batismo"]
PORTAL_ROTULOS = dict(nome="Nome completo", data_nascimento="Data de nascimento", sexo="Sexo", estado_civil="Estado civil", cpf="CPF", sem_cpf="Não possui CPF", cep="CEP", logradouro="Logradouro", congregacao_id="Congregação",
                      numero="Número", complemento="Complemento", bairro="Bairro", cidade="Cidade", uf="UF", data_conversao="Data de conversão", data_batismo="Data de batismo", foto="Foto")

def validar_portal(d):
    """Mesmas regras do cadastro feito pela secretaria, mas só para os campos que o membro pode alterar."""
    erros, v = [], {}
    campos = v["_campos"] = {}
    def s(k):
        x = d.get(k); return x.strip() if isinstance(x, str) else ""
    def erro(k, msg): erros.append(msg); campos[msg] = k
    def obr(k, rot): erro(k, f"{rot}: campo obrigatório.")
    v["nome"] = " ".join(s("nome").split())
    if not v["nome"]: obr("nome", "Nome completo")
    elif len(v["nome"]) > 200: erro("nome", "Nome muito longo (máximo de 200 caracteres).")
    for k in ("data_nascimento", "data_conversao", "data_batismo"):
        rot = ROT_DATAS[k]; dt = parse_data(s(k))
        if dt is False or (dt and dt.year < 1900): erro(k, f"{rot} inválida."); dt = None
        elif dt and k == "data_nascimento" and dt > date.today(): erro(k, "A data de nascimento não pode ser futura."); dt = None
        v[k] = dt
    if not s("data_nascimento"): obr("data_nascimento", "Data de nascimento")
    for k, lista, rot in (("sexo", SEXOS, "Sexo"), ("estado_civil", ESTADOS_CIVIS, "Estado civil")):
        v[k] = s(k) or None
        if not v[k]: obr(k, rot)
        elif v[k] not in lista: erro(k, f"{rot} inválido."); v[k] = None
    cg = d.get("congregacao_id"); cg = str(cg).strip() if isinstance(cg, (int, str)) and not isinstance(cg, bool) else ""
    v["congregacao_id"] = None
    if not cg: obr("congregacao_id", "Congregação")
    else:
        v["congregacao_id"] = _int(cg, None)
        if v["congregacao_id"] is None: erro("congregacao_id", "Congregação inválida.")
    cpf = re.sub(r"\D", "", s("cpf")); v["cpf"] = None
    v["sem_cpf"] = d.get("sem_cpf") is True
    if v["sem_cpf"]:
        if s("cpf"): erro("cpf", "Desmarque “Não possui CPF” ou apague o CPF informado.")
    elif not cpf: obr("cpf", "CPF")
    elif not cpf_valido(cpf): erro("cpf", "CPF inválido.")
    else: v["cpf"] = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    cep = re.sub(r"\D", "", s("cep")); v["cep"] = None
    if not s("cep"): obr("cep", "CEP")
    elif len(cep) != 8: erro("cep", "CEP inválido (use 8 dígitos).")
    else: v["cep"] = cep[:5] + "-" + cep[5:]
    v["uf"] = s("uf").upper() or None
    if not v["uf"]: obr("uf", "Estado (UF)")
    elif not re.fullmatch(r"[A-Z]{2}", v["uf"]): erro("uf", "UF inválida (2 letras)."); v["uf"] = None
    for k, rot in (("logradouro", "Rua / Logradouro"), ("complemento", "Complemento"), ("bairro", "Bairro"), ("cidade", "Cidade")):
        v[k] = " ".join(s(k).split()) or None
        if not v[k]: obr(k, rot)
        elif len(v[k]) > 200: erro(k, f"{rot}: máximo de 200 caracteres."); v[k] = None
    num = " ".join(s("numero").split()) or None
    sn = d.get("sem_numero") is True or (num or "").upper().replace(" ", "") in ("S/N", "SN", "S.N.")
    if sn:
        if num and num.upper().replace(" ", "") not in ("S/N", "SN", "S.N."): erro("numero", "Desmarque “Sem número (S/N)” ou apague o número informado.")
        v["numero"] = "S/N"
    elif not num: obr("numero", "Número"); v["numero"] = None
    elif len(num) > 200: erro("numero", "Número: máximo de 200 caracteres."); v["numero"] = None
    else: v["numero"] = num
    return v, erros

def _norm(x):
    if x is None or x == "": return None
    return x.isoformat() if hasattr(x, "isoformat") else x

def _ver_valor(k, x):
    """Valor como texto para o registro de alterações (CPF sempre mascarado: proteção de dados)."""
    if x is None or x == "": return None
    if k == "cpf": return "***." + str(x)[4:7] + ".***-**"
    if k == "sem_cpf": return "Sim" if x else "Não"
    return x.isoformat() if hasattr(x, "isoformat") else str(x)

def _erro_cong_portal(c, cid, atual_id=None):
    """A congregação escolhida no portal precisa existir e estar ativa (ou ser a que o membro já tem)."""
    if c.execute("SELECT 1 FROM congregacoes WHERE id=%s AND (ativo OR id=%s)", (cid, atual_id or 0)).fetchone(): return None
    return jsonify(erros=[dict(msg="Selecione uma congregação válida.", campo="congregacao_id")]), 400

def _dif_cong(c, dif):
    """No registro de alterações a congregação aparece pelo NOME (não pelo número interno)."""
    d = dif.get("congregacao_id")
    if not d: return
    ids = [int(x) for x in (d["antes"], d["depois"]) if x]
    nomes = {str(r["id"]): r["nome"] for r in c.execute("SELECT id,nome FROM congregacoes WHERE id = ANY(%s::int[])", (ids,)).fetchall()}
    for k in ("antes", "depois"):
        if d[k]: d[k] = nomes.get(str(d[k]), d[k])

@app.get("/portal")
@so_membro
def portal():
    novo = getattr(g, "novo", False)
    with conn() as c:
        m = {"nome": ""} if novo else c.execute("SELECT nome FROM membros WHERE id=%s", (g.membro_id,)).fetchone()
    cod = getattr(g, "codigo", False)
    return render_template("portal.html", csrf=csrf(), nome=m["nome"], primeiro_nome=(m["nome"].split() or [""])[0], novo=novo, trocar=g.trocar_senha, sexos=SEXOS, estados_civis=ESTADOS_CIVIS,
                           sair="/membro/sair" if cod else "/logout", entrada="/membro" if cod else "/login")

@app.get("/api/portal/meu-cadastro")
@so_membro
def portal_ver():
    if getattr(g, "novo", False):   # cadastro novo: formulário em branco
        out = {k: None for k in PORTAL_EDITAVEIS}; out["sem_cpf"] = False; out["leitura"] = {}; out["tem_foto"], out["foto_v"] = False, None
        with conn() as c: out["congregacoes"] = c.execute("SELECT id,nome FROM congregacoes WHERE ativo ORDER BY nome").fetchall()
        return jsonify(out)
    with conn() as c:
        m = c.execute("SELECT m.*,cg.nome congregacao FROM membros m LEFT JOIN congregacoes cg ON cg.id=m.congregacao_id WHERE m.id=%s AND m.excluido_em IS NULL", (g.membro_id,)).fetchone()
        if not m: return jsonify(erro="Cadastro não encontrado."), 404
        f = c.execute("SELECT EXTRACT(EPOCH FROM atualizado_em)::bigint v FROM membro_foto WHERE membro_id=%s", (g.membro_id,)).fetchone()
        congs = c.execute("SELECT id,nome FROM congregacoes WHERE ativo OR id=%s ORDER BY nome", (m["congregacao_id"] or 0,)).fetchall()
    out = {k: m[k] for k in PORTAL_EDITAVEIS}; out["congregacoes"] = congs
    # somente leitura: situação eclesiástica oficial é da secretaria
    out["leitura"] = {k: m[k] for k in ("status_membresia", "cargo", "data_chegada", "data_recebimento")}
    out["tem_foto"], out["foto_v"] = bool(f), (f["v"] if f else None)
    with conn() as c:   # acompanhamento: situação das últimas solicitações do próprio membro
        out["solicitacoes"] = c.execute("""SELECT situacao,enviada_em,decidida_em,CASE WHEN situacao='recusada' THEN justificativa END justificativa
                                            FROM portal_fichas WHERE membro_id=%s ORDER BY id DESC LIMIT 5""", (g.membro_id,)).fetchall()
    return jsonify(out)

@app.get("/api/portal/foto")
@so_membro
def portal_foto():
    if getattr(g, "novo", False): return jsonify(erro="Foto não encontrada."), 404
    with conn() as c:
        r = c.execute("SELECT imagem,tipo,EXTRACT(EPOCH FROM atualizado_em)::bigint v FROM membro_foto WHERE membro_id=%s", (g.membro_id,)).fetchone()
    if not r: return jsonify(erro="Foto não encontrada."), 404
    resp = Response(bytes(r["imagem"]), mimetype=r["tipo"])
    resp.headers["X-Content-Type-Options"] = "nosniff"; resp.headers["Content-Disposition"] = "inline"
    return resp

NOVOS_MAX = 30   # no máximo 30 cadastros novos por hora pelo portal (evita encher o sistema de cadastros falsos)

def _dados_ficha(v):
    """Valores enviados pelo membro, prontos para o JSON da ficha (datas em texto)."""
    return {k: (v[k].isoformat() if hasattr(v[k], "isoformat") else v[k]) for k in PORTAL_EDITAVEIS}

def _portal_criar(v, foto):
    """Membro entrou com a senha do portal e escolheu fazer o PRÓPRIO cadastro. Nada vai para o cadastro oficial: a ficha fica
    PENDENTE até a Secretaria conferir e aprovar (aí nasce 'Ativo', sem Status da Membresia, sem cargo) ou recusar com justificativa."""
    with conn() as c:
        if c.execute("SELECT count(*) n FROM portal_fichas WHERE tipo='novo_cadastro' AND enviada_em > now() - interval '1 hour'").fetchone()["n"] >= NOVOS_MAX:
            return jsonify(erros=[dict(msg="Muitos cadastros foram enviados agora. Tente novamente em alguns minutos ou fale com o secretário.")]), 429
        er = _erro_cong_portal(c, v["congregacao_id"])
        if er: return er
        if v["cpf"] and c.execute("SELECT 1 FROM membros WHERE cpf=%s AND excluido_em IS NULL", (v["cpf"],)).fetchone():
            return jsonify(erros=[dict(msg="Este CPF já está cadastrado. Volte ao início e use “Atualizar meu cadastro” com o seu CPF, ou fale com o secretário.", campo="cpf")]), 400
        dif = {k: dict(antes=None, depois=_ver_valor(k, v[k])) for k in PORTAL_EDITAVEIS if v[k] not in (None, "", False)}
        _dif_cong(c, dif)
        if v["cpf"]:   # novo envio do mesmo CPF substitui o pendente anterior (que continua guardado)
            c.execute("UPDATE portal_fichas SET situacao='substituida',decidida_em=now(),justificativa='Substituída por um novo envio do membro' WHERE tipo='novo_cadastro' AND situacao='pendente' AND dados->>'cpf'=%s", (v["cpf"],))
        fid = c.execute("INSERT INTO portal_fichas(tipo,dados,foto,foto_tipo,enviada_por_nome) VALUES('novo_cadastro',%s,%s,%s,%s) RETURNING id",
                        (Jsonb(_dados_ficha(v)), foto[0] if foto else None, foto[1] if foto else None, v["nome"])).fetchone()["id"]
        if foto: dif["foto"] = dict(antes=None, depois="Nova foto enviada")
        audit(c, "portal_ficha_enviada", "portal_fichas", fid, depois=dict(tipo="novo_cadastro", campos=list(dif)), usuario_id=None, usuario_nome=v["nome"])
    return jsonify(ok=True, alterados=len(dif), novo=True, pendente=True)

@app.put("/api/portal/meu-cadastro")
@so_membro
def portal_salvar():
    if (request.content_length or 0) > 4 * 1024 * 1024: return jsonify(erros=[dict(msg="Os dados enviados são grandes demais.")]), 413
    d = request.get_json(silent=True)
    if not isinstance(d, dict): d = {}
    v, erros = validar_portal(d)   # qualquer "id"/"membro_id" enviado é ignorado: só vale o vínculo da conta, vindo do banco
    foto = None
    if d.get("foto"):
        foto = validar_foto(d["foto"])
        if foto[2]: erros.append(foto[2])
    if erros: return jsonify(erros=[dict(msg=m, campo=v["_campos"].get(m)) for m in erros]), 400
    if getattr(g, "novo", False): return _portal_criar(v, foto)
    with conn() as c:   # uma transação: a ficha pendente (com a foto) ou nada. O cadastro oficial NÃO é tocado aqui.
        a = c.execute("SELECT * FROM membros WHERE id=%s AND excluido_em IS NULL FOR UPDATE", (g.membro_id,)).fetchone()
        if not a: return jsonify(erro="Cadastro não encontrado."), 404
        er = _erro_cong_portal(c, v["congregacao_id"], a["congregacao_id"])
        if er: return er
        if v["cpf"] and c.execute("SELECT 1 FROM membros WHERE cpf=%s AND id<>%s AND excluido_em IS NULL", (v["cpf"], g.membro_id)).fetchone():
            return jsonify(erros=[dict(msg="Este CPF já está cadastrado para outra pessoa. Procure a secretaria.", campo="cpf")]), 400
        # compara os valores REAIS; a máscara do CPF vale só para o que é gravado em registros de consulta
        dif = {k: dict(antes=_ver_valor(k, a[k]), depois=_ver_valor(k, v[k])) for k in PORTAL_EDITAVEIS if _norm(a[k]) != _norm(v[k])}
        _dif_cong(c, dif)
        tem_foto = bool(c.execute("SELECT 1 FROM membro_foto WHERE membro_id=%s", (g.membro_id,)).fetchone())
        remover = d.get("foto_remover") is True and tem_foto and not foto
        if foto: dif["foto"] = dict(antes=None, depois="Nova foto enviada")
        elif remover: dif["foto"] = dict(antes="Com foto", depois="Foto removida")
        if not dif: return jsonify(ok=True, alterados=0)
        c.execute("UPDATE portal_fichas SET situacao='substituida',decidida_em=now(),justificativa='Substituída por um novo envio do membro' WHERE membro_id=%s AND situacao='pendente'", (g.membro_id,))
        fid = c.execute("INSERT INTO portal_fichas(tipo,membro_id,dados,foto,foto_tipo,remover_foto,enviada_por_nome,enviada_por_usuario_id) VALUES('atualizacao',%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                        (g.membro_id, Jsonb(_dados_ficha(v)), foto[0] if foto else None, foto[1] if foto else None, remover, v["nome"], None if getattr(g, "codigo", False) else session.get("uid"))).fetchone()["id"]
        audit(c, "portal_ficha_enviada", "portal_fichas", fid, depois=dict(tipo="atualizacao", membro_id=g.membro_id, campos=list(dif)),
              usuario_id=None if getattr(g, "codigo", False) else session.get("uid"), usuario_nome=v["nome"])
    return jsonify(ok=True, alterados=len(dif), pendente=True)

def _campos_ficha(c, f):
    """Lista antes/depois de uma ficha, para a Secretaria conferir (CPF mascarado)."""
    dados = f["dados"]
    if f["tipo"] == "novo_cadastro": ant = {}
    elif f["situacao"] == "aprovada" and f["dados_anteriores"]: ant = f["dados_anteriores"]
    else: ant = c.execute("SELECT * FROM membros WHERE id=%s", (f["membro_id"],)).fetchone() or {}
    dif = {k: dict(antes=_ver_valor(k, ant.get(k)), depois=_ver_valor(k, dados.get(k))) for k in PORTAL_EDITAVEIS if _norm(ant.get(k)) != _norm(dados.get(k)) and (ant or dados.get(k) not in (None, "", False))}
    _dif_cong(c, dif)
    return [dict(campo=k, rotulo=PORTAL_ROTULOS[k], antes=x["antes"], depois=x["depois"]) for k, x in dif.items()]

@app.get("/api/portal/fichas")
@so_admin
def fichas_lista():
    sit = request.args.get("situacao", "pendente")
    w, p = ("", ()) if sit == "todas" else ("WHERE f.situacao=%s", (sit if sit in ("pendente", "aprovada", "recusada", "substituida") else "pendente",))
    with conn() as c:
        return jsonify(c.execute(f"""SELECT f.id,f.tipo,f.situacao,f.enviada_em,f.enviada_por_nome,f.membro_id,m.nome membro_nome,f.decidida_em,f.decidida_por_nome,f.justificativa,
                                     (f.foto IS NOT NULL OR f.remover_foto) tem_foto FROM portal_fichas f LEFT JOIN membros m ON m.id=f.membro_id {w} ORDER BY f.id DESC LIMIT 200""", p).fetchall())

@app.get("/api/portal/fichas/<int:id>")
@so_admin
def ficha_detalhe(id):
    with conn() as c:
        f = c.execute("SELECT * FROM portal_fichas WHERE id=%s", (id,)).fetchone()
        if not f: return jsonify(erro="Ficha não encontrada."), 404
        out = {k: f[k] for k in ("id", "tipo", "situacao", "enviada_em", "enviada_por_nome", "membro_id", "decidida_em", "decidida_por_nome", "justificativa", "membro_criado_id")}
        out["campos"] = _campos_ficha(c, f); out["foto_nova"] = f["foto"] is not None; out["remover_foto"] = f["remover_foto"]
        audit(c, "portal_ficha_consultada", "portal_fichas", id)   # a ficha traz dados pessoais completos
    return jsonify(out)

@app.get("/api/portal/fichas/<int:id>/foto")
@so_admin
def ficha_foto(id):
    with conn() as c:
        f = c.execute("SELECT foto,foto_tipo FROM portal_fichas WHERE id=%s", (id,)).fetchone()
        if not f or not f["foto"]: return jsonify(erro="Foto não encontrada."), 404
        audit(c, "portal_ficha_foto_consultada", "portal_fichas", id)
    resp = Response(bytes(f["foto"]), mimetype=f["foto_tipo"]); resp.headers["Content-Disposition"] = "inline"
    return resp

@app.post("/api/portal/fichas/<int:id>/aprovar")
@so_admin
def ficha_aprovar(id):
    """A Secretaria autoriza: só agora os dados aprovados são aplicados ao cadastro oficial (uma transação). A versão anterior fica guardada."""
    try:
        with conn() as c:
            f = c.execute("SELECT * FROM portal_fichas WHERE id=%s FOR UPDATE", (id,)).fetchone()
            if not f: return jsonify(erro="Ficha não encontrada."), 404
            if f["situacao"] != "pendente": return jsonify(erro="Esta ficha já foi decidida."), 409
            dados, quem = f["dados"], f["enviada_por_nome"]
            if f["tipo"] == "novo_cadastro":
                if dados.get("cpf") and c.execute("SELECT 1 FROM membros WHERE cpf=%s AND excluido_em IS NULL", (dados["cpf"],)).fetchone():
                    return jsonify(erro="Já existe um cadastro com este CPF. Recuse a ficha ou ajuste o cadastro existente."), 409
                mid = c.execute("INSERT INTO membros(" + ",".join(PORTAL_EDITAVEIS) + ",nome_busca,status,status_membresia) VALUES(" + ",".join(["%s"] * len(PORTAL_EDITAVEIS)) + ",f_unaccent(%s),'Ativo',NULL) RETURNING id",
                                [dados[k] for k in PORTAL_EDITAVEIS] + [dados["nome"]]).fetchone()["id"]
                dif = {k: dict(antes=None, depois=_ver_valor(k, dados[k])) for k in PORTAL_EDITAVEIS if dados[k] not in (None, "", False)}
                _dif_cong(c, dif)
                if f["foto"]:
                    c.execute("INSERT INTO membro_foto(membro_id,imagem,tipo,tamanho) VALUES(%s,%s,%s,%s)", (mid, f["foto"], f["foto_tipo"], len(f["foto"]))); dif["foto"] = dict(antes=None, depois="Nova foto enviada")
                desc = f"Cadastro criado pelo próprio membro no Portal do Membro e aprovado por {session['nome']}. Confira situação, congregação e cargo."
                ant = None
            else:
                mid = f["membro_id"]
                a = c.execute("SELECT * FROM membros WHERE id=%s AND excluido_em IS NULL FOR UPDATE", (mid,)).fetchone()
                if not a: return jsonify(erro="O cadastro oficial deste membro não está mais ativo."), 409
                dif = {k: dict(antes=_ver_valor(k, a[k]), depois=_ver_valor(k, dados[k])) for k in PORTAL_EDITAVEIS if _norm(a[k]) != _norm(dados[k])}
                _dif_cong(c, dif)
                ant = {k: (a[k].isoformat() if hasattr(a[k], "isoformat") else a[k]) for k in PORTAL_EDITAVEIS}   # versão anterior preservada na ficha
                if dif:
                    c.execute("UPDATE membros SET " + ",".join(k + "=%s" for k in PORTAL_EDITAVEIS) + ",nome_busca=f_unaccent(%s),updated_at=now() WHERE id=%s", [dados[k] for k in PORTAL_EDITAVEIS] + [dados["nome"], mid])
                    if "nome" in dif: c.execute("UPDATE usuarios SET nome=%s,updated_at=now() WHERE membro_id=%s", (dados["nome"], mid))
                if f["foto"]:   # a foto anterior é arquivada pelo banco (membro_foto_versoes)
                    c.execute("INSERT INTO membro_foto(membro_id,imagem,tipo,tamanho) VALUES(%s,%s,%s,%s) ON CONFLICT(membro_id) DO UPDATE SET imagem=EXCLUDED.imagem,tipo=EXCLUDED.tipo,tamanho=EXCLUDED.tamanho,atualizado_em=now()", (mid, f["foto"], f["foto_tipo"], len(f["foto"])))
                    dif["foto"] = dict(antes=None, depois="Nova foto enviada")
                elif f["remover_foto"] and c.execute("DELETE FROM membro_foto WHERE membro_id=%s", (mid,)).rowcount: dif["foto"] = dict(antes="Com foto", depois="Foto removida")
                desc = None
                if dif: desc = f"Atualização cadastral feita pelo próprio membro no Portal do Membro e aprovada por {session['nome']}. Campos alterados: " + ", ".join(PORTAL_ROTULOS.get(k, "Foto") for k in dif) + "."
            if dif:
                c.execute("INSERT INTO membro_alteracoes(membro_id,usuario_id,usuario_nome,campos) VALUES(%s,%s,%s,%s)", (mid, f["enviada_por_usuario_id"], quem, Jsonb(dif)))
            if desc:
                c.execute("INSERT INTO historico_membro(membro_id,data,secretario_id,secretario_nome,descricao) VALUES(%s,%s,NULL,%s,%s)", (mid, date.today(), quem + " (membro, via portal)", desc))
            c.execute("UPDATE portal_fichas SET situacao='aprovada',decidida_em=now(),decidida_por=%s,decidida_por_nome=%s,dados_anteriores=%s,membro_criado_id=%s WHERE id=%s",
                      (session["uid"], session["nome"], Jsonb(ant) if ant is not None else None, mid if f["tipo"] == "novo_cadastro" else None, id))
            audit(c, "portal_ficha_aprovada", "portal_fichas", id, antes=({k: _ver_valor(k, x) for k, x in ant.items()} if ant else None), depois=dif)   # CPF mascarado
    except psycopg.errors.UniqueViolation:
        return jsonify(erro="O CPF desta ficha já está em outro cadastro. Recuse a ficha ou corrija o cadastro existente."), 409
    return jsonify(ok=True, membro_id=mid)

@app.post("/api/portal/fichas/<int:id>/recusar")
@so_admin
def ficha_recusar(id):
    d = request.get_json(silent=True) or {}
    just = (d.get("justificativa") or "").strip()[:500] if isinstance(d.get("justificativa"), str) else ""
    if len(just) < 5: return jsonify(erro="Informe a justificativa da recusa (mínimo de 5 caracteres)."), 400
    with conn() as c:
        f = c.execute("UPDATE portal_fichas SET situacao='recusada',decidida_em=now(),decidida_por=%s,decidida_por_nome=%s,justificativa=%s WHERE id=%s AND situacao='pendente' RETURNING membro_id,enviada_por_nome",
                      (session["uid"], session["nome"], just, id)).fetchone()
        if not f: return jsonify(erro="Ficha não encontrada ou já decidida."), 409
        if f["membro_id"]:
            c.execute("INSERT INTO historico_membro(membro_id,data,secretario_id,secretario_nome,descricao) VALUES(%s,%s,%s,%s,%s)",
                      (f["membro_id"], date.today(), session["uid"], session["nome"], "Atualização cadastral enviada pelo membro no Portal foi recusada. Justificativa: " + just))
        audit(c, "portal_ficha_recusada", "portal_fichas", id, motivo=just)
    return jsonify(ok=True)

@app.post("/api/portal/senha")
@so_membro
def portal_senha():
    d = request.get_json(silent=True)
    if not isinstance(d, dict): d = {}
    atual, nova, conf = d.get("atual"), d.get("nova"), d.get("confirmacao")
    with conn() as c:
        u = c.execute("SELECT login,senha_hash FROM usuarios WHERE id=%s", (session["uid"],)).fetchone()
        if not isinstance(atual, str) or not check_password_hash(u["senha_hash"], atual): return jsonify(erros=[dict(msg="A senha atual não confere.", campo="atual")]), 400
        er = []
        if not isinstance(nova, str) or not 8 <= len(nova) <= 128: er.append(dict(msg="A nova senha deve ter entre 8 e 128 caracteres.", campo="nova"))
        elif not (re.search(r"[A-Za-z]", nova) and re.search(r"\d", nova)): er.append(dict(msg="A nova senha deve ter letras e números.", campo="nova"))
        elif check_password_hash(u["senha_hash"], nova) or nova.lower() == u["login"].lower(): er.append(dict(msg="Escolha uma senha diferente da atual e do seu login.", campo="nova"))
        elif nova != conf: er.append(dict(msg="A confirmação não é igual à nova senha.", campo="confirmacao"))
        if er: return jsonify(erros=er), 400
        novo = generate_password_hash(nova)
        c.execute("UPDATE usuarios SET senha_hash=%s,trocar_senha=false,updated_at=now() WHERE id=%s", (novo, session["uid"]))
        _log_acesso(c, g.membro_id, "troca_senha_inicial" if g.trocar_senha else "troca_senha")
    session["sv"] = _sv(novo)   # a sessão atual continua valida; qualquer outra sessão aberta com a senha antiga cai
    return jsonify(ok=True)

# ----- CHAVE GERAL + SENHA ÚNICA (Configuração > Portal do Membro)
# Administrador: liga/desliga o portal. Ligar gera uma senha aleatória de 8 dígitos, única para todos os membros enquanto
# estiver ativa. Desligar apaga a senha e derruba as sessões abertas. O membro informa a senha e (a) o CPF, para atualizar o
# cadastro que já existe, ou (b) escolhe fazer o PRÓPRIO cadastro novo. Só esse cadastro fica acessível na sessão (as 3 primeiras seções, nada mais).
def gerar_codigo_portal(anterior=None):
    while True:
        cod = "".join(secrets.choice("0123456789") for _ in range(8))
        if len(set(cod)) > 1 and cod != anterior: return cod   # evita 00000000, 11111111...

def _estado_portal(c):
    cf = c.execute("SELECT ativo,codigo,alterado_em,alterado_por_nome FROM portal_config WHERE id=1").fetchone()
    log = c.execute("SELECT acao,por_nome,created_at FROM portal_config_log ORDER BY id DESC LIMIT 10").fetchall()
    return dict(ativo=cf["ativo"], codigo=cf["codigo"] if cf["ativo"] else None, alterado_em=cf["alterado_em"], alterado_por=cf["alterado_por_nome"], log=log)

@app.get("/api/config/portal-membro")
@so_admin
def ver_portal_config():
    with conn() as c: return jsonify(_estado_portal(c))

@app.put("/api/config/portal-membro")
@so_admin
def salvar_portal_config():
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not isinstance(d.get("ativo"), bool): return jsonify(erro="Informe se o portal deve ficar ativado ou desativado."), 400
    with conn() as c:
        atual = c.execute("SELECT ativo FROM portal_config WHERE id=1 FOR UPDATE").fetchone()["ativo"]
        if d["ativo"] != atual:   # repetir o mesmo estado não troca a senha nem registra nada
            if d["ativo"]:
                c.execute("UPDATE portal_config SET ativo=true,codigo=%s,versao=versao+1,alterado_em=now(),alterado_por_nome=%s WHERE id=1", (gerar_codigo_portal(), session["nome"]))
            else:   # apaga a senha e sobe a versão: as sessões de membros abertas caem na próxima requisição
                c.execute("UPDATE portal_config SET ativo=false,codigo=NULL,versao=versao+1,alterado_em=now(),alterado_por_nome=%s WHERE id=1", (session["nome"],))
            c.execute("INSERT INTO portal_config_log(acao,por_id,por_nome) VALUES(%s,%s,%s)", ("ativou" if d["ativo"] else "desativou", session["uid"], session["nome"]))
            audit(c, "portal_ativado" if d["ativo"] else "portal_desativado", "portal_config", 1)
        return jsonify(_estado_portal(c))

@app.post("/api/config/portal-membro/nova-senha")
@so_admin
def nova_senha_portal():
    with conn() as c:
        cf = c.execute("SELECT ativo,codigo FROM portal_config WHERE id=1 FOR UPDATE").fetchone()
        if not cf["ativo"]: return jsonify(erro="Ative o portal para gerar uma senha."), 409
        c.execute("UPDATE portal_config SET codigo=%s,versao=versao+1,alterado_em=now(),alterado_por_nome=%s WHERE id=1", (gerar_codigo_portal(cf["codigo"]), session["nome"]))
        c.execute("INSERT INTO portal_config_log(acao,por_id,por_nome) VALUES('nova_senha',%s,%s)", (session["uid"], session["nome"]))
        audit(c, "portal_nova_senha_geral", "portal_config", 1)   # a senha em si nunca é gravada
        return jsonify(_estado_portal(c))

# ----- entrada do membro (pública: só mostra o formulário ou o aviso; nada do cadastro antes de conferir tudo)
FALHAS_MAX, FALHAS_JANELA = 20, "15 minutes"   # tentativa e erro: 20 falhas em 15 min travam a entrada de todos por alguns minutos

@app.route("/membro", methods=["GET", "POST"])
def membro_entrada():
    ctx = dict(csrf=csrf(), msg="", indisponivel=False, campo=None, f={})
    status = 200
    with conn() as c:
        cf = c.execute("SELECT ativo,codigo,versao FROM portal_config WHERE id=1").fetchone()
        if not cf["ativo"]:
            ctx["indisponivel"] = True; ctx["msg"] = PORTAL_INDISPONIVEL
            status = 503 if request.method == "POST" else 200
        elif request.method == "POST":
            f = request.form; cod = re.sub(r"\s", "", f.get("codigo", "")); cpfd = re.sub(r"\D", "", f.get("cpf", "")); novo = f.get("acao") == "novo"
            ctx["f"] = dict(cpf=f.get("cpf", "")[:20])
            if c.execute("SELECT count(*) n FROM portal_falhas WHERE created_at > now() - %s::interval", (FALHAS_JANELA,)).fetchone()["n"] >= FALHAS_MAX:
                ctx["msg"] = "Muitas tentativas erradas. Aguarde alguns minutos e tente novamente, ou fale com o secretário."; status = 429
            elif not re.fullmatch(r"\d{8}", cod) or not secrets.compare_digest(cod, cf["codigo"]):
                c.execute("INSERT INTO portal_falhas DEFAULT VALUES"); audit(c, "portal_entrada_falha", "portal_config", 1, resultado="falha", motivo="Senha do portal incorreta", usuario_id=None, usuario_nome=None, perfil="membro (senha única)"); ctx["msg"] = "Senha do portal incorreta. Confira os 8 dígitos."; ctx["campo"] = "codigo"; status = 400
            elif novo:   # senha certa + "quero me cadastrar": abre o formulário em branco (nada é gravado até ele enviar)
                _abrir_sessao_portal(dict(m=None, v=cf["versao"]))
                audit(c, "portal_entrada_cadastro_novo", "portal_config", 1, usuario_id=None, usuario_nome=None, perfil="membro (senha única)")
                return redirect("/portal")
            elif not cpf_valido(cpfd):
                ctx["msg"] = "Informe um CPF válido (11 dígitos)."; ctx["campo"] = "cpf"; status = 400
            else:
                m = c.execute("SELECT id FROM membros WHERE excluido_em IS NULL AND cpf=%s", (f"{cpfd[:3]}.{cpfd[3:6]}.{cpfd[6:9]}-{cpfd[9:]}",)).fetchone()
                if m:
                    _abrir_sessao_portal(dict(m=m["id"], v=cf["versao"]))
                    audit(c, "portal_entrada", "membros", m["id"], usuario_id=None, usuario_nome=None, perfil="membro (senha única)")
                    return redirect("/portal")
                c.execute("INSERT INTO portal_falhas DEFAULT VALUES"); audit(c, "portal_entrada_falha", "portal_config", 1, resultado="falha", motivo="CPF não encontrado", usuario_id=None, usuario_nome=None, perfil="membro (senha única)")
                ctx["msg"] = "Não encontramos esse CPF no cadastro. Confira o número. Se você ainda não tem cadastro, use “Quero fazer o meu cadastro”; se não tem CPF, fale com o secretário."; ctx["campo"] = "cpf"; status = 400
    return render_template("membro.html", **ctx), status

@app.get("/membro/sair")
def membro_sair():
    session.pop("pm", None); return redirect("/membro")   # sai só do portal; a sessão do secretário (se houver) continua

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
    pw = getpass("Nova senha do admin (mín. 8 caracteres, com letras e números): ")
    if erro_senha(pw, LOGIN) or pw != getpass("Repita a senha: "): sys.exit((erro_senha(pw, LOGIN) or "As senhas não conferem.") + " Nada foi alterado.")
    with conn() as c:   # trocar a senha NÃO desbloqueia a conta (para isso: python app.py desbloquear <login>)
        n = c.execute("UPDATE usuarios SET senha_hash=%s,updated_at=now() WHERE login=%s", (generate_password_hash(pw), LOGIN)).rowcount
        if n: audit(c, "senha_alterada_servidor", "usuarios", LOGIN, motivo="Comando técnico no servidor", usuario_id=None, usuario_nome="Procedimento técnico (servidor)", perfil="admin")
    print("Senha do admin alterada." if n else "Usuário admin não encontrado. Rode: python app.py migrate")

def desbloquear_cli(login):
    """Recuperação excepcional (a única conta administradora bloqueada). Exige acesso ao servidor e à DATABASE_URL; fica na auditoria."""
    if not login: sys.exit("Uso: python app.py desbloquear <login>")
    if input(f"Digite o login '{login}' novamente para confirmar: ").strip() != login: sys.exit("Confirmação diferente. Nada foi alterado.")
    motivo = input("Motivo da recuperação (obrigatório): ").strip()
    if len(motivo) < 5: sys.exit("Informe o motivo. Nada foi alterado.")
    with conn() as c:
        r = c.execute("UPDATE usuarios SET bloqueado=false,falhas_login=0,bloqueado_ate=NULL,desbloqueado_em=now(),desbloqueado_por=NULL,desbloqueado_por_nome=%s,updated_at=now() "
                      "WHERE login=%s AND bloqueado RETURNING id,nome,perfil", ("Recuperação técnica (servidor)", login)).fetchone()
        if not r: sys.exit("Conta não encontrada ou não está bloqueada. Nada foi alterado.")
        audit(c, "desbloqueio_tecnico", "usuarios", r["id"], motivo=motivo, usuario_id=None, usuario_nome="Recuperação técnica (servidor)", perfil="servidor",
              depois=dict(login=login, perfil=r["perfil"]))
    print(f"Conta '{login}' desbloqueada e registrada na auditoria. Se foi um ataque, troque a senha: python app.py senha")

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
        elif cmd == "desbloquear":
            desbloquear_cli(sys.argv[2] if len(sys.argv) > 2 else "")
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
