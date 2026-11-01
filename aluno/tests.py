from django.contrib.auth.models import User, Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from aluno.calculo_frequencia import calcular_frequencia
from aluno.models import Disciplina, Turma, Matricula, Nota, Aula, Falta


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

    def _marcar(self, aula, matricula, periodo, falta):
        return self.client.post(reverse('marcar-falta-ajax'), {
            'aula': aula.id, 'matricula': matricula.id, 'periodo': periodo, 'falta': '1' if falta else '0',
        }).json()

    def _aula_realizada(self, aula, realizada):
        return self.client.post(reverse('aula-realizada-ajax'), {
            'aula': aula.id, 'realizada': '1' if realizada else '0',
        }).json()

    # ---------- Acesso ----------

    def test_exige_login(self):
        resposta = self.client.get(reverse('diario-classe'))
        self.assertRedirects(resposta, reverse('login'), fetch_redirect_response=False)

    def test_aluno_nao_acessa(self):
        self.client.force_login(self.bruna)
        self.assertEqual(self.client.get(reverse('diario-classe')).status_code, 403)
        self.assertEqual(self.client.post(reverse('marcar-falta-ajax')).status_code, 403)
        self.assertEqual(self.client.post(reverse('aula-realizada-ajax')).status_code, 403)

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

    def test_ajax_em_aula_de_outro_professor(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.outro_professor)
        self.assertFalse(self._marcar(aula, self.matricula_bruna, 1, True)['ok'])
        self.assertFalse(self._aula_realizada(aula, False)['ok'])
        self.assertFalse(Falta.objects.exists())
        aula.refresh_from_db()
        self.assertTrue(aula.realizada)

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

    # ---------- Presença e falta ----------

    def test_nao_marca_falta_com_aula_nao_realizada(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA)
        self.client.force_login(self.professor)
        self.assertFalse(self._marcar(aula, self.matricula_bruna, 1, True)['ok'])
        self.assertFalse(Falta.objects.exists())

    def test_marcar_e_desmarcar_falta(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.professor)

        resposta = self._marcar(aula, self.matricula_bruna, 2, True)
        self.assertEqual((resposta['ok'], resposta['faltas'], resposta['frequencia']), (True, 1, '98,61%'))
        self.assertTrue(Falta.objects.filter(aula=aula, matricula=self.matricula_bruna, periodo=2).exists())

        # Repetir o envio não duplica a falta
        self.assertEqual(self._marcar(aula, self.matricula_bruna, 2, True)['faltas'], 1)

        resposta = self._marcar(aula, self.matricula_bruna, 2, False)
        self.assertEqual((resposta['faltas'], resposta['frequencia']), (0, '100,00%'))
        self.assertFalse(Falta.objects.exists())

    def test_periodo_fora_da_aula(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        self.client.force_login(self.professor)
        self.assertFalse(self._marcar(aula, self.matricula_bruna, 0, True)['ok'])
        self.assertFalse(self._marcar(aula, self.matricula_bruna, 5, True)['ok'])
        self.assertFalse(Falta.objects.exists())

    def test_desligar_a_aula_guarda_as_faltas_mas_nao_conta(self):
        aula = Aula.objects.create(turma=self.turma, data=self.DATA, realizada=True)
        Falta.objects.create(aula=aula, matricula=self.matricula_bruna, periodo=1)
        self.client.force_login(self.professor)

        resposta = self._aula_realizada(aula, False)
        self.assertEqual(resposta['alunos'][str(self.matricula_bruna.id)], {'faltas': 0, 'frequencia': '100,00%'})
        self.assertEqual(Falta.objects.count(), 1)

        resposta = self._aula_realizada(aula, True)
        self.assertEqual(resposta['alunos'][str(self.matricula_bruna.id)], {'faltas': 1, 'frequencia': '98,61%'})

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
        self._marcar(aula, self.matricula_bruna, 1, True)
        self._aula_realizada(aula, False)
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
