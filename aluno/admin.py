from django.contrib import admin
from aluno.models import Turma, Matricula, Disciplina, Nota


@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ("nome", "disciplina")
    search_fields = ("nome", "disciplina")
    list_filter = ("nome", "disciplina")


@admin.register(Matricula)
class MatriculaAdmin(admin.ModelAdmin):
    list_display = ("aluno", "turma")
    search_fields = ("aluno", "turma")
    list_filter = ("aluno", "turma")


@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ("nome", "professor")
    search_fields = ("nome", "professor")
    list_filter = ("nome", "professor")


@admin.register(Nota)
class NotaAdmin(admin.ModelAdmin):
    list_display = ("aluno", "disciplina", "nota", "tipo", "situacao", "media_final")
    search_fields = ("aluno", "disciplina")
    list_filter = ("aluno", "disciplina")