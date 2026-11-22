import csv
import re
from collections import Counter
from datetime import datetime
from io import StringIO
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User, Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from aluno.calculo_notas import calcular_nota_final, calcular_pos_exame, classificar
from aluno.management.commands.popular_dados import SENHA_TESTE
from aluno.models import Disciplina, Turma, Matricula, Nota, Avaliacao, NotaAvaliacao, Aula

CSV_ALUNOS = "/app/aluno/arquivos/alunos_exemplo.csv"

TURMAS = ["1A", "1B", "2A", "2B", "3A"]

MODOS = {
    'Matemática': 'ponderada',
    'Física': 'ponderada',
    'Português': 'soma',
    'Literatura': 'soma',
    'História': 'aritmetica',
    'Geografia': 'aritmetica',
    'Educação física': None,
    'Artes': None,
}

QTD_AVALIACOES = {
    'Matemática': 2,
    'Física': 4,
    'Português': 3,
    'Literatura': 6,
    'História': 4,
    'Geografia': 4,
}

# A tabela combinada: situação das notas de cada turma, por modo de cálculo.
SITUACOES = {
    '1A': {'ponderada': 'sem_nota', 'soma': 'sem_nota', 'aritmetica': 'sem_nota'},
    '1B': {'ponderada': 'sem_nota', 'soma': 'parcial', 'aritmetica': 'tudo'},
    '2A': {'ponderada': 'parcial', 'soma': 'parcial', 'aritmetica': 'parcial'},
    '2B': {'ponderada': 'tudo', 'soma': 'sem_nota', 'aritmetica': 'parcial'},
    '3A': {'ponderada': 'tudo', 'soma': 'tudo', 'aritmetica': 'tudo'},
}


def popular():
    saida = StringIO()
    call_command('popular_dados', stdout=saida)
    return saida.getvalue()


def lancadas_por_aluno(nome_turma, nome_disciplina, ano=None):
    """Quantas notas cada aluno da turma tem lançadas, contando também quem não tem nenhuma."""
    ano = ano or datetime.now().year
    matriculas = Matricula.objects.filter(
        turma__nome=nome_turma, turma__disciplina__nome=nome_disciplina, turma__ano=ano, ativo=True
    )
    return Counter(
        NotaAvaliacao.objects.filter(nota__matricula=matricula, nota_obtida__isnull=False).count()
        for matricula in matriculas
    )


def fotografia_dos_dados():
    return sorted(
        (
            nota_avaliacao.nota.matricula.aluno.username,
            nota_avaliacao.nota.matricula.turma.nome,
            nota_avaliacao.nota.matricula.turma.ano,
            nota_avaliacao.avaliacao.disciplina.nome,
            nota_avaliacao.avaliacao.nome,
            nota_avaliacao.nota_obtida,
            nota_avaliacao.nota.media_final,
            nota_avaliacao.nota.situacao,
            nota_avaliacao.nota.nota_exame or -1,
        )
        for nota_avaliacao in NotaAvaliacao.objects.select_related(
            'nota__matricula__aluno', 'nota__matricula__turma', 'avaliacao__disciplina'
        )
    ), sorted(
        Matricula.objects.values_list('aluno__username', 'turma__nome', 'turma__disciplina__nome', 'turma__ano')
    )


class DistribuicaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.saida = popular()

    def test_cada_turma_tem_8_alunos_em_cada_disciplina(self):
        contagem = Counter(
            Matricula.objects.filter(ativo=True, turma__ativo=True).values_list(
                'turma__nome', 'turma__disciplina__nome'
            )
        )
        esperado = {(turma, disciplina): 8 for turma in TURMAS for disciplina in MODOS}
        self.assertEqual(dict(contagem), esperado)

    def test_a_turma_tem_os_mesmos_alunos_em_todas_as_disciplinas(self):
        for turma in TURMAS:
            grupos = {
                frozenset(Matricula.objects.filter(
                    turma__nome=turma, turma__disciplina__nome=disciplina
                ).values_list('aluno_id', flat=True))
                for disciplina in MODOS
            }
            self.assertEqual(len(grupos), 1, turma)

    def test_cada_aluno_fica_em_uma_turma_so(self):
        turmas_por_aluno = {}
        for aluno_id, turma in Matricula.objects.values_list('aluno_id', 'turma__nome').distinct():
            turmas_por_aluno.setdefault(aluno_id, set()).add(turma)
        self.assertEqual(len(turmas_por_aluno), 40)
        self.assertEqual({len(turmas) for turmas in turmas_por_aluno.values()}, {1})

    def test_modo_de_calculo_de_cada_disciplina(self):
        self.assertEqual(dict(Disciplina.objects.values_list('nome', 'modo_calculo')), MODOS)

    def test_quantidade_de_avaliacoes_de_cada_disciplina(self):
        contagem = Counter(Avaliacao.objects.values_list('disciplina__nome', flat=True))
        self.assertEqual(dict(contagem), QTD_AVALIACOES)

    def test_notas_lancadas_seguem_a_tabela_combinada(self):
        for turma, por_modo in SITUACOES.items():
            for disciplina, total in QTD_AVALIACOES.items():
                situacao = por_modo[MODOS[disciplina]]
                if situacao == 'sem_nota':
                    esperado = Counter({0: 8})
                elif situacao == 'tudo':
                    esperado = Counter({total: 8})
                else:
                    # Na mesma turma: 2 sem nada, 2 com a primeira, 2 só sem a última e 2 com tudo.
                    esperado = Counter({0: 2})
                    esperado[1] += 2
                    esperado[total - 1] += 2
                    esperado[total] += 2
                self.assertEqual(lancadas_por_aluno(turma, disciplina), esperado, (turma, disciplina))

    def test_disciplinas_sem_configuracao_nao_tem_nota_em_nenhuma_turma(self):
        for nome in ('Educação física', 'Artes'):
            self.assertFalse(Nota.objects.filter(matricula__turma__disciplina__nome=nome).exists(), nome)
            self.assertFalse(Avaliacao.objects.filter(disciplina__nome=nome).exists(), nome)

    def test_sem_nota_cobre_com_e_sem_registro(self):
        # Primeira disciplina do cálculo: nunca abriram a turma. Segunda: a Lista de Notas já criou o registro.
        self.assertFalse(Nota.objects.filter(
            matricula__turma__nome='1A', matricula__turma__disciplina__nome='Matemática'
        ).exists())
        registros = Nota.objects.filter(matricula__turma__nome='1A', matricula__turma__disciplina__nome='Física')
        self.assertEqual(Counter(registros.values_list('situacao', flat=True)), Counter({'cursando': 8}))

    def test_quem_nao_tem_tudo_lancado_fica_cursando_e_sem_media(self):
        for nota in Nota.objects.select_related('matricula__turma__disciplina'):
            total = QTD_AVALIACOES[nota.disciplina.nome]
            if nota.notaavaliacao_set.count() < total:
                self.assertEqual(nota.situacao, 'cursando')
                self.assertIsNone(nota.media_final)

    def test_media_e_situacao_batem_com_o_calculo_do_sistema(self):
        completas = 0
        for nota in Nota.objects.filter(media_final__isnull=False).select_related('matricula__turma__disciplina'):
            completas += 1
            pares = [
                (na.avaliacao, na.nota_obtida)
                for na in nota.notaavaliacao_set.select_related('avaliacao').order_by('avaliacao_id')
            ]
            self.assertEqual(nota.media_final, calcular_nota_final(nota.disciplina, pares))
            if nota.nota_exame is None:
                self.assertEqual(nota.situacao, classificar(nota.media_final))
            else:
                self.assertEqual(nota.situacao, calcular_pos_exame(nota.media_final, nota.nota_exame)[1])
        self.assertGreater(completas, 0)

    def test_notas_ficam_de_0_a_10(self):
        for nota in NotaAvaliacao.objects.values_list('nota_obtida', flat=True):
            self.assertTrue(0 <= nota <= 10, nota)

    def _situacoes(self, turma, disciplina):
        return Counter(Nota.objects.filter(
            matricula__turma__nome=turma, matricula__turma__disciplina__nome=disciplina
        ).values_list('situacao', flat=True))

    def test_situacoes_de_cada_celula(self):
        tudo = {
            'Matemática': {'aprovado': 2, 'exame': 2, 'reprovado': 4},
            'Português': {'aprovado': 2, 'exame': 2, 'reprovado': 4},
            'História': {'aprovado': 4, 'exame': 2, 'reprovado': 2},
            'Física': {'aprovado': 5, 'reprovado': 3},
            'Literatura': {'aprovado': 5, 'reprovado': 3},
            'Geografia': {'aprovado': 5, 'reprovado': 3},
        }
        # Dos 2 com tudo lançado, um é aprovado e o outro cai em exame (que, já lançado, ele não passa).
        parcial_primeira = {'cursando': 4, 'aprovado': 1, 'exame': 1}
        parcial_segunda = {'cursando': 4, 'aprovado': 1, 'reprovado': 1}
        parcial = {
            'Matemática': parcial_primeira, 'Português': parcial_primeira, 'História': parcial_primeira,
            'Física': parcial_segunda, 'Literatura': parcial_segunda, 'Geografia': parcial_segunda,
        }
        for turma, por_modo in SITUACOES.items():
            for disciplina in QTD_AVALIACOES:
                situacao = por_modo[MODOS[disciplina]]
                if situacao == 'tudo':
                    esperado = tudo[disciplina]
                elif situacao == 'parcial':
                    esperado = parcial[disciplina]
                elif turma == '1A' and disciplina in ('Física', 'Literatura', 'Geografia'):
                    esperado = {'cursando': 8}
                else:
                    esperado = {}
                self.assertEqual(self._situacoes(turma, disciplina), Counter(esperado), (turma, disciplina))

    def test_os_50_alunos_ficam_no_grupo_aluno(self):
        # 40 nas turmas e 10 sem turma.
        self.assertEqual(User.objects.filter(groups__name='aluno').count(), 50)

    def test_dez_alunos_ficam_sem_turma_para_testar_a_matricula(self):
        sem_turma = User.objects.filter(groups__name='aluno', matricula__isnull=True)
        self.assertEqual(sem_turma.count(), 10)
        aluno = sem_turma.first()
        self.assertTrue(aluno.first_name)
        self.assertTrue(self.client.login(username=aluno.username, password=SENHA_TESTE))
        linha = next(l for l in self.saida.splitlines() if l.startswith("Sem turma"))
        self.assertEqual(set(re.findall(r"\b\d{6,}\b", linha)), set(sem_turma.values_list('username', flat=True)))

    def test_professor_criado_fica_no_grupo_e_entra_com_a_senha_de_teste(self):
        for username in ('carlos.souza', 'ana.oliveira', 'roberto.lima'):
            self.assertTrue(User.objects.filter(username=username, groups__name='professor').exists(), username)
            self.assertTrue(self.client.login(username=username, password=SENHA_TESTE), username)

    def test_notas_sorteadas_tem_uma_casa(self):
        notas = NotaAvaliacao.objects.exclude(nota__matricula__turma__nome='3A').values_list('nota_obtida', flat=True)
        self.assertGreater(len(notas), 0)
        for nota in notas:
            self.assertEqual(round(nota, 1), nota)

    def test_parcial_mostra_os_resultados_de_quem_ja_tem_tudo(self):
        # 2 sem registro, 4 cursando e os 2 com tudo lançado: 1 aprovado e 1 em exame.
        self.assertEqual(
            self._situacoes('2A', 'Matemática'),
            Counter({'cursando': 4, 'aprovado': 1, 'exame': 1}),
        )

    def test_primeira_disciplina_do_calculo_fica_com_exame_pendente(self):
        # 2 aprovados, 2 em exame, 2 reprovados em poucas disciplinas (Matemática é uma delas) e 2 em todas.
        self.assertEqual(self._situacoes('3A', 'Matemática'), Counter({'reprovado': 4, 'aprovado': 2, 'exame': 2}))
        self.assertFalse(Nota.objects.filter(
            matricula__turma__nome='3A', matricula__turma__disciplina__nome='Matemática', nota_exame__isnull=False
        ).exists())

    def test_segunda_disciplina_do_calculo_fica_com_exame_lancado(self):
        # 2 aprovados, 1 que passou no exame, 1 que não passou, 2 aprovados aqui e 2 reprovados em tudo.
        self.assertEqual(self._situacoes('3A', 'Física'), Counter({'aprovado': 5, 'reprovado': 3}))
        self.assertEqual(Nota.objects.filter(
            matricula__turma__nome='3A', matricula__turma__disciplina__nome='Física', nota_exame__isnull=False
        ).count(), 2)

    def _media_na_3a(self, disciplina, media):
        return Nota.objects.get(
            matricula__turma__nome='3A', matricula__turma__disciplina__nome=disciplina, media_final=media
        )

    def test_valores_de_fronteira_da_media_em_cada_calculo(self):
        for disciplina in ('Matemática', 'Português', 'História'):
            self.assertEqual(self._media_na_3a(disciplina, 7.0).situacao, 'aprovado')
            self.assertEqual(self._media_na_3a(disciplina, 6.99).situacao, 'exame')
            self.assertEqual(self._media_na_3a(disciplina, 4.0).situacao, 'exame')
            self.assertEqual(self._media_na_3a(disciplina, 3.99).situacao, 'reprovado')

    def test_valores_de_fronteira_do_exame(self):
        passou = self._media_na_3a('Física', 6.99)
        self.assertEqual((passou.nota_exame, passou.situacao), (5.01, 'aprovado'))
        nao_passou = self._media_na_3a('Física', 4.0)
        self.assertEqual((nao_passou.nota_exame, nao_passou.situacao), (7.98, 'reprovado'))

    def test_nota_zero_lancada_e_nota_maxima(self):
        zerado = self._media_na_3a('Português', 0.0)
        self.assertEqual(set(zerado.notaavaliacao_set.values_list('nota_obtida', flat=True)), {0.0})
        maximo = self._media_na_3a('Matemática', 10.0)
        self.assertEqual(set(maximo.notaavaliacao_set.values_list('nota_obtida', flat=True)), {10.0})
        # Na soma a média é a soma das notas: a média 10 fica dividida pelos pesos, 4 + 3 + 3.
        maximo = self._media_na_3a('Português', 10.0)
        self.assertEqual(sorted(maximo.notaavaliacao_set.values_list('nota_obtida', flat=True)), [3.0, 3.0, 4.0])

    def test_boletim_da_3a_mostra_as_tres_situacoes_finais(self):
        finais = Counter()
        alunos = User.objects.filter(matricula__turma__nome='3A').distinct()
        for aluno in alunos:
            self.client.force_login(aluno)
            finais[self.client.get(reverse('minhas-notas')).context['situacao_final']] += 1
        self.assertEqual(finais, Counter({'aprovado': 4, 'em_andamento': 2, 'reprovado': 2}))

    def test_aluno_entra_com_a_senha_de_teste(self):
        aluno = Matricula.objects.first().aluno
        self.assertTrue(self.client.login(username=aluno.username, password=SENHA_TESTE))

    def test_resumo_mostra_logins_da_propria_turma(self):
        for turma in TURMAS:
            linha = next(l for l in self.saida.splitlines() if l.startswith(f"{turma}:"))
            logins = re.findall(r"\b\d{6,}\b", linha)
            self.assertEqual(len(logins), 4, linha)
            self.assertEqual(
                Matricula.objects.filter(aluno__username__in=logins, turma__nome=turma)
                .values('aluno_id').distinct().count(),
                4,
            )

    def test_rodar_de_novo_da_o_mesmo_resultado(self):
        antes = fotografia_dos_dados()
        popular()
        self.assertEqual(fotografia_dos_dados(), antes)


class ExcessoTests(TestCase):
    """Banco no estado bagunçado de hoje: todos os alunos do CSV e matrículas de execuções anteriores."""

    def setUp(self):
        self.ano = datetime.now().year
        self.grupo_aluno, _ = Group.objects.get_or_create(name='aluno')
        grupo_professor, _ = Group.objects.get_or_create(name='professor')

        with open(CSV_ALUNOS, newline='', encoding='utf-8') as arquivo:
            self.nomes_csv = {linha['matricula']: linha['nome'] for linha in csv.DictReader(arquivo)}
        self.matriculas_csv = list(self.nomes_csv)
        alunos = User.objects.bulk_create([User(username=m, password='senha-sem-hash') for m in self.matriculas_csv])
        self.grupo_aluno.user_set.add(*User.objects.filter(username__in=self.matriculas_csv))
        self.assertEqual(len(alunos), 206)

        self.senha_conta_de_teste = make_password('senha-que-ja-funciona')
        self.testealuno = User.objects.create(username='testealuno', password=self.senha_conta_de_teste)
        self.testealuno.groups.add(self.grupo_aluno)

        self.testeprofessor = User.objects.create(username='testeprofessor', password=self.senha_conta_de_teste)
        self.testeprofessor.groups.add(grupo_professor)
        self.maria = User.objects.create(username='maria.oliveira', password='senha-sem-hash')
        self.maria.groups.add(grupo_professor)

        self.geografia = Disciplina.objects.create(
            nome='Geografia', professor=self.testeprofessor, modo_calculo='soma', total_periodos=72
        )
        Avaliacao.objects.create(nome='p1', tipo='prova', valor=10, disciplina=self.geografia)
        self.portugues = Disciplina.objects.create(nome='Português', professor=self.maria, modo_calculo='soma')
        self.matematica = Disciplina.objects.create(
            nome='Matemática', professor=self.testeprofessor, modo_calculo='ponderada'
        )
        self.geopolitica = Disciplina.objects.create(nome='Geopolítica', professor=self.testeprofessor, ativo=False)

        self.turma_inativa = Turma.objects.create(nome='3A', disciplina=self.matematica, ano=self.ano, ativo=False)
        self.turma_de_teste = Turma.objects.create(nome='TESTE-A', disciplina=self.matematica, ano=self.ano)
        self.turma_com_aula = Turma.objects.create(nome='1A', disciplina=self.geografia, ano=self.ano)
        self.turma_antiga = Turma.objects.create(nome='1A', disciplina=self.geografia, ano=self.ano - 1)
        self.turma_antiga_matematica = Turma.objects.create(nome='3A', disciplina=self.matematica, ano=self.ano - 1)
        self.turma_fora_da_lista = Turma.objects.create(nome='1A', disciplina=self.geopolitica, ano=self.ano)
        self.aula = Aula.objects.create(turma=self.turma_com_aula, data='2026-10-25')

        # O mesmo aluno em duas turmas, como ficou depois de rodar o script antigo mais de uma vez.
        for turma in (self.turma_inativa, self.turma_de_teste, self.turma_com_aula, self.turma_antiga):
            for aluno in User.objects.filter(username__in=self.matriculas_csv)[:30]:
                Matricula.objects.create(aluno=aluno, turma=turma)
        Matricula.objects.create(aluno=self.testealuno, turma=self.turma_de_teste)

    def test_alunos_do_csv_que_sobram_saem_do_banco(self):
        popular()
        self.assertEqual(User.objects.filter(username__in=self.matriculas_csv).count(), 50)

    def test_conta_de_teste_continua_igual_e_sem_turma(self):
        popular()
        self.testealuno.refresh_from_db()
        self.assertEqual(self.testealuno.password, self.senha_conta_de_teste)
        self.assertFalse(self.testealuno.matricula_set.exists())

    def test_turma_com_nome_fora_da_lista_sai(self):
        popular()
        self.assertFalse(Turma.objects.filter(nome='TESTE-A').exists())

    def test_nao_aumenta_o_numero_de_turmas(self):
        popular()
        self.assertEqual(set(Turma.objects.values_list('nome', flat=True)), set(TURMAS))
        # 5 nomes x 8 disciplinas no ano atual, mais as duas turmas de ano anterior.
        self.assertEqual(Turma.objects.count(), 42)

    def test_turma_de_ano_futuro_sai(self):
        Turma.objects.create(nome='1A', disciplina=self.matematica, ano=self.ano + 1)
        popular()
        self.assertFalse(Turma.objects.filter(ano__gt=self.ano).exists())

    def test_turma_repetida_nao_derruba_o_comando(self):
        # Excluir pela tela só inativa, e recadastrar cria outra linha igual.
        repetida = Turma.objects.create(nome='3A', disciplina=self.matematica, ano=self.ano)
        Turma.objects.create(nome='1A', disciplina=self.geografia, ano=self.ano - 1)
        popular()
        linhas = Turma.objects.filter(nome='3A', disciplina=self.matematica, ano=self.ano)
        self.assertEqual([turma.id for turma in linhas], [repetida.id])
        self.assertEqual(Turma.objects.filter(nome='1A', disciplina=self.geografia, ano=self.ano - 1).count(), 1)

    def test_disciplina_inativa_da_lista_volta_a_ativa(self):
        self.portugues.ativo = False
        self.portugues.save()
        popular()
        self.portugues.refresh_from_db()
        self.assertTrue(self.portugues.ativo)

    def test_disciplina_repetida_usa_a_ativa_e_mantem_as_aulas_dela(self):
        Disciplina.objects.create(nome='Física', professor=self.testeprofessor, ativo=False)
        fisica = Disciplina.objects.create(nome='Física', professor=self.maria, modo_calculo='soma')
        turma = Turma.objects.create(nome='3A', disciplina=fisica, ano=self.ano)
        aula = Aula.objects.create(turma=turma, data='2026-10-25')
        popular()
        fisica.refresh_from_db()
        self.assertEqual(fisica.modo_calculo, 'ponderada')
        self.assertTrue(Aula.objects.filter(id=aula.id).exists())

    def test_aluno_que_ja_existia_entra_com_a_senha_de_teste(self):
        popular()
        aluno = Matricula.objects.first().aluno
        self.assertTrue(self.client.login(username=aluno.username, password=SENHA_TESTE))

    def test_rodar_de_novo_restaura_aluno_mexido_pelas_telas(self):
        popular()
        aluno = Matricula.objects.first().aluno
        nome = self.nomes_csv[aluno.username]
        self.assertEqual(aluno.first_name, nome)

        # Excluir pela tela só inativa, e a edição troca o nome e o grupo.
        aluno.is_active = False
        aluno.first_name = 'Outro nome'
        aluno.save()
        aluno.groups.set(Group.objects.filter(name='professor'))

        popular()
        aluno.refresh_from_db()
        self.assertTrue(aluno.is_active)
        self.assertEqual(aluno.first_name, nome)
        self.assertEqual(list(aluno.groups.values_list('name', flat=True)), ['aluno'])
        self.assertTrue(self.client.login(username=aluno.username, password=SENHA_TESTE))

    def test_aluno_do_csv_que_sobra_e_tambem_professor_fica_com_a_disciplina(self):
        popular()
        sobra = next(m for m in self.matriculas_csv if not User.objects.filter(username=m).exists())
        usuario = User.objects.create(username=sobra, password='senha-sem-hash')
        usuario.groups.set(Group.objects.filter(name__in=('aluno', 'professor')))
        quimica = Disciplina.objects.create(nome='Química', professor=usuario)

        popular()
        self.assertTrue(User.objects.filter(username=sobra).exists())
        self.assertTrue(Disciplina.objects.filter(id=quimica.id).exists())

    def test_turma_inativa_da_lista_volta_a_ativa_com_8_alunos(self):
        popular()
        self.turma_inativa.refresh_from_db()
        self.assertTrue(self.turma_inativa.ativo)
        self.assertEqual(self.turma_inativa.matricula_set.count(), 8)

    def test_disciplina_existente_nao_duplica_e_mantem_o_professor(self):
        popular()
        self.assertEqual(Disciplina.objects.filter(nome='Geografia').count(), 1)
        self.assertEqual(Disciplina.objects.filter(nome='Português').count(), 1)
        self.portugues.refresh_from_db()
        self.assertEqual(self.portugues.professor, self.maria)

    def test_geografia_vira_aritmetica_e_mantem_os_periodos(self):
        popular()
        self.geografia.refresh_from_db()
        self.assertEqual(self.geografia.modo_calculo, 'aritmetica')
        self.assertEqual(self.geografia.total_periodos, 72)
        self.assertEqual(
            list(self.geografia.avaliacao_set.order_by('id').values_list('nome', 'valor')),
            [('Prova 1', None), ('Prova 2', None), ('Trabalho 1', None), ('Atividade 1', None)],
        )

    def test_disciplina_fora_da_lista_nao_muda(self):
        popular()
        self.geopolitica.refresh_from_db()
        self.assertFalse(self.geopolitica.ativo)
        self.assertFalse(self.geopolitica.turma_set.exists())

    def test_aula_da_turma_que_fica_nao_e_apagada(self):
        popular()
        self.assertTrue(Aula.objects.filter(id=self.aula.id, turma=self.turma_com_aula).exists())

    def test_turma_de_ano_anterior_fica_com_os_alunos_do_mesmo_nome_e_tudo_lancado(self):
        popular()
        do_ano = set(Matricula.objects.filter(turma=self.turma_com_aula).values_list('aluno_id', flat=True))
        anteriores = set(Matricula.objects.filter(turma=self.turma_antiga).values_list('aluno_id', flat=True))
        self.assertEqual(len(anteriores), 8)
        self.assertEqual(anteriores, do_ano)
        self.assertEqual(lancadas_por_aluno('1A', 'Geografia', ano=self.ano - 1), Counter({4: 8}))
        self.assertFalse(Nota.objects.filter(matricula__turma=self.turma_antiga, situacao='exame').exists())

    def test_turma_de_ano_anterior_tem_exame_lancado_tambem_na_primeira_disciplina_do_calculo(self):
        popular()
        notas = Nota.objects.filter(matricula__turma=self.turma_antiga_matematica)
        self.assertFalse(notas.filter(situacao='exame').exists())
        self.assertEqual(notas.filter(nota_exame__isnull=False).count(), 2)

    def test_professor_sem_senha_valida_passa_a_entrar_com_a_senha_de_teste(self):
        popular()
        self.assertTrue(self.client.login(username='maria.oliveira', password=SENHA_TESTE))

    def test_professor_com_senha_valida_nao_muda(self):
        popular()
        self.testeprofessor.refresh_from_db()
        self.assertEqual(self.testeprofessor.password, self.senha_conta_de_teste)


class ForaDoBancoLocalTests(TestCase):
    def _fora_do_banco_local(self):
        return mock.patch.dict(connection.settings_dict, {'HOST': 'outro-banco'})

    def test_com_dados_para_sem_alterar_nada(self):
        professor = User.objects.create(username='professor')
        disciplina = Disciplina.objects.create(nome='Matemática', professor=professor, modo_calculo='soma')
        turma = Turma.objects.create(nome='9Z', disciplina=disciplina, ano=2020)
        aluno = User.objects.create(username='aluno')
        Matricula.objects.create(aluno=aluno, turma=turma)

        with self._fora_do_banco_local(), self.assertRaises(CommandError):
            popular()

        self.assertTrue(Turma.objects.filter(id=turma.id).exists())
        self.assertEqual(Matricula.objects.count(), 1)
        disciplina.refresh_from_db()
        self.assertEqual(disciplina.modo_calculo, 'soma')
        self.assertEqual(User.objects.count(), 2)

    def test_em_banco_vazio_popula_sem_senha_de_teste(self):
        with self._fora_do_banco_local():
            popular()
        self.assertEqual(Matricula.objects.values('aluno_id').distinct().count(), 40)
        aluno = Matricula.objects.first().aluno
        self.assertFalse(self.client.login(username=aluno.username, password=SENHA_TESTE))
        for username in ('carlos.souza', 'ana.oliveira', 'roberto.lima'):
            self.assertFalse(self.client.login(username=username, password=SENHA_TESTE), username)

    def test_com_disciplina_e_sem_turma_para_sem_alterar_nada(self):
        professor = User.objects.create(username='professor', is_superuser=True)
        disciplina = Disciplina.objects.create(nome='Matemática', professor=professor, modo_calculo='soma')
        avaliacao = Avaliacao.objects.create(nome='p1', tipo='prova', valor=10, disciplina=disciplina)

        with self._fora_do_banco_local(), self.assertRaises(CommandError):
            popular()

        disciplina.refresh_from_db()
        self.assertEqual(disciplina.modo_calculo, 'soma')
        self.assertTrue(Avaliacao.objects.filter(id=avaliacao.id).exists())

    def test_com_usuario_e_sem_turma_para_sem_alterar_nada(self):
        with open(CSV_ALUNOS, newline='', encoding='utf-8') as arquivo:
            matriculas = [linha['matricula'] for linha in csv.DictReader(arquivo)]
        User.objects.bulk_create([User(username=m) for m in matriculas])

        with self._fora_do_banco_local(), self.assertRaises(CommandError):
            popular()

        self.assertEqual(User.objects.count(), 206)

    def test_so_com_superusuario_popula(self):
        User.objects.create_superuser(username='admin', password='senha-do-admin')
        with self._fora_do_banco_local():
            popular()
        self.assertEqual(Matricula.objects.values('aluno_id').distinct().count(), 40)
