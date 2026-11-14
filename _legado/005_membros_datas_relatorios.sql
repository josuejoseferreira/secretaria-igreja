-- Datas que abrem e fecham a contagem de cada pessoa (usadas pelo módulo Relatórios):
--   data_chegada   = veio para a igreja            (começa a contar como Congregado)
--   data_membresia = registrado como membro        (passa a contar como Membro)
--   data_saida     = saiu da igreja (carta de mudança ou falecimento) — encerra a contagem
--   data_cargo     = desde quando exerce o cargo eclesiástico
-- Nenhum dado existente é alterado: cadastros antigos ficam com as datas vazias e o relatório
-- estima a partir das datas que já existem (conversão, recebimento, cadastro).
ALTER TABLE membros
  ADD COLUMN data_chegada date,
  ADD COLUMN data_membresia date,
  ADD COLUMN data_saida date,
  ADD COLUMN motivo_saida text CHECK (motivo_saida IN ('Carta de mudança','Falecimento')),
  ADD COLUMN data_cargo date,
  ADD CONSTRAINT ck_saida_completa CHECK ((data_saida IS NULL) = (motivo_saida IS NULL));
CREATE INDEX ix_membros_saida ON membros(data_saida);
