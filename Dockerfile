FROM python:3.10

# Instala tini para gerenciar sinais corretamente
RUN apt-get update && apt-get install -y tini && apt-get clean

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do projeto
COPY . .

# Define o entrypoint para usar tini
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["sh", "-c", "python manage.py collectstatic --noinput && gunicorn core.wsgi --bind 0.0.0.0:$PORT --log-file -"]

#arquivo de especificação, diz exatamente o que o projeto precisa para funcionar dockerfile
#docker compose= como vou conectar dois ou mais containers ,container do backend ,frontend e banco de dados 3 imagens
#a forma de conectar os 3 é atraves do docker compose:defino os serviços que vou ter na minha aplicação
#