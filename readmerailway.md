# Deploy Django + PostgreSQL no Railway

Guia completo e definitivo para hospedar uma aplicação Django com banco de dados PostgreSQL no Railway.

---

## Pré-requisitos

- Conta no [GitHub](https://github.com) com o projeto já enviado
- Conta no [Railway](https://railway.app) criada com login do GitHub
- Arquivo `requirements.txt` na raiz do projeto

---

## 1. Dependências

**Onde:** arquivo `requirements.txt` na raiz do projeto

Adiciona as três linhas abaixo:

```
gunicorn>=21.0,<22.0
dj-database-url>=2.0,<3.0
whitenoise>=6.0,<7.0
```

---

## 2. Ajustes no `settings.py`

**Onde:** arquivo `settings.py` dentro da pasta principal do projeto

### 2.1 ALLOWED_HOSTS

Encontra a linha `ALLOWED_HOSTS` e substitui por:

```python
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='', cast=lambda v: [s.strip() for s in v.split(',')])
```

> Ao preencher essa variável no Railway, coloca **apenas o domínio**, sem `https://` e sem `/` no final.
> Ex: `seuapp.up.railway.app`

### 2.2 CSRF_TRUSTED_ORIGINS

Adiciona essa linha para liberar o envio de formulários como a tela de login:

```python
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=lambda v: [s.strip() for s in v.split(',')])
```

> Ao preencher essa variável no Railway, coloca o domínio **com `https://`** na frente.
> Ex: `https://seuapp.up.railway.app`

### 2.3 Banco de dados

Encontra o trecho `DATABASES` e substitui por:

```python
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL'),
        conn_max_age=600,
    )
}
```

### 2.4 Arquivos estáticos

No `MIDDLEWARE`, adiciona o Whitenoise logo abaixo do `SecurityMiddleware`:

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    ...
]
```

Em qualquer lugar do `settings.py`, adiciona:

```python
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
```

---

## 3. Dockerfile

**Onde:** arquivo `Dockerfile` na raiz do projeto (mesmo nível do `manage.py`)

Cria o arquivo com o conteúdo abaixo:

```dockerfile
FROM python:3.10

# Instala o tini para gerenciar sinais do container corretamente
RUN apt-get update && apt-get install -y tini && apt-get clean

# Define a pasta de trabalho dentro do servidor
WORKDIR /app

# Instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia os arquivos do projeto
COPY . .

ENTRYPOINT ["/usr/bin/tini", "--"]

# Coleta os arquivos estáticos e inicia o servidor web
CMD ["sh", "-c", "python manage.py collectstatic --noinput && gunicorn core.wsgi --bind 0.0.0.0:$PORT --log-file -"]
```

> Substitui `core` pelo nome da pasta onde fica o `wsgi.py`.
> O `$PORT` é preenchido automaticamente pelo Railway , nunca use uma porta fixa no lugar.

---

## 4. Enviar para o GitHub

**Onde:** terminal da sua máquina

```bash
git add .
git commit -m "configurações para deploy no Railway"
git push
```

---

## 5. Criar o projeto no Railway

**Onde:** [railway.app](https://railway.app)

1. Clica em **Login** e entra com o GitHub
2. Clica em **New Project**
3. Escolhe **Deploy from GitHub repo**
4. Seleciona o repositório do projeto

O Railway inicia o primeiro deploy automaticamente.

---

## 6. Adicionar o banco de dados PostgreSQL

**Onde:** site do Railway

1. Na tela do projeto, clica em **Add Service → Database → PostgreSQL**
2. O Railway cria o banco e aparece um novo bloco no canvas
Agora você precisa passar a URL do banco para a aplicação:
3. Clica no bloco do **PostgreSQL → aba Variables**
4. Copia o valor da variável `DATABASE_URL`
5. Clica no bloco da **aplicação Django → aba Variables**
6. Clica em **New Variable**, coloca o nome `DATABASE_URL` e cola o valor copiado

Uma seta vai aparecer conectando os dois blocos no canvas.confirmando o vínculo.

---

## 7. Configurar as variáveis de ambiente

**Onde:** aba **Variables** do serviço da aplicação no Railway

Clica em **New Variable** e adiciona cada uma abaixo:

| Variável | Valor |
|---|---|
| `DJANGO_SECRET_KEY` | chave secreta gerada no terminal (veja abaixo) |
| `DJANGO_DEBUG` | `False` |
| `ALLOWED_HOSTS` | domínio do site sem `https://` e sem `/` no final |
| `CSRF_TRUSTED_ORIGINS` | domínio do site com `https://` na frente |
| `DATABASE_URL` | copiado do serviço PostgreSQL |

Para gerar a secret key, roda no terminal da sua máquina:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---
Copia o resultado e cola como valor da variável `DJANGO_SECRET_KEY`.

## 8. Start Command e porta

**Onde:** aba **Settings** do serviço da aplicação no Railway

**Start Command:** em **Deploy → Start Command**, deixa o campo **vazio**. O Dockerfile já cuida de iniciar o servidor.

**Porta:** após o deploy, abre os logs em **Deployments → deploy mais recente** e procura a linha:

```
[INFO] Listening at: http://0.0.0.0:XXXX
```

Vai em **Settings → Networking → Public Networking** e coloca o número `XXXX` no campo de porta.

---

## 9. Gerar o domínio público

**Onde:** aba **Settings → Networking** no Railway

1. Clica em **Generate Domain**
2. Copia o domínio gerado ex: `seuapp.up.railway.app`
3. Volta em **Variables** e atualiza:
   - `ALLOWED_HOSTS` → `seuapp.up.railway.app`
   - `CSRF_TRUSTED_ORIGINS` → `https://seuapp.up.railway.app`

---

## 10. Criar as tabelas no banco

**Onde:** no terminal do Railway

As tabelas são onde os dados ficam armazenados. Você precisa criá-las após o primeiro deploy.

Para abrir o terminal do Railway:
1. Clica em **Deployments**
2. Clica no deploy ativo
3. Abre a aba **Terminal**

No terminal, executa os comandos abaixo, um de cada vez:

```bash
# Prepara as instruções de criação das tabelas
python manage.py makemigrations
```

```bash
# Cria as tabelas no banco de dados
python manage.py migrate
```

```bash
# Cria o usuário administrador para acessar o painel do Django (opcional)
python manage.py createsuperuser
```

---

## 11. Popular os dados iniciais

**Onde:** terminal do Railway (para o site) ou terminal da sua máquina (para o localhost)

```bash
python manage.py popular_dados
```

Caso os vínculos entre alunos e turmas não apareçam após popular, execute o SQL abaixo no DBeaver:

```sql
INSERT INTO core_turma_alunos (turma_id, user_id)
SELECT DISTINCT m.turma_id, m.aluno_id
FROM core_matricula m
WHERE NOT EXISTS (
    SELECT 1 FROM core_turma_alunos ta
    WHERE ta.turma_id = m.turma_id
    AND ta.user_id = m.aluno_id
);
```

---

## 12. Conectar ao banco pelo DBeaver

**Onde:** Railway e programa DBeaver

**Habilitar acesso externo:**

1. Clica no serviço **PostgreSQL → Settings → Networking → Public Networking**
2. Clica em **Add Public Port**
3. O Railway gera um endereço público

**Pegar as credenciais:**

Na aba **Variables** do PostgreSQL, localiza `DATABASE_PUBLIC_URL`:

```
postgresql://postgres:SENHA@xxxx.proxy.rlwy.net:PORTA/railway
```

**Configurar no DBeaver:**
Abre o DBeaver, cria uma nova conexão do tipo PostgreSQL e preenche os campos:

| Campo | O que colocar |
|-------|--------------|
| **Host** | o endereço entre `@` e `:` — ex: `xxxx.proxy.rlwy.net` |
| **Port** | o número após o último `:` — ex: `35628` |
| **Database** | o que vem após a última `/` — geralmente `railway` |
| **Username** | `postgres` |
| **Password** | o que está entre `postgres:` e o `@` na URL |

Na aba **SSL** da conexão, marca **Use SSL** e define o modo como `require`.


> Após terminar de usar o DBeaver, desativa o acesso público em **Settings → Networking** para proteger o banco.

---

## 13. Atualizando o site

**Onde:** terminal da sua máquina

Sempre que alterar o código e quiser atualizar o site:

```bash
git add .
git commit -m "descrição da alteração"
git push
```

O Railway detecta o push e atualiza o site automaticamente.

---

## Observações

- Qualquer `push` para o GitHub dispara um novo deploy automaticamente.
- O banco do site e o banco local são independentes — dados cadastrados em um não aparecem no outro.
- Nunca sobe o arquivo `.env` para o GitHub — as informações sensíveis ficam nas variáveis de ambiente do Railway.
- A aplicação pode demorar ~10s para responder após um período de inatividade.
