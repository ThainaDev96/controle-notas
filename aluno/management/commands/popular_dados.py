"""
Popula o banco com dados de teste: 5 turmas de 8 alunos, os mesmos em todas as
disciplinas, com as situações de notas divididas por turma e modo de cálculo
(ver SITUACOES_TURMA). Cada aluno fica em uma turma só. Além deles ficam 10 alunos
sem turma, para testar o cadastro de matrícula.

Rodar de novo dá sempre o mesmo resultado, então também serve para voltar as
turmas, os alunos e as notas ao ponto inicial depois de testar.

No banco local o comando tira o excesso: alunos do CSV que sobram, turmas fora
da lista e as matrículas, notas e faltas de antes. As aulas das turmas que ficam
continuam. Em outro banco (Railway) ele só popula banco vazio; se já houver
dados, para sem alterar nada.
"""

import csv
import random
import secrets
import string
from datetime import datetime

from django.contrib.auth.hashers import identify_hasher, make_password
from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from aluno.models import Disciplina, Nota, Turma, Matricula, Avaliacao, NotaAvaliacao
from aluno.calculo_notas import NOTA_MAXIMA, calcular_nota_final, calcular_pos_exame, classificar

# Senha dos alunos gerados e dos professores sem senha válida, só no banco local.
SENHA_TESTE = "Teste@123"

# Host do banco no docker compose: o único banco em que o comando apaga alguma coisa.
HOST_BANCO_LOCAL = "db"

# Sorteio fixo: rodar de novo dá sempre o mesmo resultado.
SEMENTE_SORTEIO = 2026

NOMES_TURMA = ["1A", "1B", "2A", "2B", "3A"]
# Poucos alunos de propósito: a lista de matrículas do administrador fica leve, e ainda
# sobram no CSV alunos sem matrícula para cadastrar à mão nos testes.
ALUNOS_POR_TURMA = 8
# Alunos cadastrados e sem matrícula nenhuma, para testar o cadastro de matrícula pela tela.
ALUNOS_SEM_TURMA = 10

# Total de períodos da disciplina sem esse número: é obrigatório no cadastro e é a base da frequência.
TOTAL_PERIODOS = 72

SEM_NOTA, PARCIAL, TUDO = "sem_nota", "parcial", "tudo"

# O modo de cálculo é da disciplina, então a situação é por turma e modo. Cada
# situação aparece 5 vezes e cada modo passa pelas 3 situações.
SITUACOES_TURMA = {
    "1A": {"ponderada": SEM_NOTA, "soma": SEM_NOTA, "aritmetica": SEM_NOTA},
    "1B": {"ponderada": SEM_NOTA, "soma": PARCIAL, "aritmetica": TUDO},
    "2A": {"ponderada": PARCIAL, "soma": PARCIAL, "aritmetica": PARCIAL},
    "2B": {"ponderada": TUDO, "soma": SEM_NOTA, "aritmetica": PARCIAL},
    "3A": {"ponderada": TUDO, "soma": TUDO, "aritmetica": TUDO},
}

# Cada aluno tem um perfil pela posição na turma, com 2 alunos em cada um.
QTD_PERFIS = 4
APROVADO, EXAME, REPROVA_POUCO, REPROVA_TUDO = range(QTD_PERFIS)

# REPROVA_POUCO só reprova nestas: com até 2 reprovações o aluno ainda passa de ano.
DISCIPLINAS_REPROVA_POUCO = {"Matemática", "Português"}

# Primeira disciplina de cada modo: o exame fica pendente. Na segunda, já fica lançado.
PRIMEIRAS_DO_MODO = {"Matemática", "Português", "História"}

# Médias exatas da 3A, por posição do aluno: as fronteiras da situação, a média 10
# (com nota máxima em tudo, menos na soma) e a nota zero lançada (que não é o mesmo
# que nota em branco).
MEDIAS_FRONTEIRA_3A = {0: 7.0, 4: 10.0, 1: 6.99, 5: 4.0, 3: 3.99, 7: 0.0}


def gerar_senha(tamanho=12):
    caracteres = string.ascii_letters + string.digits + string.punctuation
    while True:
        senha = ''.join(secrets.choice(caracteres) for _ in range(tamanho))
        if (any(c.isupper() for c in senha) and
            any(c.islower() for c in senha) and
            any(c.isdigit() for c in senha) and
            any(c in string.punctuation for c in senha)):
            return senha


def senha_valida(senha):
    try:
        identify_hasher(senha)
    except ValueError:
        return False
    return True


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
    'Geografia': [
        {'nome': 'Prova 1', 'tipo': 'prova', 'valor': None},
        {'nome': 'Prova 2', 'tipo': 'prova', 'valor': None},
        {'nome': 'Trabalho 1', 'tipo': 'trabalho', 'valor': None},
        {'nome': 'Atividade 1', 'tipo': 'atividade_aula', 'valor': None},
    ],
    # Educação física: de propósito SEM entrada aqui — é a disciplina não
    # configurada (ver popular_disciplinas).
}


class Command(BaseCommand):
    help = "Popula o banco com dados de teste: 5 turmas de 8 alunos, com as situações de notas divididas por turma"

    def handle(self, *args, **kwargs):
        self.banco_local = connection.settings_dict['HOST'] == HOST_BANCO_LOCAL
        # Matrícula, nota, avaliação e aula dependem destes. O superusuário não conta: ele vem antes deste comando.
        tem_dados = (
            Disciplina.objects.exists()
            or Turma.objects.exists()
            or User.objects.filter(is_superuser=False).exists()
        )
        if not self.banco_local and tem_dados:
            raise CommandError("Este banco já tem dados e não é o banco local: nada foi alterado.")

        self.ano = datetime.now().year
        self.sorteio = random.Random(SEMENTE_SORTEIO)

        with transaction.atomic():
            self.popular_grupos()
            self.popular_usuarios()
            self.popular_disciplinas()
            self.popular_turmas()
            self.popular_matriculas()
            self.popular_avaliacoes()
            self.popular_notas()

        self.mostrar_resumo()

    def popular_grupos(self):
        for nome in ("professor", "aluno", "administrador"):
            Group.objects.get_or_create(name=nome)

    def popular_usuarios(self):
        grupo_aluno = Group.objects.get(name="aluno")
        with open("/app/aluno/arquivos/alunos_exemplo.csv", newline='', encoding='utf-8') as file:
            linhas = list(csv.DictReader(file))

        self.sorteio.shuffle(linhas)
        com_turma = len(NOMES_TURMA) * ALUNOS_POR_TURMA
        quantidade = com_turma + ALUNOS_SEM_TURMA

        # Os primeiros vão para as turmas; os seguintes ficam cadastrados, sem turma.
        cadastrados = []
        for row in linhas[:quantidade]:
            user, criado = User.objects.get_or_create(
                username=row['matricula'],
                defaults={'password': gerar_senha()}
            )
            # Desfaz o que as telas possam ter mudado: excluir só inativa, e a edição troca nome e grupo.
            user.first_name = row['nome']
            user.last_name = ''
            user.is_active = True
            user.save()
            user.groups.set([grupo_aluno])
            cadastrados.append(user)
        self.alunos = cadastrados[:com_turma]
        self.alunos_sem_turma = cadastrados[com_turma:]

        # Os alunos do CSV que sobram são o excesso. Quem também é professor ou
        # administrador fica, porque a disciplina do professor seria apagada junto.
        User.objects.filter(
            username__in=[row['matricula'] for row in linhas[quantidade:]],
            groups=grupo_aluno,
            is_superuser=False,
        ).exclude(groups__name__in=("professor", "administrador")).delete()

        if self.banco_local:
            User.objects.filter(id__in=[aluno.id for aluno in cadastrados]).update(
                password=make_password(SENHA_TESTE)
            )

    def popular_disciplinas(self):
        grupo_professor = Group.objects.get(name="professor")

        # Modo de cálculo por disciplina (não por professor), porque a
        # Educação física fica de propósito com modo_calculo=None: é a disciplina
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
                # Educação física e Artes ficam de propósito sem configuração: são as disciplinas para
                # configurar à mão (o modo de cálculo e as avaliações) em testes e apresentações.
                "disciplinas": {"Matemática": "ponderada", "Física": "ponderada", "Educação física": None, "Artes": None},
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
                "disciplinas": {"História": "aritmetica", "Geografia": "aritmetica"},
            },
        ]

        self.disciplinas = []
        for item in professores_disciplinas:
            for nome, modo in item['disciplinas'].items():
                # Procura só pelo nome: o professor da disciplina pode ter sido trocado pela tela.
                disciplina = Disciplina.objects.filter(nome=nome).order_by('-ativo', 'id').first()
                if disciplina is None:
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
                    disciplina = Disciplina(nome=nome, professor=professor)
                disciplina.modo_calculo = modo
                disciplina.ativo = True
                # Quem já tem o total informado pela tela continua com ele.
                if not disciplina.total_periodos:
                    disciplina.total_periodos = TOTAL_PERIODOS
                disciplina.save()
                self.disciplinas.append(disciplina)

        if self.banco_local:
            for professor in User.objects.filter(groups=grupo_professor):
                if not senha_valida(professor.password):
                    professor.password = make_password(SENHA_TESTE)
                    professor.save(update_fields=['password'])

    def popular_turmas(self):
        # Turma com nome ou disciplina fora da lista, ou de ano futuro, é excesso.
        Turma.objects.exclude(nome__in=NOMES_TURMA, disciplina__in=self.disciplinas).delete()
        Turma.objects.filter(ano__gt=self.ano).delete()

        # Excluir pela tela só inativa, então recadastrar deixa turma repetida: fica a ativa.
        vistas = set()
        for turma in Turma.objects.order_by('-ativo', 'id'):
            chave = (turma.nome, turma.disciplina_id, turma.ano)
            if chave in vistas:
                turma.delete()
            vistas.add(chave)

        for disciplina in self.disciplinas:
            for nome in NOMES_TURMA:
                Turma.objects.get_or_create(nome=nome, disciplina=disciplina, ano=self.ano)

        Turma.objects.filter(ativo=False).update(ativo=True)

    def popular_matriculas(self):
        # O que havia antes é excesso: cada aluno fica em uma turma só, a mesma em todas as disciplinas.
        Matricula.objects.all().delete()

        self.alunos_por_turma = {
            nome: self.alunos[i::len(NOMES_TURMA)] for i, nome in enumerate(NOMES_TURMA)
        }
        Matricula.objects.bulk_create([
            Matricula(aluno=aluno, turma=turma)
            for turma in Turma.objects.all()
            for aluno in self.alunos_por_turma[turma.nome]
        ])

    def popular_avaliacoes(self):
        for disciplina in self.disciplinas:
            config = CONFIG_AVALIACOES.get(disciplina.nome, [])
            if list(disciplina.avaliacao_set.order_by('id').values('nome', 'tipo', 'valor')) == config:
                continue
            disciplina.avaliacao_set.all().delete()
            for cfg in config:
                Avaliacao.objects.create(
                    nome=cfg['nome'],
                    tipo=cfg['tipo'],
                    valor=cfg['valor'],
                    disciplina=disciplina,
                )

    def popular_notas(self):
        for turma in Turma.objects.select_related('disciplina').order_by('-ano', 'nome', 'disciplina__nome'):
            modo = turma.disciplina.modo_calculo
            if not modo:
                continue  # Educação física, de propósito: sem avaliação, não há o que lançar

            avaliacoes = list(turma.disciplina.avaliacao_set.order_by('id'))
            # Turma de ano anterior já fechou: fica com tudo lançado.
            situacao = SITUACOES_TURMA[turma.nome][modo] if turma.ano == self.ano else TUDO
            matriculas = {matricula.aluno_id: matricula for matricula in turma.matricula_set.all()}

            for posicao, aluno in enumerate(self.alunos_por_turma[turma.nome]):
                self.lancar_notas(matriculas[aluno.id], turma, avaliacoes, situacao, posicao)

    def lancar_notas(self, matricula, turma, avaliacoes, situacao, posicao):
        disciplina = turma.disciplina
        lancadas = self.avaliacoes_lancadas(avaliacoes, situacao, posicao)

        if not lancadas:
            # A 1A cobre os dois jeitos de estar sem nota: sem registro nenhum e com
            # o registro em "cursando" que a Lista de Notas cria ao abrir a turma.
            if situacao == SEM_NOTA and turma.nome == "1A" and disciplina.nome not in PRIMEIRAS_DO_MODO:
                Nota.objects.create(matricula=matricula, situacao='cursando')
            return

        notas_avaliacao = list(zip(lancadas, self.notas_do_aluno(turma, situacao, posicao, lancadas, avaliacoes)))
        nota = Nota(matricula=matricula, situacao='cursando')

        # Mesma regra da tela: só tem média quem tem todas as avaliações lançadas.
        if len(lancadas) == len(avaliacoes):
            nota.media_final = calcular_nota_final(disciplina, notas_avaliacao)
            nota.situacao = classificar(nota.media_final)
            exame_lancado = turma.ano != self.ano or disciplina.nome not in PRIMEIRAS_DO_MODO
            if nota.situacao == 'exame' and exame_lancado:
                nota.nota_exame = self.nota_do_exame(turma, nota.media_final, posicao)
                _, nota.situacao = calcular_pos_exame(nota.media_final, nota.nota_exame)

        nota.save()
        NotaAvaliacao.objects.bulk_create([
            NotaAvaliacao(nota=nota, avaliacao=avaliacao, nota_obtida=valor)
            for avaliacao, valor in notas_avaliacao
        ])

    def avaliacoes_lancadas(self, avaliacoes, situacao, posicao):
        if situacao == SEM_NOTA:
            return []
        if situacao == TUDO:
            return avaliacoes
        # Parcial, na mesma turma: 2 sem nada, 2 com a primeira, 2 só sem a última e 2 com tudo.
        return [[], avaliacoes[:1], avaliacoes[:-1], avaliacoes][posicao % QTD_PERFIS]

    def media_fronteira(self, turma, posicao):
        if turma.nome == "3A" and turma.ano == self.ano:
            return MEDIAS_FRONTEIRA_3A.get(posicao)
        return None

    def notas_do_aluno(self, turma, situacao, posicao, lancadas, avaliacoes):
        fronteira = self.media_fronteira(turma, posicao)
        if fronteira is not None:
            # Notas iguais dão exatamente essa média na ponderada e na aritmética.
            notas = [fronteira] * len(lancadas)
        else:
            # O desvio é menor que a folga da faixa sorteada, então a situação não muda.
            media = self.sortear_media(turma.disciplina, situacao, posicao)
            notas = [
                round(min(NOTA_MAXIMA, max(0, media + self.sorteio.uniform(-0.4, 0.4))), 1)
                for _ in lancadas
            ]

        if turma.disciplina.modo_calculo == 'soma':
            # A média da fronteira tem duas casas: 6,99 não pode virar 7,0.
            return self.notas_da_soma(notas, lancadas, avaliacoes, 1 if fronteira is None else 2)
        return notas

    def notas_da_soma(self, notas, lancadas, avaliacoes, casas_da_media):
        """
        Na soma a média é a soma das notas, sem peso. Para a soma cair na média do aluno,
        cada nota vira a parte dela no peso da avaliação: nota x peso / soma dos pesos.
        """
        soma_dos_pesos = sum(avaliacao.valor for avaliacao in avaliacoes)
        partes, acumulado, somado = [], 0, 0
        for nota, avaliacao in zip(notas, lancadas):
            acumulado += nota * avaliacao.valor / soma_dos_pesos
            # Arredonda o acumulado, e não cada parte, para os arredondamentos não se somarem.
            # Só a última avaliação fecha com as casas da média.
            ate_aqui = round(acumulado, casas_da_media if avaliacao == avaliacoes[-1] else 1)
            partes.append(round(ate_aqui - somado, 2))
            somado = ate_aqui
        return partes

    def sortear_media(self, disciplina, situacao, posicao):
        perfil = posicao % QTD_PERFIS
        if situacao == PARCIAL:
            # No parcial o perfil já diz o que foi lançado; o resultado alterna entre os alunos.
            perfil = (APROVADO, EXAME, REPROVA_TUDO)[(posicao // QTD_PERFIS) % 3]

        if perfil == REPROVA_TUDO or (perfil == REPROVA_POUCO and disciplina.nome in DISCIPLINAS_REPROVA_POUCO):
            return self.sorteio.uniform(1.0, 3.4)
        if perfil == EXAME:
            return self.sorteio.uniform(4.6, 6.4)
        return self.sorteio.uniform(7.6, 9.5)

    def nota_do_exame(self, turma, media, posicao):
        # Os alunos em exame alternam entre passar e não passar. Passa quem fecha
        # com 6 em (média + exame) / 2, ou seja, média + exame >= 12.
        passa = (posicao // QTD_PERFIS) % 2 == 0
        if self.media_fronteira(turma, posicao) is not None:
            return round((12 if passa else 11.98) - media, 2)
        if passa:
            return round(min(NOTA_MAXIMA, 12 - media + self.sorteio.uniform(0.5, 2)), 1)
        return round(max(0, 12 - media - self.sorteio.uniform(1, 3)), 1)

    def mostrar_resumo(self):
        for nome in NOMES_TURMA:
            situacoes = ', '.join(f"{modo}={situacao}" for modo, situacao in SITUACOES_TURMA[nome].items())
            # Os 4 primeiros alunos da turma têm um perfil cada.
            logins = ', '.join(aluno.username for aluno in self.alunos_por_turma[nome][:QTD_PERFIS])
            self.stdout.write(f"{nome}: {situacoes} | logins: {logins}")
        sem_turma = ', '.join(aluno.username for aluno in self.alunos_sem_turma)
        self.stdout.write(f"Sem turma, para testar o cadastro de matrícula: {sem_turma}")
        if self.banco_local:
            self.stdout.write("Senha de teste: veja SENHA_TESTE em aluno/management/commands/popular_dados.py")
