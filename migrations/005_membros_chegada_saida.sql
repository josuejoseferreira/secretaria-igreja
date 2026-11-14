-- Datas que iniciam/encerram a contagem de cada pessoa (usadas no Relatório comparativo).
-- data_chegada: quando veio para a igreja; data_recebimento (já existia): quando foi registrado como membro;
-- data_saida/forma_saida: quando saiu (carta de mudança, falecimento, desligamento).
ALTER TABLE membros ADD COLUMN IF NOT EXISTS data_chegada date, ADD COLUMN IF NOT EXISTS data_saida date, ADD COLUMN IF NOT EXISTS forma_saida text;
