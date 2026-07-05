### Estrutura básica para iniciar o desenvolvimento de uma aplicação em Django com Docker e docker compose.

### Estrutura do projeto
```plaintext
projeto/

├── .env                 # Variáveis de ambiente/ credenciais do banco de dados
├── .gitignore           # Arquivos ignorados pelo git
├── Dockerfile           # Criação da imagem do container da aplicação, como o container da aplicação deve ser construído 
├── docker compose.yml   # Orquestração dos containers (app + banco) 
├── manage.py            # Permite rodar qualquer instrução do Django (migração, criar app, etc)
├── requirements.txt     # Bibliotecas do projeto

├── core/                # Diretório principal do projeto com as configurações globais
│   ├── __init__.py      # Torna o diretório um pacote Python, permite importações entre arquivos
│   ├── settings.py      # Configurações gerais do projeto (DB, apps, middlewares etc.)
│   ├── urls.py          # Arquivo principal de rotas/URLs do projeto, liga um endereço do navegador a uma função do views
│   ├── asgi.py          # Configuração para servidores ASGI (WebSockets, etc.) 
│   └── wsgi.py          # Configuração para servidores WSGI (produção tradicional)
│
└── app/aluno
    ├── __init__.py             
    ├── admin.py         # Registro dos modelos para o admin do Django, painel técnico do programador
    ├── apps.py          # Configuração do app para o Django, registra os app aluno
    ├── models.py        # Definição das classes que representam as tabelas do banco de dados
    ├── views.py         # Funções que retornam respostas (lógica)
    ├── tests.py         # (opcional) Testes automatizados (usando unittest ou pytest)
    └── migrations/      # Histórico de migrações do banco de dados
        └── __init__.py

```

#### Pré-requisitos

- Ambiente Linux nativo ou [WSL](https://learn.microsoft.com/pt-br/windows/wsl/install)
- [Python 3.10+](https://www.python.org/)
- [Docker](https://www.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)

#### Criação inicial do projeto 

Instalar o Django na máquina:
``` console
pip install django
```

Criar o esqueleto do projeto:
``` console
django-admin startproject core .
```

Criar o app principal:
``` console
python manage.py startapp aluno
```

***Observações***
Criar o requirements.txt com as bibliotecas do projeto: 
Django
psycopg2-binary
gunicorn

Criar o Dockerfile, com as instruções de como o container da aplicação deve ser construído.

Criar o .env, com as credenciais do banco (usuário, senha, nome do banco).
```console
cp .env.example .env
```

Criar o docker-compose.yml, organizando dois containers: o da aplicação (web) e o do banco (db).

Ajustar o DATABASES no core/settings.py, apontando para o Postgres usando as credenciais do .env.


## Executando o projeto
Criar containers e subir a aplicação:
```console
docker compose up --build -d
```

Aplicar as tabelas no banco (criar migração):
```console
docker compose exec web python manage.py makemigrations
```
Executar as migrações:
```console
docker compose exec web python manage.py migrate
```

Após executar os comandos de iniciação, acesse a aplicação em http://localhost:8000.


Subir o projeto: 
Se o computador for reiniciado ou o Docker Desktop for fechado, os containers desligam. Para ligar de novo sem reconstruir tudo:

```console
docker compose up
```

Parar os containers do projeto: desligar os containers manualmente:
```console
docker compose down
```

## Visualizando logs
```console
docker compose logs -f
```

## Executando testes e populando o banco

Para simular os registros, foi criado uma função que lê os dados de um arquivo CSV e cria os usuários no banco. Para executá-la, é necessário utilizado o seguinte comando:
```
docker compose exec web python manage.py popular_dados
```