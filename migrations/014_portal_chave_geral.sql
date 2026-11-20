-- Portal do Membro com CHAVE GERAL e SENHA ÚNICA de 8 dígitos (Configuração > Portal do Membro).
-- Só ADICIONA tabelas e afrouxa duas colunas de auditoria (para registrar quem alterou quando não há conta de usuário).
-- Nenhum cadastro, usuário ou permissão existente é alterado. O portal nasce DESATIVADO.

-- 1) Chave geral (uma única linha). Ativo => existe senha de 8 dígitos; desativado => sem senha.
--    'versao' sobe a cada nova senha: derruba as sessões de membros abertas com a senha anterior.
CREATE TABLE portal_config(
  id                int PRIMARY KEY CHECK (id = 1),
  ativo             boolean NOT NULL DEFAULT false,
  codigo            text CHECK (codigo ~ '^[0-9]{8}$'),
  versao            int NOT NULL DEFAULT 0,
  alterado_em       timestamptz NOT NULL DEFAULT now(),
  alterado_por_nome text,
  CONSTRAINT ck_portal_codigo CHECK (NOT ativo OR codigo IS NOT NULL)
);
INSERT INTO portal_config(id) VALUES (1);

-- 2) Quem ativou, desativou ou trocou a senha (permanente; a senha NÃO é gravada aqui).
CREATE TABLE portal_config_log(
  id         serial PRIMARY KEY,
  acao       text NOT NULL CHECK (acao IN ('ativou', 'desativou', 'nova_senha')),
  por_id     int NOT NULL REFERENCES usuarios(id),
  por_nome   text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER tg_portal_config_log_imutavel BEFORE UPDATE OR DELETE ON portal_config_log
  FOR EACH ROW EXECUTE FUNCTION f_registro_permanente();

-- 3) Tentativas erradas de entrada (senha ou identificação), para travar tentativa e erro. Não guarda dado pessoal.
CREATE TABLE portal_falhas(
  id         bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_portal_falhas ON portal_falhas(created_at);

-- 4) Quando o membro entra pela senha única não existe conta de usuário: o registro permanente de alterações e o
--    Histórico do membro passam a aceitar "sem usuário" (o nome do membro continua gravado, como antes).
ALTER TABLE membro_alteracoes ALTER COLUMN usuario_id DROP NOT NULL;
ALTER TABLE historico_membro  ALTER COLUMN secretario_id DROP NOT NULL;
