-- Cadastro de membros: nome da mãe, nome do pai, observações e fotografia.
-- Apenas ADICIONA colunas (anuláveis) e uma tabela nova: os cadastros existentes ficam intactos.
ALTER TABLE membros ADD COLUMN IF NOT EXISTS nome_mae text;
ALTER TABLE membros ADD COLUMN IF NOT EXISTS nome_pai text;
ALTER TABLE membros ADD COLUMN IF NOT EXISTS observacoes text;

-- A foto fica no próprio PostgreSQL (bytea): entra no backup do banco, sobrevive a reinício,
-- troca de servidor e não depende de pasta do disco. Tabela separada para que as listagens
-- (SELECT em membros) nunca carreguem os bytes da imagem.
CREATE TABLE IF NOT EXISTS membro_foto(
  membro_id int PRIMARY KEY REFERENCES membros(id) ON DELETE CASCADE,
  imagem bytea NOT NULL,
  tipo text NOT NULL CHECK (tipo IN ('image/jpeg','image/png','image/webp')),
  tamanho int NOT NULL CHECK (tamanho BETWEEN 1 AND 2097152),
  atualizado_em timestamptz NOT NULL DEFAULT now()
);
