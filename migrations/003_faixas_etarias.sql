-- Faixas etárias configuráveis (aba Configuração > Faixa etária).
-- idade_ate = idade máxima (em anos completos) da faixa; a última faixa (idoso) não tem limite.
CREATE TABLE faixas_etarias(
  slug text PRIMARY KEY,
  nome text NOT NULL,
  ordem int NOT NULL UNIQUE,
  idade_ate int CHECK (idade_ate BETWEEN 0 AND 119),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO faixas_etarias(slug,nome,ordem,idade_ate) VALUES
  ('crianca','Crianças',1,11),
  ('adolescente','Adolescentes',2,17),
  ('jovem','Jovens',3,29),
  ('adulto','Adultos',4,59),
  ('idoso','Idosos',5,NULL);
