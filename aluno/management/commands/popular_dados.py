"""
Versão corrigida de aluno/management/commands/popular_dados.py

O que mudou em relação à versão antiga (e por quê):

1. Cria os grupos "professor", "aluno" e "administrador" se eles ainda não
   existirem (a versão antiga assumia que o grupo "professor" já existia,
   e quebrava — ou pior, rodava silenciosamente errado — se ele não existisse).
2. Coloca cada professor no grupo "professor" (a versão antiga nunca fazia
   isso, então popular_matriculas() não conseguia distinguir professor de
   aluno de verdade, e podia matricular professor como se fosse aluno).
3. Define disciplina.modo_calculo para cada disciplina (a versão antiga
   deixava esse campo em branco, então nenhuma tela nova — cadastrar
   avaliação, configurar avaliações — funcionava com os dados gerados).
4. Cria Avaliacao (uma ou mais por disciplina, no modo escolhido) e
   NotaAvaliacao (uma por nota x avaliação), respeitando os mesmos
   limites que a tela usa (LIMITE_MODO_CALCULO / teto_nota_avaliacao).
5. Calcula e grava media_final/situacao em Nota usando as MESMAS funções
   que as views usam (calcular_nota_final / classificar), em vez de deixar
   tudo em "cursando" com nota em branco.

Como usar: sobrescreva o arquivo
aluno/management/commands/popular_dados.py com este conteúdo (ou troque
o nome da classe/arquivo se preferir manter os dois lado a lado).
"""

import csv
import random
import secrets
import string
from datetime import datetime

from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand

from aluno.models import Disciplina, Nota, Turma, Matricula, Avaliacao, NotaAvaliacao
from aluno.calculo_notas import calcular_nota_final, classificar


def gerar_senha(tamanho=12):
    caracteres = string.ascii_letters + string.digits + string.punctuation
    while True:
        senha = ''.join(secrets.choice(caracteres) for _ in range(tamanho))
        if (any(c.isupper() for c in senha) and
            any(c.islower() for c in senha) and
            any(c.isdigit() for c in senha) and
            any(c in string.punctuation for c in senha)):
            return senha


# Configuração POR DISCIPLINA (não por modo) — cada uma com uma composição
# diferente de avaliações, pra não parecer que existe um formato fixo por
# trás. O que precisa fechar é só a soma dos "valor" dentro do limite do
# modo daquela disciplina (100 pra ponderada, 10 pra soma; aritmética não
# usa "valor" nenhum, só a quantidade de avaliações).
CONFIG_AVALIACOES = {
    # ponderada: soma dos pesos tem que fechar em 100
    'Matemática': [
        {'nome': 'Prova 1', 'tipo': 'prova', 'valor': 60},
        {'nome': 'Trabalho 1', 'tipo': 'trabalho', 'valor': 40},
    ],
    'Física': [
        {'nome': 'Prova 1', 'tipo': 'prova', 'valor': 30},
        {'nome': 'Prova 2', 'tipo': 'prova', 'valor': 20},
        {'nome': 'Trabalho 1', 'tipo': 'trabalho', 'valor': 30},
        {'nome': 'Atividade 1', 'tipo': 'atividade_aula', 'valor': 20},
    ],
    # soma: soma dos pesos (pontos) tem que fechar em 10
    'Português': [
        {'nome': 'Prova 1', 'tipo': 'prova', 'valor': 4},
        {'nome': 'Trabalho 1', 'tipo': 'trabalho', 'valor': 3},
        {'nome': 'Atividade 1', 'tipo': 'atividade_aula', 'valor': 3},
    ],
    'Literatura': [
        {'nome': 'Prova 1', 'tipo': 'prova', 'valor': 2.5},
        {'nome': 'Prova 2', 'tipo': 'prova', 'valor': 1.5},
        {'nome': 'Trabalho 1', 'tipo': 'trabalho', 'valor': 2},
        {'nome': 'Trabalho 2', 'tipo': 'trabalho', 'valor': 1},
        {'nome': 'Atividade 1', 'tipo': 'atividade_aula', 'valor': 1.5},
        {'nome': 'Atividade 2', 'tipo': 'atividade_aula', 'valor': 1.5},
    ],
    # aritmética: "valor" não entra na conta, só a quantidade de notas —
    # mas isso não significa que só possa ter "prova": mistura os tipos
    # normalmente, o professor só está dizendo "tira a média simples de tudo".
    'História': [
        {'nome': 'Prova 1', 'tipo': 'prova', 'valor': None},
        {'nome': 'Prova 2', 'tipo': 'prova', 'valor': None},
        {'nome': 'Trabalho 1', 'tipo': 'trabalho', 'valor': None},
        {'nome': 'Atividade 1', 'tipo': 'atividade_aula', 'valor': None},
    ],
    # Geografia: de propósito SEM entrada aqui — é a disciplina não
    # configurada (ver popular_disciplinas).
}


class Command(BaseCommand):
    help = "Popula o banco com dados de teste, já no formato do novo modelo de avaliações"

    def handle(self, *args, **kwargs):
        self.popular_grupos()
        self.popular_usuarios()
        self.popular_disciplinas()
        self.popular_turmas()
        self.popular_matriculas()
        self.popular_avaliacoes()
        self.popular_notas()

    def popular_grupos(self):
        for nome in ("professor", "aluno", "administrador"):
            Group.objects.get_or_create(name=nome)

    def popular_usuarios(self):
        grupo_aluno = Group.objects.get(name="aluno")
        with open("/app/aluno/arquivos/alunos_exemplo.csv", newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                user, criado = User.objects.get_or_create(
                    username=row['matricula'],
                    defaults={
                        'first_name': row['nome'],
                        'last_name': '',
                        'password': gerar_senha(),
                    }
                )
                user.groups.add(grupo_aluno)

    def popular_disciplinas(self):
        grupo_professor = Group.objects.get(name="professor")

        # Modo de cálculo por disciplina (não por professor), porque a
        # Geografia fica de propósito com modo_calculo=None: é a disciplina
        # "não configurada" pra você testar a tela zerada, sem nenhuma
        # avaliação/nota nela.
        professores_disciplinas = [
            {
                "professor": {
                    "username": "carlos.souza",
                    "first_name": "Carlos",
                    "last_name": "Souza",
                    "email": "carlos.souza@dombosco.com",
                },
                "disciplinas": {"Matemática": "ponderada", "Física": "ponderada"},
            },
            {
                "professor": {
                    "username": "ana.oliveira",
                    "first_name": "Ana",
                    "last_name": "Oliveira",
                    "email": "ana.oliveira@dombosco.com",
                },
                "disciplinas": {"Português": "soma", "Literatura": "soma"},
            },
            {
                "professor": {
                    "username": "roberto.lima",
                    "first_name": "Roberto",
                    "last_name": "Lima",
                    "email": "roberto.lima@dombosco.com",
                },
                # Geografia = None de propósito: fica sem modo_calculo, sem
                # avaliação e sem nota lançada, pra testar a tela "zerada".
                "disciplinas": {"História": "aritmetica", "Geografia": None},
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
            professor.groups.add(grupo_professor)

            for nome, modo in item['disciplinas'].items():
                disciplina, _ = Disciplina.objects.get_or_create(
                    nome=nome,
                    professor=professor,
                )
                if modo and not disciplina.modo_calculo:
                    disciplina.modo_calculo = modo
                    disciplina.save(update_fields=['modo_calculo'])

    def popular_turmas(self):
        nome_turma = ["1A", "1B", "2A", "2B", "3A"]

        for disciplina in Disciplina.objects.all():
            for nome in nome_turma:
                Turma.objects.get_or_create(
                    nome=nome, disciplina=disciplina, defaults={'ano': datetime.now().year}
                )

    def popular_matriculas(self):
        grupo_professor = Group.objects.get(name="professor")
        alunos = list(User.objects.exclude(groups=grupo_professor).exclude(is_superuser=True))

        nomes_turma = ["1A", "1B", "2A", "2B", "3A"]
        random.shuffle(alunos)

        for i, aluno in enumerate(alunos):
            nome = nomes_turma[i % len(nomes_turma)]
            turmas_do_aluno = Turma.objects.filter(nome=nome, ativo=True)
            for turma in turmas_do_aluno:
                Matricula.objects.get_or_create(aluno=aluno, turma=turma)

    def popular_avaliacoes(self):
        for disciplina in Disciplina.objects.all():
            if not disciplina.modo_calculo:
                continue  # Geografia, de propósito: fica sem avaliação nenhuma
            if disciplina.avaliacao_set.exists():
                continue
            for cfg in CONFIG_AVALIACOES.get(disciplina.nome, []):
                Avaliacao.objects.create(
                    nome=cfg['nome'],
                    tipo=cfg['tipo'],
                    valor=cfg['valor'],
                    disciplina=disciplina,
                )

    def popular_notas(self):
        matriculas = Matricula.objects.filter(
            ativo=True, aluno__groups__name="aluno"
        ).select_related('aluno', 'turma__disciplina')

        for matricula in matriculas:
            disciplina = matricula.turma.disciplina
            avaliacoes = list(disciplina.avaliacao_set.all())

            nota, _ = Nota.objects.get_or_create(
                matricula=matricula,
                defaults={"situacao": "cursando"},
            )

            notas_avaliacao = []
            for avaliacao in avaliacoes:
                teto = avaliacao.valor if (disciplina.modo_calculo == 'soma' and avaliacao.valor) else 10
                # Faixa larga (20% a 100% do teto) de propósito: com 206
                # alunos, isso garante que apareçam os três resultados
                # possíveis (aprovado, exame, reprovado) em cada
                # disciplina configurada, em vez de só nota alta.
                valor_nota = round(random.uniform(teto * 0.2, teto), 1)
                NotaAvaliacao.objects.get_or_create(
                    nota=nota,
                    avaliacao=avaliacao,
                    defaults={"nota_obtida": valor_nota},
                )
                notas_avaliacao.append((avaliacao, valor_nota))

            if notas_avaliacao:
                nota.media_final = calcular_nota_final(disciplina, notas_avaliacao)
                nota.situacao = classificar(nota.media_final)
                nota.save(update_fields=['media_final', 'situacao'])
