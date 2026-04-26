from django.db import models
from django.contrib.auth.models import User


class Disciplina(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    professor = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Professor")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")

    def __str__(self):
        return self.nome


class Turma(models.Model):
    nome = models.CharField(max_length=50, verbose_name="Nome")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")
    alunos = models.ManyToManyField(User, related_name="turmas", verbose_name="Turmas")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")

    def __str__(self):
        return self.nome


class Matricula(models.Model):
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Aluno")
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE, verbose_name="Disciplina")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")

    # def __str__(self):
    #     return self.aluno


class Nota(models.Model):
    aluno = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Aluno")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE, verbose_name="Disciplina")
    situacao = models.CharField(choices=[
        ("aprovado", "Aprovado"),
        ("recuperacao", "Em recuperação"),
        ("reprovado", "Reprovado"),
    ], verbose_name="Situação")
    nota_p1 = models.FloatField(verbose_name="P1", null=True)
    nota_p2 = models.FloatField(verbose_name="P2", null=True)
    nota_t1 = models.FloatField(verbose_name="T1", null=True)
    nota_t2 = models.FloatField(verbose_name="T2", null=True)
    media_final = models.FloatField(verbose_name="Média Final")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")

    # def __str__(self):
    #     return self.aluno
