# Relatório técnico e de testes

## Correções feitas (sobre o código existente)
- Configuração por `.env` (python-dotenv) com validação e mensagens claras; sem senha fixa (`ADMIN_PASSWORD` obrigatória, mín. 8) e sem SECRET_KEY padrão.
- Migrations fora do import: `python app.py migrate` (aplica só as pendentes, registra em schema_migrations) e `python app.py check` (conexão, extensões, pendências). O app se recusa a iniciar com migrations pendentes.
- Admin criado só se não existir; senha nunca redefinida.
- Login com `session.clear()`, logout real (`/logout`), cookies HttpOnly/SameSite, `COOKIE_SECURE` por ambiente.
- Proteção CSRF (token na sessão, header `X-CSRF-Token` na API e campo no login).
- Tratamento de erros: JSON sem detalhes internos; banco indisponível → 503; `/health`.
- Painel: "Registros no mês" agora conta pessoas/registros individuais; o terceiro indicador é "Lotes aguardando conferência" (antes misturava lote com registro).
- Filtro por Situação (API + tela).
- Compatível com Render/Neon: gunicorn, PORT/HOST, `DATABASE_URL_DIRECT` para migrations.
- Testes reescritos para banco separado (`*_test`), com autenticação, CSRF, rollback real e idempotência.

## Resultado dos testes (execução real, PostgreSQL 16)
```
OK    cadastro duplicado de congregação é recusado
OK    11 tipos disponíveis
OK    cadastro individual: Novo convertido
OK    cadastro individual: Batismo nas águas
OK    cadastro individual: Batismo com Espírito Santo
OK    cadastro individual: Falecimento
OK    cadastro individual: Carta de mudança – saída
OK    cadastro individual: Carta de mudança – entrada
OK    cadastro individual: Desligamento
OK    cadastro individual: Aclamação
OK    cadastro individual: Cura divina
OK    cadastro individual: Criança apresentada
OK    cadastro individual: Casamento
OK    lote de 15 pessoas salvo
OK    exibe Várias (15) com os 15 nomes
OK    registro individual mostra 1 nome
OK    erro apontado na linha 2
OK    nada gravado após erro
OK    congregação obrigatória
OK    busca por nome sem acento dentro do lote
OK    busca por mãe
OK    busca por noiva
OK    filtro período exclui
OK    filtro tipo
OK    filtro congregação
OK    filtros combinados
OK    edição remove 1 pessoa (14 ativas)
OK    conferir lote
OK    lote conferido não edita
OK    cancelar lote preserva registros
OK    histórico registra ações
OK    congregação inativa recusada em novo lote
OK    inativa continua na consulta histórica
OK    painel com dados reais
OK    rota da API sem login responde 401
OK    página inicial sem login redireciona
OK    login sem token CSRF é recusado
OK    senha errada não abre sessão
OK    POST na API sem CSRF é recusado
OK    /health responde ok
OK    filtro por situação
OK    falha no meio da gravação: erro 500 e ROLLBACK integral do lote
OK    migrate repetido preserva dados e NÃO redefine a senha do admin
OK    banco vazio inicializado: 11 tipos, 1 admin, migration registrada
OK    dados persistem com nova instância da aplicação
Aprovados: 45 | Reprovados: 0 | Não executados: 0 (JS no navegador não é coberto por este script)
```

## Não executado / limitações
- Interface no navegador (JS, tooltip por hover/toque, modal): não há navegador neste ambiente; validar manualmente.
- `instalar.bat` / `iniciar.bat`: escritos, mas não executados em Windows real.
- Gunicorn, Render e Neon: apenas preparados; nada publicado.
- CSS/JS continuam embutidos no index.html; as pastas `static/` existem vazias para uso futuro.
- Validado no Linux com Python 3.12 e PostgreSQL 16; o seu Windows usa Python 3.14.

---
**Atualização (etapa de conferência removida):** os itens acima sobre "Lotes aguardando conferência", `conferir lote` e `lote conferido não edita` deixaram de valer. Veja a seção 14 do README e a migration `004_remove_conferencia.sql`.

