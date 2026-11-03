import importlib
import re
import threading
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth.models import User, Group
from django.db import connection, connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse

from aluno.calculo_frequencia import calcular_frequencia
from aluno.calculo_notas import calcular_nota_final, calcular_pos_exame, classificar, montar_resumo_formula
from aluno.models import Disciplina, Turma, Matricula, Nota, Avaliacao, NotaAvaliacao, Aula, Falta
from aluno.views import SITUACAO_TEXTO

# O nome do arquivo de migração começa com número, então não dá para usar import comum.
CONVERSAO_SOMA = importlib.import_module('aluno.migrations.0029_converter_notas_soma_para_0_a_10')


class CalcularFrequenciaTests(SimpleTestCase):
    def test_duas_casas_com_virgula(self):
        self.assertEqual(calcular_frequencia(72, 4), "94,44%")

    def test_sem_faltas(self):
        self.assertEqual(calcular_frequencia(72, 0), "100,00%")

    def test_arredonda_a_segunda_casa(self):
        # (72 - 1) / 72 x 100 = 98,6111...
        self.assertEqual(calcular_frequencia(72, 1), "98,61%")

    def test_sem_total_de_periodos(self):
        self.assertEqual(calcular_frequencia(None, 3), "-")
        self.assertEqual(calcular_frequencia(0, 3), "-")


class CalculoSomaDosPesosTests(SimpleTestCase):
    def _notas(self, *pares):
        return [(Avaliacao(valor=peso), nota) for peso, nota in pares]

    def test_mf_e_a_soma_das_notas(self):
        # 8 + 7 + 9, sem o peso
        disciplina = Disciplina(modo_calculo='soma')
        self.assertEqual(calcular_nota_final(disciplina, self._notas((4, 8), (3, 7), (3, 9))), 24.0)

    def test_mf_pode_passar_de_10(self):
        # Cada nota vai até 10 e a MF soma todas: 10 + 10 + 10.
        disciplina = Disciplina(modo_calculo='soma')
        self.assertEqual(calcular_nota_final(disciplina, self._notas((4, 10), (3, 10), (3, 10))), 30.0)

    def test_peso_nao_muda_a_mf(self):
        # 3 + 3 + 3 com qualquer peso, e até sem peso.
        disciplina = Disciplina(modo_calculo='soma')
        for pesos in ((4, 3, 3), (1, 1, 8), (None, None, None)):
            with self.subTest(pesos=pesos):
                self.assertEqual(calcular_nota_final(disciplina, self._notas(*zip(pesos, (3, 3, 3)))), 9.0)

    def test_outros_modos_nao_mudam(self):
        self.assertEqual(calcular_nota_final(Disciplina(modo_calculo='ponderada'), self._notas((60, 8), (40, 5))), 6.8)
        self.assertEqual(calcular_nota_final(Disciplina(modo_calculo='aritmetica'), self._notas((None, 8), (None, 5))), 6.5)

    def test_formula_mostra_a_conta_feita(self):
        resumo = montar_resumo_formula(Disciplina(modo_calculo='soma'), self._notas((4, 8), (3, 7), (3, 9)))
        self.assertEqual(resumo['formula_texto'], '8,0 + 7,0 + 9,0 = 24,0')
        self.assertEqual(resumo['resultado'], 24.0)


class RecalculoAoMudarPesoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.professor = User.objects.create_user('carlos', first_name='Carlos')
        cls.professor.groups.add(Group.objects.create(name='professor'))
        # Média ponderada: é nela que o peso entra na MF. Na soma dos pesos a MF é só a soma das notas.
        cls.disciplina = Disciplina.objects.create(nome='Português', professor=cls.professor, modo_calculo='ponderada')
        cls.turma = Turma.objects.create(nome='1A', disciplina=cls.disciplina, ano=2026)
        cls.prova = Avaliacao.objects.create(nome='Prova', valor=40, disciplina=cls.disciplina)
        cls.trabalho = Avaliacao.objects.create(nome='Trabalho', valor=60, disciplina=cls.disciplina)

    def setUp(self):
        self.client.force_login(self.professor)

    def _aluno(self, nome, prova, trabalho, media_final=None, situacao='cursando', nota_exame=None, ativo=True, turma=None, avaliacoes=None):
        usuario = User.objects.create_user(nome, first_name=nome)
        matricula = Matricula.objects.create(aluno=usuario, turma=turma or self.turma, ativo=ativo)
        nota = Nota.objects.create(matricula=matricula, situacao=situacao, media_final=media_final, nota_exame=nota_exame)
        primeira, segunda = avaliacoes or (self.prova, self.trabalho)
        for avaliacao, valor in ((primeira, prova), (segunda, trabalho)):
            if valor is not None:
                NotaAvaliacao.objects.create(nota=nota, avaliacao=avaliacao, nota_obtida=valor)
        return nota

    def _mudar_peso(self, avaliacao, valor):
        return self.client.post(reverse('editar-avaliacao-ajax'), {'id': avaliacao.id, 'campo': 'valor', 'valor': valor}).json()

    def _estado(self, nota):
        nota.refresh_from_db()
        return nota.media_final, nota.situacao, nota.nota_exame

    def test_mudar_o_peso_recalcula_a_mf_e_a_situacao(self):
        # 10 x 40% + 5 x 60% = 7, e com peso 20: 10 x 20% + 5 x 60% = 5
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado')
        self.assertTrue(self._mudar_peso(self.prova, '20')['ok'])
        self.assertEqual(self._estado(nota), (5.0, 'exame', None))

    def test_quando_a_mf_muda_o_exame_fica_e_decide_a_situacao(self):
        # A MF 5 cai no exame, e com o exame 8 já lançado: (5 + 8) / 2 = 6,5
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', nota_exame=8.0)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (5.0, 'aprovado', 8.0))

    def test_exame_baixo_ja_lancado_reprova_quando_a_mf_muda(self):
        # (5 + 6) / 2 = 5,5, abaixo de 6
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', nota_exame=6.0)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (5.0, 'reprovado', 6.0))

    def test_quem_fez_o_exame_continua_decidido_por_ele_com_mf_abaixo_de_4(self):
        # 10 x 10% + 3 x 60% = 2,8, mas quem já fez o exame não volta para as notas: (2,8 + 10) / 2 = 6,4
        nota = self._aluno('ana', 10, 3, media_final=5.8, situacao='aprovado', nota_exame=10.0)
        self._mudar_peso(self.prova, '10')
        self.assertEqual(self._estado(nota), (2.8, 'aprovado', 10.0))

    def test_quando_a_mf_nao_muda_o_exame_fica(self):
        # 0 x 40% + 10 x 60% = 6, e com peso 20 continua 6
        nota = self._aluno('ana', 0, 10, media_final=6.0, situacao='aprovado', nota_exame=8.0)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (6.0, 'aprovado', 8.0))

    def test_aluno_com_nota_faltando_nao_muda(self):
        nota = self._aluno('ana', 10, None, media_final=None, situacao='cursando')
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (None, 'cursando', None))

    def test_salvar_o_mesmo_peso_nao_recalcula(self):
        # A tabela de Configurar Avaliações salva ao sair da célula, mesmo sem mudança.
        nota = self._aluno('ana', 10, 5, media_final=9.9, situacao='aprovado', nota_exame=8.0)
        for valor in ('40', '40.0'):
            self.assertTrue(self._mudar_peso(self.prova, valor)['ok'])
            self.assertEqual(self._estado(nota), (9.9, 'aprovado', 8.0))

    def test_mudar_o_nome_nao_recalcula(self):
        nota = self._aluno('ana', 10, 5, media_final=9.9, situacao='aprovado', nota_exame=8.0)
        self.client.post(reverse('editar-avaliacao-ajax'), {'id': self.prova.id, 'campo': 'nome', 'valor': 'Prova 1'})
        self.assertEqual(self._estado(nota), (9.9, 'aprovado', 8.0))

    def test_peso_recusado_nao_recalcula(self):
        nota = self._aluno('ana', 10, 5, media_final=9.9, situacao='aprovado')
        self.assertFalse(self._mudar_peso(self.prova, '80')['ok'])
        self.assertEqual(self._estado(nota), (9.9, 'aprovado', None))

    def test_matricula_inativa_nao_muda(self):
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', ativo=False)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (7.0, 'aprovado', None))

    def test_outra_disciplina_nao_muda(self):
        outra = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='ponderada')
        turma = Turma.objects.create(nome='1B', disciplina=outra, ano=2026)
        avaliacoes = (
            Avaliacao.objects.create(nome='Prova', valor=40, disciplina=outra),
            Avaliacao.objects.create(nome='Trabalho', valor=60, disciplina=outra),
        )
        nota = self._aluno('ana', 10, 5, media_final=9.9, situacao='aprovado', nota_exame=8.0, turma=turma, avaliacoes=avaliacoes)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (9.9, 'aprovado', 8.0))

    def _aluno_sensivel_a_ordem(self):
        # 8.2 x 60% + 7 x 15% + 4.1 x 25% dá 7.0 somando nessa ordem e 6.99 somando a última parcela antes.
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='ponderada')
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        avaliacoes = [
            Avaliacao.objects.create(nome=nome, valor=valor, disciplina=disciplina)
            for nome, valor in (('A', 60), ('B', 15), ('C', 25))
        ]
        usuario = User.objects.create_user('ana', first_name='ana')
        nota = Nota.objects.create(matricula=Matricula.objects.create(aluno=usuario, turma=turma), situacao='aprovado', media_final=7.0, nota_exame=8.0)
        for avaliacao, valor in zip(avaliacoes, (8.2, 7, 4.1)):
            NotaAvaliacao.objects.create(nota=nota, avaliacao=avaliacao, nota_obtida=valor)
        # Atualizar uma avaliação muda a ordem em que o banco devolve as linhas, quando ele lê a tabela
        # inteira. É o que acontece com a tabela real; neste banco de teste, é preciso pedir.
        avaliacoes[1].save()
        with connection.cursor() as cursor:
            cursor.execute('SET LOCAL enable_indexscan = off')
            cursor.execute('SET LOCAL enable_bitmapscan = off')
        self.assertEqual(list(Avaliacao.objects.filter(disciplina=disciplina).values_list('nome', flat=True)), ['A', 'C', 'B'])
        return nota, avaliacoes

    def test_mf_gravada_nao_depende_da_ordem_das_avaliacoes_no_banco(self):
        nota, avaliacoes = self._aluno_sensivel_a_ordem()
        NotaAvaliacao.objects.filter(nota=nota, avaliacao=avaliacoes[0]).update(nota_obtida=6)
        self.client.post(reverse('editar-nota', args=[nota.id]), {
            f'nota_avaliacao_{avaliacao.id}': valor for avaliacao, valor in zip(avaliacoes, ('8.2', '7', '4.1'))
        })
        self.assertEqual(self._estado(nota), (7.0, 'aprovado', 8.0))

    def test_mudar_peso_sem_mudar_a_mf_nao_apaga_o_exame_pela_ordem_no_banco(self):
        nota, avaliacoes = self._aluno_sensivel_a_ordem()
        # Com nota zero, o peso dessa avaliação, vazio ou 0, não muda a MF.
        sem_peso = Avaliacao.objects.create(nome='D', valor=None, disciplina=avaliacoes[0].disciplina)
        NotaAvaliacao.objects.create(nota=nota, avaliacao=sem_peso, nota_obtida=0)
        self._mudar_peso(sem_peso, '0')
        self.assertEqual(self._estado(nota), (7.0, 'aprovado', 8.0))

    def test_turma_inativa_nao_muda(self):
        turma = Turma.objects.create(nome='1B', disciplina=self.disciplina, ano=2025, ativo=False)
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', nota_exame=8.0, turma=turma)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (7.0, 'aprovado', 8.0))

    def test_turma_ativa_de_outro_ano_tambem_recalcula(self):
        turma = Turma.objects.create(nome='1B', disciplina=self.disciplina, ano=2025)
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', turma=turma)
        self._mudar_peso(self.prova, '20')
        self.assertEqual(self._estado(nota), (5.0, 'exame', None))

    def test_peso_com_virgula_e_salvo_e_recalcula(self):
        # 10 x 22,5% + 5 x 60% = 5,25
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado')
        self.assertTrue(self._mudar_peso(self.prova, ' 22,5 ')['ok'])
        self.prova.refresh_from_db()
        self.assertEqual(self.prova.valor, 22.5)
        self.assertEqual(self._estado(nota), (5.25, 'exame', None))

    def test_peso_invalido_e_recusado_sem_recalcular(self):
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', nota_exame=8.0)
        for valor in ('abc', 'nan', 'inf', '-1'):
            with self.subTest(valor=valor):
                resposta = self._mudar_peso(self.prova, valor)
                self.assertFalse(resposta['ok'])
                self.assertTrue(resposta['erro'])
                self.prova.refresh_from_db()
                self.assertEqual(self.prova.valor, 40)
                self.assertEqual(self._estado(nota), (7.0, 'aprovado', 8.0))

    def test_valor_invalido_tambem_e_recusado_na_media_aritmetica(self):
        # Esse modo não tem limite de soma, então a recusa não pode depender dele.
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='aritmetica')
        avaliacao = Avaliacao.objects.create(nome='Prova', valor=10, disciplina=disciplina)
        for valor in ('nan', 'inf', '-1'):
            with self.subTest(valor=valor):
                self.assertFalse(self._mudar_peso(avaliacao, valor)['ok'])
                avaliacao.refresh_from_db()
                self.assertEqual(avaliacao.valor, 10)

    def test_tela_de_editar_avaliacao_recusa_peso_negativo(self):
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado', nota_exame=8.0)
        self.client.post(reverse('editar-avaliacao', args=[self.prova.id]), {
            'nome': 'Prova', 'tipo': 'prova', 'valor': '-1', 'disciplina': self.disciplina.id,
        })
        self.prova.refresh_from_db()
        self.assertEqual(self.prova.valor, 40)
        self.assertEqual(self._estado(nota), (7.0, 'aprovado', 8.0))

    def test_nova_avaliacao_com_peso_negativo_e_recusada(self):
        resposta = self.client.post(reverse('cadastrar-avaliacao-ajax'), {
            'nome': 'Extra', 'valor': '-1', 'disciplina': self.disciplina.id,
        }).json()
        self.assertFalse(resposta['ok'])
        self.assertFalse(Avaliacao.objects.filter(nome='Extra').exists())

    def test_peso_vazio_nao_conta_na_mf(self):
        # 5 x 60% = 3
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado')
        self.assertTrue(self._mudar_peso(self.prova, '')['ok'])
        self.assertEqual(self._estado(nota), (3.0, 'reprovado', None))

    def test_peso_zero_nao_conta_na_mf(self):
        # 5 x 60% = 3
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado')
        self.assertTrue(self._mudar_peso(self.prova, '0')['ok'])
        self.assertEqual(self._estado(nota), (3.0, 'reprovado', None))

    def test_se_o_recalculo_falhar_o_peso_nao_muda(self):
        self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado')
        with mock.patch('aluno.views.calcular_nota_final', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self._mudar_peso(self.prova, '20')
        self.prova.refresh_from_db()
        self.assertEqual(self.prova.valor, 40)

    def test_media_ponderada_tambem_recalcula(self):
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='ponderada')
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        prova = Avaliacao.objects.create(nome='Prova', valor=60, disciplina=disciplina)
        trabalho = Avaliacao.objects.create(nome='Trabalho', valor=40, disciplina=disciplina)
        # 10 x 60% + 5 x 40% = 8, e com peso 30: 10 x 30% + 5 x 40% = 5
        nota = self._aluno('ana', 10, 5, media_final=8.0, situacao='aprovado', turma=turma, avaliacoes=(prova, trabalho))
        self._mudar_peso(prova, '30')
        self.assertEqual(self._estado(nota), (5.0, 'exame', None))

    def test_na_soma_dos_pesos_mudar_o_peso_nao_muda_a_mf(self):
        # A MF é a soma das notas, 10 + 5 = 15, com qualquer peso.
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='soma')
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        avaliacoes = (
            Avaliacao.objects.create(nome='Prova', valor=4, disciplina=disciplina),
            Avaliacao.objects.create(nome='Trabalho', valor=6, disciplina=disciplina),
        )
        nota = self._aluno('ana', 10, 5, media_final=15.0, situacao='aprovado', turma=turma, avaliacoes=avaliacoes)
        self.assertTrue(self._mudar_peso(avaliacoes[0], '2')['ok'])
        self.assertEqual(self._estado(nota), (15.0, 'aprovado', None))

    def test_tela_de_editar_avaliacao_tambem_recalcula(self):
        nota = self._aluno('ana', 10, 5, media_final=7.0, situacao='aprovado')
        self.client.post(reverse('editar-avaliacao', args=[self.prova.id]), {
            'nome': 'Prova', 'tipo': 'prova', 'valor': '20', 'disciplina': self.disciplina.id,
        })
        self.assertEqual(self._estado(nota), (5.0, 'exame', None))

    def test_tela_de_editar_avaliacao_sem_mudar_o_peso_nao_recalcula(self):
        nota = self._aluno('ana', 10, 5, media_final=9.9, situacao='aprovado', nota_exame=8.0)
        self.client.post(reverse('editar-avaliacao', args=[self.prova.id]), {
            'nome': 'Prova 1', 'tipo': 'prova', 'valor': '40', 'disciplina': self.disciplina.id,
        })
        self.assertEqual(self._estado(nota), (9.9, 'aprovado', 8.0))


class ConversaoNotasSomaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        professor = User.objects.create_user('carlos', first_name='Carlos')
        aluno = User.objects.create_user('20260001', first_name='Ana')

        cls.disciplina = Disciplina.objects.create(nome='Português', professor=professor, modo_calculo='soma')
        cls.prova = Avaliacao.objects.create(nome='Prova', valor=4, disciplina=cls.disciplina)
        cls.trabalho = Avaliacao.objects.create(nome='Trabalho', valor=3, disciplina=cls.disciplina)
        cls.atividade = Avaliacao.objects.create(nome='Atividade', valor=3, disciplina=cls.disciplina)
        turma = Turma.objects.create(nome='1A', disciplina=cls.disciplina, ano=2026)
        cls.nota = Nota.objects.create(matricula=Matricula.objects.create(aluno=aluno, turma=turma), situacao='aprovado', media_final=7.0)
        for avaliacao, valor in ((cls.prova, 3.0), (cls.trabalho, 2.3), (cls.atividade, 1.7)):
            NotaAvaliacao.objects.create(nota=cls.nota, avaliacao=avaliacao, nota_obtida=valor)

        ponderada = Disciplina.objects.create(nome='Física', professor=professor, modo_calculo='ponderada')
        cls.avaliacao_ponderada = Avaliacao.objects.create(nome='Prova', valor=60, disciplina=ponderada)
        turma_ponderada = Turma.objects.create(nome='1A', disciplina=ponderada, ano=2026)
        cls.nota_ponderada = Nota.objects.create(matricula=Matricula.objects.create(aluno=aluno, turma=turma_ponderada), situacao='cursando')
        NotaAvaliacao.objects.create(nota=cls.nota_ponderada, avaliacao=cls.avaliacao_ponderada, nota_obtida=6.0)

    def _gravada(self, avaliacao, nota=None):
        return NotaAvaliacao.objects.get(nota=nota or self.nota, avaliacao=avaliacao).nota_obtida

    def test_converte_para_a_escala_de_0_a_10(self):
        CONVERSAO_SOMA.converter_para_0_a_10(django_apps, None)
        self.assertEqual(self._gravada(self.prova), 7.5)
        self.assertEqual(self._gravada(self.trabalho), 7.67)
        self.assertEqual(self._gravada(self.atividade), 5.67)

    def test_nao_mexe_em_outro_modo_nem_em_nota_vazia(self):
        NotaAvaliacao.objects.filter(nota=self.nota, avaliacao=self.prova).update(nota_obtida=None)
        CONVERSAO_SOMA.converter_para_0_a_10(django_apps, None)
        self.assertEqual(self._gravada(self.avaliacao_ponderada, self.nota_ponderada), 6.0)
        self.assertIsNone(self._gravada(self.prova))

    def test_nota_acima_do_peso_ja_esta_na_escala_nova(self):
        NotaAvaliacao.objects.filter(nota=self.nota, avaliacao=self.trabalho).update(nota_obtida=8.0)
        CONVERSAO_SOMA.converter_para_0_a_10(django_apps, None)
        self.assertEqual(self._gravada(self.trabalho), 8.0)

    def test_desfazer_volta_as_notas_originais(self):
        CONVERSAO_SOMA.converter_para_0_a_10(django_apps, None)
        CONVERSAO_SOMA.voltar_para_o_peso(django_apps, None)
        self.assertEqual(self._gravada(self.prova), 3.0)
        self.assertEqual(self._gravada(self.trabalho), 2.3)
        self.assertEqual(self._gravada(self.atividade), 1.7)
        self.assertEqual(self._gravada(self.avaliacao_ponderada, self.nota_ponderada), 6.0)


class DiarioClasseTests(TestCase):
    DATA = '2026-10-19'  # uma segunda-feira

    @classmethod
    def setUpTestData(cls):
        grupo_professor = Group.objects.create(name='professor')
        grupo_aluno = Group.objects.create(name='aluno')

        cls.professor = User.objects.create_user('carlos', first_name='Carlos', password='senha-de-teste-1')
        cls.professor.groups.add(grupo_professor)
        cls.outro_professor = User.objects.create_user('roberto', first_name='Roberto')
        cls.outro_professor.groups.add(grupo_professor)

        cls.bruna = User.objects.create_user('20260002', first_name='Bruna', password='senha-de-teste-2')
        cls.ana = User.objects.create_user('20260001', first_name='Ana')
        cls.carla = User.objects.create_user('20260003', first_name='Carla')
        for aluno in (cls.bruna, cls.ana, cls.carla):
            aluno.groups.add(grupo_aluno)

        cls.disciplina = Disciplina.objects.create(nome='Matemática', professor=cls.professor, total_periodos=72)
        cls.turma = Turma.objects.create(nome='1A', disciplina=cls.disciplina, ano=2026)
        cls.matricula_bruna = Matricula.objects.create(aluno=cls.bruna, turma=cls.turma)
        cls.matricula_ana = Matricula.objects.create(aluno=cls.ana, turma=cls.turma)
        # Matrícula inativa: não aparece no diário
        Matricula.objects.create(aluno=cls.carla, turma=cls.turma, ativo=False)

        cls.disciplina_outro = Disciplina.objects.create(nome='História', professor=cls.outro_professor)
        cls.turma_outro = Turma.objects.create(nome='1A', disciplina=cls.disciplina_outro, ano=2026)

    def _abrir_diario(self, turma=None, data=DATA):
        turma = turma or self.turma
        url = f"{reverse('diario-classe')}?data={data}"
        self.client.post(url, {'disciplina': turma.disciplina_id, 'turma': turma.id})
        return self.client.get(url)

    def _salvar(self, aula, realizada=True, estados=None):
        """Mesmo envio do "Sim, salvar": a aula e o estado de cada célula, {(matrícula, período): 'P', 'F' ou 'J'}."""
        dados = {'aula': aula.id, 'realizada': '1' if realizada else '0'}
        for (matricula, periodo), estado in (estados or {}).items():
            dados[f'periodo_{matricula.id}_{periodo}'] = estado
        return self.client.post(reverse('salvar-diario-ajax'), dados).json()

    def _faltas_da_bruna(self):
        linha = self._abrir_diario().context['linhas'][1]
        self.assertEqual(linha['aluno'], self.bruna)
        return linha['faltas'], linha['frequencia']

    # ---------- Acesso ----------

    def test_exige_login(self):
        resposta = self.client.get(reverse('diario-classe'))
        self.assertRedirects(resposta, reverse('login'), fetch_redirect_response=False)

    def test_aluno_nao_acessa(self):
        self.client.force_login(self.bruna)
        self.assertEqual(self.client.get(reverse('diario-classe')).status_code, 403)
        self.assertEqual(self.client.post(reverse('salvar-diario-ajax')).status_code, 403)

    def test_professor_ve_so_as_proprias_disciplinas(self):
        self.client.force_login(self.professor)
        resposta = self.client.get(reverse('diario-classe'))
        self.assertEqual(list(resposta.context['disciplinas']), [self.disciplina])
        self.assertNotContains(resposta, 'ainda não tem nenhuma disciplina ativa')

    def test_professor_sem_disciplina_ativa_ve_aviso(self):
        Disciplina.objects.filter(professor=self.professor).update(ativo=False)
        self.client.force_login(self.professor)
        resposta = self.client.get(reverse('diario-classe'))
        self.assertContains(resposta, 'Você ainda não tem nenhuma disciplina ativa')
        self.assertNotContains(resposta, 'Selecione a disciplina e a turma para ver o diário')

    def test_turma_de_outro_professor_nao_abre(self):
        self.client.force_login(self.professor)
        resposta = self._abrir_diario(turma=self.turma_outro)
        self.assertIsNone(resposta.context['turma_atual'])
        self.assertContains(resposta, 'Selecione a disciplina e a turma para ver o diário')
        self.assertFalse(Aula.objects.exists())

    def test_salvar_em_aula_de_outro_professor(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA)
        self.client.force_login(self.outro_professor)
        self.assertFalse(self._salvar(aula, True, {(self.matricula_bruna, 1): 'F'})['ok'])
        self.assertFalse(Falta.objects.exists())
        aula.refresh_from_db()
        self.assertFalse(aula.realizada)

    def test_painel_antigo_leva_ao_diario(self):
        self.client.force_login(self.professor)
        resposta = self.client.get(reverse('professor-dashboard'))
        self.assertRedirects(resposta, reverse('diario-classe'), fetch_redirect_response=False)

    def test_login_do_professor_abre_o_diario(self):
        resposta = self.client.post(reverse('login'), {'username': 'carlos', 'password': 'senha-de-teste-1'})
        self.assertRedirects(resposta, reverse('diario-classe'), fetch_redirect_response=False)

    def test_login_do_aluno_continua_no_boletim(self):
        resposta = self.client.post(reverse('login'), {'username': '20260002', 'password': 'senha-de-teste-2'})
        self.assertRedirects(resposta, reverse('minhas-notas'), fetch_redirect_response=False)

    # ---------- Tela ----------

    def test_abrir_a_data_cria_a_aula_uma_vez(self):
        self.client.force_login(self.professor)
        self._abrir_diario()
        self._abrir_diario()
        aula = Aula.objects.get()
        self.assertEqual((aula.turma, str(aula.data), aula.realizada, aula.periodos), (self.turma, self.DATA, False, 4))

    def test_setas_andam_1_dia(self):
        self.client.force_login(self.professor)
        resposta = self._abrir_diario()
        self.assertEqual(str(resposta.context['data_anterior']), '2026-10-18')
        self.assertEqual(str(resposta.context['data_proxima']), '2026-10-20')
        self.assertEqual(resposta.context['dia_semana'], 'Segunda-feira')

    def test_frequencias_prontas_para_mostrar_na_hora_do_clique(self):
        # Ana tem 2 faltas de outra aula; nesta aula de 4 períodos ela pode chegar a 6.
        outra = Aula.objects.create(turma=self.turma, data='2026-10-12', realizada=True)
        Falta.objects.create(aula=outra, matricula=self.matricula_ana, periodo=1)
        Falta.objects.create(aula=outra, matricula=self.matricula_ana, periodo=2)
        self.client.force_login(self.professor)
        frequencias = self._abrir_diario().context['frequencias']
        self.assertEqual(list(frequencias), list(range(7)))
        self.assertEqual(frequencias[0], '100,00%')
        self.assertEqual(frequencias[6], calcular_frequencia(72, 6))

    def test_data_invalida_abre_em_hoje(self):
        self.client.force_login(self.professor)
        resposta = self._abrir_diario(data='nao-e-data')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Aula.objects.count(), 1)

    def test_lista_de_alunos_igual_a_lista_de_notas(self):
        self.client.force_login(self.professor)
        resposta = self._abrir_diario()
        nomes = [linha['aluno'].first_name for linha in resposta.context['linhas']]
        self.assertEqual(nomes, ['Ana', 'Bruna'])

    def test_aula_nao_realizada_mostra_celulas_travadas(self):
        self.client.force_login(self.professor)
        resposta = self._abrir_diario()
        self.assertContains(resposta, 'período 1: aula não realizada')
        self.assertNotContains(resposta, 'período 1: presente')
        # Enquanto não foi salva como realizada, o interruptor pode ser ligado
        self.assertContains(resposta, 'id="dc-aula-realizada" role="switch" autocomplete="off" >')

    def test_tela_tem_a_falta_justificada_e_o_salvar_com_confirmacao(self):
        self.client.force_login(self.professor)
        resposta = self._abrir_diario()
        self.assertContains(resposta, 'Falta justificada</span>')
        self.assertContains(resposta, 'id="dc-salvar"')
        self.assertContains(resposta, 'id="modalSalvar"')
        self.assertContains(resposta, 'data-nome="o diário de 19/10/2026"')

    # ---------- Presença e falta ----------

    def test_salvar_com_aula_nao_realizada_nao_grava_falta(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA)
        self.client.force_login(self.professor)
        self.assertTrue(self._salvar(aula, False, {(self.matricula_bruna, 1): 'F'})['ok'])
        self.assertFalse(Falta.objects.exists())
        aula.refresh_from_db()
        self.assertFalse(aula.realizada)

    def test_ligar_a_aula_e_marcar_falta_no_mesmo_salvar(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA)
        self.client.force_login(self.professor)
        self.assertTrue(self._salvar(aula, True, {(self.matricula_bruna, 3): 'F'})['ok'])
        aula.refresh_from_db()
        self.assertTrue(aula.realizada)
        self.assertEqual(self._faltas_da_bruna(), (1, '98,61%'))

    def test_salvar_grava_a_falta_e_a_presenca(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.professor)

        self.assertTrue(self._salvar(aula, True, {(self.matricula_bruna, 2): 'F'})['ok'])
        self.assertEqual(
            list(Falta.objects.values_list('matricula_id', 'periodo', 'justificada')),
            [(self.matricula_bruna.id, 2, False)],
        )
        self.assertEqual(self._faltas_da_bruna(), (1, '98,61%'))

        # Salvar de novo não duplica a falta
        self._salvar(aula, True, {(self.matricula_bruna, 2): 'F'})
        self.assertEqual(Falta.objects.count(), 1)

        self._salvar(aula, True, {(self.matricula_bruna, 2): 'P'})
        self.assertFalse(Falta.objects.exists())
        self.assertEqual(self._faltas_da_bruna(), (0, '100,00%'))

    def test_falta_justificada_conta_na_frequencia(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.professor)

        self._salvar(aula, True, {(self.matricula_bruna, 1): 'J'})
        self.assertEqual(list(Falta.objects.values_list('periodo', 'justificada')), [(1, True)])
        self.assertEqual(self._faltas_da_bruna(), (1, '98,61%'))
        resposta = self._abrir_diario()
        self.assertEqual(resposta.context['linhas'][1]['periodos'][0]['estado'], 'J')
        self.assertContains(resposta, 'Bruna, período 1: falta justificada')

        # De justificada para falta e de volta, a mesma falta muda só a marca
        self._salvar(aula, True, {(self.matricula_bruna, 1): 'F'})
        self.assertEqual(list(Falta.objects.values_list('periodo', 'justificada')), [(1, False)])
        self._salvar(aula, True, {(self.matricula_bruna, 1): 'J'})
        self.assertEqual(list(Falta.objects.values_list('periodo', 'justificada')), [(1, True)])

        self._salvar(aula, True, {(self.matricula_bruna, 1): 'P'})
        self.assertFalse(Falta.objects.exists())

    def test_periodo_fora_da_aula_e_estado_invalido_nao_gravam(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.professor)
        self._salvar(aula, True, {
            (self.matricula_bruna, 0): 'F',
            (self.matricula_bruna, 5): 'F',
            (self.matricula_bruna, 1): 'X',
        })
        self.assertFalse(Falta.objects.exists())

    def test_periodo_que_nao_veio_no_envio_fica_como_esta(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        Falta.objects.create(aula=aula, matricula=self.matricula_bruna, periodo=1)
        self.client.force_login(self.professor)
        self._salvar(aula, True, {(self.matricula_ana, 1): 'F'})
        self.assertEqual(Falta.objects.count(), 2)

    def test_salvar_nao_mexe_na_falta_de_matricula_inativa(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        matricula_inativa = Matricula.objects.get(aluno=self.carla)
        Falta.objects.create(aula=aula, matricula=matricula_inativa, periodo=1)
        self.client.force_login(self.professor)
        self._salvar(aula, True, {(matricula_inativa, 1): 'P'})
        self.assertTrue(Falta.objects.filter(matricula=matricula_inativa).exists())

    def test_aula_salva_como_realizada_nao_volta(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        Falta.objects.create(aula=aula, matricula=self.matricula_bruna, periodo=1)
        self.client.force_login(self.professor)

        self.assertTrue(self._salvar(aula, False)['ok'])
        aula.refresh_from_db()
        self.assertTrue(aula.realizada)
        # A falta lançada continua contando
        self.assertEqual(self._faltas_da_bruna(), (1, '98,61%'))
        # O interruptor vem travado na tela
        self.assertContains(self._abrir_diario(), 'id="dc-aula-realizada" role="switch" autocomplete="off" checked disabled>')

    def test_tela_com_a_aula_desligada_nao_mexe_nas_faltas_salvas_por_outra_aba(self):
        # A página abriu antes de a aula ser salva como realizada em outra aba: as células estavam travadas.
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        Falta.objects.create(aula=aula, matricula=self.matricula_bruna, periodo=1)
        self.client.force_login(self.professor)
        self.assertTrue(self._salvar(aula, False, {(self.matricula_bruna, 1): 'P', (self.matricula_ana, 2): 'F'})['ok'])
        self.assertEqual(list(Falta.objects.values_list('matricula_id', 'periodo')), [(self.matricula_bruna.id, 1)])

    def test_tela_guarda_o_estado_de_cada_celula_ao_abrir(self):
        # O Salvar manda só as células que mudaram desde que a página abriu.
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        Falta.objects.create(aula=aula, matricula=self.matricula_bruna, periodo=1, justificada=True)
        self.client.force_login(self.professor)
        resposta = self._abrir_diario()
        self.assertContains(resposta, 'data-estado="J"')
        self.assertContains(resposta, 'data-inicial="J"')

    def test_salvar_mostra_a_mensagem_de_sucesso(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA)
        self.client.force_login(self.professor)
        self._salvar(aula, True)
        self.assertContains(self._abrir_diario(), 'Diário salvo com sucesso!')

    def test_faltas_somam_as_aulas_realizadas(self):
        segunda = Aula.objects.create(turma=self.turma, data='2026-10-12', realizada=True)
        nao_realizada = Aula.objects.create(turma=self.turma, data='2026-10-05')
        Falta.objects.create(aula=segunda, matricula=self.matricula_ana, periodo=1)
        Falta.objects.create(aula=segunda, matricula=self.matricula_ana, periodo=2)
        Falta.objects.create(aula=nao_realizada, matricula=self.matricula_ana, periodo=1)

        self.client.force_login(self.professor)
        linha_ana = self._abrir_diario().context['linhas'][0]
        self.assertEqual((linha_ana['faltas'], linha_ana['frequencia']), (2, '97,22%'))

    def test_frequencia_sem_total_de_periodos(self):
        Disciplina.objects.filter(id=self.disciplina.id).update(total_periodos=None)
        self.client.force_login(self.professor)
        resposta = self._abrir_diario()
        self.assertEqual(resposta.context['linhas'][0]['frequencia'], '-')
        self.assertContains(resposta, 'que ainda não foi informado')

    def test_diario_nao_mexe_nas_notas(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.professor)
        self._abrir_diario()
        self._salvar(aula, True, {(self.matricula_bruna, 1): 'F', (self.matricula_ana, 2): 'J'})
        self._salvar(aula, False)
        self.assertFalse(Nota.objects.exists())


class TotalPeriodosDisciplinaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.professor = User.objects.create_user('carlos', first_name='Carlos')
        cls.professor.groups.add(Group.objects.create(name='professor'))

    def test_cadastrar_com_e_sem_total(self):
        self.client.post(reverse('cadastrar-disciplina'), {'nome': 'Física', 'professor': self.professor.id, 'total_periodos': '72'})
        self.client.post(reverse('cadastrar-disciplina'), {'nome': 'Química', 'professor': self.professor.id, 'total_periodos': ''})
        self.assertEqual(Disciplina.objects.get(nome='Física').total_periodos, 72)
        self.assertIsNone(Disciplina.objects.get(nome='Química').total_periodos)

    def test_cadastrar_recusa_total_invalido(self):
        for valor in ('0', '-5', 'abc', '3000000000'):
            self.client.post(reverse('cadastrar-disciplina'), {'nome': 'Física', 'professor': self.professor.id, 'total_periodos': valor})
        self.assertFalse(Disciplina.objects.exists())

    def test_editar_preenche_e_limpa_o_total(self):
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor)
        url = reverse('editar-disciplina', args=[disciplina.id])

        self.client.post(url, {'nome': 'Física', 'professor': self.professor.id, 'total_periodos': '80'})
        disciplina.refresh_from_db()
        self.assertEqual(disciplina.total_periodos, 80)

        resposta = self.client.post(url, {'nome': 'Física II', 'professor': self.professor.id, 'total_periodos': '0'})
        self.assertContains(resposta, 'O total de períodos deve ser um número inteiro maior que zero.')
        disciplina.refresh_from_db()
        self.assertEqual((disciplina.nome, disciplina.total_periodos), ('Física', 80))

        self.client.post(url, {'nome': 'Física', 'professor': self.professor.id, 'total_periodos': ''})
        disciplina.refresh_from_db()
        self.assertIsNone(disciplina.total_periodos)


def _envio_do_cadastro(matricula, avaliacao, valor):
    """O que a célula do Cadastro de Notas envia ao salvar."""
    return {
        'aluno': matricula.aluno_id, 'turma': matricula.turma_id, 'avaliacao': avaliacao.id,
        'valor': valor, 'so_lancar': '1',
    }


class CadastroNotasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        grupo_aluno = Group.objects.create(name='aluno')

        cls.professor = User.objects.create_user('carlos', first_name='Carlos')
        cls.professor.groups.add(Group.objects.create(name='professor'))

        cls.ana = User.objects.create_user('20260001', first_name='Ana')
        cls.bruna = User.objects.create_user('20260002', first_name='Bruna')
        cls.carla = User.objects.create_user('20260003', first_name='Carla')
        for aluno in (cls.ana, cls.bruna, cls.carla):
            aluno.groups.add(grupo_aluno)

        cls.disciplina = Disciplina.objects.create(nome='Matemática', professor=cls.professor, modo_calculo='aritmetica')
        cls.turma = Turma.objects.create(nome='1A', disciplina=cls.disciplina, ano=2026)
        cls.prova = Avaliacao.objects.create(nome='Prova', disciplina=cls.disciplina)
        cls.trabalho = Avaliacao.objects.create(nome='Trabalho', disciplina=cls.disciplina)
        cls.matricula_ana = Matricula.objects.create(aluno=cls.ana, turma=cls.turma)
        cls.matricula_bruna = Matricula.objects.create(aluno=cls.bruna, turma=cls.turma)
        # Matrícula inativa: não aparece na tabela nem recebe nota
        cls.matricula_carla = Matricula.objects.create(aluno=cls.carla, turma=cls.turma, ativo=False)

    def setUp(self):
        self.client.force_login(self.professor)

    def _abrir(self, turma=None):
        turma = turma or self.turma
        url = reverse('cadastrar-notas')
        self.client.post(url, {'acao': 'selecionar_turma', 'disciplina': turma.disciplina_id, 'turma': turma.id})
        return self.client.get(url)

    def _ja_lancada(self, matricula, avaliacao, valor):
        nota, _ = Nota.objects.get_or_create(matricula=matricula, defaults={'situacao': 'cursando'})
        NotaAvaliacao.objects.create(nota=nota, avaliacao=avaliacao, nota_obtida=valor)
        return nota

    def _lancar(self, matricula, avaliacao, valor):
        return self.client.post(
            reverse('editar-nota-avaliacao-ajax'), _envio_do_cadastro(matricula, avaliacao, valor)
        ).json()

    def _gravada(self, matricula, avaliacao):
        return NotaAvaliacao.objects.get(nota__matricula=matricula, avaliacao=avaliacao).nota_obtida

    def _em_exame(self, matricula=None):
        """Aluno com as duas notas lançadas e média 5, que leva ao exame."""
        matricula = matricula or self.matricula_ana
        nota = self._ja_lancada(matricula, self.prova, 5.0)
        self._ja_lancada(matricula, self.trabalho, 5.0)
        Nota.objects.filter(id=nota.id).update(media_final=5.0, situacao='exame')
        return nota

    def _como_navegador(self):
        """Cliente que exige o token de segurança, a página da turma e o token que ela traz."""
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.professor)
        sessao = cliente.session
        sessao['cadastro_notas_disciplina'] = str(self.disciplina.id)
        sessao['cadastro_notas_turma'] = str(self.turma.id)
        sessao.save()
        pagina = cliente.get(reverse('cadastrar-notas')).content.decode()
        return cliente, pagina, re.search(r'data-csrf="([^"]+)"', pagina).group(1)

    # ---------- Acesso ----------

    def test_exige_login(self):
        self.client.logout()
        for resposta in (
            self.client.get(reverse('cadastrar-notas')),
            self.client.post(reverse('editar-nota-avaliacao-ajax')),
            self.client.post(reverse('lancar-nota-exame-ajax')),
        ):
            self.assertRedirects(resposta, reverse('login'), fetch_redirect_response=False)

    def test_aluno_nao_acessa(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('cadastrar-notas')).status_code, 403)
        self.assertEqual(self.client.post(reverse('editar-nota-avaliacao-ajax')).status_code, 403)
        self.assertEqual(self.client.post(reverse('lancar-nota-exame-ajax')).status_code, 403)
        self.assertFalse(Nota.objects.exists())

    def test_lancamento_so_por_post(self):
        self.assertEqual(self.client.get(reverse('editar-nota-avaliacao-ajax')).status_code, 405)

    # ---------- Filtro das notas ----------

    def _filtrar(self, lancamento):
        return self.client.post(reverse('cadastrar-notas'), {'acao': 'filtrar_notas', 'lancamento': lancamento})

    def _nomes_na_tabela(self):
        return [item['aluno'].first_name for item in self.client.get(reverse('cadastrar-notas')).context['alunos_notas']]

    def _com_tudo_lancado(self, matricula):
        nota = self._ja_lancada(matricula, self.prova, 8.0)
        self._ja_lancada(matricula, self.trabalho, 9.0)
        Nota.objects.filter(id=nota.id).update(media_final=8.5, situacao='aprovado')

    def test_card_de_filtro_so_aparece_com_a_tabela(self):
        self.assertNotContains(self.client.get(reverse('cadastrar-notas')), 'id="filtro-lancamento"')
        resposta = self._abrir()
        self.assertContains(resposta, 'id="filtro-lancamento"')
        self.assertContains(resposta, f'href="{reverse("cadastrar-notas")}?limpar_filtro=1"')

    def test_filtro_fica_guardado_e_a_tela_volta_por_get(self):
        self._abrir()
        resposta = self._filtrar('a_cadastrar')
        self.assertRedirects(resposta, reverse('cadastrar-notas'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['cadastro_notas_lancamento'], 'a_cadastrar')
        self.assertContains(
            self.client.get(reverse('cadastrar-notas')),
            '<option value="a_cadastrar" selected>A cadastrar</option>', html=True,
        )

    def test_filtro_separa_as_notas_a_cadastrar_das_cadastradas(self):
        self._com_tudo_lancado(self.matricula_ana)
        self._ja_lancada(self.matricula_bruna, self.prova, 7.0)
        self._abrir()

        self._filtrar('a_cadastrar')
        self.assertEqual(self._nomes_na_tabela(), ['Bruna'])

        self._filtrar('cadastradas')
        self.assertEqual(self._nomes_na_tabela(), ['Ana'])
        # Cadastradas é só para ver: nenhuma célula abre
        self.assertNotContains(self.client.get(reverse('cadastrar-notas')), 'data-rotulo')

        self._filtrar('')
        self.assertEqual(self._nomes_na_tabela(), ['Ana', 'Bruna'])

    def test_exame_aberto_conta_como_nota_a_cadastrar(self):
        self._em_exame(self.matricula_ana)
        self._com_tudo_lancado(self.matricula_bruna)
        self._abrir()
        self._filtrar('a_cadastrar')
        self.assertEqual(self._nomes_na_tabela(), ['Ana'])
        self._filtrar('cadastradas')
        self.assertEqual(self._nomes_na_tabela(), ['Bruna'])

    def test_filtro_nao_muda_o_aviso_de_todas_lancadas(self):
        self._com_tudo_lancado(self.matricula_ana)
        self._abrir()
        self._filtrar('cadastradas')
        self.assertFalse(self.client.get(reverse('cadastrar-notas')).context['todas_lancadas'])

    def test_filtro_sem_ninguem_continua_mostrando_a_tabela(self):
        self._abrir()
        self._filtrar('cadastradas')
        resposta = self.client.get(reverse('cadastrar-notas'))
        self.assertEqual(resposta.context['alunos_notas'], [])
        self.assertContains(resposta, 'id="TabelaNotas"')
        self.assertContains(resposta, 'js/tabela-notas.js')
        self.assertNotContains(resposta, 'Nenhum aluno matriculado nessa turma')

    def test_filtro_invalido_mostra_todas(self):
        self._abrir()
        self._filtrar('outro')
        resposta = self.client.get(reverse('cadastrar-notas'))
        self.assertEqual(len(resposta.context['alunos_notas']), 2)
        self.assertEqual(resposta.context['lancamento_selecionado'], '')

    def test_limpar_filtros_continua_na_turma_e_trocar_tira_tudo(self):
        self._abrir()
        self._filtrar('a_cadastrar')
        resposta = self.client.get(reverse('cadastrar-notas') + '?limpar_filtro=1')
        self.assertRedirects(resposta, reverse('cadastrar-notas'), fetch_redirect_response=False)
        self.assertNotIn('cadastro_notas_lancamento', self.client.session)
        self.assertEqual(self.client.session['cadastro_notas_turma'], str(self.turma.id))

        self._filtrar('a_cadastrar')
        self.client.get(reverse('cadastrar-notas') + '?limpar=1')
        self.assertNotIn('cadastro_notas_lancamento', self.client.session)
        self.assertNotIn('cadastro_notas_turma', self.client.session)

    # ---------- Tela ----------

    def test_tabela_lista_os_alunos_ativos_da_turma(self):
        resposta = self._abrir()
        self.assertEqual([item['aluno'].first_name for item in resposta.context['alunos_notas']], ['Ana', 'Bruna'])
        self.assertContains(resposta, '20260001')
        self.assertContains(resposta, '20260002')
        self.assertNotContains(resposta, '20260003')

    def test_professor_matriculado_nao_aparece_na_tabela(self):
        Matricula.objects.create(aluno=self.professor, turma=self.turma)
        resposta = self._abrir()
        self.assertEqual([item['aluno'].first_name for item in resposta.context['alunos_notas']], ['Ana', 'Bruna'])

    def test_colunas_da_tabela_com_o_exame_antes_da_media(self):
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        self._ja_lancada(self.matricula_ana, self.trabalho, 5.0)
        Nota.objects.filter(id=nota.id).update(media_final=5.0, nota_exame=8.0, situacao='aprovado')

        resposta = self._abrir()
        pagina = resposta.content.decode()

        cabecalho = re.search(r'<thead>(.*?)</thead>', pagina, re.S).group(1)
        self.assertEqual(
            re.findall(r'<th>\s*(.*?)\s*</th>', cabecalho, re.S),
            ['Matrícula', 'Nome', 'Prova', 'Trabalho', 'Exame', 'MF', 'Situação'],
        )
        # Célula a mais ou a menos em uma linha quebra o DataTables, e com ele o lançamento.
        for linha in re.findall(r'<tr data-aluno.*?</tr>', pagina, re.S):
            self.assertEqual(len(re.findall(r'<td\b', linha)), 7)
        self.assertContains(resposta, '<td class="col-exame">8.0</td>', html=True)
        self.assertNotContains(resposta, 'data-rotulo="Ana, Exame"')
        # Só o duplo clique abre uma célula: nada vem editável ao abrir a tela.
        corpo = re.search(r'<tbody>.*?</tbody>', pagina, re.S).group(0)
        self.assertNotIn('contenteditable', corpo)
        self.assertNotIn('<input', corpo)

    def test_cada_etapa_carrega_o_proprio_script(self):
        self.assertContains(self.client.get(reverse('cadastrar-notas')), '/turmas-por-disciplina/')

        resposta = self._abrir()
        self.assertContains(resposta, 'data-modo="lancar"')
        self.assertContains(resposta, 'js/tabela-notas.js')
        self.assertContains(resposta, 'jquery.dataTables.min.js')

    def test_exame_fica_em_branco_para_quem_esta_em_exame(self):
        nota = self._em_exame()
        self.assertContains(
            self._abrir(),
            f'<td class="col-exame editavel" data-nota="{nota.id}" data-rotulo="Ana, Exame"></td>',
            html=True,
        )

    def test_exame_nao_abre_para_quem_nao_esta_em_exame(self):
        # Ana aprovada, sem exame; Bruna sem nenhuma nota: as duas com traço, como na Lista de Notas.
        nota = self._ja_lancada(self.matricula_ana, self.prova, 9.0)
        self._ja_lancada(self.matricula_ana, self.trabalho, 9.0)
        Nota.objects.filter(id=nota.id).update(media_final=9.0, situacao='aprovado')
        resposta = self._abrir()
        self.assertContains(resposta, '<td class="col-exame">-</td>', count=2, html=True)
        self.assertNotContains(resposta, 'data-nota=')

    def test_valores_da_pagina_lancam_o_exame(self):
        nota = self._em_exame()
        cliente, pagina, token = self._como_navegador()

        celula = re.search(r'data-nota="(\d+)"\s+data-rotulo="Ana, Exame"', pagina)
        endereco = re.search(r'data-url-exame="([^"]+)"', pagina).group(1)

        resposta = cliente.post(endereco, {'id': celula.group(1), 'valor': '8', 'csrfmiddlewaretoken': token}).json()

        self.assertTrue(resposta['ok'])
        nota.refresh_from_db()
        self.assertEqual(nota.nota_exame, 8.0)

    def test_valores_da_pagina_lancam_a_nota_no_aluno_certo(self):
        # O que o navegador enviaria: aluno, turma, avaliação, endereço e token lidos da própria página.
        cliente, pagina, token = self._como_navegador()

        linha = re.search(r'<tr data-aluno="(\d+)">(?:(?!</tr>).)*data-rotulo="Bruna, Trabalho"', pagina, re.S)
        campo = re.search(r'data-avaliacao="(\d+)"\s+data-rotulo="Bruna, Trabalho"', pagina)
        turma = re.search(r'data-turma="(\d+)"', pagina).group(1)
        endereco = re.search(r'data-url-nota="([^"]+)"', pagina).group(1)

        resposta = cliente.post(endereco, {
            'aluno': linha.group(1), 'turma': turma, 'avaliacao': campo.group(1), 'valor': '8', 'so_lancar': '1',
            'csrfmiddlewaretoken': token,
        }).json()

        self.assertTrue(resposta['ok'])
        self.assertEqual(self._gravada(self.matricula_bruna, self.trabalho), 8.0)
        self.assertFalse(NotaAvaliacao.objects.filter(nota__matricula=self.matricula_ana).exists())

    def test_nota_lancada_vem_bloqueada_e_a_que_falta_vem_aberta(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        resposta = self._abrir()
        self.assertContains(resposta, '<td>8.5</td>', html=True)
        self.assertNotContains(resposta, 'data-rotulo="Ana, Prova"')
        self.assertContains(resposta, 'data-rotulo="Ana, Trabalho"')
        self.assertContains(resposta, 'data-rotulo="Bruna, Prova"')
        self.assertContains(resposta, 'data-rotulo="Bruna, Trabalho"')

    def test_nota_zero_vem_bloqueada(self):
        self._ja_lancada(self.matricula_ana, self.prova, 0.0)
        resposta = self._abrir()
        self.assertContains(resposta, '<td>0.0</td>', html=True)
        self.assertNotContains(resposta, 'data-rotulo="Ana, Prova"')

    def test_nota_continua_bloqueada_com_registro_nota_duplicado(self):
        # O banco não impede duas Nota para a mesma matrícula, e a segunda pode ter o registro vazio.
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        segunda = Nota.objects.create(matricula=self.matricula_ana, situacao='cursando')
        NotaAvaliacao.objects.create(nota=segunda, avaliacao=self.prova, nota_obtida=None)
        self.assertNotContains(self._abrir(), 'data-rotulo="Ana, Prova"')

    def test_nota_continua_bloqueada_com_matricula_duplicada(self):
        # Nem duas matrículas ativas do mesmo aluno na mesma turma.
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        Matricula.objects.create(aluno=self.ana, turma=self.turma)
        self.assertNotContains(self._abrir(), 'data-rotulo="Ana, Prova"')

    def test_registro_sem_nota_continua_aberto(self):
        self._ja_lancada(self.matricula_ana, self.prova, None)
        resposta = self._abrir()
        self.assertContains(resposta, 'data-rotulo="Ana, Prova"')

    def test_nota_de_outra_turma_nao_bloqueia_a_celula(self):
        # Aluno repetente: a avaliação é da disciplina, mas a nota de 2025 não é da turma de 2026.
        turma_antiga = Turma.objects.create(nome='1A', disciplina=self.disciplina, ano=2025)
        self._ja_lancada(Matricula.objects.create(aluno=self.ana, turma=turma_antiga), self.prova, 8.5)
        self.assertContains(self._abrir(), 'data-rotulo="Ana, Prova"')

    def test_nota_de_matricula_inativa_nao_bloqueia_a_celula(self):
        # Aluno desvinculado e matriculado de novo na mesma turma.
        self._ja_lancada(Matricula.objects.create(aluno=self.ana, turma=self.turma, ativo=False), self.prova, 8.5)
        self.assertContains(self._abrir(), 'data-rotulo="Ana, Prova"')

    def test_celula_que_falta_vem_em_branco(self):
        # Sem campo, sem cadeado e sem mostrar limite, mesmo em disciplina de soma dos pesos.
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='soma')
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        Avaliacao.objects.create(nome='Prova', valor=3, disciplina=disciplina)
        Matricula.objects.create(aluno=self.ana, turma=turma)
        pagina = self._abrir(turma).content.decode()
        self.assertRegex(pagina, r'<td [^>]*data-rotulo="Ana, Prova"[^>]*></td>')

    def test_com_nota_faltando_nao_avisa_que_todas_foram_lancadas(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        resposta = self._abrir()
        self.assertFalse(resposta.context['todas_lancadas'])
        self.assertContains(resposta, 'id="aviso-todas" class="alert alert-warning d-none"')

    def test_com_todas_lancadas_avisa_e_nao_abre_nenhum_campo(self):
        for matricula in (self.matricula_ana, self.matricula_bruna):
            self._ja_lancada(matricula, self.prova, 8.0)
            self._ja_lancada(matricula, self.trabalho, 6.0)
        resposta = self._abrir()
        self.assertTrue(resposta.context['todas_lancadas'])
        self.assertContains(resposta, 'id="aviso-todas" class="alert alert-warning"')
        self.assertContains(resposta, 'Todas as notas dessa turma já foram lançadas')
        self.assertNotContains(resposta, 'data-rotulo="Ana,')
        self.assertNotContains(resposta, 'data-rotulo="Bruna,')

    def test_exame_pendente_nao_conta_como_todas_lancadas(self):
        nota = self._em_exame()
        self._ja_lancada(self.matricula_bruna, self.prova, 9.0)
        self._ja_lancada(self.matricula_bruna, self.trabalho, 9.0)
        self.assertFalse(self._abrir().context['todas_lancadas'])

        self.client.post(reverse('lancar-nota-exame-ajax'), {'id': nota.id, 'valor': '8'})
        self.assertTrue(self._abrir().context['todas_lancadas'])

    def test_turma_sem_alunos_nao_avisa_que_todas_foram_lancadas(self):
        turma = Turma.objects.create(nome='1C', disciplina=self.disciplina, ano=2026)
        resposta = self._abrir(turma)
        self.assertFalse(resposta.context['todas_lancadas'])
        self.assertContains(resposta, 'Nenhum aluno matriculado nessa turma')

    def test_abrir_a_tela_nao_cria_registros(self):
        self._abrir()
        self.assertFalse(Nota.objects.exists())
        self.assertFalse(NotaAvaliacao.objects.exists())

    def test_situacao_vem_do_registro_nota(self):
        Nota.objects.create(matricula=self.matricula_ana, situacao='reprovado', media_final=2.0)
        resposta = self._abrir()
        item = resposta.context['alunos_notas'][0]
        self.assertEqual((item['media_final'], item['situacao_display']), (2.0, 'Reprovado'))
        self.assertContains(resposta, '<td class="col-mf">2.0</td>', html=True)
        self.assertContains(resposta, '<td class="col-situacao">Reprovado</td>', html=True)

    def test_sem_avaliacoes_orienta_a_cadastrar(self):
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor)
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        self.assertContains(self._abrir(turma), 'Cadastre as avaliações dessa disciplina')

    def test_sessao_com_turma_que_nao_vale_mais_volta_para_a_escolha(self):
        outra_disciplina = Disciplina.objects.create(nome='Física', professor=self.professor)
        turma_inativa = Turma.objects.create(nome='1B', disciplina=self.disciplina, ano=2026, ativo=False)
        for disciplina_id, turma_id in (
            ('abc', 'xyz'),
            (self.disciplina.id, 999999),
            (outra_disciplina.id, self.turma.id),
            (self.disciplina.id, turma_inativa.id),
        ):
            sessao = self.client.session
            sessao['cadastro_notas_disciplina'] = str(disciplina_id)
            sessao['cadastro_notas_turma'] = str(turma_id)
            sessao.save()
            resposta = self.client.get(reverse('cadastrar-notas'))
            self.assertEqual(resposta.status_code, 200)
            self.assertContains(resposta, 'id="gate-disciplina"')

    # ---------- Lançamento ----------

    def test_lanca_a_nota_que_falta(self):
        resposta = self._lancar(self.matricula_ana, self.prova, '8.5')
        self.assertEqual((resposta['ok'], resposta['valor']), (True, '8.5'))
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 8.5)

    def test_nao_altera_nota_ja_lancada(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        resposta = self._lancar(self.matricula_ana, self.prova, '1.5')
        self.assertEqual((resposta['ok'], resposta['ja_lancada'], resposta['valor']), (False, True, '8.5'))
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 8.5)

    def test_nao_altera_nota_zero(self):
        self._ja_lancada(self.matricula_ana, self.prova, 0.0)
        resposta = self._lancar(self.matricula_ana, self.prova, '9')
        self.assertEqual((resposta['ok'], resposta['valor']), (False, '0.0'))
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 0.0)

    def test_lanca_em_registro_sem_nota(self):
        self._ja_lancada(self.matricula_ana, self.prova, None)
        self.assertTrue(self._lancar(self.matricula_ana, self.prova, '7')['ok'])
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 7.0)

    def test_aceita_virgula_ou_ponto(self):
        self.assertTrue(self._lancar(self.matricula_ana, self.prova, '8,5')['ok'])
        self.assertTrue(self._lancar(self.matricula_ana, self.trabalho, ' 7.5 ')['ok'])
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 8.5)
        self.assertEqual(self._gravada(self.matricula_ana, self.trabalho), 7.5)

    def test_nota_vale_zero_e_dez(self):
        self.assertTrue(self._lancar(self.matricula_ana, self.prova, '0')['ok'])
        self.assertTrue(self._lancar(self.matricula_ana, self.trabalho, '10')['ok'])
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 0.0)
        self.assertEqual(self._gravada(self.matricula_ana, self.trabalho), 10.0)

    def test_aceita_mais_de_uma_casa_decimal_como_a_lista_de_notas(self):
        self.assertTrue(self._lancar(self.matricula_ana, self.prova, '7,25')['ok'])
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 7.25)

    def test_recusa_valor_vazio_ou_invalido_sem_gravar(self):
        for valor in ('', '   ', 'abc', '8,5,1', 'nan', 'inf', '-inf', '-1', '10.5'):
            with self.subTest(valor=valor):
                resposta = self._lancar(self.matricula_ana, self.prova, valor)
                self.assertFalse(resposta['ok'])
                self.assertTrue(resposta['erro'])
                self.assertNotIn('ja_lancada', resposta)
        self.assertFalse(Nota.objects.exists())
        self.assertFalse(NotaAvaliacao.objects.exists())

    def _avaliacao_de_peso_3(self):
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='soma')
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        avaliacao = Avaliacao.objects.create(nome='Prova', valor=3, disciplina=disciplina)
        return avaliacao, Matricula.objects.create(aluno=self.ana, turma=turma)

    def test_cadastro_aceita_de_0_a_10_mesmo_com_peso_menor(self):
        avaliacao, matricula = self._avaliacao_de_peso_3()
        self.assertFalse(self._lancar(matricula, avaliacao, '10.5')['ok'])
        self.assertTrue(self._lancar(matricula, avaliacao, '10')['ok'])
        self.assertEqual(self._gravada(matricula, avaliacao), 10.0)

    def test_mf_de_disciplina_de_soma_e_a_soma_das_notas(self):
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='soma')
        turma = Turma.objects.create(nome='1B', disciplina=disciplina, ano=2026)
        prova = Avaliacao.objects.create(nome='Prova', valor=4, disciplina=disciplina)
        trabalho = Avaliacao.objects.create(nome='Trabalho', valor=6, disciplina=disciplina)
        matricula = Matricula.objects.create(aluno=self.ana, turma=turma)

        self._lancar(matricula, prova, '10')
        resposta = self._lancar(matricula, trabalho, '5')

        # 10 + 5, sem o peso: a MF pode passar de 10
        self.assertEqual((resposta['media_final'], resposta['situacao']), ('15.0', 'Aprovado'))

    def test_exemplo_da_configuracao_usa_nota_de_0_a_10(self):
        disciplina = Disciplina.objects.create(nome='Física', professor=self.professor, modo_calculo='soma')
        Avaliacao.objects.create(nome='Prova', valor=4, disciplina=disciplina)
        Avaliacao.objects.create(nome='Trabalho', valor=6, disciplina=disciplina)

        resposta = self.client.post(reverse('configurar-avaliacoes'), {'disciplina': disciplina.id, 'modo': 'soma'}, follow=True)

        self.assertEqual(resposta.context['resumo_exemplo']['formula_texto'], '8,0 + 8,0 = 16,0')

    def test_lista_aceita_de_0_a_10_mesmo_com_peso_menor(self):
        avaliacao, matricula = self._avaliacao_de_peso_3()
        self._ja_lancada(matricula, avaliacao, 2.0)
        envio = {'aluno': self.ana.id, 'avaliacao': avaliacao.id, 'turma': matricula.turma_id}
        url = reverse('editar-nota-avaliacao-ajax')
        self.assertFalse(self.client.post(url, {**envio, 'valor': '10.5'}).json()['ok'])
        self.assertTrue(self.client.post(url, {**envio, 'valor': '8'}).json()['ok'])
        self.assertEqual(self._gravada(matricula, avaliacao), 8.0)

    def test_tela_de_edicao_mostra_a_nota_com_duas_casas(self):
        # As notas convertidas têm duas casas: mostrando uma, salvar arredondaria a nota sem aviso.
        nota = self._ja_lancada(self.matricula_ana, self.prova, 7.67)
        self._ja_lancada(self.matricula_ana, self.trabalho, 8.0)
        resposta = self.client.get(reverse('editar-nota', args=[nota.id]))
        self.assertContains(resposta, 'value="7.67"')
        self.assertContains(resposta, 'value="8"')
        self.assertNotContains(resposta, 'step="0.1"')

    def test_tela_de_edicao_aceita_de_0_a_10_mesmo_com_peso_menor(self):
        avaliacao, matricula = self._avaliacao_de_peso_3()
        nota = self._ja_lancada(matricula, avaliacao, 2.0)
        url = reverse('editar-nota', args=[nota.id])
        self.client.post(url, {f'nota_avaliacao_{avaliacao.id}': '10.5'})
        self.assertEqual(self._gravada(matricula, avaliacao), 2.0)
        self.client.post(url, {f'nota_avaliacao_{avaliacao.id}': '8'})
        self.assertEqual(self._gravada(matricula, avaliacao), 8.0)

    def test_mf_e_situacao_so_saem_com_todas_as_notas(self):
        resposta = self._lancar(self.matricula_ana, self.prova, '8')
        self.assertEqual((resposta['media_final'], resposta['situacao']), ('-', 'Cursando'))

        resposta = self._lancar(self.matricula_ana, self.trabalho, '6')
        self.assertEqual((resposta['media_final'], resposta['situacao']), ('7.0', 'Aprovado'))

        nota = Nota.objects.get(matricula=self.matricula_ana)
        self.assertEqual((nota.media_final, nota.situacao), (7.0, 'aprovado'))

    def test_ao_cair_em_exame_a_resposta_abre_o_exame(self):
        resposta = self._lancar(self.matricula_ana, self.prova, '5')
        self.assertFalse(resposta['exame_aberto'])

        resposta = self._lancar(self.matricula_ana, self.trabalho, '5')
        self.assertEqual((resposta['situacao'], resposta['exame_aberto']), ('Exame', True))
        self.assertEqual(resposta['nota_id'], Nota.objects.get(matricula=self.matricula_ana).id)

    def test_quem_passa_direto_nao_abre_o_exame(self):
        self._lancar(self.matricula_ana, self.prova, '9')
        resposta = self._lancar(self.matricula_ana, self.trabalho, '9')
        self.assertEqual((resposta['situacao'], resposta['exame_aberto']), ('Aprovado', False))

    def test_exame_lancado_mostra_o_resultado_e_nao_altera_depois(self):
        nota = self._em_exame()
        url = reverse('lancar-nota-exame-ajax')

        # (5 + 8.5) / 2 = 6.75, a partir de 6 fica aprovado
        resposta = self.client.post(url, {'id': nota.id, 'valor': '8.5'}).json()
        self.assertEqual((resposta['ok'], resposta['situacao'], resposta['nota_exame']), (True, 'Aprovado', '8.5'))

        self.assertFalse(self.client.post(url, {'id': nota.id, 'valor': '2'}).json()['ok'])
        nota.refresh_from_db()
        self.assertEqual((nota.nota_exame, nota.situacao, nota.media_final), (8.5, 'aprovado', 5.0))

    def test_exame_aceita_virgula(self):
        nota = self._em_exame()
        self.assertTrue(self.client.post(reverse('lancar-nota-exame-ajax'), {'id': nota.id, 'valor': '8,5'}).json()['ok'])
        nota.refresh_from_db()
        self.assertEqual(nota.nota_exame, 8.5)

    def test_exame_recusa_valor_que_nao_e_nota(self):
        nota = self._em_exame()
        for valor in ('nan', 'inf', 'abc', '-1', '10.5', ''):
            with self.subTest(valor=valor):
                resposta = self.client.post(reverse('lancar-nota-exame-ajax'), {'id': nota.id, 'valor': valor}).json()
                self.assertFalse(resposta['ok'])
        nota.refresh_from_db()
        self.assertEqual((nota.nota_exame, nota.situacao), (None, 'exame'))

    def test_nota_recusada_nao_mexe_na_media_nem_no_exame(self):
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        self._ja_lancada(self.matricula_ana, self.trabalho, 5.0)
        Nota.objects.filter(id=nota.id).update(media_final=5.0, nota_exame=8.0, situacao='aprovado')

        resposta = self._lancar(self.matricula_ana, self.prova, '10')

        self.assertEqual(
            (resposta['media_final'], resposta['situacao'], resposta['nota_exame']),
            ('5.0', 'Aprovado', '8.0'),
        )
        nota.refresh_from_db()
        self.assertEqual((nota.media_final, nota.nota_exame, nota.situacao), (5.0, 8.0, 'aprovado'))

    def test_lanca_nota_nova_em_aluno_com_exame_lancado_e_mantem_o_exame(self):
        # Avaliação criada depois do exame: a nota nova só entra por aqui, e a média muda como em qualquer correção.
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        Nota.objects.filter(id=nota.id).update(media_final=5.0, nota_exame=8.0, situacao='aprovado')

        resposta = self._lancar(self.matricula_ana, self.trabalho, '6')

        self.assertTrue(resposta['ok'])
        self.assertEqual(self._gravada(self.matricula_ana, self.trabalho), 6.0)
        # (5 + 6) / 2 = 5,5 cai no exame, e com o exame 8: (5,5 + 8) / 2 = 6,75
        self.assertEqual((resposta['situacao'], resposta['nota_exame'], resposta['exame_aberto']), ('Aprovado', '8.0', False))
        nota.refresh_from_db()
        self.assertEqual((nota.media_final, nota.nota_exame, nota.situacao), (5.5, 8.0, 'aprovado'))

    def test_nao_lanca_em_matricula_ou_avaliacao_que_nao_valem(self):
        outra_disciplina = Disciplina.objects.create(nome='Física', professor=self.professor)
        avaliacao_outra = Avaliacao.objects.create(nome='Prova', disciplina=outra_disciplina)
        turma_inativa = Turma.objects.create(nome='1B', disciplina=self.disciplina, ano=2026, ativo=False)
        Matricula.objects.create(aluno=self.ana, turma=turma_inativa)

        for aluno_id, turma_id, avaliacao_id in (
            (self.carla.id, self.turma.id, self.prova.id),
            (self.ana.id, turma_inativa.id, self.prova.id),
            (self.ana.id, self.turma.id, avaliacao_outra.id),
            ('abc', self.turma.id, self.prova.id),
            (self.ana.id, 'abc', self.prova.id),
            (self.ana.id, self.turma.id, 'abc'),
            (999999, self.turma.id, self.prova.id),
            (self.ana.id, self.turma.id, 999999),
        ):
            with self.subTest(aluno=aluno_id, turma=turma_id, avaliacao=avaliacao_id):
                resposta = self.client.post(reverse('editar-nota-avaliacao-ajax'), {
                    'aluno': aluno_id, 'turma': turma_id, 'avaliacao': avaliacao_id, 'valor': '8', 'so_lancar': '1',
                }).json()
                self.assertFalse(resposta['ok'])
                self.assertTrue(resposta['erro'])
        self.assertFalse(NotaAvaliacao.objects.exists())

    def test_formulario_antigo_nao_altera_nem_apaga_nota(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        self._abrir()
        for valor in ('1.5', ''):
            self.client.post(reverse('cadastrar-notas'), {
                'acao': 'salvar_notas', 'aluno': self.ana.id, f'nota_avaliacao_{self.prova.id}': valor,
            })
            self.assertEqual(self._gravada(self.matricula_ana, self.prova), 8.5)

    # ---------- Lista de Notas: a mesma tabela e o mesmo salvamento, para corrigir ----------

    def _salvar_pela_lista(self, avaliacao, valor):
        return self.client.post(reverse('editar-nota-avaliacao-ajax'), {
            'aluno': self.ana.id, 'avaliacao': avaliacao.id, 'turma': self.turma.id, 'valor': valor,
        }).json()

    def _com_exame_lancado(self):
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        self._ja_lancada(self.matricula_ana, self.trabalho, 5.0)
        Nota.objects.filter(id=nota.id).update(media_final=5.0, nota_exame=8.0, situacao='aprovado')
        return nota

    def test_lista_usa_a_mesma_tabela_com_as_notas_lancadas_abertas(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        resposta = self.client.post(reverse('lista-notas'), {'disciplina': self.disciplina.id, 'turma': self.turma.id}, follow=True)
        pagina = resposta.content.decode()

        self.assertContains(resposta, 'data-modo="editar"')
        self.assertContains(resposta, 'js/tabela-notas.js')
        cabecalho = re.search(r'<thead>(.*?)</thead>', pagina, re.S).group(1)
        self.assertEqual(
            re.findall(r'<th>\s*(.*?)\s*</th>', cabecalho, re.S),
            ['Matrícula', 'Nome', 'Prova', 'Trabalho', 'MF', 'Situação', 'Exame', 'Ações'],
        )
        for linha in re.findall(r'<tr data-aluno.*?</tr>', pagina, re.S):
            self.assertEqual(len(re.findall(r'<td\b', linha)), 8)
        self.assertRegex(pagina, r'<td class="editavel"[^>]*data-rotulo="Ana, Prova"[^>]*>8\.5</td>')

    def test_lista_garante_o_registro_nota_para_editar_e_excluir(self):
        resposta = self.client.post(reverse('lista-notas'), {'disciplina': self.disciplina.id, 'turma': self.turma.id}, follow=True)
        nota_ana = Nota.objects.get(matricula=self.matricula_ana)
        self.assertContains(resposta, reverse('editar-nota', args=[nota_ana.id]))
        self.assertTrue(Nota.objects.filter(matricula=self.matricula_bruna).exists())
        self.assertFalse(Nota.objects.filter(matricula=self.matricula_carla).exists())

    def test_lista_de_notas_continua_corrigindo_nota_lancada(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        resposta = self._salvar_pela_lista(self.prova, '9')
        self.assertTrue(resposta['ok'])
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 9.0)

    def test_lista_sem_mudar_a_nota_nao_apaga_o_exame(self):
        nota = self._com_exame_lancado()
        for valor in ('5', '5.0', '5,0'):
            with self.subTest(valor=valor):
                self.assertTrue(self._salvar_pela_lista(self.prova, valor)['ok'])
                nota.refresh_from_db()
                self.assertEqual((nota.nota_exame, nota.situacao), (8.0, 'aprovado'))

    def test_lista_mudando_a_nota_recalcula_a_media_e_mantem_o_exame(self):
        nota = self._com_exame_lancado()
        resposta = self._salvar_pela_lista(self.prova, '6')
        # (6 + 5) / 2 = 5,5 cai no exame, e com o exame 8: (5,5 + 8) / 2 = 6,75
        self.assertEqual((resposta['media_final'], resposta['situacao'], resposta['nota_exame']), ('5.5', 'Aprovado', '8.0'))
        nota.refresh_from_db()
        self.assertEqual((nota.media_final, nota.nota_exame, nota.situacao), (5.5, 8.0, 'aprovado'))

    def test_lista_media_de_7_com_exame_lancado_continua_decidida_pelo_exame(self):
        nota = self._com_exame_lancado()
        Nota.objects.filter(id=nota.id).update(nota_exame=2.0, situacao='reprovado')
        resposta = self._salvar_pela_lista(self.prova, '9')
        # (9 + 5) / 2 = 7, mas quem já fez o exame não volta para as notas: (7 + 2) / 2 = 4,5
        self.assertEqual((resposta['media_final'], resposta['situacao'], resposta['nota_exame']), ('7.0', 'Reprovado', '2.0'))
        nota.refresh_from_db()
        self.assertEqual((nota.media_final, nota.nota_exame, nota.situacao), (7.0, 2.0, 'reprovado'))

    def test_lista_nao_lanca_nota_nova_com_exame_lancado(self):
        # Na Lista a nota em branco é só para ver: nota nova entra pelo Cadastro.
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        Nota.objects.filter(id=nota.id).update(media_final=5.0, nota_exame=8.0, situacao='aprovado')
        self.assertFalse(self._salvar_pela_lista(self.trabalho, '6')['ok'])
        nota.refresh_from_db()
        self.assertEqual(nota.nota_exame, 8.0)

    def test_lista_limpando_a_celula_apaga_a_nota(self):
        self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        resposta = self._salvar_pela_lista(self.prova, '')
        self.assertEqual((resposta['ok'], resposta['valor']), (True, ''))
        self.assertIsNone(self._gravada(self.matricula_ana, self.prova))

    # ---------- Tela de edição ----------

    def test_tela_de_edicao_continua_corrigindo_nota_lancada(self):
        nota = self._ja_lancada(self.matricula_ana, self.prova, 8.5)
        url = reverse('editar-nota', args=[nota.id])
        resposta = self.client.get(url)
        self.assertContains(resposta, f'name="nota_avaliacao_{self.prova.id}"')
        self.assertContains(resposta, "getElementById('media_final')")

        self.client.post(url, {f'nota_avaliacao_{self.prova.id}': '9'})
        self.assertEqual(self._gravada(self.matricula_ana, self.prova), 9.0)

    def _html_da_edicao(self):
        nota, _ = Nota.objects.get_or_create(matricula=self.matricula_ana, defaults={'situacao': 'cursando'})
        return self.client.get(reverse('editar-nota', args=[nota.id])).content.decode()

    def test_previa_da_edicao_usa_o_modo_da_disciplina(self):
        for modo in ('soma', 'ponderada', 'aritmetica'):
            with self.subTest(modo=modo):
                Disciplina.objects.filter(id=self.disciplina.id).update(modo_calculo=modo)
                self.assertIn(f'const MODO_CALCULO = "{modo}";', self._html_da_edicao())

    def test_previa_da_edicao_faz_a_conta_do_calculo(self):
        # A prévia é JavaScript, mas essas contas também são Python válido: dá para comparar com o servidor.
        html = self._html_da_edicao()
        for modo, soma in (('soma', 'somaPontos'), ('ponderada', 'somaPercentual')):
            with self.subTest(modo=modo):
                conta = re.search(rf'{soma} \+= (.+);', html).group(1)
                esperado = calcular_nota_final(Disciplina(modo_calculo=modo), [(Avaliacao(valor=3), 8)])
                self.assertEqual(eval(conta, {'val': 8, 'peso': 3}), esperado)
        self.assertIn('{ soma: somaPontos, ponderada: somaPercentual, aritmetica: somaSimples / count }', html)

    def test_previa_da_edicao_usa_os_limites_da_situacao(self):
        # Vale para qualquer limite escrito na prévia: precisa ser onde classificar() muda de situação.
        html = self._html_da_edicao()
        limites = re.findall(r"media >= ([\d.]+)\) \{\s*situacaoInput\.value = '(\w+)'", html)
        self.assertEqual(len(limites), 2)
        # A prévia testa os limites nessa ordem: o menor primeiro nunca deixaria chegar no maior.
        self.assertGreater(float(limites[0][0]), float(limites[1][0]))
        for limite, situacao in limites:
            with self.subTest(limite=limite):
                self.assertEqual(SITUACAO_TEXTO[classificar(float(limite))], situacao)
                self.assertNotEqual(SITUACAO_TEXTO[classificar(float(limite) - 0.01)], situacao)

    def test_previa_da_edicao_sabe_a_nota_gravada(self):
        # O campo mostra a nota arredondada: a prévia compara com a gravada, como o salvar.
        self._ja_lancada(self.matricula_ana, self.prova, 7.125)
        html = self._html_da_edicao()
        self.assertIn('data-gravada="7.125"', html)
        self.assertIn('data-gravada=""', html)

    def test_previa_e_salvar_concordam_no_que_conta_como_mudanca(self):
        # Se discordarem, a prévia mostra a situação gravada e o salvar recalcula, ou o contrário.
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        Nota.objects.filter(id=nota.id).update(situacao='aprovado', media_final=5.0, nota_exame=8.0)
        limite = float(re.search(r'Math\.abs\(digitada - gravada\) < ([\d.]+)', self._html_da_edicao()).group(1))
        url = reverse('editar-nota', args=[nota.id])
        campo = f'nota_avaliacao_{self.prova.id}'

        self.client.post(url, {campo: str(5.0 + limite - 0.0001)})
        nota.refresh_from_db()
        self.assertEqual((nota.media_final, nota.situacao), (5.0, 'aprovado'))

        self.client.post(url, {campo: str(5.0 + limite + 0.0001)})
        nota.refresh_from_db()
        # Recalculou: sem a nota do Trabalho não há média
        self.assertEqual((nota.media_final, nota.situacao), (None, 'cursando'))

    def test_previa_da_edicao_usa_o_exame_lancado(self):
        # Mudando uma nota, quem já fez o exame fica com o resultado dele, como no salvar.
        nota = self._ja_lancada(self.matricula_ana, self.prova, 5.0)
        Nota.objects.filter(id=nota.id).update(situacao='aprovado', media_final=5.0, nota_exame=8.0)
        html = self._html_da_edicao()
        self.assertRegex(html, r'<input[^>]*id="situacao"[^>]*data-exame="8.0"')
        self.assertIn('const exameGravado = parseFloat(situacaoInput.dataset.exame);', html)

        conta = re.search(r"arredondar\((\(media \+ exameGravado\) / 2)\) >= ([\d.]+) \? '(\w+)' : '(\w+)'", html)
        expressao, limite, passou, nao_passou = conta.groups()
        for media, exame in ((5.0, 7.0), (5.0, 6.98), (4.0, 8.0), (6.99, 5.0)):
            with self.subTest(media=media, exame=exame):
                resultado, situacao = calcular_pos_exame(media, exame)
                self.assertEqual(round(eval(expressao, {'media': media, 'exameGravado': exame}), 2), resultado)
                self.assertEqual(passou if resultado >= float(limite) else nao_passou, SITUACAO_TEXTO[situacao])

        # Quem fez o exame não volta para os limites das notas: o exame é olhado antes deles.
        self.assertLess(html.index('if (!isNaN(exameGravado))'), html.index('media >= 7.0'))

        Nota.objects.filter(id=nota.id).update(nota_exame=None)
        self.assertRegex(self._html_da_edicao(), r'<input[^>]*id="situacao"[^>]*data-exame=""')


class LancamentoSimultaneoTests(TransactionTestCase):
    REPETICOES = 8

    def _lancar_ao_mesmo_tempo(self, professor, envios):
        """Cada envio roda na própria thread e conexão, e todos saem juntos."""
        largada = threading.Barrier(len(envios))
        respostas = []

        def enviar(dados):
            cliente = Client()
            cliente.force_login(professor)
            try:
                largada.wait()
                respostas.append(cliente.post(reverse('editar-nota-avaliacao-ajax'), dados).json())
            finally:
                connections.close_all()

        threads = [threading.Thread(target=enviar, args=(dados,)) for dados in envios]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return respostas

    def setUp(self):
        self.professor = User.objects.create_user('carlos', first_name='Carlos')
        self.professor.groups.add(Group.objects.create(name='professor'))
        self.disciplina = Disciplina.objects.create(nome='Matemática', professor=self.professor, modo_calculo='aritmetica')
        self.turma = Turma.objects.create(nome='1A', disciplina=self.disciplina, ano=2026)
        self.prova = Avaliacao.objects.create(nome='Prova', disciplina=self.disciplina)
        self.trabalho = Avaliacao.objects.create(nome='Trabalho', disciplina=self.disciplina)

    def test_duas_notas_do_mesmo_aluno_ao_mesmo_tempo(self):
        for numero in range(self.REPETICOES):
            aluno = User.objects.create_user(f'aluno{numero}', first_name='Ana')
            matricula = Matricula.objects.create(aluno=aluno, turma=self.turma)

            respostas = self._lancar_ao_mesmo_tempo(self.professor, [
                _envio_do_cadastro(matricula, self.prova, '8'),
                _envio_do_cadastro(matricula, self.trabalho, '6'),
            ])

            self.assertEqual([resposta['ok'] for resposta in respostas], [True, True])
            nota = Nota.objects.get(matricula=matricula)
            self.assertEqual((nota.media_final, nota.situacao), (7.0, 'aprovado'))
            self.assertEqual(NotaAvaliacao.objects.filter(nota=nota).count(), 2)

    def test_a_mesma_nota_ao_mesmo_tempo_grava_so_a_primeira(self):
        for numero in range(self.REPETICOES):
            aluno = User.objects.create_user(f'aluno{numero}', first_name='Ana')
            matricula = Matricula.objects.create(aluno=aluno, turma=self.turma)

            respostas = self._lancar_ao_mesmo_tempo(self.professor, [
                _envio_do_cadastro(matricula, self.prova, '8'),
                _envio_do_cadastro(matricula, self.prova, '6'),
            ])

            aceitas = [resposta for resposta in respostas if resposta['ok']]
            recusadas = [resposta for resposta in respostas if not resposta['ok']]
            self.assertEqual((len(aceitas), len(recusadas)), (1, 1))
            gravada = NotaAvaliacao.objects.get(nota__matricula=matricula, avaliacao=self.prova).nota_obtida
            self.assertEqual(str(gravada), aceitas[0]['valor'])
            self.assertEqual(recusadas[0]['valor'], aceitas[0]['valor'])
