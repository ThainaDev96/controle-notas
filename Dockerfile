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
