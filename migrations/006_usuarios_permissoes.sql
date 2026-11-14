-- Perfis de acesso (Administrador / Usuário comum) e congregações liberadas por usuário.
-- Perfis antigos (ex.: 'secretario') viram 'usuario'; o 'admin' continua igual.
UPDATE usuarios SET perfil = 'usuario' WHERE perfil <> 'admin';
ALTER TABLE usuarios ALTER COLUMN perfil SET DEFAULT 'usuario';
ALTER TABLE usuarios ADD CONSTRAINT ck_usuarios_perfil CHECK (perfil IN ('admin', 'usuario'));
ALTER TABLE usuarios ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE usuarios ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- Quais congregações cada usuário comum pode acessar (administrador acessa todas, sem linhas aqui).
CREATE TABLE usuario_congregacoes(
  usuario_id     int NOT NULL REFERENCES usuarios(id)     ON DELETE CASCADE,
  congregacao_id int NOT NULL REFERENCES congregacoes(id) ON DELETE CASCADE,
  PRIMARY KEY (usuario_id, congregacao_id)
);
CREATE INDEX ix_usuario_congregacoes_cong ON usuario_congregacoes(congregacao_id);
