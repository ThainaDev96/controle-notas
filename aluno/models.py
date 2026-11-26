from django.db import models
from django.db.models import Sum
from django.contrib.auth.models import User

LIMITE_MODO_CALCULO = {'soma': 10, 'ponderada': 100}


class Disciplina(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    professor = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Professor")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    modo_calculo = models.CharField(max_length=20, null=True, blank=True, choices=[
        ("soma", "Soma dos pesos"),
        ("ponderada", "Média ponderada"),
        ("aritmetica", "Média aritmética"),
    ], verbose_name="Modo de cálculo")

    def __str__(self):
        return self.nome

    @property
    def rotulo_valor(self):
        ROTULOS_VALOR = {
            'soma': 'Peso (pontos)',
            'ponderada': 'Peso (%)',
            'aritmetica': 'Valor',
        }
        return ROTULOS_VALOR[self.modo_calculo]

    @property
    def limite_valor_avaliacoes(self):
        return LIMITE_MODO_CALCULO.get(self.modo_calculo)

    def total_valor_avaliacoes(self, excluir_id=None):
        qs = self.avaliacao_set.all()
        if excluir_id:
            qs = qs.exclude(id=excluir_id)
        return qs.aggregate(total=Sum('valor'))['total'] or 0


class Turma(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    ano = models.PositiveIntegerField(verbose_name="Ano")

    def __str__(self):
        return f"{self.nome} - {self.disciplina.nome}"


class Matricula(models.Model):
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Aluno")
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE, verbose_name="Turma")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")


class Nota(models.Model):
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, verbose_name="Matrícula")
    situacao = models.CharField(max_length=20, choices=[
        ("aprovado", "Aprovado"),
        ("recuperacao", "Em recuperação"),
        ("exame", "Exame"),
        ("reprovado", "Reprovado"),
        ("cursando", "Cursando"),
    ], verbose_name="Situação")
    media_final = models.FloatField(verbose_name="Média Final", null=True, blank=True)
    nota_exame = models.FloatField(verbose_name="Nota do Exame", null=True, blank=True)

    @property
    def aluno(self):
        return self.matricula.aluno

    @property
    def aluno_id(self):
        return self.matricula.aluno_id

    @property
    def turma(self):
        return self.matricula.turma

    @property
    def disciplina(self):
        return self.matricula.turma.disciplina

    @property
    def disciplina_id(self):
        return self.matricula.turma.disciplina_id


class Avaliacao(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    tipo = models.CharField(max_length=20, choices=[
            ("prova", "Prova"),
            ("trabalho", "Trabalho"),
            ("atividade_aula", "Atividade em aula"),
        ], verbose_name="Tipo", default="prova")
    valor = models.FloatField(verbose_name="valor", null=True, blank=True)
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")


class NotaAvaliacao(models.Model):
    nota = models.ForeignKey(Nota, on_delete=models.CASCADE, verbose_name="Nota")
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, verbose_name="Avaliação")
    nota_obtida = models.FloatField(db_column='nota', verbose_name="Nota obtida", null=True, blank=True)

    class Meta:
        unique_together = ('nota', 'avaliacao')
