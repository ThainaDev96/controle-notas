### Estrutura básica para iniciar o desenvolvimento de uma aplicação em Django com Docker e docker compose.

### Estrutura do projeto
```plaintext
projeto/

├── .env                 # Variáveis de ambiente
├── .gitignore           # Arquivos ignorados pelo git
├── Dockerfile           # Criação da imagem do container da aplicação
├── docker compose.yml   # Orquestração dos containers
├── manage.py            # Utilizado para interagir com o projeto via linha de comando
├── requirements.txt     # Dependências do projeto
│
├── core/                # Diretório principal do projeto com as configurações globais
│   ├── __init__.py      # Torna o diretório um pacote Python
│   ├── settings.py      # Configurações gerais do projeto (DB, apps, middlewares etc.)
│   ├── urls.py          # Arquivo principal de rotas/URLs do projeto
│   ├── asgi.py          # Configuração para servidores ASGI (WebSockets, etc.)
│   └── wsgi.py          # Configuração para servidores WSGI (produção tradicional)
│
└── app/
    ├── __init__.py             
    ├── admin.py         # Registro dos modelos para o admin do Django
    ├── apps.py          # Configuração do app para o Django
    ├── models.py        # Definição das classes que representam as tabelas do banco de dados
    ├── views.py         # Funções ou classes que retornam respostas (lógica de exibição)
    ├── urls.py          # (opcional) Rotas específicas do app
    ├── forms.py         # (opcional) Formulários baseados em Django Forms ou ModelForms
    ├── tests.py         # (opcional) Testes automatizados (usando unittest ou pytest)
    └── migrations/      # Histórico de migrações do banco de dados
        └── __init__.py
```

#### Pré-requisitos

- Ambiente Linux nativo ou [WSL](https://learn.microsoft.com/pt-br/windows/wsl/install)
- [Python 3.10+](https://www.python.org/)
- [Docker](https://www.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)

**Observação:** Para utizar o Docker e docker compose sem a necessidadde do comando sudo, é necessário
conceder permissão desses comandos para o usuário Linux.
Você pode fazer isso com os comandos abaixo:

##### 1. Crie um grupo `docker` caso ele não exista

```console
groupadd docker
```

##### 2. Adicione o usuário logado `$USER` ao grupo docker

```console
gpasswd -a $USER docker
```

##### 3. Reinicie o `docker`

```console
service docker restart
```
Após executar esses comandos, feche o terminal e abra novamente. 
Se mesmo assim ainda não funcionar, reinicie a sua máquina.


## Executando o projeto

Se estiver executando o projeto pela primeira vez, você precisa criar o arquivo com as variáveis de ambiente. <br>
Você pode fazer isso copiando o arquivo de exemplo, com o comando abaixo:
```console
cp .env.example .env
```
Em seguida, altere o valor passando 

Criar containers e subir a aplicação:
```console
docker compose up --build -d
```

Sobe o projeto:
```console
docker compose up
```

Para os containers do projeto:
```console
docker compose down
```

Atualizar estrutura do banco de dados (criar migração):
```console
docker compose exec web python manage.py makemigrations
```

Executar as migrações:
```console
docker compose exec web python manage.py migrate
```

Criar superusuário (opcional):
```console
docker compose exec web python manage.py createsuperuser
```

Após executar os comandos de iniciação, acesse a aplicação em http://localhost:8000.

## Visualizando logs
```console
docker compose logs -f
```


## Criando um novo aplicativo (app)
```console
docker compose exec web python3 manage.py startapp nome_do_app
```

---

### Comandos Django via terminal
```console
# Inicia o servidor local
docker-compose exec web python manage.py runserver

# Aplica migrações no banco de dados
docker-compose exec web python manage.py migrate

# Gera arquivos de migração
docker-compose exec web python manage.py makemigrations

# Cria usuário admin
docker-compose exec web python manage.py createsuperuser

# Acessa shell Python com contexto do Django
docker-compose exec web python manage.py shell

# Cria um novo app com o nome definido
docker-compose exec web python manage.py startapp aluno
```