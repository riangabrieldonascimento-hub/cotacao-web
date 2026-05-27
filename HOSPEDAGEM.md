# Hospedar o CotaçãoWeb na internet

O projeto já está preparado para produção com **Gunicorn** e variáveis de ambiente.

## Antes de publicar

1. **Não suba** a pasta `venv/`, arquivos `.db` locais nem senhas no Git.
2. Gere uma chave secreta forte para `SECRET_KEY` (ex.: string aleatória de 32+ caracteres).
3. Altere as senhas padrão (`admin` / `compras`) depois do primeiro acesso em produção.

---

## Opção 1 — Render.com (recomendado para começar)

Grátis para testes. URL pública tipo `https://seu-app.onrender.com`.

### Passos

1. Crie conta em [https://render.com](https://render.com)
2. Suba o código no **GitHub** (repositório novo, sem `venv/`)
3. No Render: **New → Web Service**
4. Conecte o repositório
5. Configuração:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance type:** Free (se disponível)
6. Variáveis de ambiente (**Environment**):
   - `SECRET_KEY` = sua chave secreta
   - `FLASK_DEBUG` = `0`
7. Clique em **Create Web Service**

### Importante (banco SQLite no plano grátis)

No plano gratuito do Render o disco pode ser **apagado** quando o serviço reinicia. Para uso real, use **PostgreSQL** no Render:

1. Crie um **PostgreSQL** no Render
2. Copie a **Internal Database URL**
3. Cole em `DATABASE_URL` no Web Service

O código já aceita `DATABASE_URL` automaticamente.

---

## Opção 2 — PythonAnywhere (bom para SQLite)

Mantém o arquivo `database.db` de forma estável no plano gratuito (com limitações).

1. Conta em [https://www.pythonanywhere.com](https://www.pythonanywhere.com)
2. **Upload** dos arquivos do projeto (sem `venv`)
3. Console Bash:
   ```bash
   cd ~/cotacao_web
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Web** → **Add a new web app** → Manual config → Python 3.10+
5. **WSGI configuration file** — substitua o conteúdo por:
   ```python
   import sys
   path = '/home/SEU_USUARIO/cotacao_web'
   if path not in sys.path:
       sys.path.append(path)
   from app import app as application
   ```
6. **Virtualenv:** `/home/SEU_USUARIO/cotacao_web/venv`
7. Variáveis (opcional) em arquivo `.env` ou no WSGI:
   ```python
   import os
   os.environ['SECRET_KEY'] = 'sua-chave-secreta'
   os.environ['FLASK_DEBUG'] = '0'
   ```
8. **Reload** o app

URL: `https://SEU_USUARIO.pythonanywhere.com`

---

## Opção 3 — VPS (Hostinger, DigitalOcean, etc.)

Para equipe maior ou domínio próprio (`cotacao.suaempresa.com.br`).

1. Servidor Linux (Ubuntu) com Python 3.10+
2. Instale dependências e rode com Gunicorn:
   ```bash
   pip install -r requirements.txt
   export SECRET_KEY="sua-chave"
   export FLASK_DEBUG=0
   gunicorn -w 2 -b 127.0.0.1:8000 app:app
   ```
3. Use **Nginx** como proxy reverso para a porta 8000
4. Configure **HTTPS** com Certbot (Let's Encrypt)
5. Mantenha `instance/database.db` em backup diário

---

## Testar localmente como produção

```bash
pip install -r requirements.txt
set SECRET_KEY=minha-chave-secreta-teste
set FLASK_DEBUG=0
gunicorn app:app
```

Acesse: `http://127.0.0.1:8000`

---

## Login padrão (primeira instalação)

| Usuário  | Senha       | Perfil    |
|----------|-------------|-----------|
| admin    | admin123    | gestor    |
| compras  | compras123  | comprador |

**Troque essas senhas** assim que publicar o sistema.

---

## Checklist rápido

- [ ] Código no GitHub (sem `venv/`)
- [ ] `SECRET_KEY` definida no servidor
- [ ] `FLASK_DEBUG=0` em produção
- [ ] Banco persistente (PostgreSQL ou PythonAnywhere/VPS)
- [ ] Senhas padrão alteradas
- [ ] HTTPS ativo (Render e PythonAnywhere já oferecem)

Se quiser, no próximo passo posso ajudar a subir no **Render** ou **PythonAnywhere** passo a passo com o seu repositório GitHub.
