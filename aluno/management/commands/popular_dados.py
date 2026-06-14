from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from aluno.models import Disciplina, Nota, Turma, Matricula
import random
import csv
import os
import secrets
import string


def gerar_senha(tamanho=12):
    caracteres = string.ascii_letters + string.digits + string.punctuation
    while True:
        senha = ''.join(secrets.choice(caracteres) for _ in range(tamanho))
        if (any(c.isupper() for c in senha) and
            any(c.islower() for c in senha) and
            any(c.isdigit() for c in senha) and
            any(c in string.punctuation for c in senha)):
            return senha


class Command(BaseCommand):
    help = "Popula o banco com dados de teste"
    #chama métodos
    def handle(self, *args, **kwargs):
        self.popular_usuarios()
        self.popular_disciplinas()
        self.popular_turmas()
        self.popular_matriculas()
        self.popular_notas()
        

    def popular_usuarios(self):
        with open("/app/aluno/arquivos/alunos_exemplo.csv", newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                user, criado = User.objects.get_or_create(
                    username=row['matricula'],
                    password=gerar_senha(),
                    first_name=row['nome'],
                    last_name=""
                )

    def popular_disciplinas(self):
        professores_disciplinas = [
            {
                "professor": {
                    "username": "carlos.souza",
                    "first_name": "Carlos",
                    "last_name": "Souza",
                    "email": "carlos.souza@dombosco.com",
                },
                "disciplinas": ["Matemática", "Física"]
            },
            {
                "professor": {
                    "username": "ana.oliveira",
                    "first_name": "Ana",
                    "last_name": "Oliveira",
                    "email": "ana.oliveira@dombosco.com",
                },
                "disciplinas": ["Português", "Literatura"]
            },
            {
                "professor": {
                    "username": "roberto.lima",
                    "first_name": "Roberto",
                    "last_name": "Lima",
                    "email": "roberto.lima@dombosco.com",
                },
                "disciplinas": ["História", "Geografia"]
            },
        ]

        for item in professores_disciplinas:
            professor, criado = User.objects.get_or_create(
                username=item['professor']['username'],
                defaults={
                    "first_name": item['professor']['first_name'],
                    "last_name": item['professor']['last_name'],
                    "password": gerar_senha(),
                    "email": item['professor']['email'],
                    "is_staff": False,
                }
            )
            for nome in item['disciplinas']:
                Disciplina.objects.get_or_create(
                    nome=nome,
                    professor=professor
                )

    def popular_turmas(self):
        nome_turma = ["1A", "1B", "2A", "2B", "3A"]

        alunos = list(User.objects.filter(is_staff=False))
        disciplinas = list(Disciplina.objects.all())

        alunos_por_turma = len(alunos) // len(nome_turma)

        for i, nome in enumerate(nome_turma):
            inicio = i * alunos_por_turma
            fim = inicio + alunos_por_turma if i < len(nome_turma) - 1 else len(alunos)
            alunos_da_turma = alunos[inicio:fim]

            for disciplina in disciplinas:
                turma, _ = Turma.objects.get_or_create(nome=nome, disciplina=disciplina)
                turma.alunos.set(alunos_da_turma)  # vincula os alunos à turma

    def popular_matriculas(self):
        grupo_professor = Group.objects.get(name="professor")
        alunos = list(User.objects.exclude(groups=grupo_professor).exclude(is_superuser=True))
        turmas = list(Turma.objects.filter(ativo=True))

        # Embaralha os alunos para distribuição aleatória
        random.shuffle(alunos)

        for i, aluno in enumerate(alunos):
            # Distribui ciclicamente entre as turmas disponíveis
            turma = turmas[i % len(turmas)]
            Matricula.objects.get_or_create(aluno=aluno, turma=turma)
    
    def popular_notas(self):
        disciplinas = Disciplina.objects.all()
        alunos = User.objects.all()

        for aluno in alunos:
            for disciplina in disciplinas:
                p1 = round(random.uniform(0, 10), 1)
                t1 = round(random.uniform(0, 10), 1)
                p2 = round(random.uniform(0, 10), 1)
                t2 = round(random.uniform(0, 10), 1)

                media = round((p1 + t1 + p2 + t2) / 4, 2)

                if media >= 7:
                    situacao = "aprovado"
                elif media >= 5:
                    situacao = "recuperacao"
                else:
                    situacao = "reprovado"

                Nota.objects.get_or_create(
                    aluno=aluno,
                    disciplina=disciplina,
                    defaults={
                        "nota_p1": p1,
                        "nota_p2": p2,
                        "nota_t1": t1,
                        "nota_t2": t2,
                        "media_final": media,
                        "situacao": situacao,
                    }
                )
