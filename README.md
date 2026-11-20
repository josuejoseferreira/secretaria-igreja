# Secretaria Geral — módulos Ocorrências, Membros e Aniversariantes (Flask + PostgreSQL 16)

## 1. Pré-requisitos (Windows)
- **Python 3.11+** (python.org, marcar "Add python.exe to PATH").
- **PostgreSQL 16** (postgresql.org/download/windows). Guarde a senha do usuário `postgres`. O instalador já inclui as extensões `unaccent` e `pg_trgm`.

## 2. Criar os bancos (uma vez)
PowerShell ou Git Bash (se `psql` não for reconhecido, use o "SQL Shell (psql)" do menu Iniciar):

    psql -U postgres -c "CREATE DATABASE secretaria"
    psql -U postgres -c "CREATE DATABASE secretaria_test"

As extensões são criadas pela própria migration (usuário `postgres` tem permissão). Para criar manualmente:
`psql -U postgres -d secretaria -c "CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS pg_trgm;"`

## 3. Instalar
Dê dois cliques em **instalar.bat** (cria `.venv`, instala dependências e cria o `.env` a partir do `.env.example`, sem sobrescrever um `.env` existente).

## 4. Configurar o .env
Edite `DATABASE_URL` (com a senha do postgres), `SECRET_KEY` (texto longo e aleatório), `ADMIN_PASSWORD` (mín. 8 caracteres) e `TEST_DATABASE_URL`.
Se a senha tiver `@ # / :` troque por `%40 %23 %2F %3A` na URL.

## 5. Migrations e administrador
    # PowerShell                          # Git Bash
    .\.venv\Scripts\Activate.ps1          source .venv/Scripts/activate
    python app.py migrate

Cria as tabelas pendentes e o usuário `admin` (senha = `ADMIN_PASSWORD`) **somente se ele não existir**; a senha nunca é redefinida depois. As migrations **não** rodam ao iniciar o app. Para conferir: `python app.py check`.

### Trocar a senha do admin depois
    python app.py senha
(pede a nova senha duas vezes). Mudar `ADMIN_PASSWORD` no `.env` depois do primeiro `migrate` **não** altera a senha já criada.

## 6. Iniciar
Dê dois cliques em **iniciar.bat** (ou `python app.py`) e abra http://localhost:5000. Login: `admin` + a senha do `ADMIN_PASSWORD`. O iniciar.bat reinicia normalmente após religar o computador (o serviço do PostgreSQL sobe sozinho).

## 7. Testes
    python test_ocorrencias.py
Usa **somente** `TEST_DATABASE_URL` (o nome do banco precisa terminar em `_test`; ele é apagado e recriado a cada execução). Recusa rodar se não for assim.

## 8. Erros comuns
- `Configuração ausente: ...` → falta variável no `.env`.
- `connection refused` → serviço PostgreSQL parado (`services.msc` → postgresql-x64-16 → Iniciar).
- `password authentication failed` → senha/usuário errados na `DATABASE_URL`.
- `database "secretaria" does not exist` → passo 2.
- `Migrations pendentes` → `python app.py migrate`.
- `Extensões ausentes` → reinstale o PostgreSQL com o pacote padrão ou instale o contrib.

## 9. Preparado para publicação futura (nada foi publicado)
- **GitHub:** `.gitignore` já exclui `.env`, `.venv`, dumps e logs.
- **Render:** `gunicorn app:app`, lê `PORT`, `HOST=0.0.0.0`, `COOKIE_SECURE=1`, debug desligado, rota `/health`. Migrations por comando controlado (`python app.py migrate`), nunca no start.
- **Neon:** `DATABASE_URL` pooled (com `sslmode=require`) para o app; `DATABASE_URL_DIRECT` (conexão direta) usada só pelo `migrate`.

## 10. Backup
    pg_dump -U postgres -Fc secretaria > backup.dump
    pg_restore -U postgres -d secretaria --clean backup.dump

## 11. Membros, Histórico e Aniversariantes
**Atualizando uma instalação existente:** copie os arquivos novos e rode `python app.py migrate` (aplica só a `002_membros.sql`, que apenas **cria** as tabelas `membros` e `historico_membro`; nada existente é alterado). Depois reinicie o app.

- **Membros** (menu): filtros por Status, Congregação, Nome, Status da Membresia e Cargo; ações Visualizar, Editar e Excluir (com confirmação). A ficha é um formulário em seções; o CEP usa o mesmo preenchimento automático da aba Ocorrências.
- **Excluir** é lógico: o cadastro some das telas, mas o registro e o histórico continuam no banco. O CPF de um membro excluído pode ser reutilizado.
- **Histórico do Membro** é permanente e só cresce: o secretário é sempre o usuário logado (gravado no servidor) e o banco bloqueia UPDATE/DELETE nessa tabela. Mudar status, congregação ou cargo nunca mexe no histórico.
- **Cargo Eclesiástico** só vale para Status da Membresia = Membro (para Congregado o sistema descarta o cargo).
- **Aniversariantes** (menu): filtros por período, mês, nome e congregação (considera dia/mês, ignora o ano; períodos que viram o ano, como 20/12 a 10/01, funcionam). **Imprimir** abre uma página limpa com os mesmos filtros. Membros com Status *Faleceu* ou *Desligado* não aparecem na lista (regra ajustável em `SEM_ANIVERSARIO`, no `app.py`).
- Testes: `python test_membros.py` (mesmo banco `*_test` dos outros testes).
- Dados de exemplo: `python gerar_membros_exemplo.py` cria ~100 membros fictícios (com histórico e aniversários espalhados pelo ano; aceita um número, ex.: `... 60`). `python gerar_membros_exemplo.py --limpar` remove só eles, identificados pelo e-mail `@exemplo.teste`.

## 12. Interface (reformulação visual)
- **CSS:** `static/css/app.css` (tokens em variáveis `:root`, componentes e regras responsivas). Serve o app e a tela de login; a impressão de aniversariantes tem CSS próprio dentro do template.
- **Telas:** `templates/index.html` (menu, telas e JavaScript), `templates/login.html` e `templates/aniversariantes_imprimir.html`. Os ícones são SVG embutidos (sem biblioteca externa).
- **Responsivo:** menu lateral fixo no desktop e recolhível (botão ☰) até 900 px; tabelas viram cartões até 1100 px; modais ocupam a tela toda no celular; filtros recolhíveis no celular.
- **Fonte:** Inter é carregada do Google Fonts; sem internet o sistema usa Segoe UI/fonte do aparelho automaticamente. O preenchimento de CEP continua usando o ViaCEP.
- Nenhuma rota, API, regra de negócio ou tabela foi alterada nesta etapa; só a marcação HTML da tela de login (agora em `login.html`).

## 13. Configuração > Faixa etária e resumo dos membros
**Ao atualizar:** copie os arquivos e rode `python app.py migrate` (aplica `003_faixas_etarias.sql`, que só cria a tabela `faixas_etarias` com os padrões). Depois reinicie o app.
- Menu **Configuração > Faixa etária**: você define até que idade vai cada faixa (padrão: Crianças até 11, Adolescentes até 17, Jovens até 29, Adultos até 59; **Idosos** começa onde Adultos termina). Os limites precisam ser crescentes. A idade é calculada em anos completos pela data de nascimento.
- A tela **Início** mostra o **Resumo dos membros**: total de ativos, membros e congregados (pela *Status da Membresia*), mulheres, homens e as 5 faixas etárias. **Só entram cadastros com Status Ativo** e não excluídos; cadastros sem data de nascimento ou sem sexo são avisados na nota do resumo (não entram nessas contagens).
- API: `GET/PUT /api/config/faixas-etarias` e `GET /api/painel/membros` (o contrato de `/api/painel` não mudou).

## 14. Etapa de "conferência" removida
Toda ocorrência registrada já vale; não existe mais "aguardando conferência". Foram removidos: o cartão do Início, o endpoint `POST /api/lotes/<id>/conferir`, o contador `conf` de `/api/painel` e a regra que só permitia editar lotes "Lançada" (agora só lotes **Cancelados** não são editáveis).
**Ao atualizar:** rode `python app.py migrate` (aplica `004_remove_conferencia.sql`): lotes que estavam "Conferida" passam a "Lançada" e o banco deixa de aceitar o valor *Conferida*. Nenhuma ocorrência é apagada; o histórico antigo ("lote conferida") é mantido.

## 15. Relatórios > Comparativo (período atual x anterior) e datas de chegada/saída
**Ao atualizar:** rode `python app.py migrate` (aplica `005_membros_chegada_saida.sql`: só **acrescenta** as colunas `data_chegada`, `data_saida` e `forma_saida` em `membros`) e reinicie.
- **Tela Relatórios:** filtros *Descrição* (Todas as ocorrências, cada tipo de ocorrência, Membros ativos, Congregados ativos e cada cargo eclesiástico), *Data inicial* e *Data final*. Mostra cartões (anterior, atual e variação), gráfico de barras por congregação e o grid **Congregação | Período anterior | Período atual** (com ▲ cresceu, ▼ diminuiu ou = manteve), linha de total e botão Imprimir.
- **Período anterior** é calculado para trás: meses fechados (um mês, trimestre, ano...) voltam o mesmo número de meses; outros períodos voltam o mesmo número de dias, terminando na véspera da data inicial.
- **Ocorrências:** contam os registros (pessoas) pela data da ocorrência dentro de cada período; canceladas não entram.
- **Membros, congregados e cargos:** é a **posição no fim de cada período**, calculada pelas datas da ficha: *Data de chegada à igreja* (veio para a igreja), *Data de recebimento* (registrado como membro) e *Data de saída* (carta de mudança, falecimento ou desligamento; só aparece com Status Desligado ou Faleceu). Quem chegou como congregado e depois foi registrado como membro conta como congregado até o recebimento e como membro depois. Na data de saída a pessoa deixa de contar.
- **Limites conhecidos** (avisados na própria tela): sem data = considerado presente em todos os períodos; Desligado/Faleceu sem data de saída não entram; congregação e cargo são os **atuais** do cadastro (não há histórico de mudanças).
- Cadastros antigos continuam válidos: as novas datas são opcionais. API: `GET /api/relatorios/comparativo` e página de impressão `/relatorios/comparativo/imprimir`.

## 16. Usuários e permissões

Dois perfis de acesso:

- **Administrador:** acesso total a todas as funções e congregações. É o único que cadastra usuários (comuns ou administradores), define o perfil, escolhe as congregações de cada um, ativa/desativa contas, redefine senhas e gerencia congregações e a configuração de faixa etária. Menu **Usuários**.
- **Usuário comum:** só enxerga e lança dados nas congregações liberadas pelo administrador. Ocorrências, membros, aniversariantes, resumo do Início, relatórios e impressões mostram somente essas congregações. Não vê (e o servidor bloqueia) Usuários, Congregações e Configuração.

Como funciona a segurança:

- Tudo é verificado **no servidor**, a cada requisição, direto no banco. Acessar uma rota ou API administrativa na mão, sem passar pela tela, retorna **403**. Registro de congregação não liberada retorna **404** (como se não existisse) e tentar gravar nela retorna erro de acesso.
- Desativar o usuário, trocar o perfil, as congregações ou a senha vale **imediatamente**, mesmo com a pessoa logada.
- O administrador não consegue rebaixar nem desativar a si mesmo, então o sistema nunca fica sem administrador. O login não pode ser alterado depois de criado. Usuários não são apagados, só desativados (o histórico dos lançamentos é preservado).
- Usuário comum precisa ter ao menos uma congregação. Membros sem congregação só aparecem para administradores.
- A migration `006_usuarios_permissoes.sql` converte perfis antigos (ex.: `secretario`) em usuário comum **sem congregação liberada**: depois de atualizar, entre como administrador e libere as congregações de cada um.
- Teste automático: `python test_usuarios.py` (usa o banco `_test`).
- **Produção (Render/Neon):** o deploy **não** roda migrations sozinho. Depois de publicar uma versão nova, rode `python app.py migrate` apontando para o banco do Neon (use `DATABASE_URL_DIRECT` com a conexão direta). Sem isso, telas novas dão "Erro interno" ou "banco desatualizado".

## 15. Portal do Membro (atualização cadastral pelo próprio membro)

- **Quem libera:** somente o **Administrador**. No cadastro do membro (Editar), o cartão *Acesso ao Portal do Membro* mostra a situação (**Acesso não liberado / Acesso ativo / Acesso revogado**) e as ações **Liberar acesso ao membro**, **Gerar nova senha temporária** e **Revogar acesso**. Usuário comum não vê o cartão e o servidor responde 403.
- **Credenciais:** login (ex.: `maria.souza482`) e senha inicial aleatória de 12 caracteres são mostrados **uma única vez**, com botão *Copiar credenciais*. No banco fica só o hash.
- **Primeiro acesso:** o membro entra em `/login`, cai em `/portal` e é obrigado a criar a própria senha antes de ver o formulário.
- **O que o membro faz:** vê e altera apenas o próprio cadastro (Dados pessoais, Endereço e Dados eclesiásticos: datas de conversão e batismo). Status da membresia, cargo, congregação e datas oficiais são somente leitura. Ajuste a lista em `PORTAL_EDITAVEIS` (app.py) se quiser liberar outros campos.
- **Registro:** cada envio com mudança grava um registro permanente (antes/depois, CPF mascarado) e uma linha no Histórico do membro ("... (membro, via portal)"). Liberações, novas senhas, trocas e revogações ficam em `membro_acesso_log` (permanente).
- **Revogar:** bloqueia novos logins e derruba as sessões abertas; cadastro, conta e histórico são mantidos. Liberar de novo gera novas credenciais (mesmo login).
- **Segurança:** conta de membro só alcança as rotas do portal (lista fechada no `auth()`); o vínculo conta→cadastro vem do banco, nunca do navegador; CSRF em todas as gravações; 5 senhas erradas = 15 min de bloqueio (só contas de membro).
- **Instalação:** `python app.py migrate` (aplica `013_portal_membro.sql`; só adiciona colunas/tabelas) e reinicie. Testes: `TEST_DATABASE_URL=... python test_portal_membro.py`.

## 16. Portal do Membro com chave geral e senha única (Configuração > Portal do Membro)
- **Onde:** menu **Configuração → Portal do Membro** (somente **Administrador**; o servidor responde 403 para qualquer outro usuário).
- **Chave geral:** o administrador **ativa/desativa** o portal. O portal nasce **desativado**. Ativado, a tela mostra **“Portal ativado”** e uma **senha aleatória de 8 dígitos**, a mesma para todos os membros enquanto estiver ativa. Há botões para copiar a mensagem pronta (endereço + senha), copiar a senha, copiar o endereço e **gerar nova senha**.
- **Desativado:** quem abrir o endereço `/membro` vê **“O acesso está temporariamente indisponível, fale com o secretário!”**. A senha é apagada e as sessões abertas caem na próxima ação do membro. Ativar de novo gera uma senha nova.
- **Ativado:** o membro abre `/membro` e informa a **senha do portal + nome completo + data de nascimento** (sem diferenciar maiúsculas/acentos). O servidor localiza o cadastro e só ele fica acessível na sessão: **Dados Pessoais, Endereço e Dados Eclesiásticos**. Nenhuma outra tela ou rota do sistema funciona para ele (lista fechada no `auth()`). Se houver dois cadastros com o mesmo nome e nascimento, ninguém entra por eles: a secretaria corrige.
- **Nova senha ou desativação:** derrubam na hora as sessões abertas (a cada requisição o servidor compara a “versão” da senha gravada no banco).
- **Tentativa e erro:** 20 tentativas erradas em 15 minutos travam a entrada de todos por alguns minutos (tabela `portal_falhas`, sem dado pessoal).
- **Registro:** cada envio com mudança grava um registro permanente (antes/depois, CPF mascarado) e uma linha no Histórico do membro (“… (membro, via portal)”). Quem ativou, desativou ou trocou a senha fica em `portal_config_log` (permanente; a senha não é gravada ali).
- **Contas individuais (seção 15):** continuam existindo, mas também obedecem à chave geral: com o portal desativado elas veem a mesma mensagem.
- **Atenção:** a senha única fica gravada em texto no banco (o administrador precisa vê-la); trate o banco como já deve tratar. Quem sabe a senha, o nome e a data de nascimento de um membro consegue abrir o cadastro dele; por isso toda alteração é registrada. Troque a senha quando quiser (por exemplo, a cada campanha de atualização).
- **Instalação:** `python app.py migrate` (aplica `014_portal_chave_geral.sql`; só adiciona tabelas e permite “sem usuário” nos registros de alteração) e reinicie. Testes: `TEST_DATABASE_URL=... python test_portal_codigo.py`.
