from django.db import models
from django.db.models import Sum
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

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

    def total_valor_avaliacoes(self, ano, excluir_id=None):
        qs = self.avaliacao_set.filter(ano=ano)
        if excluir_id:
            qs = qs.exclude(id=excluir_id)
        return qs.aggregate(total=Sum('valor'))['total'] or 0


class Turma(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")
    alunos = models.ManyToManyField(User, related_name="turmas", verbose_name="Turmas")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")

    def __str__(self):
        return f"{self.nome} - {self.disciplina.nome}"


class Matricula(models.Model):
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Aluno")
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE, verbose_name="Disciplina")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")

    # def __str__(self):
    #     return self.aluno


class Nota(models.Model):
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Aluno")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")
    situacao = models.CharField(max_length=20, choices=[
        ("aprovado", "Aprovado"),
        ("recuperacao", "Em recuperação"),
        ("exame", "Exame"),
        ("reprovado", "Reprovado"),
        ("cursando", "Cursando"),
    ], verbose_name="Situação")
    media_final = models.FloatField(verbose_name="Média Final", null=True, blank=True)
    nota_exame = models.FloatField(verbose_name="Nota do Exame", null=True, blank=True)
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    ano = models.PositiveIntegerField(verbose_name="Ano", null=True)

    # def __str__(self):
    #     return self.aluno


@receiver(post_save, sender=Matricula)
def vincular_aluno_turma(sender, instance, created, **kwargs):
    if created:
        instance.turma.alunos.add(instance.aluno)


class Avaliacao(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    tipo = models.CharField(max_length=20, choices=[
            ("prova", "Prova"),
            ("trabalho", "Trabalho"),
            ("atividade_aula", "Atividade em aula"),
        ], verbose_name="Tipo", default="prova")
    valor = models.FloatField(verbose_name="valor", null=True, blank=True)
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")
    ano = models.PositiveIntegerField(verbose_name="Ano", null=True, blank=True)


class NotaAvaliacao(models.Model):
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Aluno")
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, verbose_name="Avaliação")
    nota = models.FloatField(verbose_name="Nota", null=True, blank=True)

    class Meta:
        unique_together = ('aluno', 'avaliacao')
