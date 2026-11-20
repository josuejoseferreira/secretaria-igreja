-- O cadastro feito pelo próprio membro no Portal nasce SEM "Status da Membresia" (Membro / Congregado):
-- essa definição é da secretaria. Só afrouxa a coluna para aceitar vazio (a CHECK IN ('Membro','Congregado')
-- já aceita NULL). Nenhum cadastro existente é alterado.
ALTER TABLE membros ALTER COLUMN status_membresia DROP NOT NULL;
