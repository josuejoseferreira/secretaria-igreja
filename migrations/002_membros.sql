CREATE TABLE membros(
  id serial PRIMARY KEY,
  nome text NOT NULL,
  nome_busca text NOT NULL DEFAULT '',
  data_nascimento date,
  sexo text CHECK (sexo IN ('Masculino','Feminino')),
  estado_civil text CHECK (estado_civil IN ('Solteiro(a)','Casado(a)','Divorciado(a)','Viúvo(a)','União estável')),
  cpf text,
  telefone text,
  email text,
  cep text, logradouro text, numero text, complemento text, bairro text, cidade text, uf text,
  congregacao_id int REFERENCES congregacoes,
  data_conversao date, data_batismo date, data_recebimento date, forma_recebimento text,
  status text NOT NULL DEFAULT 'Ativo' CHECK (status IN ('Ativo','Inativo','Desligado','Faleceu','Disciplinado','Congregado')),
  status_membresia text NOT NULL DEFAULT 'Membro' CHECK (status_membresia IN ('Membro','Congregado')),
  cargo text CHECK (cargo IN ('Obreiro','Diácono','Presbítero','Evangelista','Pastor')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  excluido_em timestamptz,
  CONSTRAINT ck_cargo_so_membro CHECK (cargo IS NULL OR status_membresia = 'Membro')
);
CREATE UNIQUE INDEX ux_membros_cpf ON membros(cpf) WHERE cpf IS NOT NULL AND excluido_em IS NULL;
CREATE INDEX ix_membros_nome ON membros USING gin (nome_busca gin_trgm_ops);
CREATE INDEX ON membros(congregacao_id);
CREATE INDEX ON membros(status);

CREATE TABLE historico_membro(
  id serial PRIMARY KEY,
  membro_id int NOT NULL REFERENCES membros,
  data date NOT NULL,
  secretario_id int NOT NULL REFERENCES usuarios,
  secretario_nome text NOT NULL,
  descricao text NOT NULL CHECK (length(btrim(descricao)) > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON historico_membro(membro_id, data, id);

-- O histórico é permanente: o próprio banco impede alterar ou apagar registros já gravados.
CREATE OR REPLACE FUNCTION f_historico_membro_imutavel() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'O histórico do membro é permanente e não pode ser alterado ou apagado.';
END $$ LANGUAGE plpgsql;
CREATE TRIGGER tg_historico_membro_imutavel BEFORE UPDATE OR DELETE ON historico_membro
  FOR EACH ROW EXECUTE FUNCTION f_historico_membro_imutavel();
