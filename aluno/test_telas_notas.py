import re
from datetime import datetime
from unittest import mock

from django.contrib.auth.models import User, Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from reportlab.pdfgen.canvas import Canvas

from aluno.calculo_notas import _fmt_pt, montar_resumo_formula
from aluno.models import Disciplina, Turma, Matricula, Nota, Avaliacao, NotaAvaliacao


class FormatoDaNotaTests(SimpleTestCase):
    def test_mostra_a_segunda_casa_so_quando_ela_existe(self):
        casos = {6.99: '6,99', 3.99: '3,99', 7.25: '7,25', 8.0: '8,0', 2.5: '2,5', 0: '0,0', 10.0: '10,0'}
        for valor, texto in casos.items():
            self.assertEqual(_fmt_pt(valor), texto)

    def test_formula_nao_arredonda_a_media_de_fronteira(self):
        disciplina = Disciplina(modo_calculo='ponderada')
        resumo = montar_resumo_formula(disciplina, [(Avaliacao(valor=60), 6.99), (Avaliacao(valor=40), 6.99)])
        self.assertEqual(resumo['formula_texto'], '6,99 × 60,0% + 6,99 × 40,0% = 6,99')


class DadosDasTelas(TestCase):
    """Uma aluna na 1A, em uma disciplina de cada modo de cálculo e uma sem avaliação."""

    @classmethod
    def setUpTestData(cls):
        cls.ano = datetime.now().year
        cls.professor = User.objects.create_user('prof', first_name='Prof', password='senha-do-prof')
        cls.professor.groups.add(Group.objects.get_or_create(name='professor')[0])
        cls.aluna = User.objects.create_user('ana', first_name='Ana', password='senha-da-ana')
        cls.aluna.groups.add(Group.objects.get_or_create(name='aluno')[0])

        cls.matematica = Disciplina.objects.create(nome='Matemática', professor=cls.professor, modo_calculo='ponderada')
        cls.prova = Avaliacao.objects.create(nome='Prova', tipo='prova', valor=60, disciplina=cls.matematica)
        cls.trabalho = Avaliacao.objects.create(nome='Trabalho', tipo='trabalho', valor=40, disciplina=cls.matematica)

        cls.portugues = Disciplina.objects.create(nome='Português', professor=cls.professor, modo_calculo='soma')
        Avaliacao.objects.create(nome='Redação', tipo='prova', valor=10, disciplina=cls.portugues)

        cls.historia = Disciplina.objects.create(nome='História', professor=cls.professor, modo_calculo='aritmetica')
        Avaliacao.objects.create(nome='Prova', tipo='prova', valor=None, disciplina=cls.historia)

        cls.educacao_fisica = Disciplina.objects.create(nome='Educação física', professor=cls.professor)

        cls.matriculas = {}
        for disciplina in (cls.matematica, cls.portugues, cls.historia, cls.educacao_fisica):
            turma = Turma.objects.create(nome='1A', disciplina=disciplina, ano=cls.ano)
            cls.matriculas[disciplina.nome] = Matricula.objects.create(aluno=cls.aluna, turma=turma)

    def _nota(self, disciplina, situacao='aprovado', media_final=8.0, nota_exame=None):
        return Nota.objects.create(
            matricula=self.matriculas[disciplina.nome], situacao=situacao, media_final=media_final, nota_exame=nota_exame
        )

    def _todas_aprovadas(self):
        for disciplina in (self.matematica, self.portugues, self.historia):
            self._nota(disciplina)


class BoletimComDisciplinaSemNotaTests(DadosDasTelas):
    def setUp(self):
        self.client.force_login(self.aluna)

    def _boletim(self):
        return self.client.get(reverse('minhas-notas'))

    def test_disciplina_sem_registro_nao_deixa_fechar_o_ano(self):
        self._nota(self.matematica)
        resposta = self._boletim()
        self.assertEqual(resposta.context['situacao_final'], 'em_andamento')
        self.assertEqual(resposta.context['total_cursando'], 2)

    def test_disciplina_sem_registro_aparece_como_cursando(self):
        self._nota(self.matematica)
        resposta = self._boletim()
        self.assertContains(resposta, '<td>Português</td>', html=True)
        self.assertContains(resposta, '<td>História</td>', html=True)
        self.assertContains(resposta, '<span class="badge-situacao badge-cursando">Cursando</span>', count=2, html=True)

    def test_disciplina_com_varias_avaliacoes_aparece_uma_vez_so(self):
        self._nota(self.portugues)
        self._nota(self.historia)
        resposta = self._boletim()
        self.assertEqual(resposta.context['total_cursando'], 1)
        self.assertContains(resposta, '<td>Matemática</td>', count=1, html=True)

    def test_disciplina_sem_avaliacao_nao_impede_fechar_o_ano(self):
        self._todas_aprovadas()
        resposta = self._boletim()
        self.assertEqual(resposta.context['situacao_final'], 'aprovado')
        self.assertNotContains(resposta, 'Educação física')

    def test_aluno_sem_nenhum_registro_ve_em_andamento_e_nao_tabela_vazia(self):
        resposta = self._boletim()
        self.assertContains(resposta, 'Em andamento')
        self.assertContains(resposta, '3 cursando')
        self.assertNotContains(resposta, 'Nenhuma nota encontrada')

    def test_aluno_sem_matricula_continua_sem_nota(self):
        outra = User.objects.create_user('bia', password='senha-da-bia')
        self.client.force_login(outra)
        self.assertContains(self._boletim(), 'Nenhuma nota encontrada')

    def _com_matematica_e_historia_aprovadas(self):
        self._nota(self.matematica)
        self._nota(self.historia)

    def test_matricula_inativa_nao_conta(self):
        self._com_matematica_e_historia_aprovadas()
        Matricula.objects.filter(id=self.matriculas['Português'].id).update(ativo=False)
        self.assertEqual(self._boletim().context['situacao_final'], 'aprovado')

    def test_turma_inativa_nao_conta(self):
        self._com_matematica_e_historia_aprovadas()
        Turma.objects.filter(disciplina=self.portugues).update(ativo=False)
        self.assertEqual(self._boletim().context['situacao_final'], 'aprovado')

    def test_disciplina_inativa_nao_conta(self):
        self._com_matematica_e_historia_aprovadas()
        Disciplina.objects.filter(id=self.portugues.id).update(ativo=False)
        self.assertEqual(self._boletim().context['situacao_final'], 'aprovado')

    def test_matricula_repetida_com_nota_nao_conta_como_cursando(self):
        self._todas_aprovadas()
        Matricula.objects.create(aluno=self.aluna, turma=self.matriculas['Português'].turma)
        self.assertEqual(self._boletim().context['situacao_final'], 'aprovado')

    def test_cada_ano_conta_so_as_suas_disciplinas(self):
        self._todas_aprovadas()
        turma_antiga = Turma.objects.create(nome='1A', disciplina=self.matematica, ano=self.ano - 1)
        Matricula.objects.create(aluno=self.aluna, turma=turma_antiga)
        self.assertEqual(self._boletim().context['situacao_final'], 'aprovado')

        resposta = self.client.post(reverse('minhas-notas'), {'ano': str(self.ano - 1)}, follow=True)
        self.assertEqual(resposta.context['situacao_final'], 'em_andamento')
        self.assertContains(resposta, '<td>Matemática</td>', count=1, html=True)
        self.assertNotContains(resposta, 'Português</td>')


class PdfDoBoletimTests(DadosDasTelas):
    def setUp(self):
        self.client.force_login(self.aluna)

    def _textos_do_pdf(self, consulta=''):
        textos = []
        original = Canvas.drawString

        def registrar(canvas, x, y, texto, *args, **kwargs):
            textos.append(texto)
            return original(canvas, x, y, texto, *args, **kwargs)

        with mock.patch.object(Canvas, 'drawString', registrar):
            resposta = self.client.get(reverse('gerar-relatorio') + consulta)
        self.assertEqual(resposta.status_code, 200)
        return textos

    def test_pdf_sai_do_ano_que_esta_na_tela(self):
        # A seta voltar pode mostrar outro ano que o guardado: o link de Download leva o ano da tela.
        self.client.post(reverse('minhas-notas'), {'ano': str(self.ano - 1)})
        self.assertContains(
            self.client.get(reverse('minhas-notas')), f'href="{reverse("gerar-relatorio")}?ano={self.ano - 1}"'
        )
        self.assertIn(f'Turma: 1A  ·  Ano: {self.ano}', self._textos_do_pdf(f'?ano={self.ano}'))
        # Sem ano válido no link, vale o da sessão
        self.assertIn(f'Turma: -  ·  Ano: {self.ano - 1}', self._textos_do_pdf('?ano=abc'))

    def test_disciplina_sem_registro_nao_deixa_fechar_o_ano(self):
        self._nota(self.matematica)
        textos = self._textos_do_pdf()
        self.assertIn('Situação Final: Em andamento', textos)
        self.assertIn('1 aprovadas  ·  0 em exame  ·  0 reprovadas  ·  2 cursando', textos)

    def test_disciplina_sem_registro_aparece_como_cursando(self):
        self._nota(self.matematica)
        textos = self._textos_do_pdf()
        self.assertIn('Português', textos)
        self.assertIn('História', textos)
        self.assertEqual(textos.count('Cursando'), 2)
        self.assertNotIn('Educação física', textos)

    def test_com_tudo_fechado_continua_aprovado_de_ano(self):
        self._todas_aprovadas()
        self.assertIn('Situação Final: Aprovado de ano', self._textos_do_pdf())


class EdicaoDeNotaTests(DadosDasTelas):
    def setUp(self):
        self.client.force_login(self.professor)
        self.nota = self._nota(self.matematica, situacao='aprovado', media_final=6.2, nota_exame=7.0)
        self.na_prova = NotaAvaliacao.objects.create(nota=self.nota, avaliacao=self.prova, nota_obtida=6.0)
        self.na_trabalho = NotaAvaliacao.objects.create(nota=self.nota, avaliacao=self.trabalho, nota_obtida=6.5)
        self.url = reverse('editar-nota', args=[self.nota.id])

    def _salvar(self, prova, trabalho):
        return self.client.post(self.url, {
            f'nota_avaliacao_{self.prova.id}': prova,
            f'nota_avaliacao_{self.trabalho.id}': trabalho,
        })

    def _campo(self, resposta, campo):
        html = resposta.content.decode()
        return re.search(rf'<input[^>]*id="{campo}"[^>]*value="([^"]*)"', html, re.S).group(1)

    def test_salvar_sem_mudar_nada_mantem_o_exame(self):
        self._salvar('6', '6.5')
        self.nota.refresh_from_db()
        self.assertEqual((self.nota.media_final, self.nota.nota_exame, self.nota.situacao), (6.2, 7.0, 'aprovado'))

    def test_nota_com_mais_casas_salva_como_o_formulario_mostra_nao_e_mudanca(self):
        NotaAvaliacao.objects.filter(id=self.na_prova.id).update(nota_obtida=6.125)
        self.assertContains(self.client.get(self.url), 'value="6.13"')

        self._salvar('6.13', '6.5')

        self.na_prova.refresh_from_db()
        self.nota.refresh_from_db()
        self.assertEqual(self.na_prova.nota_obtida, 6.125)
        self.assertEqual(self.nota.nota_exame, 7.0)

    def test_mudar_uma_nota_recalcula_e_mantem_o_exame(self):
        self._salvar('7', '6.5')
        self.nota.refresh_from_db()
        # 7 x 60% + 6,5 x 40% = 6,8 cai no exame, e com o exame 7 já lançado: (6,8 + 7) / 2 = 6,9
        self.assertEqual((self.nota.media_final, self.nota.nota_exame, self.nota.situacao), (6.8, 7.0, 'aprovado'))

    def test_falha_no_recalculo_nao_deixa_a_nota_nova_com_a_media_antiga(self):
        with mock.patch('aluno.views._recalcular_media_disciplina', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self._salvar('7', '6.5')
        self.na_prova.refresh_from_db()
        self.assertEqual(self.na_prova.nota_obtida, 6.0)

    def test_limpar_uma_nota_volta_para_cursando(self):
        self._salvar('', '6.5')
        self.na_prova.refresh_from_db()
        self.nota.refresh_from_db()
        self.assertIsNone(self.na_prova.nota_obtida)
        # Sem média volta a cursando, mas o exame lançado fica
        self.assertEqual((self.nota.media_final, self.nota.nota_exame, self.nota.situacao), (None, 7.0, 'cursando'))

    def test_lancar_nota_zero_conta_como_mudanca(self):
        self._salvar('0', '6.5')
        self.na_prova.refresh_from_db()
        self.nota.refresh_from_db()
        self.assertEqual(self.na_prova.nota_obtida, 0.0)
        # 0 x 60% + 6,5 x 40%
        self.assertEqual((self.nota.media_final, self.nota.situacao), (2.6, 'reprovado'))

    def test_tela_mostra_a_situacao_gravada_de_quem_passou_no_exame(self):
        self.assertEqual(self._campo(self.client.get(self.url), 'situacao'), 'Aprovado')

    def test_tela_mostra_exame_para_quem_esta_em_exame(self):
        Nota.objects.filter(id=self.nota.id).update(situacao='exame', nota_exame=None)
        self.assertEqual(self._campo(self.client.get(self.url), 'situacao'), 'Exame')

    def test_tela_mostra_cursando_para_quem_nao_tem_media(self):
        Nota.objects.filter(id=self.nota.id).update(situacao='cursando', media_final=None, nota_exame=None)
        self.assertEqual(self._campo(self.client.get(self.url), 'situacao'), 'Cursando')

    def test_tela_nao_arredonda_a_media_de_fronteira(self):
        Nota.objects.filter(id=self.nota.id).update(media_final=6.99)
        self.assertEqual(self._campo(self.client.get(self.url), 'media_final'), '6.99')


class CadastroDeDisciplinaSemAvaliacaoTests(DadosDasTelas):
    def test_deixa_trocar_de_disciplina(self):
        self.client.force_login(self.professor)
        url = reverse('cadastrar-notas')
        turma = self.matriculas['Educação física'].turma

        resposta = self.client.post(
            url, {'acao': 'selecionar_turma', 'disciplina': self.educacao_fisica.id, 'turma': turma.id}, follow=True
        )
        self.assertTrue(resposta.context['sem_avaliacoes'])
        self.assertContains(resposta, f'href="{url}?limpar=1"')

        resposta = self.client.get(f'{url}?limpar=1', follow=True)
        self.assertContains(resposta, 'id="gate-disciplina"')


class NotaNaoLancadaTests(DadosDasTelas):
    """Na edição, a nota em branco é só para ver: nota nova entra pelo Cadastro."""

    def setUp(self):
        self.client.force_login(self.professor)
        self.nota = self._nota(self.matematica, situacao='cursando', media_final=None)
        self.na_prova = NotaAvaliacao.objects.create(nota=self.nota, avaliacao=self.prova, nota_obtida=6.0)
        self.turma = self.matriculas['Matemática'].turma
        self.url_edicao = reverse('editar-nota', args=[self.nota.id])

    def _lista(self):
        resposta = self.client.post(reverse('lista-notas'), {'disciplina': self.matematica.id, 'turma': self.turma.id}, follow=True)
        return resposta.content.decode()

    def _cadastro(self):
        resposta = self.client.post(reverse('cadastrar-notas'), {
            'acao': 'selecionar_turma', 'disciplina': self.matematica.id, 'turma': self.turma.id,
        }, follow=True)
        return resposta.content.decode()

    def _salvar_celula(self, avaliacao, valor, **extras):
        return self.client.post(reverse('editar-nota-avaliacao-ajax'), {
            'aluno': self.aluna.id, 'avaliacao': avaliacao.id, 'turma': self.turma.id, 'valor': valor, **extras,
        }).json()

    def _gravada(self, avaliacao):
        return NotaAvaliacao.objects.filter(nota=self.nota, avaliacao=avaliacao).values_list('nota_obtida', flat=True).first()

    def _campo(self, avaliacao):
        html = self.client.get(self.url_edicao).content.decode()
        return re.search(rf'<input[^>]*name="nota_avaliacao_{avaliacao.id}"[^>]*>', html, re.S).group(0)

    # ---------- Tabela da Lista de Notas ----------

    def test_lista_abre_so_a_nota_lancada(self):
        html = self._lista()
        self.assertRegex(html, rf'<td class="editavel" data-avaliacao="{self.prova.id}"[^>]*>6\.0</td>')
        self.assertNotIn(f'data-avaliacao="{self.trabalho.id}"', html)

    def test_lista_mostra_a_nota_nao_lancada_em_branco_sem_tirar_a_coluna(self):
        linha = re.search(r'<tr data-aluno.*?</tr>', self._lista(), re.S).group(0)
        self.assertEqual(len(re.findall(r'<td\b', linha)), 8)
        self.assertIn('<td></td>', linha)

    def test_cadastro_abre_so_a_nota_nao_lancada(self):
        html = self._cadastro()
        self.assertRegex(html, rf'<td class="editavel" data-avaliacao="{self.trabalho.id}"[^>]*></td>')
        self.assertNotIn(f'data-avaliacao="{self.prova.id}"', html)
        self.assertIn('<td>6.0</td>', html)

    def test_celula_da_lista_recusa_nota_nao_lancada(self):
        resposta = self._salvar_celula(self.trabalho, '7')
        self.assertFalse(resposta['ok'])
        self.assertIn('Cadastro', resposta['erro'])
        self.assertIsNone(self._gravada(self.trabalho))

    def test_celula_da_lista_recusa_mesmo_com_o_registro_vazio(self):
        NotaAvaliacao.objects.create(nota=self.nota, avaliacao=self.trabalho, nota_obtida=None)
        self.assertFalse(self._salvar_celula(self.trabalho, '7')['ok'])
        self.assertIsNone(self._gravada(self.trabalho))

    def test_celula_da_lista_continua_corrigindo_nota_lancada(self):
        self.assertTrue(self._salvar_celula(self.prova, '7')['ok'])
        self.assertEqual(self._gravada(self.prova), 7.0)

    def test_nota_limpa_na_lista_nao_volta_pela_lista(self):
        resposta = self._salvar_celula(self.prova, '')
        self.assertEqual((resposta['ok'], resposta['valor']), (True, ''))
        self.assertIsNone(self._gravada(self.prova))
        self.assertFalse(self._salvar_celula(self.prova, '7')['ok'])
        self.assertIsNone(self._gravada(self.prova))

    def test_cadastro_continua_lancando_a_nota_em_branco(self):
        resposta = self._salvar_celula(self.trabalho, '7', so_lancar='1')
        self.assertTrue(resposta['ok'])
        # 6 x 60% + 7 x 40%
        self.assertEqual((resposta['media_final'], resposta['situacao']), ('6.4', 'Exame'))

    def test_cadastro_lanca_nota_nova_com_exame_lancado_e_mantem_o_exame(self):
        Nota.objects.filter(id=self.nota.id).update(nota_exame=8.0)
        resposta = self._salvar_celula(self.trabalho, '7', so_lancar='1')
        self.assertTrue(resposta['ok'])
        self.assertEqual(self._gravada(self.trabalho), 7.0)
        self.nota.refresh_from_db()
        # 6 x 60% + 7 x 40% = 6,4 cai no exame, e com o exame 8 já lançado: (6,4 + 8) / 2 = 7,2
        self.assertEqual((self.nota.media_final, self.nota.nota_exame, self.nota.situacao), (6.4, 8.0, 'aprovado'))

    # ---------- Exame ----------

    def _em_exame(self, nota_exame=None):
        NotaAvaliacao.objects.create(nota=self.nota, avaliacao=self.trabalho, nota_obtida=6.5)
        Nota.objects.filter(id=self.nota.id).update(situacao='exame', media_final=6.2, nota_exame=nota_exame)

    def test_lista_mostra_o_exame_nao_lancado_so_para_ver(self):
        self._em_exame()
        html = self._lista()
        self.assertInHTML('<td class="col-exame">-</td>', html)
        self.assertNotIn('data-nota=', html)

    def test_lista_mostra_o_exame_ja_lancado(self):
        self._em_exame(nota_exame=7.0)
        self.assertInHTML('<td class="col-exame">7.0</td>', self._lista())

    def test_cadastro_continua_abrindo_o_exame_nao_lancado(self):
        self._em_exame()
        self.assertInHTML(
            f'<td class="col-exame editavel" data-nota="{self.nota.id}" data-rotulo="Ana, Exame"></td>', self._cadastro()
        )

    # ---------- Tela de edição ----------

    def test_tela_de_edicao_deixa_a_nota_nao_lancada_so_para_ver(self):
        self.assertIn('disabled', self._campo(self.trabalho))

    def test_tela_de_edicao_deixa_a_nota_lancada_aberta(self):
        self.assertNotIn('disabled', self._campo(self.prova))

    def test_campo_bloqueado_nao_apaga_nota_lancada_depois_em_outra_aba(self):
        # A tela abriu com o Trabalho em branco, e a nota entrou pelo Cadastro antes de salvar.
        # O navegador não envia campo bloqueado, então só a Prova chega aqui.
        self.assertIn('disabled', self._campo(self.trabalho))
        Nota.objects.filter(id=self.nota.id).update(situacao='exame', media_final=6.2, nota_exame=8.0)
        NotaAvaliacao.objects.create(nota=self.nota, avaliacao=self.trabalho, nota_obtida=6.5)

        self.client.post(self.url_edicao, {f'nota_avaliacao_{self.prova.id}': '6'})

        self.assertEqual(self._gravada(self.trabalho), 6.5)
        self.nota.refresh_from_db()
        self.assertEqual((self.nota.media_final, self.nota.nota_exame), (6.2, 8.0))

    def test_campo_aberto_enviado_em_branco_continua_limpando_a_nota(self):
        self.client.post(self.url_edicao, {f'nota_avaliacao_{self.prova.id}': ''})
        self.assertIsNone(self._gravada(self.prova))

    def test_tela_de_edicao_recusa_nota_nao_lancada(self):
        resposta = self.client.post(self.url_edicao, {
            f'nota_avaliacao_{self.prova.id}': '7', f'nota_avaliacao_{self.trabalho.id}': '8',
        }, follow=True)
        self.assertContains(resposta, 'Cadastro de Notas')
        self.assertIsNone(self._gravada(self.trabalho))
        self.assertEqual(self._gravada(self.prova), 6.0)

    def test_tela_de_edicao_continua_corrigindo_nota_lancada(self):
        self.client.post(self.url_edicao, {
            f'nota_avaliacao_{self.prova.id}': '7', f'nota_avaliacao_{self.trabalho.id}': '',
        })
        self.assertEqual(self._gravada(self.prova), 7.0)


class ConfigurarAvaliacoesTests(DadosDasTelas):
    """O modo de cálculo só vai para o banco quando a configuração é salva, com a primeira avaliação."""

    def setUp(self):
        self.client.force_login(self.professor)
        self.url = reverse('configurar-avaliacoes')

    def _modo_da_educacao_fisica(self):
        return Disciplina.objects.get(id=self.educacao_fisica.id).modo_calculo

    def _escolher(self, modo):
        return self.client.post(self.url, {'disciplina': self.educacao_fisica.id, 'modo': modo}, follow=True)

    def _salvar_avaliacao(self, valor):
        return self.client.post(reverse('cadastrar-avaliacao-ajax'), {
            'nome': 'Prova 1', 'tipo': 'prova', 'valor': valor, 'disciplina': self.educacao_fisica.id,
        }).json()

    def test_escolher_o_modo_abre_a_configuracao_sem_gravar(self):
        resposta = self._escolher('soma')
        self.assertEqual(resposta.context['disciplina_atual'].id, self.educacao_fisica.id)
        self.assertEqual(resposta.context['disciplina_atual'].modo_calculo, 'soma')
        self.assertIsNone(self._modo_da_educacao_fisica())

    def test_voltar_para_a_tela_mantem_a_escolha_sem_gravar(self):
        self._escolher('soma')
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.context['disciplina_atual'].modo_calculo, 'soma')
        self.assertIsNone(self._modo_da_educacao_fisica())

    def test_abrir_a_tela_nao_grava_o_modo_guardado_na_sessao(self):
        self._escolher('soma')
        self.client.get(self.url, follow=True)
        self.assertIsNone(self._modo_da_educacao_fisica())

    def test_salvar_a_primeira_avaliacao_grava_o_modo(self):
        self._escolher('soma')
        self.assertTrue(self._salvar_avaliacao('10')['ok'])
        self.assertEqual(self._modo_da_educacao_fisica(), 'soma')
        self.assertEqual(self.educacao_fisica.avaliacao_set.count(), 1)

    def test_peso_da_primeira_avaliacao_segue_o_modo_escolhido(self):
        # Na soma dos pesos o total vai até 10: peso 60 é recusado e nada é gravado.
        self._escolher('soma')
        self.assertFalse(self._salvar_avaliacao('60')['ok'])
        self.assertIsNone(self._modo_da_educacao_fisica())
        self.assertFalse(self.educacao_fisica.avaliacao_set.exists())

    def test_sem_modo_escolhido_nao_salva_avaliacao(self):
        self.assertFalse(self._salvar_avaliacao('10')['ok'])
        self.assertFalse(self.educacao_fisica.avaliacao_set.exists())

    def test_disciplina_com_avaliacao_mantem_o_modo_gravado(self):
        self.client.post(self.url, {'disciplina': self.matematica.id, 'modo': 'ponderada'})
        resposta = self.client.post(reverse('cadastrar-avaliacao-ajax'), {
            'nome': 'Prova 2', 'tipo': 'prova', 'valor': '0', 'disciplina': self.matematica.id,
        }).json()
        self.assertTrue(resposta['ok'])
        self.assertEqual(Disciplina.objects.get(id=self.matematica.id).modo_calculo, 'ponderada')


class SalvarAvaliacoesTests(DadosDasTelas):
    """O botão Salvar de Configurar Avaliações grava tudo de uma vez, depois da confirmação."""

    def setUp(self):
        self.client.force_login(self.professor)
        self.url = reverse('configurar-avaliacoes')

    def _salvar(self, disciplina, novas=(), **campos):
        """Mesmo envio do "Sim, salvar": as linhas novas e os campos das já gravadas (nome_<id>, tipo_<id>, valor_<id>)."""
        dados = {
            'disciplina': disciplina.id,
            'nova_nome': [nome for nome, _, _ in novas],
            'nova_tipo': [tipo for _, tipo, _ in novas],
            'nova_valor': [valor for _, _, valor in novas],
        }
        dados.update(campos)
        return self.client.post(reverse('salvar-avaliacoes-ajax'), dados).json()

    def _pesos_da_matematica(self):
        return list(Avaliacao.objects.filter(disciplina=self.matematica).order_by('id').values_list('nome', 'tipo', 'valor'))

    def test_tela_tem_o_salvar_com_confirmacao(self):
        self.client.post(self.url, {'disciplina': self.matematica.id, 'modo': 'ponderada'})
        resposta = self.client.get(self.url)
        self.assertContains(resposta, 'id="btn-salvar-avaliacoes"')
        self.assertContains(resposta, 'id="modalSalvar"')
        self.assertContains(resposta, 'data-nome="as avaliações de Matemática"')

    def test_salvar_grava_as_novas_e_o_modo_da_primeira_configuracao(self):
        self.client.post(self.url, {'disciplina': self.educacao_fisica.id, 'modo': 'soma'})
        resposta = self._salvar(self.educacao_fisica, novas=[('Prova 1', 'prova', '6'), ('Trabalho', 'trabalho', '4,0')])
        self.assertTrue(resposta['ok'])
        self.assertEqual(Disciplina.objects.get(id=self.educacao_fisica.id).modo_calculo, 'soma')
        self.assertEqual(
            list(self.educacao_fisica.avaliacao_set.order_by('id').values_list('nome', 'tipo', 'valor')),
            [('Prova 1', 'prova', 6.0), ('Trabalho', 'trabalho', 4.0)],
        )
        self.assertContains(self.client.get(self.url), 'Avaliações salvas com sucesso!')

    def test_acima_do_limite_nao_grava_nada(self):
        self.client.post(self.url, {'disciplina': self.educacao_fisica.id, 'modo': 'soma'})
        resposta = self._salvar(self.educacao_fisica, novas=[('Prova 1', 'prova', '6'), ('Prova 2', 'prova', '5')])
        self.assertFalse(resposta['ok'])
        self.assertIn('não pode passar de 10', resposta['erro'])
        self.assertIsNone(Disciplina.objects.get(id=self.educacao_fisica.id).modo_calculo)
        self.assertFalse(self.educacao_fisica.avaliacao_set.exists())

    def test_sem_modo_escolhido_nao_grava(self):
        resposta = self._salvar(self.educacao_fisica, novas=[('Prova 1', 'prova', '10')])
        self.assertFalse(resposta['ok'])
        self.assertFalse(self.educacao_fisica.avaliacao_set.exists())

    def test_trocar_os_pesos_de_lugar_e_aceito(self):
        # Gravando uma de cada vez, 40 para 60 antes de 60 para 40 passaria de 100 no meio do caminho.
        resposta = self._salvar(self.matematica, **{
            f'valor_{self.trabalho.id}': '60', f'valor_{self.prova.id}': '40',
        })
        self.assertTrue(resposta['ok'])
        self.assertEqual(self._pesos_da_matematica(), [('Prova', 'prova', 40.0), ('Trabalho', 'trabalho', 60.0)])

    def test_nova_avaliacao_conta_no_limite_junto_com_as_gravadas(self):
        resposta = self._salvar(self.matematica, novas=[('Prova 2', 'prova', '10')])
        self.assertFalse(resposta['ok'])
        self.assertIn('Ficaria em 110', resposta['erro'])
        self.assertEqual(Avaliacao.objects.filter(disciplina=self.matematica).count(), 2)

    def test_mudar_o_peso_recalcula_a_mf_e_mantem_o_exame(self):
        nota = self._nota(self.matematica, situacao='aprovado', media_final=5.2, nota_exame=8.0)
        NotaAvaliacao.objects.create(nota=nota, avaliacao=self.prova, nota_obtida=4)
        NotaAvaliacao.objects.create(nota=nota, avaliacao=self.trabalho, nota_obtida=7)
        self._salvar(self.matematica, **{f'valor_{self.prova.id}': '40', f'valor_{self.trabalho.id}': '60'})
        nota.refresh_from_db()
        # 4 x 40% + 7 x 60% = 5,8; com o exame 8, (5,8 + 8) / 2 = 6,9
        self.assertEqual((nota.media_final, nota.nota_exame, nota.situacao), (5.8, 8.0, 'aprovado'))

    def test_salvar_sem_mudar_o_peso_nao_recalcula(self):
        with mock.patch('aluno.views._recalcular_notas_da_disciplina') as recalcular:
            resposta = self._salvar(self.matematica, **{
                f'nome_{self.prova.id}': 'Prova final', f'tipo_{self.prova.id}': 'trabalho',
                f'valor_{self.prova.id}': '60,0', f'valor_{self.trabalho.id}': '40',
            })
        self.assertTrue(resposta['ok'])
        recalcular.assert_not_called()
        self.assertEqual(self._pesos_da_matematica(), [('Prova final', 'trabalho', 60.0), ('Trabalho', 'trabalho', 40.0)])

    def test_campo_que_nao_veio_no_envio_fica_como_esta(self):
        self.assertTrue(self._salvar(self.matematica, **{f'nome_{self.trabalho.id}': 'Seminário'})['ok'])
        self.assertEqual(self._pesos_da_matematica(), [('Prova', 'prova', 60.0), ('Seminário', 'trabalho', 40.0)])

    def test_valor_recusado_nao_grava_nada(self):
        for campos, erro in (
            ({f'nome_{self.prova.id}': ' '}, 'Informe o nome da avaliação.'),
            ({f'valor_{self.prova.id}': 'abc'}, 'Valor inválido.'),
            ({f'valor_{self.prova.id}': '-1'}, 'O valor da avaliação deve ser um número de 0 para cima.'),
            ({f'valor_{self.prova.id}': 'nan'}, 'O valor da avaliação deve ser um número de 0 para cima.'),
            ({f'tipo_{self.prova.id}': 'outro'}, 'Tipo inválido.'),
            ({f'nome_{self.prova.id}': 'x' * 51}, 'O nome da avaliação pode ter até 50 caracteres.'),
        ):
            # Uma mudança válida junto com a recusada também não é gravada
            campos[f'nome_{self.trabalho.id}'] = 'Seminário'
            resposta = self._salvar(self.matematica, **campos)
            self.assertEqual((resposta['ok'], resposta['erro']), (False, erro))
            self.assertEqual(self._pesos_da_matematica(), [('Prova', 'prova', 60.0), ('Trabalho', 'trabalho', 40.0)])

    def test_linha_nova_sem_nome_e_recusada(self):
        resposta = self._salvar(self.historia, novas=[('', 'prova', '')])
        self.assertEqual((resposta['ok'], resposta['erro']), (False, 'Informe o nome da avaliação.'))
        self.assertEqual(self.historia.avaliacao_set.count(), 1)

    def test_so_o_professor_salva(self):
        self.client.logout()
        resposta = self.client.post(reverse('salvar-avaliacoes-ajax'), {'disciplina': self.matematica.id})
        self.assertRedirects(resposta, reverse('login'), fetch_redirect_response=False)
        self.client.force_login(self.aluna)
        resposta = self.client.post(reverse('salvar-avaliacoes-ajax'), {'disciplina': self.matematica.id})
        self.assertEqual(resposta.status_code, 403)


class MenuAbreAsTelasSemFiltroTests(DadosDasTelas):
    """
    Pelo menu cada tela abre sem filtro. O filtro escolhido volta por GET e fica guardado, então a seta
    voltar do navegador reabre a tela anterior com o filtro que ela tinha.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user('adm', password='senha-do-adm')
        cls.admin.groups.add(Group.objects.get_or_create(name='administrador')[0])

    def test_menu_do_professor_abre_as_telas_sem_filtro(self):
        self.client.force_login(self.professor)
        resposta = self.client.get(reverse('cadastrar-notas'))
        for nome in ('diario-classe', 'lista-notas', 'cadastrar-notas', 'configurar-avaliacoes'):
            self.assertContains(resposta, f'href="{reverse(nome)}?limpar=1"')

    def test_menu_do_aluno_abre_o_boletim_sem_filtro(self):
        self.client.force_login(self.aluna)
        self.assertContains(self.client.get(reverse('minhas-notas')), f'href="{reverse("minhas-notas")}?limpar=1"')

    def test_menu_do_administrador_abre_as_telas_sem_filtro(self):
        self.client.force_login(self.admin)
        resposta = self.client.get(reverse('gestao-usuarios'))
        for nome in ('gestao-disciplinas', 'gestao-turmas', 'gestao-matriculas', 'gestao-usuarios'):
            self.assertContains(resposta, f'href="{reverse(nome)}?limpar=1"')
        # O painel guarda o ano na própria URL, então o link puro já abre sem filtro
        self.assertContains(resposta, f'href="{reverse("dashboard-admin")}"')

    def test_filtro_volta_por_get_e_o_limpar_tira_o_filtro_da_tela(self):
        turma_1a = self.matriculas['Matemática'].turma
        casos = [
            (self.professor, 'lista-notas', {'disciplina': self.matematica.id, 'turma': turma_1a.id},
             ['filtro_notas_disciplina', 'filtro_notas_turma']),
            (self.professor, 'configurar-avaliacoes', {'disciplina': self.educacao_fisica.id, 'modo': 'soma'},
             ['config_aval_disciplina', 'config_aval_modo']),
            (self.aluna, 'minhas-notas', {'ano': str(self.ano - 1)}, ['boletim_ano']),
            (self.admin, 'gestao-disciplinas', {'professor': self.professor.id, 'disciplina': ''},
             ['filtro_gestao_professor', 'filtro_gestao_disciplina']),
            (self.admin, 'gestao-turmas', {'disciplina': '', 'turma': '1A'},
             ['filtro_gestao_turma_disciplina', 'filtro_gestao_turma_nome']),
            (self.admin, 'gestao-matriculas', {'turma': '1A', 'aluno': ''},
             ['filtro_gestao_mat_turma', 'filtro_gestao_mat_aluno']),
            (self.admin, 'gestao-usuarios', {'grupo': 'aluno'}, ['filtro_gestao_usuario_grupo']),
        ]
        for usuario, nome, filtro, chaves in casos:
            with self.subTest(nome):
                self.client.force_login(usuario)
                url = reverse(nome)
                self.assertRedirects(self.client.post(url, filtro), url, fetch_redirect_response=False)
                self.assertTrue(all(chave in self.client.session for chave in chaves))
                self.assertEqual(self.client.get(url).status_code, 200)

                self.assertRedirects(self.client.get(f'{url}?limpar=1'), url, fetch_redirect_response=False)
                self.assertFalse(any(chave in self.client.session for chave in chaves))

    def test_abrir_outra_tela_pelo_menu_mantem_o_filtro_da_anterior(self):
        self.client.force_login(self.professor)
        turma_1a = self.matriculas['Matemática'].turma
        self.client.post(reverse('lista-notas'), {'disciplina': self.matematica.id, 'turma': turma_1a.id})
        self.client.get(f"{reverse('cadastrar-notas')}?limpar=1")
        resposta = self.client.get(reverse('lista-notas'))
        self.assertEqual(resposta.context['disciplina_selecionada'], str(self.matematica.id))
        self.assertEqual(resposta.context['turma_selecionada'], str(turma_1a.id))

    def test_boletim_pelo_menu_volta_para_o_ano_atual(self):
        self.client.force_login(self.aluna)
        self.client.post(reverse('minhas-notas'), {'ano': str(self.ano - 1)})
        self.assertEqual(self.client.get(reverse('minhas-notas')).context['ano_selecionado'], str(self.ano - 1))
        self.client.get(f"{reverse('minhas-notas')}?limpar=1")
        self.assertEqual(self.client.get(reverse('minhas-notas')).context['ano_selecionado'], str(self.ano))


class PainelDoAdministradorTests(DadosDasTelas):
    def test_disciplina_sem_avaliacao_nao_entra_no_painel(self):
        admin = User.objects.create_user('adm', password='senha-do-adm')
        admin.groups.add(Group.objects.get_or_create(name='administrador')[0])
        self.client.force_login(admin)

        resposta = self.client.get(reverse('dashboard-admin'))

        geral = resposta.context['dados_graficos']['geral']['disciplinas']
        self.assertEqual([d['nome'] for d in geral], ['História', 'Matemática', 'Português'])
        self.assertEqual(sum(d['cursando'] for d in geral), 3)
        turma = resposta.context['turmas'][0]
        self.assertNotIn('Educação física', [d['nome'] for d in turma['disciplinas']])


class MediaZeroNoBoletimTests(DadosDasTelas):
    def test_boletim_mostra_zero(self):
        self._nota(self.matematica, situacao='reprovado', media_final=0.0)
        self.client.force_login(self.aluna)
        resposta = self.client.get(reverse('minhas-notas'))
        self.assertContains(resposta, '<td class="nota-reprovado">0.0</td>', html=True)

    def test_boletim_mostra_traco_para_quem_nao_tem_media(self):
        self._nota(self.matematica, situacao='cursando', media_final=None)
        self.client.force_login(self.aluna)
        resposta = self.client.get(reverse('minhas-notas'))
        self.assertContains(resposta, '<td class="nota-cursando">-</td>', html=True)
        self.assertNotContains(resposta, '<td class="nota-cursando">None</td>', html=True)
