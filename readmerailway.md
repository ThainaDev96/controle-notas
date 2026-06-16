# Deploy de aplicação Django no Railway

Guia completo para hospedar uma aplicação Django com banco de dados PostgreSQL no Railway.

---

## Pré-requisitos

- Conta no [Railway](https://railway.app) criada com login do GitHub
- Arquivo `requirements.txt` na raiz do projeto

---

## 1. Dependências
No requirements.txt:

```
gunicorn>=21.0,<22.0
dj-database-url>=2.0,<3.0
whitenoise>=6.0,<7.0
```

---

## 2. Ajustes no `settings.py`

### 2.1 ALLOWED_HOSTS

```python
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='', cast=lambda v: [s.strip() for s in v.split(',')])
```

> Ao preencher essa variável no Railway, coloca **apenas o domínio**, sem `https://` e sem `/` no final.
> Ex: `seuapp.up.railway.app`

### 2.2 CSRF_TRUSTED_ORIGINS

Liberar o envio de formulários como a tela de login:

```python
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=lambda v: [s.strip() for s in v.split(',')])
```

> Ao preencher essa variável no Railway, coloca o domínio **com `https://`** na frente.
> Ex: `https://seuapp.up.railway.app`

### 2.3 Banco de dados

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

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    ...
]
```

```python
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
```

---

## 3. Dockerfile

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

---

## 4. Criar o projeto no Railway

**Onde:** [railway.app](https://railway.app)

1. Clica em **Login** e entra com o GitHub
2. Clica em **New Project**
3. Escolhe **Deploy from GitHub repo**
4. Seleciona o repositório do projeto

O Railway inicia o primeiro deploy automaticamente.
Sempre que ocorrer uma alteração no repositório, o Railway detecta de forma automática e faz um novo deploy.

---

## 5. Adicionar o banco de dados PostgreSQL

1. Na tela do projeto, clica em **Add Service → Database → PostgreSQL**
2. O Railway cria o banco e aparece na interface
Agora você precisa passar a URL do banco para a aplicação:
3. Clica no bloco do **PostgreSQL → aba Variables**
4. Copia o valor da variável `DATABASE_URL`
5. Clica no bloco da **aplicação Django → aba Variables**
6. Clica em **New Variable**, coloca o nome `DATABASE_URL` e cola o valor copiado

Uma seta vai aparecer conectando os dois blocos na interface confirmando o vínculo.

---

## 6. Configurar as variáveis de ambiente

No railway, não usamos um arquivo .env com credenciais, fazemos a configuração das variáveis de ambiente via interface.
Para fazer isso:

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

## 7. Gerar o domínio público

Na aba **Settings → Networking** no Railway

1. Clicar em **Generate Domain**
2. Copiar o domínio gerado ex: `seuapp.up.railway.app`
3. Voltar em **Variables** e atualiza:
   - `ALLOWED_HOSTS` → `seuapp.up.railway.app`
   - `CSRF_TRUSTED_ORIGINS` → `https://seuapp.up.railway.app`

---

## 8. Criar as tabelas no banco

No terminal do Railway

Para abrir o terminal do Railway:
1. Clica em **Deployments**
2. Clica no deploy ativo
3. Abre a aba **Terminal**

No terminal, é necessário executar os comandos abaixo:

```bash
# Prepara as instruções de criação das tabelas
python manage.py makemigrations
```

```bash
# Cria as tabelas no banco de dados
python manage.py migrate
```

```bash
# Cria o usuário administrador
python manage.py createsuperuser
```

---

## 9. Popular os dados iniciais

No terminal do Railway:

```bash
python manage.py popular_dados
```

---

## 10. Conectar ao banco pelo DBeaver

1. Clica no serviço **PostgreSQL → Settings → Networking → Public Networking**
2. Clica em **Add Public Port**
3. O Railway gera um endereço público

**Pegar as credenciais:**

Na aba **Variables** do PostgreSQL, localiza `DATABASE_PUBLIC_URL`:

```
postgresql://postgres:SENHA@xxxx.proxy.rlwy.net:PORTA/railway
```

**Configurar no DBeaver:**
No DBeaver, é necessário criar uma nova conexão e preenche os campos:

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

## Observações

- Qualquer `push` para o GitHub dispara um novo deploy automaticamente.
- O banco do site e o banco local são independentes - dados cadastrados em um não aparecem no outro.
- Nunca sobe o arquivo `.env` para o GitHub - as informações sensíveis ficam nas variáveis de ambiente do Railway.
- A aplicação pode demorar ~10s para responder após um período de inatividade.
