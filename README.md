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

